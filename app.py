import time
import json
import websocket 
import os 
import sys 
import fcntl # لإدارة قفل الملفات
from flask import Flask, request, render_template_string, redirect, url_for, session, flash
from datetime import datetime, timezone
from multiprocessing import Process # 💡 استخدام Multiprocessing

# ==========================================================
# BOT CONSTANT SETTINGS
# ==========================================================
WSS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL = "R_100"          
DURATION = 15            # مدة العقد 15 تيكس
DURATION_UNIT = "t" 
MARTINGALE_STEPS = 6     # الحد الأقصى لخطوات المضاعفة
MAX_CONSECUTIVE_LOSSES = 6 
RECONNECT_DELAY = 1       
USER_IDS_FILE = "user_ids.txt"
ACTIVE_SESSIONS_FILE = "active_sessions.json" # لحفظ الحالة بين العمليات
# ==========================================================

# ==========================================================
# BOT RUNTIME STATE (Global Cache for Flask)
# ==========================================================
# 💡active_processes تخزن العمليات النشطة
active_processes = {} 
# Trading State Definitions 
TRADE_STATE_DEFAULT = {"type": "CALL"}  

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
    "stop_reason": "Stopped Manually",
    "is_contract_open": False, # حالة العقد داخل ملف الجلسة
    "last_entry_time": 0,          
    "last_entry_price": 0.0,       
}
# ==========================================================

# ==========================================================
# PERSISTENT STATE MANAGEMENT FUNCTIONS (Critical for Multiprocessing)
# ==========================================================
# توفر هذه الدوال آلية آمنة لمشاركة البيانات بين عملية Flask وعملية البوت
def get_file_lock(f):
    try:
        # قفل الملف للكتابة لمنع تضارب العمليات
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
        # التأكد من وجود المفاتيح الافتراضية
        for key, default_val in DEFAULT_SESSION_STATE.items():
            if key not in data:
                data[key] = default_val
        return data
    
    return DEFAULT_SESSION_STATE.copy()

def load_allowed_users():
    if not os.path.exists(USER_IDS_FILE):
        return set()
    try:
        with open(USER_IDS_FILE, 'r', encoding='utf-8') as f:
            users = {line.strip().lower() for line in f if line.strip()}
        return users
    except Exception:
        return set()
        
def stop_bot(email, clear_data=True, stop_reason="Stopped Manually"): 
    """ Stop the process and clear data. """
    global active_processes
    
    # 1. تحديث حالة is_running في الملف (مهم جداً للعملية المنفصلة)
    current_data = get_session_data(email)
    current_data["is_running"] = False
    current_data["stop_reason"] = stop_reason 
    save_session_data(email, current_data) 

    # 2. إيقاف العملية (Process) إذا كانت قيد التشغيل
    if email in active_processes and active_processes[email].is_alive():
        try:
            # إنهاء العملية بالقوة
            active_processes[email].terminate()
            # انتظار إنهاء العملية
            active_processes[email].join() 
        except Exception as e:
            print(f"❌ ERROR terminating process: {e}")
        
    # 3. حذف تسجيل العملية
    if email in active_processes:
        del active_processes[email]
        
    if clear_data:
        if stop_reason not in ["SL Reached", "TP Reached"]:
             delete_session_data(email)
             print(f"🛑 [INFO] Bot process ended for {email} and data cleared.")
    
# ==========================================================
# TRADING BOT CORE FUNCTIONS (INDEPENDENT PROCESS)
# ==========================================================

def calculate_martingale_stake(base_stake, current_stake, current_step):
    """ يحسب قيمة الرهان بناءً على خطة المضاعفة """
    if current_step == 0:
        return base_stake
    if current_step <= MARTINGALE_STEPS:
        return current_stake * 2.2 
    return current_stake * 2.2 

