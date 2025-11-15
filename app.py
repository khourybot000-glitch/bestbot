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
# BOT CONSTANT SETTINGS (R_25 | x14 | انتظار الثانية 6)
# ==========================================================
WSS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL = "R_100"             
DURATION = 5                  
DURATION_UNIT = "t"
TICKS_TO_ANALYZE = 5 # ⬅️ ثابت جديد: عدد التيكات المراد تحليلها

# إعدادات المضاعفة
MARTINGALE_STEPS = 5          # تم تعيينه إلى 0 ليوقف البوت عند أول خسارة (إذا لم تكن تريد المضاعفة)
MAX_CONSECUTIVE_LOSSES = 6    
MARTINGALE_MULTIPLIER = 2.1  
BARRIER_OFFSET = "0.03"       

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
    "current_stake_lower": 1.0,       
    "current_stake_higher": 1.0,      
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
    
    "last_valid_tick_price": 0.0,
    # ⬅️ التعديل هنا: قائمة لتخزين آخر 5 تيكات
    "last_5_ticks": [],             
    "current_entry_id": None,             
    "open_contract_ids": [],              
    "contract_profits": {},               
    "last_barrier_value": BARRIER_OFFSET
}

# --- Persistence functions (UNCHANGED) ---
def load_persistent_sessions():
    if not os.path.exists(ACTIVE_SESSIONS_FILE): return {}
    try:
        with open(ACTIVE_SESSIONS_FILE, 'r') as f:
            content = f.read()
            # ⬅️ تحديث لضمان أن last_5_ticks يتم تهيئته كقائمة
            data = json.loads(content) if content else {}
            for email, session_data in data.items():
                if 'last_5_ticks' not in session_data:
                    session_data['last_5_ticks'] = []
            return data
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
            del active_processes[email]
    
    with PROCESS_LOCK:
        if email in active_ws and active_ws[email]:
            try: active_ws[email].close() 
            except: pass
            del active_ws[email]

    if email in is_contract_open: is_contract_open[email] = False
    
    # ⬅️ تعديل منطق مسح البيانات لتطبيق الإيقاف الفوري
    if clear_data:
        if stop_reason in ["SL Reached", "TP Reached", "API Buy Error"]:
            print(f"🛑 [INFO] Bot for {email} stopped ({stop_reason}). Data kept for display.")
        else:
            delete_session_data(email)
            print(f"🛑 [INFO] Bot for {email} stopped ({stop_reason}) and session data cleared from file.")
    else:
        print(f"⚠ [INFO] WS closed for {email}. Attempting immediate reconnect.")
# --- End of Persistence and Control functions ---

# ==========================================================
# TRADING BOT FUNCTIONS
# ==========================================================

def calculate_martingale_stake(base_stake, current_step, multiplier):
    """ منطق المضاعفة: ضرب الرهان الأساسي في معامل المضاعفة لعدد الخطوات """
    if current_step == 0: 
        return base_stake
    
    return base_stake * (multiplier ** current_step)


