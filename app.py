import time
import json
import websocket 
import threading
import os 
import sys 
import fcntl 
from flask import Flask, request, render_template_string, redirect, url_for, session, flash, g
from datetime import timedelta

# ==========================================================
# BOT CONSTANT SETTINGS
# ==========================================================
WSS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL = "R_100"          
DURATION = 1 
DURATION_UNIT = "t" 
MARTINGALE_STEPS = 4 
MAX_CONSECUTIVE_LOSSES = 3 # حد الخسارة: 3 خسارات متتالية
RECONNECT_DELAY = 1       
USER_IDS_FILE = "user_ids.txt"
ACTIVE_SESSIONS_FILE = "active_sessions.json" 
# ==========================================================

# ==========================================================
# BOT RUNTIME STATE (Runtime Cache)
# ==========================================================
active_threads = {} 
active_ws = {} 
is_contract_open = {} 
# Trading State Definitions
TRADE_STATE_DEFAULT = {"type": "DIGITOVER", "barrier": 1} 
TRADE_STATE_MARTINGALE = {"type": "DIGITUNDER", "barrier": 8} 

DEFAULT_SESSION_STATE = {
    "api_token": "",
    "base_stake": 1.0,
    "tp_target": 10.0,
    "is_running": False,
    "current_profit": 0.0,
    "current_stake": 1.0, 
    "consecutive_losses": 0,
    "current_step": 0,
    "total_wins": 0,
    "total_losses": 0,
    "current_trade_state": TRADE_STATE_DEFAULT,
    "stop_reason": "Stopped Manually" # ⚠️ سبب التوقف
}
# ==========================================================

# ==========================================================
# PERSISTENT STATE MANAGEMENT FUNCTIONS
# ==========================================================
def get_file_lock(f):
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    except Exception:
        pass

def release_file_lock(f):
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass

def load_persistent_sessions():
    if not os.path.exists(ACTIVE_SESSIONS_FILE):
        return {}
    
    with open(ACTIVE_SESSIONS_FILE, 'a+') as f:
        f.seek(0)
        get_file_lock(f)
        try:
            content = f.read()
            if content:
                data = json.loads(content)
            else:
                data = {}
        except json.JSONDecodeError:
            data = {}
        finally:
            release_file_lock(f)
            return data

def save_session_data(email, session_data):
    all_sessions = load_persistent_sessions()
    all_sessions[email] = session_data
    
    with open(ACTIVE_SESSIONS_FILE, 'w') as f:
        get_file_lock(f)
        try:
            json.dump(all_sessions, f, indent=4)
        except Exception as e:
            print(f"❌ ERROR saving session data: {e}")
        finally:
            release_file_lock(f)

def delete_session_data(email):
    all_sessions = load_persistent_sessions()
    if email in all_sessions:
        del all_sessions[email]
    
    with open(ACTIVE_SESSIONS_FILE, 'w') as f:
        get_file_lock(f)
        try:
            json.dump(all_sessions, f, indent=4)
        except Exception as e:
            print(f"❌ ERROR deleting session data: {e}")
        finally:
            release_file_lock(f)

def get_session_data(email):
    all_sessions = load_persistent_sessions()
    if email in all_sessions:
        data = all_sessions[email]
        # Ensure 'stop_reason' key exists for consistency
        if 'stop_reason' not in data:
            data['stop_reason'] = DEFAULT_SESSION_STATE['stop_reason']
        if 'current_trade_state' not in data:
             data['current_trade_state'] = TRADE_STATE_DEFAULT
        return data
    
    return DEFAULT_SESSION_STATE.copy()

def load_allowed_users():
    if not os.path.exists(USER_IDS_FILE):
        print(f"❌ ERROR: Missing {USER_IDS_FILE} file.")
        return set()
    try:
        with open(USER_IDS_FILE, 'r', encoding='utf-8') as f:
            users = {line.strip().lower() for line in f if line.strip()}
        return users
    except Exception as e:
        print(f"❌ ERROR reading {USER_IDS_FILE}: {e}")
        return set()
        
