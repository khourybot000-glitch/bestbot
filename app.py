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
from collections import deque 

# ==========================================================
# BOT CONSTANT SETTINGS (Double 10-Tick Candle Trend Continuation | R_100 | Martingale x2.1)
# ==========================================================
WSS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL = "R_100"
DURATION = 5
DURATION_UNIT = "t"

# إعدادات المضاعفة
MARTINGALE_STEPS = 5                 
MAX_CONSECUTIVE_LOSSES = 6           
MARTINGALE_MULTIPLIER = 2.1          
BARRIER_OFFSET_VALUE = "0.03"        
TICK_ANALYSIS_COUNT = 10             # 🚨 تم التعديل: تحليل 10 تكات (شمعتين)

RECONNECT_DELAY = 1
USER_IDS_FILE = "user_ids.txt"
ACTIVE_SESSIONS_FILE = "active_sessions.json"

CONTRACT_TYPE_HIGHER = "PUT"
CONTRACT_TYPE_LOWER = "CALL"          

# ==========================================================

# ==========================================================
# GLOBAL STATE 
# ==========================================================
active_processes = {}
active_ws = {}
is_contract_open = {}
PROCESS_LOCK = Lock()
TRADE_LOCK = Lock()

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
    "currency": "USD",
    "account_type": "demo",
    
    "last_valid_tick_price": 0.0,
    "current_entry_id": None,
    "open_contract_ids": [],
    "contract_profits": {},
    
    "current_contract_type": CONTRACT_TYPE_HIGHER,
    "current_barrier_offset": f"+{BARRIER_OFFSET_VALUE}", 
    
    "recent_ticks": deque(maxlen=TICK_ANALYSIS_COUNT) # الحجم الآن 10
}

# --- Persistence functions (معالجة deque) ---
def load_persistent_sessions():
    if not os.path.exists(ACTIVE_SESSIONS_FILE): return {}
    try:
        with open(ACTIVE_SESSIONS_FILE, 'r') as f:
            content = f.read()
            return json.loads(content) if content else {}
    except: return {}

def save_session_data(email, session_data):
    all_sessions = load_persistent_sessions()
    data_to_save = session_data.copy()
    if isinstance(data_to_save.get("recent_ticks"), deque):
        # نضمن حفظ الـ deque بالحجم الصحيح
        data_to_save["recent_ticks"] = list(data_to_save["recent_ticks"])
        
    all_sessions[email] = data_to_save
    
    with open(ACTIVE_SESSIONS_FILE, 'w') as f:
        try: json.dump(all_sessions, f, indent=4)
        except: pass

def get_session_data(email):
    all_sessions = load_persistent_sessions()
    data = all_sessions.get(email, DEFAULT_SESSION_STATE.copy())
    
    for key, default_val in DEFAULT_SESSION_STATE.items():
        if key not in data: data[key] = default_val
        
    if not isinstance(data.get("recent_ticks"), deque):
        tick_data = data.get("recent_ticks", []) or [] 
        # نضمن تحميل الـ deque بالحجم الجديد 10
        data["recent_ticks"] = deque(tick_data, maxlen=TICK_ANALYSIS_COUNT)
        
    return data

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
            del active_processes[email]
    
    with PROCESS_LOCK:
        if email in active_ws and active_ws[email]:
            try: active_ws[email].close()
            except: pass
            del active_ws[email]

    if email in is_contract_open: is_contract_open[email] = False

    if clear_data:
        # نحفظ البيانات مؤقتاً لعرض رسالة التوقف (SL/TP) ثم يتم مسحها في دالة index()
        if stop_reason in [f"SL Reached: {MAX_CONSECUTIVE_LOSSES} consecutive losses.", "TP Reached", "API Buy Error"]:
            print(f"🛑 [INFO] Bot for {email} stopped ({stop_reason}). Data kept temporarily for display.")
        else:
            # يتم مسح البيانات عند الإيقاف اليدوي أو الانتهاء الطبيعي
            delete_session_data(email)
            print(f"🛑 [INFO] Bot for {email} stopped ({stop_reason}) and session data cleared from file.")
    else:
        print(f"⚠ [INFO] WS closed for {email}. Attempting immediate reconnect.")


