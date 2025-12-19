import time
import json
import websocket
import multiprocessing
import os
import sys
import fcntl
import ssl
from flask import Flask, request, render_template_string, redirect, url_for, session, flash

# ==========================================================
# BOT CONSTANT SETTINGS (MODIFIED FOR 5-TICK LOGIC)
# ==========================================================
WSS_URL_UNIFIED = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL = "R_100"
DURATION = 5           
DURATION_UNIT = "t"
MARTINGALE_STEPS = 0          
MAX_CONSECUTIVE_LOSSES = 1    
RECONNECT_DELAY = 1
USER_IDS_FILE = "user_ids.txt"
ACTIVE_SESSIONS_FILE = "active_sessions.json"

# ✅ تم التعديل لتحليل 5 تيكات
TICK_HISTORY_SIZE = 5   
# ✅ شرط الدخول (الفرق السعري)
PRICE_DIFF_THRESHOLD = 0.4

MARTINGALE_MULTIPLIER = 9.0 

TRADE_CONFIGS = [
    {"type": "DIGITUNDER", "barrier": 8, "label": "DIGITUNDER_8_ENTRY"}, 
]

# ==========================================================
# BOT RUNTIME STATE
# ==========================================================
DEFAULT_SESSION_STATE = {
    "api_token": "",
    "base_stake": 0.35,
    "tp_target": 10.0,
    "current_profit": 0.0,
    "current_balance": 0.0,
    "initial_starting_balance": 0.0, 
    "before_trade_balance": 0.0,
    "current_stake": 0.35,
    "current_total_stake": 0.35 * 1,
    "current_step": 0,
    "consecutive_losses": 0,
    "total_wins": 0,
    "total_losses": 0,
    "is_running": False,
    "account_type": "demo",
    "currency": "USD",
    "last_entry_time": 0.0,
    "last_entry_d2": 'N/A', 
    "tick_history": [],
    "last_tick_data": {},
    "open_contract_ids": [],
    "martingale_stake": 0.0,
    "martingale_config": TRADE_CONFIGS,
    "is_balance_received": False,
    "stop_reason": "Stopped",
    "display_t1_price": 0.0, 
    "display_t5_price": 0.0, # تم تغيير العرض ليناسب التيك الخامس
    "last_trade_type": None
}

flask_local_processes = {}
final_check_processes = {}
active_ws = {}  
is_contract_open = None  

# ----------------------------------------------------------
# Persistent State Management Functions
# ----------------------------------------------------------

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
    with open(ACTIVE_SESSIONS_FILE, 'r+') as f: 
        get_file_lock(f)
        f.seek(0)
        try:
            content = f.read()
            data = json.loads(content) if content else {}
        except json.JSONDecodeError:
            data = {}
        finally:
            release_file_lock(f)
            return data

def save_session_data(email, session_data):
    all_sessions = load_persistent_sessions()
    if not isinstance(session_data, dict): return
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
        finally:
            release_file_lock(f)

def get_session_data(email):
    all_sessions = load_persistent_sessions()
    if email in all_sessions:
        data = all_sessions[email]
        for key, default_val in DEFAULT_SESSION_STATE.items(): 
            if key not in data: data[key] = default_val
        return data
    return DEFAULT_SESSION_STATE.copy() 

def load_allowed_users():
    if not os.path.exists(USER_IDS_FILE):
        with open(USER_IDS_FILE, 'w', encoding='utf-8') as f:
            f.write("test@example.com\n")
        return {"test@example.com"}
    try:
        with open(USER_IDS_FILE, 'r', encoding='utf-8') as f:
            return {line.strip().lower() for line in f if line.strip()}
    except Exception:
        return set()