def check_pnl_limits(email, profit_loss):
    """ تحديث الإحصائيات واتخاذ قرار بشأن العقد التالي """
    current_data = get_session_data(email)
    if not current_data.get('is_running'): return

    last_stake = current_data['current_stake'] 

    current_data['current_profit'] += profit_loss
    
    if profit_loss > 0:
        # ربح: إعادة تعيين
        current_data['total_wins'] += 1
        current_data['current_step'] = 0 
        current_data['consecutive_losses'] = 0
        current_data['current_stake'] = current_data['base_stake']
        current_data['current_trade_state'] = TRADE_STATE_DEFAULT
        
    else:
        # خسارة: تحضير المضاعفة
        current_data['total_losses'] += 1
        current_data['consecutive_losses'] += 1
        current_data['current_step'] += 1
        
        # فحص إيقاف الخسارة (SL)
        if current_data['consecutive_losses'] >= MAX_CONSECUTIVE_LOSSES: 
            current_data['is_running'] = False
            current_data['stop_reason'] = "SL Reached"
        
        # حساب قيمة المضاعفة التالية
        new_stake = calculate_martingale_stake(
            current_data['base_stake'],
            last_stake,
            current_data['current_step'] 
        )
        current_data['current_stake'] = new_stake

    # فحص تحقيق الهدف (TP)
    if current_data['current_profit'] >= current_data['tp_target']:
        current_data['is_running'] = False
        current_data['stop_reason'] = "TP Reached"
    
    save_session_data(email, current_data)
        
    state = current_data['current_trade_state']
    print(f"[LOG {email}] PNL: {current_data['current_profit']:.2f}, Step: {current_data['current_step']}, Last Stake: {last_stake:.2f}, State: {state['type']}")


def send_and_receive(token, request):
    """ يرسل طلب WebSocket ويستقبل الرد (اتصال جديد في كل مرة) """
    email = next(iter(get_session_data('')) or {}, 'unknown')
    
    try:
        # إنشاء اتصال جديد
        ws = websocket.create_connection(WSS_URL, timeout=5)
        
        # 1. تخويل
        ws.send(json.dumps({"authorize": token}))
        auth_response = json.loads(ws.recv())
        if auth_response.get('msg_type') != 'authorize':
            # إذا فشل التخويل بشكل دائم
            stop_bot(email, clear_data=True, stop_reason="Authorization Failed")
            return None
            
        # 2. إرسال الطلب
        ws.send(json.dumps(request))
        response = json.loads(ws.recv())
        ws.close()
        return response
    except Exception as e:
        print(f"❌ [WS ERROR] Failed to send/receive data: {e}")
        return None

def wait_for_settlement(email, token, contract_id):
    """ تتحقق بشكل دوري من إغلاق العقد (Polling) """
    
    current_data = get_session_data(email)
    current_data["is_contract_open"] = True
    save_session_data(email, current_data)

    max_checks = 35 # 35 ثانية كحد أقصى للتحقق
    
    print(f"⏳ [SETTLEMENT] Waiting for contract {contract_id} settlement...")
    
    for _ in range(max_checks):
        time.sleep(1) # تحقق مرة كل ثانية
        
        settlement_request = {"proposal_open_contract": 1, "contract_id": contract_id}
        settlement_response = send_and_receive(token, settlement_request)
        
        if settlement_response and settlement_response.get('proposal_open_contract'):
            contract = settlement_response['proposal_open_contract']
            
            if contract.get('is_sold') == 1:
                # العقد مغلق!
                check_pnl_limits(email, contract.get('profit', 0)) 
                
                current_data = get_session_data(email)
                current_data["is_contract_open"] = False # تحديث الحالة في الملف
                save_session_data(email, current_data)
                return 

        # حماية ضد تجاوز الوقت
        current_second = datetime.now(timezone.utc).second
        if current_second > 50 and current_second < 55:
             print("❌ [TIMEOUT] Settlement failed, resetting state.")
             current_data = get_session_data(email)
             current_data["is_contract_open"] = False 
             save_session_data(email, current_data)
             return 

    print("❌ [TIMEOUT] Failed to get settlement status after multiple checks.")
    current_data = get_session_data(email)
    current_data["is_contract_open"] = False
    save_session_data(email, current_data)