# ==========================================================
# TRADING BOT FUNCTIONS 
# ==========================================================

def calculate_martingale_stake(base_stake, current_step, multiplier):
    if current_step == 0:
        return base_stake
    return base_stake * (multiplier ** current_step)


def send_trade_order(email, stake, currency, contract_type_param, barrier_offset):
    global active_ws, DURATION, DURATION_UNIT, SYMBOL
    
    if email not in active_ws or active_ws[email] is None:
        print(f"❌ [TRADE ERROR] Cannot send trade: WebSocket connection is inactive.")
        return None
        
    ws_app = active_ws[email]
    
    stake_rounded = round(stake, 2)
    
    trade_request = {
        "buy": 1,
        "price": stake_rounded,
        "parameters": {
            "amount": stake_rounded,
            "basis": "stake",
            "contract_type": contract_type_param,
            "currency": currency,
            "duration": DURATION,
            "duration_unit": DURATION_UNIT,
            "symbol": SYMBOL,
            "barrier": str(barrier_offset) 
        }
    }
    
    try:
        ws_app.send(json.dumps(trade_request))
        return True
    except Exception as e:
        print(f"❌ [TRADE ERROR] Could not send trade order: {e}")
        return False
        
def analyze_and_set_trade(email):
    """ 💡 تحليل حركة استمرار اتجاه شمعتين 10-Tick. """
    global TICK_ANALYSIS_COUNT, BARRIER_OFFSET_VALUE, CONTRACT_TYPE_HIGHER, CONTRACT_TYPE_LOWER
    current_data = get_session_data(email)
    
    ticks = list(current_data['recent_ticks'])
    
    # 🚨 التحقق من توفر 10 تكات
    if len(ticks) < TICK_ANALYSIS_COUNT:
        return False 
    
    # === تحليل الشمعة الأولى (C1) والشمعة الثانية (C2) ===
    C1_Open = ticks[0] 
    C1_Close = ticks[4] 
    C2_Open = ticks[5]
    C2_Close = ticks[9]
    
    # تحديد اتجاه كل شمعة
    is_candle1_up = (C1_Open < C1_Close) 
    is_candle1_down = (C1_Open > C1_Close)
    is_candle2_up = (C2_Open < C2_Close)
    is_candle2_down = (C2_Open > C2_Close)
    
    trade_signal = None
    
    # 1. سيناريو الصعود (Higher): C1 صعود AND C2 صعود
    if is_candle1_up and is_candle2_up:
        current_data['current_contract_type'] = CONTRACT_TYPE_HIGHER
        current_data['current_barrier_offset'] = f"-{BARRIER_OFFSET_VALUE}" 
        trade_signal = "Double 10T: Up-Up - Higher (Strong Continuation)"
        
    # 2. سيناريو الهبوط (Lower): C1 هبوط AND C2 هبوط
    elif is_candle1_down and is_candle2_down:
        current_data['current_contract_type'] = CONTRACT_TYPE_LOWER
        current_data['current_barrier_offset'] = f"+{BARRIER_OFFSET_VALUE}" 
        trade_signal = "Double 10T: Down-Down - Lower (Strong Continuation)"
        
    else:
        # إذا لم يكن هناك نموذج استمرار قوي، لا دخول
        return False

    current_data['current_stake'] = calculate_martingale_stake(current_data['base_stake'], current_data['current_step'], MARTINGALE_MULTIPLIER)
    
    # 🚨 مسح التكات بعد التحليل
    current_data['recent_ticks'].clear() 
    
    save_session_data(email, current_data)
    print(f"✅ [10-TICK CONT] Entry: {current_data['current_contract_type']} {current_data['current_barrier_offset']} | Pattern: {trade_signal}")
    start_new_trade(email)
    return True

        