def stop_bot(email, clear_data=True, stop_reason="Stopped Manually"):
    global flask_local_processes, final_check_processes, is_contract_open, active_ws 
    current_data = get_session_data(email)
    current_data["is_running"] = False
    current_data["stop_reason"] = stop_reason
    
    if email in active_ws and active_ws[email] is not None:
         try:
             active_ws[email].close()
             active_ws[email] = None
         except Exception: pass

    if not clear_data: current_data["open_contract_ids"] = []
    save_session_data(email, current_data)

    for proc_dict in [flask_local_processes, final_check_processes]:
        if email in proc_dict:
            try:
                if proc_dict[email].is_alive():
                    proc_dict[email].terminate()
                    proc_dict[email].join(timeout=2)
                del proc_dict[email]
            except Exception: pass

    if is_contract_open is not None and email in is_contract_open:
        is_contract_open[email] = False 
    
    if clear_data:
        delete_session_data(email) 
        temp_data = DEFAULT_SESSION_STATE.copy()
        temp_data["stop_reason"] = stop_reason
        save_session_data(email, temp_data)

# ==========================================================
# TRADING BOT FUNCTIONS 
# ==========================================================

def calculate_martingale_stake(base_stake, current_step):
    if current_step == 0: return base_stake
    stake = base_stake
    for i in range(1, current_step + 1):
        stake *= MARTINGALE_MULTIPLIER
    if current_step > MARTINGALE_STEPS: return base_stake
    return round(stake, 2)

def send_trade_orders(email, base_stake, currency_code, contract_type, label, barrier, current_step, shared_is_contract_open=None):
    global final_check_processes
    if email not in active_ws or active_ws[email] is None: 
        if shared_is_contract_open is not None: shared_is_contract_open[email] = False
        return
        
    ws_app = active_ws[email]
    current_data = get_session_data(email)
    current_data['before_trade_balance'] = current_data['current_balance']
    
    rounded_stake = round(calculate_martingale_stake(current_data['base_stake'], current_step), 2)
    current_data['current_stake'] = rounded_stake
    current_data['current_total_stake'] = rounded_stake 
    
    trade_request = {
        "buy": 1, "price": rounded_stake,
        "parameters": {
            "amount": rounded_stake, "basis": "stake", "currency": currency_code,
            "duration": DURATION, "duration_unit": DURATION_UNIT, "symbol": SYMBOL,
            "contract_type": contract_type, "barrier": str(barrier)
        }
    }

    try:
        ws_app.send(json.dumps(trade_request))
        if shared_is_contract_open is not None: shared_is_contract_open[email] = True 
        current_data['last_entry_time'] = time.time() * 1000
        save_session_data(email, current_data)
        
        final_check = multiprocessing.Process(
            target=final_check_process,
            args=(email, current_data['api_token'], current_data['last_entry_time'], 6000, shared_is_contract_open)
        )
        final_check.start()
        final_check_processes[email] = final_check
    except Exception:
        if shared_is_contract_open is not None: shared_is_contract_open[email] = False

def check_pnl_limits_by_balance(email, after_trade_balance):
    current_data = get_session_data(email)
    if not current_data.get('is_running'): return

    before_trade_balance = current_data.get('before_trade_balance', 0.0)
    total_profit_loss = after_trade_balance - before_trade_balance if before_trade_balance > 0 else -current_data['current_total_stake']
    
    stop_triggered = False
    if total_profit_loss >= 0:
        current_data['total_wins'] += 1
        current_data['current_step'] = 0
        current_data['consecutive_losses'] = 0
        if (after_trade_balance - current_data['initial_starting_balance']) >= current_data['tp_target']:
            stop_triggered = "TP Reached"
    else:
        current_data['total_losses'] += 1
        current_data['consecutive_losses'] += 1
        if current_data['consecutive_losses'] >= MAX_CONSECUTIVE_LOSSES:
            stop_triggered = f"SL Reached ({MAX_CONSECUTIVE_LOSSES} Loss)"
        else:
            current_data['current_step'] += 1
            if current_data['current_step'] > MARTINGALE_STEPS: current_data['current_step'] = 0

    current_data['tick_history'] = []
    save_session_data(email, current_data)
    if stop_triggered: stop_bot(email, False, stop_triggered)

def get_balance_sync(token):
    try:
        ws = websocket.WebSocket()
        ws.connect(WSS_URL_UNIFIED, timeout=5, sslopt={"cert_reqs": ssl.CERT_NONE})
        ws.send(json.dumps({"authorize": token}))
        ws.recv()
        ws.send(json.dumps({"balance": 1}))
        res = json.loads(ws.recv())
        ws.close()
        if res.get('msg_type') == 'balance':
            return float(res['balance']['balance']), res['balance'].get('currency')
        return None, "Error"
    except Exception as e: return None, str(e)

