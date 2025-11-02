import time
import json
import websocket
import os
import sys
import fcntl
from flask import Flask, request, render_template_string, redirect, url_for, session, flash, g
from datetime import timedelta, datetime, timezone
from multiprocessing import Process
from threading import Lock

# ==========================================================
# BOT CONSTANT SETTINGS (No change)
# ==========================================================
WSS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL = "R_100"
DURATION = 5                 
DURATION_UNIT = "t"          
MARTINGALE_STEPS = 4         
MAX_CONSECUTIVE_LOSSES = 5   
RECONNECT_DELAY = 1
USER_IDS_FILE = "user_ids.txt"
ACTIVE_SESSIONS_FILE = "active_sessions.json"

# 💡 الاستراتيجية
CONTRACT_TYPE = "RISEFALL"   
# ==========================================================

# ==========================================================
# GLOBAL STATE (Shared between processes via File/Lock)
# ==========================================================
active_processes = {}
active_ws = {}
is_contract_open = {}
PROCESS_LOCK = Lock()

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
    "stop_reason": "Stopped Manually",
    "last_entry_time": 0,
    "last_entry_price": 0.0,
    "last_tick_data": None,
    "currency": "USD", 
    "account_type": "demo",
    
    # 💡 المتغيرات الجديدة للاستراتيجية
    "open_price": 0.0,          
    "open_time": 0,             
    "last_action_type": "CALL",
    # 💡 المتغير الجديد لتخزين آخر تيك تم رصده قبل قرار الدخول
    "last_valid_tick_price": 0.0 
}
# ==========================================================

# ==========================================================
# PERSISTENT STATE MANAGEMENT FUNCTIONS (No change)
# ==========================================================

def load_persistent_sessions():
    if not os.path.exists(ACTIVE_SESSIONS_FILE): return {}
    try:
        with open(ACTIVE_SESSIONS_FILE, 'r') as f:
            content = f.read()
            return json.loads(content) if content else {}
    except: return {}

def save_session_data(email, session_data):
    all_sessions = load_persistent_sessions()
    all_sessions[email] = session_data
    with open(ACTIVE_SESSIONS_FILE, 'w') as f:
        try: json.dump(all_sessions, f, indent=4)
        except: pass

def get_session_data(email):
    all_sessions = load_persistent_sessions()
    if email in all_sessions:
        data = all_sessions[email]
        for key, default_val in DEFAULT_SESSION_STATE.items():
            if key not in data: data[key] = default_val
        return data
    return DEFAULT_SESSION_STATE.copy()

def delete_session_data(email):
    all_sessions = load_persistent_sessions()
    if email in all_sessions: del all_sessions[email]
    with open(ACTIVE_SESSIONS_FILE, 'w') as f:
        try: json.dump(all_sessions, f, indent=4)
        except: pass

def load_allowed_users():
    if not os.path.exists(USER_IDS_FILE): return set()
    try:
        with open(USER_IDS_FILE, 'r', encoding='utf-8') as f:
            return {line.strip().lower() for line in f if line.strip()}
    except: return set()
        
def stop_bot(email, clear_data=True, stop_reason="Stopped Manually"):
    global is_contract_open, active_processes
    current_data = get_session_data(email)
    if current_data.get("is_running") is True:
        current_data["is_running"] = False
        current_data["stop_reason"] = stop_reason
        save_session_data(email, current_data)

    with PROCESS_LOCK:
        if email in active_processes:
            process = active_processes[email]
            if process.is_alive():
                print(f"🛑 [INFO] Terminating Process for {email}...")
                process.terminate() 
                process.join()     
            del active_processes[email]
    
    with PROCESS_LOCK:
        if email in active_ws: del active_ws[email]
    if email in is_contract_open: is_contract_open[email] = False

    if clear_data:
        if stop_reason in ["SL Reached", "TP Reached", "API Buy Error"]:
            print(f"🛑 [INFO] Bot for {email} stopped ({stop_reason}). Data kept for display.")
        else:
            delete_session_data(email)
            print(f"🛑 [INFO] Bot for {email} stopped ({stop_reason}) and session data cleared from file.")
    else:
        print(f"⚠️ [INFO] WS closed for {email}. Attempting immediate reconnect.")

# ==========================================================
# TRADING BOT FUNCTIONS
# ==========================================================