def apply_martingale_logic(email):
    global is_contract_open, MARTINGALE_MULTIPLIER, MARTINGALE_STEPS, MAX_CONSECUTIVE_LOSSES
    current_data = get_session_data(email)
    
    if not current_data.get('is_running'): return

    results = list(current_data['contract_profits'].values())
    total_profit = results[0] if results else 0

    current_data['current_profit'] += total_profit
    
    # 1. التحقق من هدف الربح (TP)
    if current_data['current_profit'] >= current_data['tp_target']:
        save_session_data(email, current_data)
        stop_bot(email, clear_data=True, stop_reason="TP Reached")
        return
        
    base_stake_used = current_data['base_stake']
    
    if total_profit < 0:
        current_data['total_losses'] += 1
        current_data['consecutive_losses'] += 1
        current_data['current_step'] += 1 
        
        # التوقف عند الوصول إلى الحد الأقصى أو تجاوزه
        if current_data['consecutive_losses'] >= MAX_CONSECUTIVE_LOSSES:
            # التوقف ومسح البيانات
            save_session_data(email, current_data)
            stop_bot(email, clear_data=True, stop_reason=f"SL Reached: {MAX_CONSECUTIVE_LOSSES} consecutive losses.")
            return # إنهاء الدالة
            
        # 2. إذا لم يتوقف البوت: التحقق من تجاوز خطوات المضاعفة (Hard Reset)
        if current_data['current_step'] > MARTINGALE_STEPS:
            current_data['current_step'] = 0
            current_data['consecutive_losses'] = 0 
            new_stake = base_stake_used
            print("🚨 [MARTINGALE RESET] Max steps reached. Resetting stake to base.")
        else:
            new_stake = calculate_martingale_stake(base_stake_used, current_data['current_step'], MARTINGALE_MULTIPLIER)
        
        current_data['current_stake'] = new_stake
        print(f"🔄 [LOSS] PnL: {total_profit:.2f}. Step {current_data['current_step']}. Next Stake: {round(new_stake, 2):.2f}. Awaiting next 10-Tick signal.")
        
    else:
        current_data['total_wins'] += 1 if total_profit > 0 else 0
        current_data['current_step'] = 0
        current_data['consecutive_losses'] = 0
        current_data['current_stake'] = base_stake_used
        
        entry_result_tag = "WIN" if total_profit > 0 else "DRAW"
        print(f"✅ [ENTRY RESULT] {entry_result_tag}. PnL: {total_profit:.2f}. Stake reset to base: {base_stake_used:.2f}. Awaiting next 10-Tick signal.")
        
    current_data['current_entry_id'] = None
    current_data['open_contract_ids'] = []
    current_data['contract_profits'] = {}
    
    is_contract_open[email] = False

    currency = current_data.get('currency', 'USD')
    print(f"[LOG {email}] PNL: {currency} {current_data['current_profit']:.2f}, Step: {current_data['current_step']}, Stake: {current_data['current_stake']:.2f} | Strategy: Double 10-Tick Continuation.")
    
    save_session_data(email, current_data)
    
    
def handle_contract_settlement(email, contract_id, profit_loss):
    current_data = get_session_data(email)
    
    if contract_id not in current_data['open_contract_ids']:
        return

    current_data['contract_profits'][contract_id] = profit_loss
    
    if contract_id in current_data['open_contract_ids']:
        current_data['open_contract_ids'].remove(contract_id)
        
    save_session_data(email, current_data)
    
    if not current_data['open_contract_ids']:
        apply_martingale_logic(email)


def start_new_trade(email): 
    global is_contract_open
    
    current_data = get_session_data(email)
    
    stake_to_use = current_data['current_stake']
    currency_to_use = current_data['currency']
    
    contract_type = current_data['current_contract_type']
    barrier = current_data['current_barrier_offset']
    
    if is_contract_open.get(email):
        print("⚠ [ENTRY] Trade already open. Skipping.")
        return
        
    current_data['current_entry_id'] = time.time()
    current_data['open_contract_ids'] = []
    current_data['contract_profits'] = {}
    
    entry_type_tag = "BASE ENTRY" if current_data['current_step'] == 0 else f"MARTINGALE STEP {current_data['current_step']}"
    
    print(f"🧠 [DOUBLE {contract_type} ENTRY] {entry_type_tag} | Stake: {round(stake_to_use, 2):.2f}. Barrier: {barrier}")
    
    if send_trade_order(email, stake_to_use, currency_to_use, contract_type, barrier):
        pass
    
    is_contract_open[email] = True 
    
    current_data['last_entry_time'] = int(time.time())
    current_data['last_entry_price'] = current_data.get('last_valid_tick_price', 0.0)

    save_session_data(email, current_data)