def final_check_process(email, token, start_time_ms, time_to_wait_ms, shared_is_contract_open):
    time.sleep(max(0, (time_to_wait_ms - (time.time() * 1000 - start_time_ms)) / 1000))
    final_balance, _ = get_balance_sync(token)
    if final_balance is not None:
        check_pnl_limits_by_balance(email, final_balance)
        data = get_session_data(email)
        data['current_balance'] = final_balance
        save_session_data(email, data)
    if shared_is_contract_open is not None: shared_is_contract_open[email] = False

# ==========================================================
# CORE BOT LOGIC (MODIFIED FOR 5-TICK ANALYSIS)
# ==========================================================

def bot_core_logic(email, token, stake, tp, account_type, currency_code, shared_is_contract_open):
    global active_ws
    session_data = get_session_data(email)
    initial_balance, curr = get_balance_sync(token)
    
    if initial_balance is None:
        stop_bot(email, True, "Balance Failed")
        return

    session_data.update({
        "api_token": token, "base_stake": stake, "tp_target": tp, "is_running": True,
        "current_balance": initial_balance, "initial_starting_balance": initial_balance,
        "currency": curr, "stop_reason": "Running", "tick_history": []
    })
    save_session_data(email, session_data)

    def on_message_wrapper(ws_app, message):
        data = json.loads(message)
        current_data = get_session_data(email)
        if not current_data.get('is_running'): ws_app.close(); return

        if data.get('msg_type') == 'tick':
            if shared_is_contract_open.get(email, False): return
            
            tick_data = {"price": float(data['tick']['quote']), "timestamp": int(data['tick']['epoch'])}
            current_data['tick_history'].append(tick_data)
            
            if len(current_data['tick_history']) > TICK_HISTORY_SIZE:
                current_data['tick_history'].pop(0)

            # ✅ التحليل بناءً على 5 تيكات
            if len(current_data['tick_history']) == TICK_HISTORY_SIZE:
                t1_price = current_data['tick_history'][0]['price']
                t5_price = current_data['tick_history'][4]['price']
                
                # تحديث قيم العرض للواجهة
                current_data['display_t1_price'] = t1_price
                current_data['display_t5_price'] = t5_price
                
                # ✅ الشرط المطلوب: T5 - T1 >= 0.4
                diff = t5_price - t1_price
                if diff >= PRICE_DIFF_THRESHOLD:
                    if (time.time() * 1000 - current_data['last_entry_time']) > 100:
                        send_trade_orders(email, current_data['base_stake'], current_data['currency'], 
                                         TRADE_CONFIGS[0]['type'], TRADE_CONFIGS[0]['label'], 
                                         TRADE_CONFIGS[0]['barrier'], current_data['current_step'], 
                                         shared_is_contract_open)
                        current_data['tick_history'] = []
                
            save_session_data(email, current_data)

    def on_open_wrapper(ws):
        ws.send(json.dumps({"authorize": token}))
        ws.send(json.dumps({"ticks": SYMBOL, "subscribe": 1}))

    while get_session_data(email).get('is_running'):
        if active_ws.get(email) is None and not shared_is_contract_open.get(email):
            ws = websocket.WebSocketApp(WSS_URL_UNIFIED, on_open=on_open_wrapper, 
                                      on_message=on_message_wrapper, on_close=lambda w,c,m: active_ws.update({email: None}))
            active_ws[email] = ws
            ws.run_forever(ping_interval=20, sslopt={"cert_reqs": ssl.CERT_NONE})
        time.sleep(1)

# ==========================================================
# FLASK APP SETUP
# ==========================================================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET_KEY', 'VERY_STRONG_SECRET_KEY_RENDER_BOT')
app.config['SESSION_PERMANENT'] = False