def calculate_martingale_stake(base_stake, current_stake, current_step):
    """ منطق المضاعفة (الرهان الخاسر × 2.2) """
    if current_step == 0:
        return base_stake
    if current_step <= MARTINGALE_STEPS:
        return current_stake * 2.2 
    return base_stake

def send_trade_order(email, stake, currency, action_type):
    """ إرسال أمر الشراء (RISE أو FALL) """
    global is_contract_open, active_ws, DURATION, DURATION_UNIT, CONTRACT_TYPE
    
    if email not in active_ws or active_ws[email] is None: return
    ws_app = active_ws[email]
    
    trade_request = {
        "buy": 1,
        "price": round(stake, 2),
        "parameters": {
            "amount": round(stake, 2),
            "basis": "stake",
            "contract_type": action_type, 
            "currency": currency, 
            "duration": DURATION,
            "duration_unit": DURATION_UNIT, 
            "symbol": SYMBOL
        }
    }
    try:
        ws_app.send(json.dumps(trade_request))
        is_contract_open[email] = True
        print(f"💰 [TRADE] Sent {action_type} {DURATION}{DURATION_UNIT} with stake: {round(stake, 2):.2f} {currency}")
    except Exception as e:
        print(f"❌ [TRADE ERROR] Could not send trade order: {e}")
        pass

def calculate_and_store_martingale(email, last_loss_stake, last_action_type):
    """ حساب الرهان الجديد وتخزينه دون الدخول الفوري """
    current_data = get_session_data(email)
    
    new_stake = calculate_martingale_stake(
        current_data['base_stake'],
        last_loss_stake,
        current_data['current_step']
    )

    current_data['current_stake'] = new_stake
    current_data['last_action_type'] = last_action_type # تخزين نوع الصفقة للمحاولة القادمة
    save_session_data(email, current_data)
    print(f"💸 [MARTINGALE] Lost. Calculating next stake: {new_stake:.2f}. Waiting for next opportunity (0s/15s cycle).")


def check_pnl_limits(email, profit_loss, last_action_type):
    """ تحديث الإحصائيات واتخاذ قرار بشأن المضاعفة/الإيقاف """
    global is_contract_open
    
    is_contract_open[email] = False

    current_data = get_session_data(email)
    if not current_data.get('is_running'): return

    last_stake = current_data['current_stake']
    current_data['current_profit'] += profit_loss
    
    if profit_loss > 0:
        current_data['total_wins'] += 1
        current_data['current_step'] = 0
        current_data['consecutive_losses'] = 0
        current_data['current_stake'] = current_data['base_stake']
        current_data['last_action_type'] = last_action_type 
        
    else:
        current_data['total_losses'] += 1
        current_data['consecutive_losses'] += 1
        current_data['current_step'] += 1
        
        # التحقق من الحد الأقصى للخسارات المتتالية وخطوات المارتنجيل
        if current_data['consecutive_losses'] > MAX_CONSECUTIVE_LOSSES or current_data['current_step'] > MARTINGALE_STEPS:
            stop_bot(email, clear_data=True, stop_reason="SL Reached")
            return
        
        save_session_data(email, current_data)
        calculate_and_store_martingale(email, last_stake, last_action_type) 
        return

    if current_data['current_profit'] >= current_data['tp_target']:
        stop_bot(email, clear_data=True, stop_reason="TP Reached")
        return
    
    save_session_data(email, current_data)
        
    rounded_last_stake = round(last_stake, 2)
    currency = current_data.get('currency', 'USD')
    print(f"[LOG {email}] PNL: {currency} {current_data['current_profit']:.2f}, Step: {current_data['current_step']}, Last Stake: {rounded_last_stake:.2f}, Strategy: {CONTRACT_TYPE}")