def stop_bot(email, clear_data=True, stop_reason="Stopped Manually"): 
    """ Stop the bot thread and clear WebSocket connection. """
    global is_contract_open 
    
    # 1. Close WebSocket connection if exists
    if email in active_ws and active_ws[email]:
        try:
            ws = active_ws[email]
            ws.send(json.dumps({"forget": "ticks", "symbol": SYMBOL}))
            ws.close()
        except:
            pass
        if email in active_ws:
             del active_ws[email]

    # 2. Update is_running state (crucial for the while True loop)
    current_data = get_session_data(email)
    if current_data.get("is_running") is True:
        current_data["is_running"] = False
        current_data["stop_reason"] = stop_reason # ⚠️ حفظ سبب التوقف
        save_session_data(email, current_data) # Save stop state

    # 3. Remove thread registration
    if clear_data and email in active_threads:
        del active_threads[email]
        
    if email in is_contract_open:
        is_contract_open[email] = False

    if clear_data:
        # 4. Delete the entire state (Explicit Stop/TP/SL)
        # We don't delete immediately if the reason is TP/SL so the index route can read it
        if stop_reason in ["SL Reached", "TP Reached"]:
             print(f"🛑 [INFO] Bot for {email} stopped ({stop_reason}). Data kept for display.")
        else:
             delete_session_data(email)
             print(f"🛑 [INFO] Bot for {email} stopped ({stop_reason}) and session data cleared from file.")
    else:
        # 4. Disconnection case (Soft Stop)
        print(f"⚠️ [INFO] WS closed for {email}. Attempting immediate reconnect.")

# ==========================================================
# TRADING BOT FUNCTIONS
# ==========================================================

def get_latest_price_digit(price):
    try:
        return int(str(price)[-1]) 
    except Exception:
        return -1

def calculate_martingale_stake(base_stake, current_stake, current_step):
    """ Martingale logic: multiply the losing stake by 7 (as requested) """
    if current_step == 0:
        return base_stake
        
    if current_step <= MARTINGALE_STEPS:
        return current_stake * 7 # Multiplier changed to 7
    else:
        return base_stake

def send_trade_order(email, stake, trade_type, barrier):
    """ Send the actual trade order using the passed contract type and barrier. """
    global is_contract_open 
    if email not in active_ws: return
    ws_app = active_ws[email]
    
    barrier_str = str(barrier)
    
    trade_request = {
        "buy": 1, "price": stake,
        "parameters": {
            "amount": stake, "basis": "stake",
            "contract_type": trade_type, "barrier": barrier_str, 
            "currency": "USD", "duration": DURATION,
            "duration_unit": DURATION_UNIT, "symbol": SYMBOL
        }
    }
    try:
        ws_app.send(json.dumps(trade_request))
        is_contract_open[email] = True 
        print(f"💰 [TRADE] Sent {trade_type} {barrier_str} with stake: {stake:.2f}")
    except Exception as e:
        print(f"❌ [TRADE ERROR] Could not send trade order: {e}")
        pass

def re_enter_immediately(email, last_loss_stake):
    """ Prepares state for the Martingale trade (Under 8). """
    current_data = get_session_data(email)
    
    new_stake = calculate_martingale_stake(
        current_data['base_stake'],
        last_loss_stake,
        current_data['current_step'] 
    )

    current_data['current_stake'] = new_stake
    current_data['current_trade_state'] = TRADE_STATE_MARTINGALE 
    save_session_data(email, current_data)


def check_pnl_limits(email, profit_loss):
    """ Update statistics and decide whether to re-enter immediately or wait. """
    global is_contract_open 
    
    is_contract_open[email] = False 

    current_data = get_session_data(email)
    if not current_data.get('is_running'): return

    last_stake = current_data['current_stake'] 

    current_data['current_profit'] += profit_loss
    
    if profit_loss > 0:
        # 1. Win: Reset and prepare for the base stake entry (Over 1)
        current_data['total_wins'] += 1
        current_data['current_step'] = 0 
        current_data['consecutive_losses'] = 0
        current_data['current_stake'] = current_data['base_stake']
        current_data['current_trade_state'] = TRADE_STATE_DEFAULT
        
    else:
        # 2. Loss: Increase step and attempt immediate recovery
        current_data['total_losses'] += 1
        current_data['consecutive_losses'] += 1
        current_data['current_step'] += 1
        
        # 2.1. Check Stop Loss (SL) limits
        if current_data['consecutive_losses'] >= MAX_CONSECUTIVE_LOSSES: 
            # 🛑 إيقاف بسبب حد الخسارة
            stop_bot(email, clear_data=True, stop_reason="SL Reached") 
            return 
        
        # 2.2. Immediate re-entry preparation
        save_session_data(email, current_data) 
        re_enter_immediately(email, last_stake) 
        return

    # 3. Check Take Profit (TP)
    if current_data['current_profit'] >= current_data['tp_target']:
        # ✅ إيقاف بسبب حد الربح
        stop_bot(email, clear_data=True, stop_reason="TP Reached") 
        return
    
    save_session_data(email, current_data)
        
    state = current_data['current_trade_state']
    print(f"[LOG {email}] PNL: {current_data['current_profit']:.2f}, Step: {current_data['current_step']}, Last Stake: {last_stake:.2f}, State: {state['type']} {state['barrier']}")