LOGIN_FORM = """
<!doctype html>
<title>Login</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
    body { font-family: Arial, sans-serif; padding: 20px; max-width: 400px; margin: auto; }
    h1 { color: #007bff; }
    input[type="email"], input[type="submit"] {
        width: 100%;
        padding: 10px;
        margin-top: 5px;
        margin-bottom: 10px;
        border: 1px solid #ccc;
        border-radius: 4px;
        box-sizing: border-box;
    }
    input[type="submit"] {
        background-color: #007bff;
        color: white;
        cursor: pointer;
        font-size: 1.1em;
    }
    .note { margin-top: 15px; padding: 10px; background-color: #f8f9fa; border-radius: 4px; }
</style>
<h1>Bot Login</h1>

{% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
        {% for category, message in messages %}
            <p style="color:{{ 'green' if category == 'success' else ('blue' if category == 'info' else 'red') }};">{{ message }}</p>
        {% endfor %}
    {% endif %}
{% endwith %}

<form method="POST" action="{{ url_for('login_route') }}">
    <label for="email">Email Address:</label><br>
    <input type="email" id="email" name="email" required><br>
    <input type="submit" value="Login">
</form>
<div class="note">
    <p>💡 Note: This is a placeholder login. Only users listed in <code>user_ids.txt</code> can log in.</p>
</div>
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
    .data-box {
        background-color: #f8f9fa;
        border: 1px solid #e9ecef;
        padding: 15px;
        border-radius: 5px;
        margin-bottom: 15px;
    }
    .tick-box {
        display: flex;
        justify-content: space-around;
        padding: 10px;
        background-color: #e9f7ff;
        border: 1px solid #007bff;
        border-radius: 4px;
        margin-bottom: 10px;
        font-weight: bold;
        font-size: 1.0em;
    }
    .current-digit {
        color: #ff5733;
        font-size: 1.2em;
    }
    .info-label {
        font-weight: normal;
        color: #555;
        font-size: 0.9em;
    }
    .tick-column {
        flex-basis: 45%; 
        text-align: center;
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

{# عرض سبب التوقف #}
{% if session_data and session_data.stop_reason and session_data.stop_reason != "Running" and session_data.stop_reason != "Stopped Manually" %}
    <p style="color:red; font-weight:bold;">Last Session Ended: {{ session_data.stop_reason }}</p>
{% endif %}


{% if session_data and session_data.is_running %}
    {% set strategy = '2 Ticks (R_100, D2 analysis) -> Entry: DIGITUNDER 8 (if T1 D2=9 AND T2 D2=0 or 1) | Ticks: ' + DURATION|string + ' | Martingale: ' + MARTINGALE_STEPS|string + ' Step (x' + MARTINGALE_MULTIPLIER|string + ') | Max Losses: ' + max_consecutive_losses|string %}

    <p class="status-running">✅ Bot is Running! (Auto-refreshing)</p>

    {# Display T1 and T2 Prices and D2 Digits #}
    <div class="tick-box">
        
        {# T1 Display (Oldest) #}
        <div class="tick-column">
            <span class="info-label">T1 Price:</span> <b>{% if session_data.display_t1_price %}{{ "%0.2f"|format(session_data.display_t1_price) }}{% else %}N/A{% endif %}</b>
            <br>
            <span class="info-label">T1 D2:</span>
            <b class="current-digit">
            {% set price_str = "%0.2f"|format(session_data.display_t1_price) %}
            {% set price_parts = price_str.split('.') %}
            {% set d2_digit_t1 = 'N/A' %}
            {% if price_parts|length > 1 and price_parts[-1]|length >= 2 %}{% set d2_digit_t1 = price_parts[-1][1] %}{% endif %}
            {{ d2_digit_t1 }}
            </b>
            <p style="font-weight: normal; font-size: 0.8em; color: {% if d2_digit_t1 == '9' %}green{% else %}red{% endif %};">
                Condition: T1 D2 = 9
            </p>
        </div>
        
        {# T2 Display (Latest) #}
        <div class="tick-column">
            <span class="info-label">T2 Price (Latest):</span> <b>{% if session_data.display_t2_price %}{{ "%0.2f"|format(session_data.display_t2_price) }}{% else %}N/A{% endif %}</b>
            <br>
            <span class="info-label">T2 D2 (Latest):</span>
            <b class="current-digit">
            {% set price_str_t2 = "%0.2f"|format(session_data.display_t2_price) %}
            {% set price_parts_t2 = price_str_t2.split('.') %}
            {% set d2_digit_t2 = 'N/A' %}
            {% if price_parts_t2|length > 1 and price_parts_t2[-1]|length >= 2 %}{% set d2_digit_t2 = price_parts_t2[-1][1] %}{% endif %}
            {{ d2_digit_t2 }}
            </b>
            
            <p style="font-weight: normal; font-size: 0.8em; color: {% if d2_digit_t2 == '0' or d2_digit_t2 == '1' %}green{% else %}red{% endif %};">
                Condition: T2 D2 = 0 or 1
            </p>
        </div>
    </div>

    {# الشرط النهائي (عرض في الأسفل) #}
    <div style="text-align: center; margin-bottom: 15px;">
        {% set t1_d2_ok = d2_digit_t1 == '9' %}
        {% set t2_d2_ok = d2_digit_t2 == '0' or d2_digit_t2 == '1' %}

         <p style="font-weight: bold; font-size: 1.0em; color: {% if t1_d2_ok and t2_d2_ok %}green{% else %}red{% endif %};">
            Final Signal: 
            {% if t1_d2_ok and t2_d2_ok %}
                DIGITUNDER 8 (T1 D2=9 AND T2 D2 $\in \{0, 1\}$)
            {% else %}
                NONE (T1 D2={{ d2_digit_t1 }} | T2 D2={{ d2_digit_t2 }})
            {% endif %}
        </p>
    </div>

    <div class="data-box">
        <p>Asset: <b>{{ SYMBOL }}</b> | Account: <b>{{ session_data.account_type.upper() }}</b> | Duration: <b>{{ DURATION }} Ticks</b></p>

        {# 💡 عرض الرصيد #}
        <p style="font-weight: bold; color: #17a2b8;">
            Current Balance: <b>{{ session_data.currency }} {{ session_data.current_balance|round(2) }}</b>
        </p>
        <p style="font-weight: bold; color: #007bff;">
            Balance BEFORE Trade: <b>{{ session_data.currency }} {{ session_data.before_trade_balance|round(2) }}</b>
        </p>

        {# حساب صافي الربح للمستخدم #}
        {% set net_profit_display = (session_data.current_balance - session_data.initial_starting_balance) if session_data.current_balance and session_data.initial_starting_balance else 0.0 %}
        <p style="font-weight: bold; color: {% if net_profit_display >= 0 %}green{% else %}red{% endif %};">
            Net Profit: <b>{{ session_data.currency }} {{ net_profit_display|round(2) }}</b> (TP Target: {{ session_data.tp_target|round(2) }})
        </p>

        <p style="font-weight: bold; color: {% if is_contract_open.get(email) %}#007bff{% else %}#555{% endif %};">
            Open Contract Status:
            <b>
            {% set is_open = is_contract_open.get(email) if is_contract_open is not none else false %}
            {% if is_open %}
                Waiting Check (1 Tick + 6s Delay) (Type: {{ session_data.last_trade_type if session_data.last_trade_type else 'N/A' }})
            {% else %}
                 Ready (Waiting for Signal)
            {% endif %}
            </b>
        </p>

        <p style="font-weight: bold; color: {% if session_data.current_step > 0 %}#ff5733{% else %}#555{% endif %};">
            Trade Status:
            <b>
                {% set is_open = is_contract_open.get(email) if is_contract_open is not none else false %}
                {% if is_open %}
                    Awaiting PNL Check (Stake: {{ session_data.current_total_stake|round(2) }})
                {% elif session_data.current_step > 0 %}
                    MARTINGALE STEP {{ session_data.current_step }} @ Stake: {{ session_data.current_total_stake|round(2) }} (Waiting for Signal)
                {% else %}
                    BASE STAKE @ Stake: {{ session_data.current_stake|round(2) }} (Searching 2-Tick Signal)
                {% endif %}
            </b>
        </p>

        <p>Current Stake: <b>{{ session_data.currency }} {{ session_data.current_stake|round(2) }}</b></p>
        <p style="font-weight: bold; color: {% if session_data.consecutive_losses > 0 %}red{% else %}green{% endif %};">
        Consecutive Losses: <b>{{ session_data.consecutive_losses }}</b> / {{ max_consecutive_losses }}
        (Last Entry D2: <b>{{ session_data.last_entry_d2 if session_data.last_entry_d2 is not none else 'N/A' }}</b>)
        </p>
        <p style="font-weight: bold; color: green;">Total Wins: {{ session_data.total_wins }} | Total Losses: {{ session_data.total_losses }}</p>
        <p style="font-weight: bold; color: #007bff;">Current Strategy: {{ strategy }}</p>

        {% if not session_data.is_balance_received %}
            <p style="font-weight: bold; color: orange;">⏳ Waiting for Initial Balance Data from Server...</p>
        {% endif %}
    </div>

    <form method="POST" action="{{ url_for('stop_route') }}">
        <button type="submit" style="background-color: red; color: white;">🛑 Stop Bot</button>
    </form>
{% else %}
    {# عرض سبب التوقف عند العودة إلى صفحة الإعدادات #}
    {% if session_data and session_data.stop_reason and session_data.stop_reason != "Stopped Manually" and session_data.stop_reason != "Stopped" %}
        <p style="color:red; font-weight:bold;">Last Session Ended: {{ session_data.stop_reason }}</p>
    {% endif %}

    <p class="status-stopped">🛑 Bot is Stopped. Enter settings to start a new session.</p>

    <form method="POST" action="{{ url_for('stop_route') }}">
        <button type="submit" style="background-color: #ff5733; color: white;">🧹 Force Stop & Clear Session</button>
        <input type="hidden" name="force_stop" value="true">
    </form>
    <hr>

    <form method="POST" action="{{ url_for('start_bot') }}">

        <label for="account_type">Account Type ({{ SYMBOL }}):</label><br>
        <select id="account_type" name="account_type" required>
            <option value="demo" {% if session_data.account_type == 'demo' %}selected{% endif %}>Demo (USD)</option>
            <option value="live" {% if session_data.account_type == 'live' %}selected{% endif %}>Live (tUSDT)</option>
        </select><br>

        <label for="token">Deriv API Token:</label><br>
        <input type="text" id="token" name="token" required value="{{ session_data.api_token if session_data and session_data.api_token else '' }}"><br>

        <label for="stake">Base Stake (For {{ SYMBOL }} contract, {{ DURATION }} Ticks):</label><br>
        <input type="number" id="stake" name="stake" value="{{ session_data.base_stake|round(2) if session_data and session_data.base_stake else 0.35 }}" step="0.01" min="0.35" required><br>

        <label for="tp">TP Target (USD/tUSDT):</label><br>
        <input type="number" id="tp" name="tp" value="{{ session_data.tp_target|round(2) if session_data and session_data.tp_target else 10.0 }}" step="0.01" required><br>

        <button type="submit" style="background-color: green; color: white;">🚀 Start Bot</button>
    </form>
{% endif %}
<hr>
<a href="{{ url_for('logout') }}" style="display: block; text-align: center; margin-top: 15px; font-size: 1.1em;">Logout</a>

<script>
    var SYMBOL = "{{ SYMBOL }}";
    var DURATION = {{ DURATION }};
    var TICK_HISTORY_SIZE = {{ TICK_HISTORY_SIZE }};

    function autoRefresh() {
        var isRunning = {{ 'true' if session_data and session_data.is_running else 'false' }};

        if (isRunning) {
            var refreshInterval = 1000;

            setTimeout(function() {
                window.location.reload();
            }, refreshInterval);
        }
    }

    autoRefresh();
</script>
"""