def fetch_minute_data_ticks(token):
    """ تجلب سعر افتتاح الدقيقة (T00) وسعر الدخول الحالي (T30) باستخدام التيكات التاريخية """
    
    now_utc = datetime.now(timezone.utc)
    start_of_current_minute = now_utc.replace(second=0, microsecond=0)
    
    ticks_request = {
        "ticks_history": SYMBOL,
        "end": "latest", 
        "start": 1, 
        "count": 500, 
        "style": "ticks"
    }
    ticks_response = send_and_receive(token, ticks_request)
    
    start_price = 0.0
    entry_price = 0.0

    if ticks_response and 'history' in ticks_response:
        prices = ticks_response['history']['prices']
        times = ticks_response['history']['times']
        
        start_of_current_minute_ts = int(start_of_current_minute.timestamp())
        
        # البحث عن أول تيك في الدقيقة الحالية (T00)
        t00_index = -1
        for i, t in enumerate(times):
            if int(t) >= start_of_current_minute_ts:
                t00_index = i
                break
        
        if t00_index != -1:
            start_price = float(prices[t00_index])
        
        # آخر تيك هو سعر الدخول (T30)
        if prices:
            entry_price = float(prices[-1])
        
    return start_price, entry_price


def bot_core_logic(email, token, stake, tp):
    """ منطق عملية البوت الرئيسية (Process) """
    
    # 1. تهيئة البيانات الأولية
    session_data = get_session_data(email)
    session_data.update({
        "api_token": token, "base_stake": stake, "tp_target": tp,
        "is_running": True, "current_stake": stake,
        "current_trade_state": TRADE_STATE_DEFAULT,
        "stop_reason": "Running",
        "is_contract_open": False
    })
    save_session_data(email, session_data)

    while True: 
        current_data = get_session_data(email)
        
        if not current_data.get('is_running'):
            break

        # 2. انتظار الوصول إلى الثانية 30
        now = datetime.now(timezone.utc)
        current_second = now.second
        
        # نافذة الدخول
        if 28 <= current_second <= 31:
            
            if current_data.get("is_contract_open") is True:
                time.sleep(1)
                continue
                
            # 3. جلب البيانات (T00 و T30)
            print(f"⏱️ [TIMER] Time reached (Second {current_second}). Fetching data...")
            start_price, current_entry_price = fetch_minute_data_ticks(token)
            
            # 4. التحليل
            if start_price == 0.0 or current_entry_price == 0.0:
                 time.sleep(1)
                 continue
            
            if current_entry_price > start_price:
                contract_type_to_use = "CALL" 
                print(f"📈 [ENTRY] Trend: Rise. Entering CALL.")
            elif current_entry_price < start_price:
                contract_type_to_use = "PUT"
                print(f"📉 [ENTRY] Trend: Fall. Entering PUT.")
            else:
                contract_type_to_use = current_data['current_trade_state']['type']
                print(f"🔄 [ENTRY] Trend: Neutral. Entering {contract_type_to_use}.")

            # 5. إرسال الصفقة
            stake_to_use = current_data['current_stake']
            buy_request = {
                "buy": 1, "price": round(stake_to_use, 2),  
                "parameters": {
                    "amount": round(stake_to_use, 2), "basis": "stake",
                    "contract_type": contract_type_to_use, "currency": "USD", 
                    "duration": DURATION, "duration_unit": DURATION_UNIT, "symbol": SYMBOL
                }
            }
            
            buy_response = send_and_receive(token, buy_request)
            
            if buy_response and buy_response.get('msg_type') == 'buy' and 'contract_id' in buy_response.get('buy', {}):
                
                # 6. الصفقة ناجحة: ننتظر النتيجة باستخدام Polling
                contract_id = buy_response['buy']['contract_id']
                wait_for_settlement(email, token, contract_id)
                
            else:
                error_msg = buy_response.get('error', {}).get('message', 'Unspecified buy error.') if buy_response else 'No response'
                print(f"❌ [TRADE FAILED] Buy request failed: {error_msg}")
                time.sleep(1) 
                
            # 7. تأخير لإكمال الدورة
            now_after_trade = datetime.now(timezone.utc)
            seconds_to_next_minute = 60 - now_after_trade.second
            
            # الانتظار حتى بداية الدورة التالية (الثانية 28)
            time_to_sleep_until_next_cycle = seconds_to_next_minute + 28
            time.sleep(time_to_sleep_until_next_cycle % 60)
            
        else:
            # الانتظار حتى الوصول إلى نافذة الدخول (الثانية 28)
            time_to_sleep = (28 - current_second) % 60
            if time_to_sleep == 0: time_to_sleep = 1
            time.sleep(time_to_sleep)

    print(f"🛑 [PROCESS] Bot process ended for {email}.")
    