def bot_core_logic(email, token, stake, tp):
    """ Main bot logic with auto-reconnect loop. """
    global is_contract_open 

    is_contract_open[email] = False

    session_data = get_session_data(email)
    session_data.update({
        "api_token": token, "base_stake": stake, "tp_target": tp,
        "is_running": True, "current_stake": stake,
        "current_trade_state": TRADE_STATE_DEFAULT,
        "stop_reason": "Running" # تحديث سبب التوقف عند التشغيل
    })
    save_session_data(email, session_data)

    while True: 
        current_data = get_session_data(email)
        
        if not current_data.get('is_running'):
            break

        print(f"🔗 [THREAD] Attempting to connect for {email}...")

        def on_open_wrapper(ws_app):
            ws_app.send(json.dumps({"authorize": current_data['api_token']})) 
            ws_app.send(json.dumps({"ticks": SYMBOL, "subscribe": 1}))
            running_data = get_session_data(email)
            running_data['is_running'] = True
            save_session_data(email, running_data)
            print(f"✅ [THREAD] Connection established for {email}.")
            is_contract_open[email] = False 

        def on_message_wrapper(ws_app, message):
            data = json.loads(message)
            msg_type = data.get('msg_type')
            
            current_data = get_session_data(email) 
            if not current_data.get('is_running'):
                ws_app.close()
                return
                
            if msg_type == 'tick':
                if is_contract_open.get(email) is True: 
                    return 
                
                # لا يوجد شرط للرقم الأخير، الدخول في كل تيك
                current_trade_state = current_data['current_trade_state']
                stake_to_use = current_data['current_stake']
                
                # إرسال الصفقة باستخدام الحالة الحالية (Over 1 أو Under 8)
                send_trade_order(email, stake_to_use, 
                                current_trade_state['type'],
                                current_trade_state['barrier'])

            elif msg_type == 'buy':
                contract_id = data['buy']['contract_id']
                ws_app.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}))
            elif msg_type == 'proposal_open_contract':
                contract = data['proposal_open_contract']
                if contract.get('is_sold') == 1:
                    check_pnl_limits(email, contract['profit']) 
                    if 'subscription_id' in data: ws_app.send(json.dumps({"forget": data['subscription_id']}))

        def on_close_wrapper(ws_app, code, msg):
             stop_bot(email, clear_data=False, stop_reason="Disconnected (Auto-Retry)") 

        try:
            ws = websocket.WebSocketApp(
                WSS_URL, on_open=on_open_wrapper, on_message=on_message_wrapper, 
                on_error=lambda ws, err: print(f"[WS Error {email}] {err}"),
                on_close=on_close_wrapper 
            )
            active_ws[email] = ws
            ws.run_forever(ping_interval=20, ping_timeout=10) 
            
        except Exception as e:
            print(f"❌ [ERROR] WebSocket failed for {email}: {e}")
        
        if get_session_data(email).get('is_running') is False:
             break
        
        print(f"💤 [THREAD] Waiting {RECONNECT_DELAY} seconds before retrying connection for {email}...")
        time.sleep(RECONNECT_DELAY)

    if email in active_threads:
        del active_threads[email] 
    print(f"🛑 [THREAD] Bot process ended for {email}.")


# ==========================================================
# FLASK APP SETUP AND ROUTES
# ==========================================================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET_KEY', 'VERY_STRONG_SECRET_KEY_RENDER_BOT')
app.config['SESSION_PERMANENT'] = False 