@app.before_request
def check_auth():
    if request.path not in [url_for('login_route'), url_for('static', filename='style.css')]:
        if 'email' not in session:
            flash("Login required.", 'info')
            return redirect(url_for('login_route'))

@app.route('/', methods=['GET', 'POST'])
def control_panel():
    if 'email' not in session:
        return redirect(url_for('login_route'))

    email = session['email']
    session_data = get_session_data(email)

    return render_template_string(CONTROL_FORM,
        email=email,
        session_data=session_data,
        SYMBOL=SYMBOL,
        DURATION=DURATION,
        TICK_HISTORY_SIZE=TICK_HISTORY_SIZE,
        max_martingale_step=MARTINGALE_STEPS,
        martingale_multiplier=MARTINGALE_MULTIPLIER,
        max_consecutive_losses=MAX_CONSECUTIVE_LOSSES,
        is_contract_open=is_contract_open, 
        TRADE_CONFIGS=TRADE_CONFIGS,
    )


@app.route('/login', methods=['GET', 'POST'])
def login_route():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()

        ALLOWED_USERS = load_allowed_users()

        if email in ALLOWED_USERS:
            session['email'] = email
            flash(f"Login successful. Welcome, {email}!", 'success')
            return redirect(url_for('control_panel'))
        else:
            flash("Invalid email or unauthorized user.", 'error')
            return render_template_string(LOGIN_FORM)

    return render_template_string(LOGIN_FORM)