def bot_core_logic(email, token, stake, tp, currency, account_type):
    global is_contract_open, active_ws, TICK_ANALYSIS_COUNT

    is_contract_open = {email: False}
    active_ws = {email: None}

    initial_barrier = f"+{BARRIER_OFFSET_VALUE}"
    
    session_data = get_session_data(email)
    session_data.update({
        "api_token": token,
        "base_stake": stake,
        "tp_target": tp,
        "is_running": True,
        "current_stake": stake,
        "stop_reason": "Running",
        "currency": currency,
        "account_type": account_type,
        "current_step": 0,
        "consecutive_losses": 0,
        
        "current_contract_type": CONTRACT_TYPE_HIGHER,
        "current_barrier_offset": initial_barrier,
        "recent_ticks": deque(maxlen=TICK_ANALYSIS_COUNT)
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
            if not current_data.get('is_running'): return
            
            if msg_type == 'tick':
                current_price = float(data['tick']['quote'])
                
                recent_ticks_deque = current_data['recent_ticks']
                recent_ticks_deque.append(current_price)
                
                current_data['last_valid_tick_price'] = current_price
                
                save_session_data(email, current_data)
                
                # التحليل يتم فقط عندما يكتمل جمع 10 تكات
                if not is_contract_open.get(email) and len(recent_ticks_deque) == TICK_ANALYSIS_COUNT:
                    analyze_and_set_trade(email)

            elif msg_type == 'buy':
                contract_id = data['buy']['contract_id']
                current_data['open_contract_ids'].append(contract_id)
                save_session_data(email, current_data)
                
                ws_app.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}))
                
            elif 'error' in data:
                error_code = data['error'].get('code', 'N/A')
                error_message = data['error'].get('message', 'Unknown Error')
                print(f"❌❌ [API ERROR] Code: {error_code}, Message: {error_message}. Trade may be disrupted.")
                
                if current_data['current_entry_id'] is not None and is_contract_open.get(email):
                    time.sleep(1)
                    if not current_data['open_contract_ids']:
                        is_contract_open[email] = False
                        apply_martingale_logic(email)
                    else:
                        print("⚠ [TRADE FAILURE] Waiting for contract result...")

            elif msg_type == 'proposal_open_contract':
                contract = data['proposal_open_contract']
                if contract.get('is_sold') == 1:
                    contract_id = contract['contract_id']
                    handle_contract_settlement(email, contract_id, contract['profit'])
                    
                    if 'subscription_id' in data: ws_app.send(json.dumps({"forget": data['subscription_id']}))

        def on_close_wrapper(ws_app, code, msg):
            print(f"⚠ [PROCESS] WS closed for {email}. RECONNECTING IMMEDIATELY.")
            is_contract_open[email] = False

        try:
            ws = websocket.WebSocketApp(
                WSS_URL, on_open=on_open_wrapper, on_message=on_message_wrapper,
                on_error=lambda ws, err: print(f"[WS Error {email}] {err}"),
                on_close=on_close_wrapper
            )
            active_ws[email] = ws
            ws.run_forever(ping_interval=10, ping_timeout=5)
            
        except Exception as e:
            print(f"❌ [ERROR] WebSocket failed for {email}: {e}")
        
        if get_session_data(email).get('is_running') is False: break
        
        print(f"💤 [PROCESS] Immediate Retrying connection for {email}...")
        time.sleep(0.5)

    print(f"🛑 [PROCESS] Bot process loop ended for {email}.")