def bot_core_logic(email, token, stake, tp, currency, account_type):
    """ منطق البوت الأساسي """
    
    global is_contract_open, active_ws, CONTRACT_TYPE

    is_contract_open = {email: False}
    active_ws = {email: None}

    session_data = get_session_data(email)
    session_data.update({
        "api_token": token, 
        "base_stake": stake, 
        "tp_target": tp,
        "is_running": True, 
        "current_stake": stake,
        "stop_reason": "Running",
        "last_entry_time": 0,
        "last_entry_price": 0.0,
        "last_tick_data": None,
        "currency": currency,
        "account_type": account_type,
        "open_price": 0.0,      
        "open_time": 0,          
        "last_action_type": "CALL", 
        "last_valid_tick_price": 0.0 # إعادة تعيين
    })
    save_session_data(email, session_data)

    while True:
        current_data = get_session_data(email)
        
        if not current_data.get('is_running'): break

        print(f"🔗 [PROCESS] Attempting to connect for {email} ({account_type.upper()}/{currency})...")

        def on_open_wrapper(ws_app):
            current_data = get_session_data(email) 
            ws_app.send(json.dumps({"authorize": current_data['api_token']}))
            ws_app.send(json.dumps({"ticks": SYMBOL, "subscribe": 1}))
            running_data = get_session_data(email)
            running_data['is_running'] = True
            save_session_data(email, running_data)
            print(f"✅ [PROCESS] Connection established for {email}.")
            is_contract_open[email] = False

        def on_message_wrapper(ws_app, message):
            data = json.loads(message)
            msg_type = data.get('msg_type')
            
            current_data = get_session_data(email)
            if not current_data.get('is_running'):
                ws_app.close()
                return
                
            if msg_type == 'tick':
                current_timestamp = int(data['tick']['epoch'])
                current_price = float(data['tick']['quote'])
                
                current_data['last_tick_data'] = {
                    "price": current_price,
                    "timestamp": current_timestamp
                }
                
                # 💡 تخزين آخر سعر تيك صحيح ليكون هو سعر الإغلاق
                current_data['last_valid_tick_price'] = current_price
                save_session_data(email, current_data)
                
                if is_contract_open.get(email) is True: return
                    
                current_second = datetime.fromtimestamp(current_timestamp, tz=timezone.utc).second
                
                # 1. تسجيل سعر الافتتاح عند الثانية 0
                if current_second == 0 and current_data['open_time'] != current_timestamp:
                    current_data['open_price'] = current_price
                    current_data['open_time'] = current_timestamp
                    current_data['last_entry_time'] = 0 
                    current_data['last_valid_tick_price'] = current_price # تحديث أول سعر تيك صحيح عند 0
                    save_session_data(email, current_data)
                    print(f"🕒 [OPEN] Recorded Open Price: {current_price} at second 0.")
                    return 

                # 2. منطق قرار الدخول: إذا كان سعر الافتتاح مُسجلاً والوقت تجاوز أو وصل الثانية 15، و لم يتم الدخول بعد
                if current_data['open_price'] != 0.0 and current_second >= 15 and current_data['last_entry_time'] == 0:
                    
                    # سعر الإغلاق هو آخر تيك تم تسجيله
                    entry_price = current_data['last_valid_tick_price'] 
                    stake_to_use = current_data['current_stake']
                    currency_to_use = current_data['currency']
                    
                    action_type = ""
                    
                    # 3. منطق تحديد الاتجاه (سعر الإغلاق > سعر الافتتاح = RISE)
                    open_price = current_data['open_price']
                    close_price = entry_price 
                    
                    if close_price > open_price:
                        action_type = "CALL" # RISE
                    elif close_price < open_price:
                        action_type = "PUT" # FALL
                    else:
                        print("⏸️ [SKIP] Open Price == Close Price. Skipping entry this cycle.")
                        current_data['open_price'] = 0.0
                        current_data['open_time'] = 0
                        current_data['last_entry_time'] = current_timestamp 
                        save_session_data(email, current_data)
                        return
                        
                    if action_type:
                        
                        current_data['last_entry_time'] = current_timestamp 
                        current_data['open_price'] = 0.0 
                        current_data['open_time'] = 0
                        
                        send_trade_order(email, stake_to_use, currency_to_use, action_type)
                        save_session_data(email, current_data)
                        
                    return # إنهاء معالجة التيك بعد قرار الدخول

            elif msg_type == 'buy':
                contract_id = data['buy']['contract_id']
                action_type = data['buy']['shortcode'].split('_')[1] 
                current_data['last_action_type'] = action_type
                save_session_data(email, current_data)
                
                ws_app.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}))
            
            elif 'error' in data:
                error_code = data['error'].get('code', 'N/A')
                error_message = data['error'].get('message', 'Unknown Error')
                print(f"❌❌ [API ERROR] Code: {error_code}, Message: {error_message}")
                
                if current_data.get('is_running'):
                    stop_bot(email, clear_data=False, stop_reason=f"API Buy Error: {error_code} - {error_message}")

            elif msg_type == 'proposal_open_contract':
                contract = data['proposal_open_contract']
                if contract.get('is_sold') == 1:
                    last_action_type = get_session_data(email).get('last_action_type', 'CALL') 
                    check_pnl_limits(email, contract['profit'], last_action_type)
                    if 'subscription_id' in data: ws_app.send(json.dumps({"forget": data['subscription_id']}))

        def on_close_wrapper(ws_app, code, msg):
            print(f"⚠️ [PROCESS] WS closed for {email}. Stopping for auto-retry.")
            is_contract_open[email] = False

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
        
        if get_session_data(email).get('is_running') is False: break
        
        print(f"💤 [PROCESS] Waiting {RECONNECT_DELAY} seconds before retrying connection for {email}...")
        time.sleep(RECONNECT_DELAY)

    print(f"🛑 [PROCESS] Bot process loop ended for {email}.")