@app.route('/logout', methods=['GET'])
def logout():
    email = session.pop('email', None)
    if email:
        pass
    flash("You have been logged out.", 'info')
    return redirect(url_for('login_route'))

@app.route('/start_bot', methods=['POST'])
def start_bot():
    if 'email' not in session:
        flash("Login required.", 'error')
        return redirect(url_for('login_route'))

    email = session['email']

    if email in flask_local_processes and flask_local_processes[email].is_alive():
        flash("Bot is already running!", 'info')
        return redirect(url_for('control_panel'))

    try:
        token = request.form['token'].strip()
        stake = float(request.form['stake'])
        tp = float(request.form['tp'])
        account_type = request.form['account_type']

        if not token or stake <= 0.0 or tp <= 0.0:
            raise ValueError("Invalid input values.")

        currency = "USD" if account_type == 'demo' else "tUSDT"

        initial_data = DEFAULT_SESSION_STATE.copy()
        initial_data.update({
            "api_token": token,
            "base_stake": stake,
            "tp_target": tp,
            "account_type": account_type,
            "currency": currency,
            "current_stake": stake,
            "current_total_stake": stake * 1,
            "is_running": False,
            "stop_reason": "Starting..."
        })
        save_session_data(email, initial_data)

        if is_contract_open is None:
              flash("Bot initialization failed (Shared state manager not ready). Please restart the service.", 'error')
              return redirect(url_for('control_panel'))
        
        process = multiprocessing.Process(
            target=bot_core_logic,
            args=(email, token, stake, tp, account_type, currency, is_contract_open) 
        )
        process.start()
        flask_local_processes[email] = process

        flash(f"Bot started successfully for {email} ({account_type.upper()}). Waiting for initial data...", 'success')
    except Exception as e:
        flash(f"Failed to start bot: {e}", 'error')

    return redirect(url_for('control_panel'))