# HTML TEMPLATE (AUTH_FORM)
AUTH_FORM = """
<!doctype html>
<title>Login - Deriv Bot</title>
<style>
    body { font-family: Arial, sans-serif; padding: 20px; max-width: 400px; margin: auto; }
    h1 { color: #007bff; }
    input[type="email"] { width: 100%; padding: 10px; margin-top: 5px; margin-bottom: 15px; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }
    button { background-color: blue; color: white; padding: 10px 15px; border: none; border-radius: 5px; cursor: pointer; }
</style>
<h1>Deriv Bot Login</h1>
<p>Please enter your authorized email address:</p>
{% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
        {% for category, message in messages %}
            <p style="color:red;">{{ message }}</p>
        {% endfor %}
    {% endif %}
{% endwith %}
<form method="POST" action="{{ url_for('login') }}">
    <label for="email">Email:</label><br>
    <input type="email" id="email" name="email" required><br><br>
    <button type="submit">Login</button>
</form>
"""

# HTML TEMPLATE (CONTROL_FORM)
CONTROL_FORM = """
<!doctype html>
<title>Control Panel</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
    body {
        font-family: Arial, sans-serif;
        padding: 10px;
        max-width: 600px;
        margin: auto;
        direction: ltr; /* English support */
        text-align: left;
    }
    h1 {
        color: #007bff;
        font-size: 1.8em;
        border-bottom: 2px solid #eee;
        padding-bottom: 10px;
    }
    p {
        font-size: 1.1em;
        line-height: 1.6;
    }
    .status-running {
        color: green;
        font-weight: bold;
        font-size: 1.3em;
    }
    .status-stopped {
        color: red;
        font-weight: bold;
        font-size: 1.3em;
    }
    form button {
        padding: 12px 20px;
        border: none;
        border-radius: 5px;
        cursor: pointer;
        font-size: 1.1em;
        margin-top: 15px;
        width: 100%; /* Full width button */
    }
    input[type="text"], input[type="number"], input[type="email"] {
        width: 98%;
        padding: 10px;
        margin-top: 5px;
        margin-bottom: 10px;
        border: 1px solid #ccc;
        border-radius: 4px;
        box-sizing: border-box;
        text-align: left;
    }
</style>
<h1>Bot Control Panel | User: {{ email }}</h1>
<hr>

{% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
        {% for category, message in messages %}
            <p style="color:{{ 'green' if category == 'success' else ('blue' if category == 'info' else 'red') }};">{{ message }}</p>
        {% endfor %}
    {% endif %}
{% endwith %}

{% if session_data and session_data.is_running %}
    {% set current_state = session_data.current_trade_state %}
    {% set strategy = current_state.type + " " + (current_state.barrier | string) %}
    
    <p class="status-running">✅ Bot is **Running**! (Auto-refreshing)</p>
    <p>Net Profit: **${{ session_data.current_profit|round(2) }}**</p>
    <p>Current Stake: **${{ session_data.current_stake|round(2) }}**</p>
    <p>Step: **{{ session_data.current_step }}** / {{ martingale_steps }}</p>
    <p>Stats: **{{ session_data.total_wins }}** Wins | **{{ session_data.total_losses }}** Losses</p>
    <p style="font-weight: bold; color: #007bff;">Current Strategy: **{{ strategy }}**</p>
    
    <form method="POST" action="{{ url_for('stop_route') }}">
        <button type="submit" style="background-color: red; color: white;">🛑 Stop Bot</button>
    </form>
{% else %}
    <p class="status-stopped">🛑 Bot is **Stopped**. Enter settings to start a new session.</p>
    <form method="POST" action="{{ url_for('start_bot') }}">
        <label for="token">Deriv API Token:</label><br>
        <input type="text" id="token" name="token" required value="{{ session_data.api_token if session_data else '' }}" {% if session_data and session_data.api_token and session_data.is_running is not none %}readonly{% endif %}><br>
        
        <label for="stake">Base Stake (USD):</label><br>
        <input type="number" id="stake" name="stake" value="{{ session_data.base_stake|round(2) if session_data else 0.35 }}" step="0.01" min="0.35" required><br>
        
        <label for="tp">TP Target (USD):</label><br>
        <input type="number" id="tp" name="tp" value="{{ session_data.tp_target|round(2) if session_data else 10.0 }}" step="0.01" required><br>
        
        <button type="submit" style="background-color: green; color: white;">🚀 Start Bot</button>
    </form>
{% endif %}
<hr>
<a href="{{ url_for('logout') }}" style="display: block; text-align: center; margin-top: 15px; font-size: 1.1em;">Logout</a>

<script>
    // Conditional auto-refresh JavaScript code
    function autoRefresh() {
        var isRunning = {{ 'true' if session_data and session_data.is_running else 'false' }};
        
        if (isRunning) {
            setTimeout(function() {
                window.location.reload();
            }, 1000);
        }
    }

    autoRefresh();
</script>
"""