# ==========================================================
# FLASK WEB APP
# ==========================================================

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET_KEY', 'VERY_STRONG_SECRET_KEY_RENDER_BOT')
app.config['SESSION_PERMANENT'] = False

AUTH_FORM = """
<!DOCTYPE html>
<html>
<head>
    <title>Bot Login</title>
    <style>
        body { font-family: Arial, sans-serif; background-color: #1a1a2e; color: #e0e0e0; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .container { background-color: #0f0f1a; padding: 30px; border-radius: 10px; box-shadow: 0 0 20px rgba(0, 0, 0, 0.5); width: 300px; text-align: center; }
        h2 { color: #88c0d0; margin-bottom: 20px; }
        input[type="email"], input[type="password"] { width: calc(100% - 20px); padding: 10px; margin-bottom: 15px; border: 1px solid #3b4252; border-radius: 5px; background-color: #2e3440; color: #e0e0e0; }
        button { background-color: #bf616a; color: white; padding: 10px 15px; border: none; border-radius: 5px; cursor: pointer; width: 100%; font-size: 16px; }
        button:hover { background-color: #b48ead; }
        .flash { padding: 10px; margin-bottom: 15px; border-radius: 5px; }
        .flash.error { background-color: #bf616a; }
        .flash.success { background-color: #a3be8c; }
    </style>
</head>
<body>
    <div class="container">
        <h2>Bot Login</h2>
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="flash {{ category }}">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        <form method="post" action="{{ url_for('login') }}">
            <input type="email" name="email" placeholder="Enter your authorized email" required>
            <button type="submit">Login</button>
        </form>
    </div>
</body>
</html>
"""