# ==========================================================
# FLASK APP SETUP AND ROUTES (No change)
# ==========================================================

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET_KEY', 'VERY_STRONG_SECRET_KEY_RENDER_BOT')
app.config['SESSION_PERMANENT'] = False

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
        direction: ltr;
        text-align: left;
    }
    h1 {
        color: #007bff;
        font-size: 1.8em;
        border-bottom: 2px solid #eee;
        padding-bottom: 10px;
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
    input[type="text"], input[type="number"], select {
        width: 98%;
        padding: 10px;
        margin-top: 5px;
        margin-bottom: 10px;
        border: 1px solid #ccc;
        border-radius: 4px;
        box-sizing: border-box;
        text-align: left;
    }
    form button {
        padding: 12px 20px;
        border: none;
        border-radius: 5px;
        cursor: pointer;
        font-size: 1.1em;
        margin-top: 15px;
        width: 100%;
    }
</style>
<h1>Bot Control Panel | User: {{ email }}</h1>
<hr>

{% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
        {% for category, message in messages %}
            <p style="color:{{ 'green' if category == 'success' else ('blue' if category == 'info' else 'red') }};">{{ message }}</p>
        {% endfor %}
        
        {% if session_data and session_data.stop_reason and session_data.stop_reason != "Running" %}
            <p style="color:red; font-weight:bold;">Last Reason: {{ session_data.stop_reason }}</p>
        {% endif %}
    {% endif %}
{% endwith %}


{% if session_data and session_data.is_running %}
    {% set strategy = contract_type + " (" + duration|string + " Ticks @ x2.2 Martingale)" %}
    
    <p class="status-running">✅ Bot is **Running**! (Auto-refreshing)</p>
    <p>Account Type: **{{ session_data.account_type.upper() }}** | Currency: **{{ session_data.currency }}**</p>
    <p>Net Profit: **{{ session_data.currency }} {{ session_data.current_profit|round(2) }}**</p>
    <p>Current Stake: **{{ session_data.currency }} {{ session_data.current_stake|round(2) }}**</p>
    <p>Step: **{{ session_data.current_step }}** / {{ martingale_steps }} (Max Loss: {{ max_consecutive_losses }})</p>
    <p>Stats: **{{ session_data.total_wins }}** Wins | **{{ session_data.total_losses }}** Losses</p>
    {% if session_data.open_price != 0.0 %}
        <p style="color: orange; font-weight: bold;">Current Open Price (0s): {{ session_data.open_price|round(5) }}</p>
    {% endif %}
    {% if session_data.last_valid_tick_price != 0.0 %}
        <p style="color: purple; font-weight: bold;">Last Tick Price: {{ session_data.last_valid_tick_price|round(5) }}</p>
    {% endif %}
    <p style="font-weight: bold; color: #007bff;">Current Strategy: **{{ strategy }}**</p>
    
    <form method="POST" action="{{ url_for('stop_route') }}">
        <button type="submit" style="background-color: red; color: white;">🛑 Stop Bot</button>
    </form>
{% else %}
    <p class="status-stopped">🛑 Bot is **Stopped**. Enter settings to start a new session.</p>
    <form method="POST" action="{{ url_for('start_bot') }}">

        <label for="account_type">Account Type:</label><br>
        <select id="account_type" name="account_type" required>
            <option value="demo" selected>Demo (USD)</option>
            <option value="live">Live (tUSDT)</option>
        </select><br>

        <label for="token">Deriv API Token:</label><br>
        <input type="text" id="token" name="token" required value="{{ session_data.api_token if session_data else '' }}" {% if session_data and session_data.api_token and session_data.is_running is not none %}readonly{% endif %}><br>
        
        <label for="stake">Base Stake (USD/tUSDT):</label><br>
        <input type="number" id="stake" name="stake" value="{{ session_data.base_stake|round(2) if session_data else 0.35 }}" step="0.01" min="0.35" required><br>
        
        <label for="tp">TP Target (USD/tUSDT):</label><br>
        <input type="number" id="tp" name="tp" value="{{ session_data.tp_target|round(2) if session_data else 10.0 }}" step="0.01" required><br>
        
        <button type="submit" style="background-color: green; color: white;">🚀 Start Bot</button>
    </form>
{% endif %}
<hr>
<a href="{{ url_for('logout') }}" style="display: block; text-align: center; margin-top: 15px; font-size: 1.1em;">Logout</a>

<script>
    function autoRefresh() {
        var isRunning = {{ 'true' if session_data and session_data.is_running else 'false' }};
        
        if (isRunning) {
            setTimeout(function() {
                window.location.reload();
            }, 5000); 
        }
    }

    autoRefresh();
</script>
"""

@app.before_request
def check_user_status():
    if request.endpoint in ('login', 'auth_page', 'logout', 'static'): return
    if 'email' in session:
        email = session['email']
        allowed_users = load_allowed_users()
        if email.lower() not in allowed_users:
            session.pop('email', None)
            flash('Your access has been revoked. Please log in again.', 'error')
            return redirect(url_for('auth_page'))

@app.route('/')
def index():
    if 'email' not in session: return redirect(url_for('auth_page'))
    email = session['email']
    session_data = get_session_data(email)

    if not session_data.get('is_running') and "stop_reason" in session_data and session_data["stop_reason"] not in ["Stopped Manually", "Running", "Disconnected (Auto-Retry)", "Displayed"]:
        reason = session_data["stop_reason"]
        if reason == "SL Reached": flash(f"🛑 STOP: الحد الأقصى للخسارة ({MAX_CONSECUTIVE_LOSSES} خسارات متتالية أو تجاوز {MARTINGALE_STEPS} خطوات مضاعفة) تم الوصول إليه! (SL Reached)", 'error')
        elif reason == "TP Reached": flash(f"✅ GOAL: هدف الربح ({session_data['tp_target']} {session_data.get('currency', 'USD')}) تم الوصول إليه بنجاح! (TP Reached)", 'success')
        elif reason.startswith("API Buy Error"): flash(f"❌ API Error: {reason}. Check your token and account status.", 'error')
            
        session_data['stop_reason'] = "Displayed"
        save_session_data(email, session_data)
        delete_session_data(email)

    return render_template_string(CONTROL_FORM,
        email=email,
        session_data=session_data,
        martingale_steps=MARTINGALE_STEPS,
        max_consecutive_losses=MAX_CONSECUTIVE_LOSSES,
        contract_type=CONTRACT_TYPE, 
        duration=DURATION  
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
    if 'email' in session: return redirect(url_for('index'))
    return render_template_string(AUTH_FORM)

@app.route('/start', methods=['POST'])
def start_bot():
    global active_processes
    if 'email' not in session: return redirect(url_for('auth_page'))
    email = session['email']
    
    with PROCESS_LOCK:
        if email in active_processes and active_processes[email].is_alive():
            flash('Bot is already running.', 'info')
            return redirect(url_for('index'))
            
    try:
        account_type = request.form['account_type']
        currency = "USD" if account_type == 'demo' else "tUSDT"
        current_data = get_session_data(email)
        token = request.form['token'] if not current_data.get('api_token') or request.form.get('token') != current_data['api_token'] else current_data['api_token']
        stake = float(request.form['stake'])
        tp = float(request.form['tp'])
    except ValueError:
        flash("Invalid stake or TP value.", 'error')
        return redirect(url_for('index'))
        
    process = Process(target=bot_core_logic, args=(email, token, stake, tp, currency, account_type))
    process.daemon = True
    process.start()
    
    with PROCESS_LOCK: active_processes[email] = process
    
    flash(f'Bot started successfully. Currency: {currency}. Account: {account_type.upper()}. Strategy: {CONTRACT_TYPE} {DURATION} Ticks (x2.2 Martingale)', 'success')
    return redirect(url_for('index'))

@app.route('/stop', methods=['POST'])
def stop_route():
    if 'email' not in session: return redirect(url_for('auth_page'))
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