# ==========================================================
# FLASK ROUTES
# ==========================================================

@app.before_request
def check_user_status():
    """ Executes before every request to check if the user's email is still authorized. """
    if request.endpoint in ('login', 'auth_page', 'logout', 'static'):
        return

    if 'email' in session:
        email = session['email']
        allowed_users = load_allowed_users()
        
        if email.lower() not in allowed_users:
            print(f"🛑 [SECURITY] User {email} removed from list. Forcing logout.")
            session.pop('email', None) 
            flash('Your access has been revoked. Please log in again.', 'error')
            return redirect(url_for('auth_page')) 

@app.route('/')
def index():
    if 'email' not in session:
        return redirect(url_for('auth_page'))
    
    email = session['email']
    session_data = get_session_data(email)

    # ⚠️ منطق عرض سبب التوقف
    if not session_data.get('is_running') and "stop_reason" in session_data and session_data["stop_reason"] not in ["Stopped Manually", "Running", "Disconnected (Auto-Retry)", "Displayed"]:
        
        reason = session_data["stop_reason"]
        
        if reason == "SL Reached":
            flash(f"🛑 STOP: الحد الأقصى للخسارة ({MAX_CONSECUTIVE_LOSSES} خسارات متتالية) تم الوصول إليه! (SL Reached)", 'error')
        elif reason == "TP Reached":
            flash(f"✅ GOAL: هدف الربح ({session_data['tp_target']} $) تم الوصول إليه بنجاح! (TP Reached)", 'success')
            
        # وضع علامة على الرسالة كمعروضة لتجنب تكرارها في كل تحديث
        session_data['stop_reason'] = "Displayed" 
        save_session_data(email, session_data) 
        
        # بعد عرض السبب، نقوم بمسح البيانات النهائية للجلسة إذا كان التوقف بسبب TP/SL
        delete_session_data(email)


    return render_template_string(CONTROL_FORM, 
        email=email,
        session_data=session_data,
        martingale_steps=MARTINGALE_STEPS
    )

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].lower()
        allowed_users = load_allowed_users()
        
        if email in allowed_users:
            session['email'] = email
            flash('Login successful.', 'success')
            return redirect(url_for('index'))
        else:
            flash('Email not authorized.', 'error')
            return redirect(url_for('auth_page'))
    
    return redirect(url_for('auth_page'))

@app.route('/auth')
def auth_page():
    if 'email' in session:
        return redirect(url_for('index'))
    return render_template_string(AUTH_FORM)

@app.route('/start', methods=['POST'])
def start_bot():
    if 'email' not in session:
        return redirect(url_for('auth_page'))
    
    email = session['email']
    
    if email in active_threads and active_threads[email].is_alive():
        flash('Bot is already running.', 'info')
        return redirect(url_for('index'))
        
    try:
        current_data = get_session_data(email)
        if current_data.get('api_token') and request.form.get('token') == current_data['api_token']:
            token = current_data['api_token']
        else:
            token = request.form['token']

        stake = float(request.form['stake'])
        tp = float(request.form['tp'])
    except ValueError:
        flash("Invalid stake or TP value.", 'error')
        return redirect(url_for('index'))
        
    thread = threading.Thread(target=bot_core_logic, args=(email, token, stake, tp))
    thread.daemon = True
    thread.start()
    active_threads[email] = thread
    
    flash('Bot started successfully. It will attempt to connect and auto-reconnect.', 'success')
    return redirect(url_for('index'))

@app.route('/stop', methods=['POST'])
def stop_route():
    if 'email' not in session:
        return redirect(url_for('auth_page'))
    
    # ⚠️ تمرير سبب التوقف اليدوي
    stop_bot(session['email'], clear_data=True, stop_reason="Stopped Manually") 
    flash('Bot stopped and session data cleared.', 'success')
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('email', None)
    flash('Logged out successfully.', 'success')
    return redirect(url_for('auth_page'))


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