CONTROL_FORM = """
<!DOCTYPE html>
<html>
<head>
    <title>Trading Bot Control</title>
    {% if session_data.get('is_running') %}
    <meta http-equiv="refresh" content="2">
    {% endif %}
    <style>
        body { font-family: Arial, sans-serif; background-color: #1a1a2e; color: #e0e0e0; padding: 20px; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; border-bottom: 2px solid #3b4252; padding-bottom: 10px; }
        .header h1 { color: #88c0d0; margin: 0; }
        .header p { margin: 0; font-size: 1.1em; }
        .content { display: flex; flex-wrap: wrap; gap: 20px; }
        .control-panel, .status-panel, .info-panel { background-color: #0f0f1a; padding: 20px; border-radius: 10px; box-shadow: 0 0 15px rgba(0, 0, 0, 0.4); flex: 1; min-width: 300px; }
        .status-panel { flex: 2; }
        h2 { color: #a3be8c; border-bottom: 1px solid #3b4252; padding-bottom: 5px; margin-top: 0; }
        label { display: block; margin-top: 10px; font-size: 0.9em; color: #b48ead; }
        input[type="text"], input[type="number"], select { width: calc(100% - 22px); padding: 10px; margin-top: 5px; margin-bottom: 10px; border: 1px solid #3b4252; border-radius: 5px; background-color: #2e3440; color: #e0e0e0; }
        button { background-color: #bf616a; color: white; padding: 10px 15px; border: none; border-radius: 5px; cursor: pointer; margin-top: 10px; font-size: 1em; }
        .start-button { background-color: #a3be8c; }
        button:hover { opacity: 0.8; }
        .status { padding: 15px; border-radius: 8px; text-align: center; font-size: 1.2em; font-weight: bold; margin-bottom: 20px; }
        .running { background-color: #2e4a40; color: #a3be8c; }
        .stopped { background-color: #4a2e2e; color: #bf616a; }
        .flash { padding: 10px; margin-bottom: 15px; border-radius: 5px; font-weight: bold; }
        .flash.error { background-color: #bf616a; color: white; }
        .flash.success { background-color: #a3be8c; color: #0f0f1a; }
        .stats table { width: 100%; border-collapse: collapse; margin-top: 15px; }
        .stats th, .stats td { border: 1px solid #3b4252; padding: 10px; text-align: left; }
        .stats th { background-color: #2e3440; color: #88c0d0; }
        .stats .profit-value { color: {{ 'green' if session_data.get('current_profit', 0) >= 0 else 'red' }}; font-weight: bold; }
        .info-panel ul { list-style: none; padding: 0; }
        .info-panel li { margin-bottom: 8px; padding: 5px; border-bottom: 1px dotted #3b4252; }
        .logout-button { background-color: #3b4252; float: right; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Trading Bot Control Panel</h1>
        <div>
            <p>User: <strong>{{ email }}</strong></p>
            <form method="get" action="{{ url_for('logout') }}" style="display:inline;">
                <button type="submit" class="logout-button">Logout</button>
            </form>
        </div>
    </div>

    {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
            {% for category, message in messages %}
                <div class="flash {{ category }}">{{ message }}</div>
            {% endfor %}
        {% endif %}
    {% endwith %}

    <div class="status {{ 'running' if session_data.get('is_running') else 'stopped' }}">
        {{ 'BOT IS RUNNING' if session_data.get('is_running') else 'BOT IS STOPPED' }} ({{ session_data.get('stop_reason', 'Stopped Manually') }})
    </div>

    <div class="content">
        <div class="control-panel">
            <h2>{{ 'Stop Bot' if session_data.get('is_running') else 'Start Bot' }}</h2>
            {% if session_data.get('is_running') %}
                <form method="post" action="{{ url_for('stop_route') }}">
                    <button type="submit">🛑 Stop Bot</button>
                </form>
            {% else %}
                <form method="post" action="{{ url_for('start_bot') }}">
                    <label for="token">Deriv API Token:</label>
                    <input type="text" id="token" name="token" value="{{ session_data.get('api_token', '') }}" placeholder="Enter API Token" required>
                    
                    <label for="account_type">Account Type:</label>
                    <select id="account_type" name="account_type" required>
                        <option value="demo" {% if session_data.get('account_type') == 'demo' %}selected{% endif %}>Demo (USD)</option>
                        <option value="real" {% if session_data.get('account_type') == 'real' %}selected{% endif %}>Real (tUSDT)</option>
                    </select>

                    <label for="stake">Base Stake (Min 0.35):</label>
                    <input type="number" id="stake" name="stake" step="0.01" min="0.35" value="{{ session_data.get('base_stake', 1.0) }}" required>
                    
                    <label for="tp">Take Profit Target (TP):</label>
                    <input type="number" id="tp" name="tp" step="1" min="1" value="{{ session_data.get('tp_target', 10.0) }}" required>
                    
                    <button type="submit" class="start-button">🚀 Start Bot</button>
                </form>
            {% endif %}
        </div>
        
        <div class="status-panel stats">
            <h2>Trading Statistics</h2>
            <table>
                <tr><th>Metric</th><th>Value</th></tr>
                <tr><td>Current PNL ({{ session_data.get('currency', 'USD') }})</td><td class="profit-value">{{ '%.2f' | format(session_data.get('current_profit', 0.0)) }}</td></tr>
                <tr><td>Target PNL (TP)</td><td>{{ '%.2f' | format(session_data.get('tp_target', 0.0)) }}</td></tr>
                <tr><td>Total Wins</td><td>{{ session_data.get('total_wins', 0) }}</td></tr>
                <tr><td>Total Losses</td><td>{{ session_data.get('total_losses', 0) }}</td></tr>
                <tr><td>Consecutive Losses</td><td>{{ session_data.get('consecutive_losses', 0) }}</td></tr>
                <tr><td>Current Martingale Step</td><td>{{ session_data.get('current_step', 0) }} / {{ martingale_steps }}</td></tr>
                <tr><td>Current Stake ({{ session_data.get('currency', 'USD') }})</td><td>{{ '%.2f' | format(session_data.get('current_stake', session_data.get('base_stake', 1.0))) }}</td></tr>
                <tr><td>Last Tick Price</td><td>{{ '%.4f' | format(session_data.get('last_valid_tick_price', 0.0)) }}</td></tr>
                <tr><td>Active Contract IDs</td><td>{{ session_data.get('open_contract_ids', []) | length }}</td></tr>
            </table>
        </div>

        <div class="info-panel">
            <h2>Strategy Configuration</h2>
            <ul>
                <li>**Strategy:** Double 10-Tick Continuation 🚨</li>
                <li>**Symbol (الزوج):** {{ symbol }}</li>
                <li>**Duration:** {{ duration }} Ticks</li>
                <li>**Barrier Offset (العائق):** +/-{{ barrier_offset }}</li>
                <li>**Base Stake:** {{ '%.2f' | format(session_data.get('base_stake', 1.0)) }} {{ session_data.get('currency', 'USD') }}</li>
                <li>**Martingale Multiplier:** x{{ martingale_multiplier }}</li>
                <li>**Max Martingale Steps:** {{ martingale_steps }}</li>
                <li>**Stop Loss (Max Cons. Losses):** {{ max_consecutive_losses }}</li>
            </ul>
        </div>
    </div>
</body>
</html>
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

    global MARTINGALE_STEPS, MAX_CONSECUTIVE_LOSSES, MARTINGALE_MULTIPLIER, BARRIER_OFFSET_VALUE, SYMBOL, DURATION

    # هذا الجزء هو الذي يتأكد من عرض رسالة التوقف ومسح البيانات عند الوصول إليها
    if not session_data.get('is_running') and "stop_reason" in session_data and session_data["stop_reason"] not in ["Stopped Manually", "Running", "Disconnected (Auto-Retry)", "Displayed"]:
        reason = session_data["stop_reason"]
        
        # إذا كان السبب SL أو TP أو API Error
        if reason.startswith("SL Reached"): 
            flash(f"🛑 STOP LOSS: The maximum limit of {MAX_CONSECUTIVE_LOSSES} consecutive losses has been reached. Bot Stopped.", 'error')
        elif reason == "TP Reached": 
            flash(f"✅ GOAL: Profit target ({session_data['tp_target']} {session_data.get('currency', 'USD')}) reached successfully! Bot Stopped.", 'success')
        elif reason.startswith("API Buy Error"): 
            flash(f"❌ API Error: {reason}. Check your token and account status. Bot Stopped.", 'error')
            
        session_data['stop_reason'] = "Displayed"
        save_session_data(email, session_data)
        
        # مسح جميع البيانات المتبقية بعد عرض رسالة التوقف (SL/TP)
        if reason.startswith("SL Reached") or reason == "TP Reached" or reason.startswith("API Buy Error"):
             delete_session_data(email)
             
        # إعادة التحميل لعرض الصفحة خالية من البيانات (إعدادات البدء)
        return redirect(url_for('index'))


    return render_template_string(CONTROL_FORM,
        email=email,
        session_data=session_data,
        martingale_steps=MARTINGALE_STEPS,
        max_consecutive_losses=MAX_CONSECUTIVE_LOSSES,
        martingale_multiplier=MARTINGALE_MULTIPLIER,
        duration=DURATION,
        barrier_offset=BARRIER_OFFSET_VALUE, 
        symbol=SYMBOL
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
    global active_processes, MARTINGALE_STEPS
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
        if stake < 0.35: raise ValueError("Stake too low")
        tp = float(request.form['tp'])
    except ValueError:
        flash("Invalid stake or TP value (Base Stake must be >= 0.35).", 'error')
        return redirect(url_for('index'))
        
    process = Process(target=bot_core_logic, args=(email, token, stake, tp, currency, account_type))
    process.daemon = True
    process.start()
    
    with PROCESS_LOCK: active_processes[email] = process
    
    flash(f'Bot started successfully. Currency: {currency}. Account: {account_type.upper()}. Strategy: Double 10-Tick Continuation (Martingale Max {MARTINGALE_STEPS} Steps)', 'success')
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
    all_sessions = load_persistent_sessions()
    for email in list(all_sessions.keys()):
        stop_bot(email, clear_data=False, stop_reason="Disconnected (Auto-Retry)")
        
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