@app.route('/stop', methods=['POST'])
def stop_route():
    if 'email' not in session:
        flash("Login required.", 'error')
        return redirect(url_for('login_route'))

    email = session['email']
    force_stop = request.form.get('force_stop') == 'true'

    clear_data_on_stop = force_stop

    stop_bot(email, clear_data=clear_data_on_stop, stop_reason="Stopped Manually")

    flash(f"Bot stopped and {'session cleared' if clear_data_on_stop else 'state saved'}.", 'info')
    return redirect(url_for('control_panel'))

# ==========================================================
# Gunicorn/Local Execution Entry Point & Shared State Setup 
# ==========================================================

try:
    manager = multiprocessing.Manager()
    is_contract_open = manager.dict() 
except Exception as e:
    print(f"⚠️ [MANAGER INIT] Multiprocessing Manager failed to initialize: {e}. Defaulting shared state to None.")
    is_contract_open = None

# ----------------------------------------------------------

if __name__ == '__main__':
    flask_local_processes = {}
    final_check_processes = {}

    for email in list(flask_local_processes.keys()):
        if flask_local_processes[email].is_alive():
            flask_local_processes[email].terminate()
            flask_local_processes[email].join()
            del flask_local_processes[email]

    if not os.path.exists(ACTIVE_SESSIONS_FILE):
        with open(ACTIVE_SESSIONS_FILE, 'w') as f:
            f.write('{}')
    if not os.path.exists(USER_IDS_FILE):
        load_allowed_users()

    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