def send_trade_order(email, stake, currency, contract_type_param, barrier_offset):
    """ إرسال طلب شراء واحد مع حاجز الإزاحة """
    global active_ws, DURATION, DURATION_UNIT, SYMBOL
    
    if email not in active_ws or active_ws[email] is None: 
        print(f"❌ [TRADE ERROR] Cannot send trade: WebSocket connection is inactive.")
        return None
        
    ws_app = active_ws[email]
    
    trade_request = {
        "buy": 1,
        "price": round(stake, 2),
        "parameters": {
            "amount": round(stake, 2),
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


def apply_martingale_logic(email):
    """ يطبق منطق المضاعفة بناءً على نتيجة الصفقة الواحدة """
    global is_contract_open, MARTINGALE_MULTIPLIER, MARTINGALE_STEPS, MAX_CONSECUTIVE_LOSSES
    current_data = get_session_data(email)
    
    if not current_data.get('is_running'): return

    results = list(current_data['contract_profits'].values())
    
    if not results or len(results) != 1:
        print("❌ [MARTINGALE ERROR] Incomplete results (Expected 1). Resetting stake to base.")
        total_profit = 0
    else:
        total_profit = results[0] 

    current_data['current_profit'] += total_profit
    
    if current_data['current_profit'] >= current_data['tp_target']:
        save_session_data(email, current_data)
        stop_bot(email, clear_data=True, stop_reason="TP Reached")
        return
    
    base_stake_used = current_data['base_stake']
    
    # ❌ Loss Condition 
    if total_profit < 0:
        current_data['total_losses'] += 1 
        current_data['consecutive_losses'] += 1
        current_data['current_step'] += 1 
        
        # ⬅️ منطق الإيقاف الفوري بعد أول خسارة (إذا كان MARTINGALE_STEPS = 0)
        if current_data['current_step'] > MARTINGALE_STEPS: 
            save_session_data(email, current_data)
            stop_bot(email, clear_data=True, stop_reason=f"SL Reached: Exceeded {MARTINGALE_STEPS} Martingale steps.")
            return
            
        if current_data['consecutive_losses'] > MAX_CONSECUTIVE_LOSSES:
             # هذا الشرط لن يتم تنفيذه إذا كان MARTINGALE_STEPS = 0، لكن أبقيناه لسلامة الكود
            save_session_data(email, current_data)
            stop_bot(email, clear_data=True, stop_reason=f"SL Reached: {MAX_CONSECUTIVE_LOSSES} consecutive losses.")
            return

        new_stake = calculate_martingale_stake(base_stake_used, current_data['current_step'], MARTINGALE_MULTIPLIER)
        
        current_data['current_stake_lower'] = new_stake
        current_data['current_stake_higher'] = new_stake 
        
        entry_tag = "WAITING @ SEC 6" 
        
        print(f"🔄 [SINGLE LOSS] PnL: {total_profit:.2f}. Step {current_data['current_step']}. Next Stake: {round(new_stake, 2):.2f}. Waiting for Sec 6.")
        
    # ✅ Win or Draw Condition
    else: 
        current_data['total_wins'] += 1 if total_profit > 0 else 0 
        current_data['current_step'] = 0 
        current_data['consecutive_losses'] = 0
        
        current_data['current_stake_lower'] = base_stake_used
        current_data['current_stake_higher'] = base_stake_used 
        
        entry_result_tag = "WIN" if total_profit > 0 else "DRAW"
        entry_tag = "WAITING @ SEC 6" 
        print(f"✅ [ENTRY RESULT] {entry_result_tag}. Total PnL: {total_profit:.2f}. Stake reset to base: {base_stake_used:.2f}.")

    # إعادة تعيين متغيرات الدخول 
    current_data['current_entry_id'] = None
    current_data['open_contract_ids'] = []
    current_data['contract_profits'] = {}
    
    is_contract_open[email] = False 

    currency = current_data.get('currency', 'USD')
    print(f"[LOG {email}] PNL: {currency} {current_data['current_profit']:.2f}, Step: {current_data['current_step']}, Stake: {current_data['current_stake_higher']:.2f}")
    
    save_session_data(email, current_data)
    
    
def handle_contract_settlement(email, contract_id, profit_loss):
    """ معالجة نتيجة عقد واحد وتجميعها """
    current_data = get_session_data(email)
    
    if contract_id not in current_data['open_contract_ids']:
        return

    current_data['contract_profits'][contract_id] = profit_loss
    
    if contract_id in current_data['open_contract_ids']:
        current_data['open_contract_ids'].remove(contract_id)
        
    save_session_data(email, current_data)
    
    if not current_data['open_contract_ids']:
        apply_martingale_logic(email)


# ⬅️ التعديل الرئيسي 3: تحليل الـ 5 تيكات وتحديد اتجاه الصفقة
def start_new_single_trade(email):
    """ يحلل آخر 5 تيكات ويرسل صفقة واحدة بناءً على استراتيجية الانعكاس """
    global is_contract_open, BARRIER_OFFSET, CONTRACT_TYPE_HIGHER, CONTRACT_TYPE_LOWER, MARTINGALE_STEPS
    
    current_data = get_session_data(email)
    
    if current_data['current_step'] > MARTINGALE_STEPS:
         stop_bot(email, clear_data=True, stop_reason=f"SL Reached: Max {MARTINGALE_STEPS} Martingale steps reached.")
         return
        
    ticks = current_data.get('last_5_ticks', [])
    
    # ⚠️ التحقق من توفر 5 تيكات
    if len(ticks) < TICKS_TO_ANALYZE:
        print(f"❌ [ENTRY FAIL] Only {len(ticks)} ticks available. Waiting for more data.")
        is_contract_open[email] = False # السماح بإعادة المحاولة
        return

    # 1. تحديد سعر الفتح (أقدم تيك) وسعر الإغلاق (أحدث تيك)
    open_price = ticks[0] 
    close_price = ticks[-1]
    
    # 2. تحديد الاتجاه ونوع العقد المطلوب (استراتيجية الانعكاس)
    if open_price < close_price:
        # صاعد (Open < Close) -> نتوقع هبوط (انعكاس) -> ندخل LOWER
        contract_type_to_use = CONTRACT_TYPE_LOWER
        barrier_to_use = f"+{BARRIER_OFFSET}"
        strategy_tag = "BULLISH -> LOWER"
        stake_to_use = current_data['current_stake_lower'] # استخدام رهان Lower
    elif open_price > close_price:
        # هابط (Open > Close) -> نتوقع صعود (انعكاس) -> ندخل HIGHER
        contract_type_to_use = CONTRACT_TYPE_HIGHER
        barrier_to_use = f"-{BARRIER_OFFSET}"
        strategy_tag = "BEARISH -> HIGHER"
        stake_to_use = current_data['current_stake_higher'] # استخدام رهان Higher
    else:
        # تعادل، ننتظر الجولة القادمة
        print("⚠ [ENTRY SKIPPED] Open price equals Close price. Waiting.")
        is_contract_open[email] = False
        return

    currency_to_use = current_data['currency']
    
    current_data['current_entry_id'] = time.time()
    current_data['open_contract_ids'] = []
    current_data['contract_profits'] = {}
    
    entry_type_tag = "BASE ENTRY" if current_data['current_step'] == 0 else f"MARTINGALE STEP {current_data['current_step']}"
    entry_timing_tag = "@ SEC 6" 
    
    print(f"🧠 [SINGLE TRADE ENTRY - {entry_timing_tag}] {entry_type_tag} | Strategy: {strategy_tag} | Stake: {round(stake_to_use, 2):.2f}. Price: {open_price:.5f} -> {close_price:.5f}")
    
    # إرسال الصفقة
    if send_trade_order(email, stake_to_use, currency_to_use, contract_type_to_use, barrier_to_use):
        pass
        
    is_contract_open[email] = True
    
    current_data['last_entry_time'] = int(time.time())
    current_data['last_entry_price'] = close_price # سعر الإغلاق لـ 5 تيكات هو سعر الدخول
    current_data['last_5_ticks'] = [] # ⬅️ مسح قائمة التيكات بعد الدخول
    
    save_session_data(email, current_data)


def bot_core_logic(email, token, stake, tp, currency, account_type):
    """ Core bot logic """
    
    global is_contract_open, active_ws

    is_contract_open = {email: False}
    active_ws = {email: None}

    session_data = get_session_data(email)
    # ⬅️ تأكد من تهيئة last_5_ticks عند بدء التشغيل
    session_data['last_5_ticks'] = [] 
    session_data.update({
        "api_token": token, 
        "base_stake": stake, 
        "tp_target": tp,
        "is_running": True, 
        "current_stake_lower": stake,        
        "current_stake_higher": stake,   
        "stop_reason": "Running",
        "last_entry_time": 0,
        "last_entry_price": 0.0,
        "last_tick_data": None,
        "currency": currency,
        "account_type": account_type,
        "last_valid_tick_price": 0.0,
        
        "current_entry_id": None,           
        "open_contract_ids": [],            
        "contract_profits": {},
        "last_barrier_value": BARRIER_OFFSET
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
                tick_epoch = data['tick']['epoch'] 
                
                current_second = datetime.fromtimestamp(tick_epoch, tz=timezone.utc).second
                
                # ⬅️ التعديل الرئيسي 4: تخزين التيكات في قائمة متجددة
                current_data['last_valid_tick_price'] = current_price
                current_data['last_tick_data'] = data['tick']
                
                # إضافة السعر للقائمة
                current_data['last_5_ticks'].append(current_price)
                # الحفاظ على حجم القائمة 5 تيكات
                if len(current_data['last_5_ticks']) > TICKS_TO_ANALYZE:
                    current_data['last_5_ticks'].pop(0) 
                
                save_session_data(email, current_data) 
                
                # === منطق الدخول الموحد (ينتظر الثانية 6) ===
                if not is_contract_open.get(email):
                    if current_second == 50:
                        start_new_single_trade(email)
                # === نهاية منطق الدخول ===

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
                        apply_martingale_logic(email)
                    else: 
                        print("⚠ [TRADE FAILURE] Waiting for the open contract result...")

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

# --- (FLASK APP SETUP AND ROUTES - UNCHANGED) ---

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
                <li>**Strategy:** 15-Tick Reversal + Sticky Martingale 🚨</li>
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

    if not session_data.get('is_running') and "stop_reason" in session_data and session_data["stop_reason"] not in ["Stopped Manually", "Running", "Disconnected (Auto-Retry)", "Displayed"]:
        reason = session_data["stop_reason"]
        
        # ⬅️ تعديل منطق مسح البيانات لضمان الانتقال لشاشة الإعدادات
        if reason in ["SL Reached", "TP Reached", "API Buy Error"]:
            
            # 1. إظهار الرسالة
            if reason.startswith("SL Reached"): flash(f"🛑 STOP: Max loss reached! ({reason.split(': ')[1]})", 'error')
            elif reason == "TP Reached": flash(f"✅ GOAL: Profit target ({session_data['tp_target']} {session_data.get('currency', 'USD')}) reached successfully! (TP Reached)", 'success')
            elif reason.startswith("API Buy Error"): flash(f"❌ API Error: {reason}. Check your token and account status.", 'error')
            
            # 2. مسح البيانات المتبقية لضمان الانتقال لشاشة الإعدادات
            delete_session_data(email)
            # 3. إعادة تحميل البيانات بعد المسح لعرض الواجهة الصحيحة
            session_data = get_session_data(email)
            
    return render_template_string(CONTROL_FORM,
        email=email,
        session_data=session_data,
        martingale_steps=MARTINGALE_STEPS,
        max_consecutive_losses=MAX_CONSECUTIVE_LOSSES,
        martingale_multiplier=MARTINGALE_MULTIPLIER, 
        duration=DURATION,
        barrier_offset=BARRIER_OFFSET,
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
        if stake < 0.35: raise ValueError("Stake too low")
        tp = float(request.form['tp'])
    except ValueError:
        flash("Invalid stake or TP value (Base Stake must be >= 0.35).", 'error')
        return redirect(url_for('index'))
        
    process = Process(target=bot_core_logic, args=(email, token, stake, tp, currency, account_type))
    process.daemon = True
    process.start()
    
    with PROCESS_LOCK: active_processes[email] = process
    
    flash(f'Bot started successfully. Currency: {currency}. Account: {account_type.upper()}. Strategy: {TICKS_TO_ANALYZE}-Tick Reversal (R_25 - Always @ Sec 6) with x{MARTINGALE_MULTIPLIER} Martingale (Max {MARTINGALE_STEPS} Steps, Max {MAX_CONSECUTIVE_LOSSES} Losses)', 'success')
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