# ==========================================================
# FLASK APP SETUP AND ROUTES (CONTROL PANEL)
# ==========================================================
app = Flask(__name__) 
app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET_KEY', 'VERY_STRONG_SECRET_KEY_RENDER_BOT')
app.config['SESSION_PERMANENT'] = False 

# HTML TEMPLATES (يجب أن تكون موجودة في ملفاتك، تم إدراجها هنا للاكتمال)
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
        width: 100%;
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
    {% set strategy = "Follow Trend (30s Polling)" %}
    
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
    <p class="status-stopped">🛑 Bot is **Stopped**. {{ session_data.stop_reason if session_data.stop_reason else "Enter settings to start a new session." }}</p>
    <form method="POST" action="{{ url_for('start_bot') }}">
        <label for="token">Deriv API Token:</label><br>
        <input type="text" id="token" name="token" required value="{{ session_data.api_token if session_data else '' }}"><br>
        
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
    // التحديث التلقائي للصفحة عند تشغيل البوت
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
    if request.endpoint in ('login', 'auth_page', 'logout', 'static'):
        return

    if 'email' in session:
        email = session['email']
        allowed_users = load_allowed_users()
        
        if email.lower() not in allowed_users:
            session.pop('email', None) 
            flash('Your access has been revoked. Please log in again.', 'error')
            return redirect(url_for('auth_page')) 

@app.route('/')
def index():
    if 'email' not in session:
        return redirect(url_for('auth_page'))
    
    email = session['email']
    
    # 💡 التحقق من حالة العملية
    if email in active_processes and not active_processes[email].is_alive():
        print(f"⚠️ [PROCESS] Process for {email} died unexpectedly. Stopping state.")
        stop_bot(email, clear_data=False, stop_reason="Process Died")
    
    session_data = get_session_data(email)

    if not session_data.get('is_running') and "stop_reason" in session_data and session_data["stop_reason"] not in ["Stopped Manually", "Running", "Displayed"]:
        
        reason = session_data["stop_reason"]
        
        if reason == "SL Reached":
            flash(f"🛑 STOP: الحد الأقصى للخسارة ({MAX_CONSECUTIVE_LOSSES} خسارات متتالية) تم الوصول إليه! (SL Reached)", 'error')
        elif reason == "TP Reached":
            flash(f"✅ GOAL: هدف الربح ({session_data['tp_target']} $) تم الوصول إليه بنجاح! (TP Reached)", 'success')
            
        session_data['stop_reason'] = "Displayed" 
        save_session_data(email, session_data) 
        
        if reason in ["SL Reached", "TP Reached"]:
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
    
    if email in active_processes and active_processes[email].is_alive():
        flash('Bot is already running.', 'info')
        return redirect(url_for('index'))
        
    try:
        # قراءة البيانات
        token = request.form['token']
        stake = float(request.form['stake'])
        tp = float(request.form['tp'])
    except ValueError:
        flash("Invalid stake or TP value.", 'error')
        return redirect(url_for('index'))
        
    # بدء عملية جديدة (Process)
    process = Process(target=bot_core_logic, args=(email, token, stake, tp))
    process.daemon = True
    process.start()
    active_processes[email] = process
    
    flash('Bot started successfully. Process is running independently.', 'success')
    return redirect(url_for('index'))

@app.route('/stop', methods=['POST'])
def stop_route():
    if 'email' not in session:
        return redirect(url_for('auth_page'))
    
    stop_bot(session['email'], clear_data=True, stop_reason="Stopped Manually") 
    flash('Bot stopped and session data cleared.', 'success')
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('email', None)
    flash('Logged out successfully.', 'success')
    return redirect(url_for('auth_page'))


if __name__ == '__main__':
    # 💡 التحقق من تهيئة ملفات الجلسة وملف المستخدمين
    if not os.path.exists(ACTIVE_SESSIONS_FILE):
        with open(ACTIVE_SESSIONS_FILE, 'w') as f:
            f.write('{}')

    if not os.path.exists(USER_IDS_FILE):
        print(f"🚨 WARNING: {USER_IDS_FILE} file not found. Create it and add authorized emails.")
        
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
