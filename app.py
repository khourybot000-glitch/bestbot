import time
import json
import websocket
import os
from flask import Flask, request, render_template_string, redirect, url_for, session, flash
from datetime import timedelta, datetime, timezone
from multiprocessing import Process
from threading import Lock
from collections import deque
import math
from flask import session as flask_session 

# ==========================================================
# BOT CONSTANT SETTINGS (NEW STRATEGY: Higher -0.6 / Lower +0.6)
# ==========================================================
WSS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL = "R_25" 
DURATION = 5
DURATION_UNIT = "t"

# إعدادات التوقيت والتحليل
ENTRY_SECOND = 0      # 🎯 الثانية المحددة للدخول (40)
TICK_ANALYSIS_COUNT = 5 # 🎯 عدد التيكات المطلوبة للتحليل (10)

# إعدادات المضاعفة
MARTINGALE_STEPS = 0 
MAX_CONSECUTIVE_LOSSES = 1
MARTINGALE_MULTIPLIER = 1.0  
BARRIER_OFFSET = "0.7"       # 🎯 الحاجز 0.6

CONTRACT_TYPE_BASE = "BARRIER_CONTRARIAN" # اسم تعريفي جديد

RECONNECT_DELAY = 1
USER_IDS_FILE = "user_ids.txt"
ACTIVE_SESSIONS_FILE = "active_sessions.json"

# ==========================================================
# GLOBAL STATE 
# ==========================================================
active_processes = {}
active_ws = {}
is_contract_open = {}
PROCESS_LOCK = Lock()
TRADE_LOCK = Lock() # قفل لتزامن القراءة والكتابة للبيانات الحالية
last_ten_ticks = {} 

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
    
    "last_valid_tick_price": 0.0,
    "current_entry_id": None,
    "open_contract_ids": [],
    "contract_profits": {},
    "last_barrier_value": BARRIER_OFFSET,
    "last_entry_barrier_sign": "", 
    "last_contract_type": "", 
    "waiting_for_entry": False, 
}

# --- Persistence and Control functions ---

def load_persistent_sessions():
    if not os.path.exists(ACTIVE_SESSIONS_FILE): return {}
    try:
        with open(ACTIVE_SESSIONS_FILE, 'r') as f:
            content = f.read()
            return json.loads(content) if content else {}
    except: return {}

def save_session_data(email, session_data):
    with TRADE_LOCK: # قفل أثناء الكتابة
        all_sessions = load_persistent_sessions()
        all_sessions[email] = session_data
        with open(ACTIVE_SESSIONS_FILE, 'w') as f:
            try: json.dump(all_sessions, f, indent=4)
            except: pass

def get_session_data(email):
    # لا حاجة للقفل للقراءة إلا إذا كنا سنعدل، لكن سنضيفه للتأكد
    with TRADE_LOCK: 
        all_sessions = load_persistent_sessions()
        if email in all_sessions:
            data = all_sessions[email]
            if 'current_stake_lower' in data: data['current_stake'] = data.pop('current_stake_lower')
            
            for key, default_val in DEFAULT_SESSION_STATE.items():
                if key not in data: data[key] = default_val
            return data
        return DEFAULT_SESSION_STATE.copy()

def delete_session_data(email):
    with TRADE_LOCK: # قفل أثناء الحذف
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
    except Exception as e: 
        print(f"❌ [USER LOAD ERROR] Failed to load allowed users: {e}")
        return set()

def stop_bot(email, clear_data=True, stop_reason="Stopped Manually"):
    """
    توقف عملية البوت بشكل كامل وإيقاف العملية، مع حذف البيانات إذا كان الإيقاف نهائياً (SL/TP/Manual).
    """
    global is_contract_open, active_processes
    current_data = get_session_data(email)
    
    # تعريف الحالات التي تتطلب حذف البيانات بشكل دائم
    permanent_clear_reasons = ["TP Reached", f"SL Reached: {MAX_CONSECUTIVE_LOSSES} consecutive losses.", f"SL Reached: Exceeded {MARTINGALE_STEPS} Martingale steps.", "Stopped Manually"]
    permanent_clear = clear_data and stop_reason in permanent_clear_reasons

    # 1. إيقاف حالة الجريان وحفظ السبب
    with TRADE_LOCK:
        if current_data.get("is_running") is True:
            current_data["is_running"] = False
            current_data["stop_reason"] = stop_reason
            # حفظ الحالة النهائية (Run=False)
            save_session_data(email, current_data) 
            
            if permanent_clear and stop_reason != "Stopped Manually":
                if 'email' in flask_session:
                    flask_session['last_stop_reason'] = stop_reason

    # 2. إنهاء عملية المعالجة (Process)
    with PROCESS_LOCK:
        if email in active_processes:
            process = active_processes[email]
            if process.is_alive():
                print(f"🛑 [INFO] Terminating Process for {email}...")
                process.terminate() 
            del active_processes[email]
    
    # 3. إغلاق اتصال الـ WebSocket
    with PROCESS_LOCK:
        if email in active_ws and active_ws[email]:
            try: active_ws[email].close() 
            except: pass
            del active_ws[email]

    if email in is_contract_open: is_contract_open[email] = False

    # 4. الحذف النهائي لبيانات الجلسة من قاعدة البيانات
    if permanent_clear:
        delete_session_data(email) 
        print(f"🛑 [INFO] Bot for {email} stopped ({stop_reason}). Session data PERMANENTLY CLEARED.")
    else:
        print(f"🛑 [INFO] Bot for {email} stopped ({stop_reason}). Data kept for debugging/display.")
# --- End of Persistence and Control functions ---


# ==========================================================
# TRADING BOT FUNCTIONS
# ==========================================================

def calculate_martingale_stake(base_stake, current_step, multiplier):
    if current_step == 0: 
        return base_stake
    
    return base_stake * (multiplier ** current_step)


def send_trade_order(email, stake, currency, contract_type_param, barrier_sign):
    global active_ws, DURATION, DURATION_UNIT, SYMBOL, BARRIER_OFFSET
    
    if email not in active_ws or active_ws[email] is None: 
        print(f"❌ [TRADE ERROR] Cannot send trade: WebSocket connection is inactive.")
        return None
        
    ws_app = active_ws[email]
    
    final_contract_type = ""
    if contract_type_param == "CALL":
        final_contract_type = "CALL" 
    elif contract_type_param == "PUT":
        final_contract_type = "PUT"
    else:
        print(f"❌ [TRADE ERROR] Invalid contract type received: {contract_type_param}")
        return None
        
    trade_request = {
        "buy": 1,
        "price": round(stake, 2), 
        "parameters": {
            "amount": round(stake, 2), 
            "basis": "stake",
            "contract_type": final_contract_type, 
            "currency": currency, 
            "duration": DURATION, 
            "duration_unit": DURATION_UNIT, 
            "symbol": SYMBOL,
            "barrier": f"{barrier_sign}{BARRIER_OFFSET}" 
        },
        "req_id": int(time.time() * 1000) 
    }
    
    try:
        ws_app.send(json.dumps(trade_request))
        return True
    except Exception as e:
        print(f"❌ [TRADE ERROR] Could not send trade order: {e}")
        return False


def apply_martingale_logic(email, total_profit):
    """ تهيئة حالة المضاعفة (زيادة الرهان والخطوة) عند الخسارة أو العودة للأساسي عند الفوز """
    global is_contract_open, MARTINGALE_MULTIPLIER, MARTINGALE_STEPS, MAX_CONSECUTIVE_LOSSES, TICK_ANALYSIS_COUNT
    current_data = get_session_data(email)
    
    if not current_data.get('is_running'): return
        
    base_stake_used = current_data['base_stake']
    
    # إعادة تعيين حالة الصفقة
    current_data['current_entry_id'] = None
    current_data['open_contract_ids'] = []
    current_data['contract_profits'] = {}
    current_data['waiting_for_entry'] = False
    
    entry_tag = "" 
    
    # ❌ Loss Condition 
    if total_profit < 0:
        current_data['total_losses'] += 1 
        current_data['consecutive_losses'] += 1
        current_data['current_step'] += 1 
        
        # 🛑 التحقق من حد الخسارة: تجاوز عدد الخسائر المتتالية
        if current_data['consecutive_losses'] > MAX_CONSECUTIVE_LOSSES:
            stop_reason_sl = f"SL Reached: {MAX_CONSECUTIVE_LOSSES} consecutive losses."
            save_session_data(email, current_data) 
            stop_bot(email, clear_data=True, stop_reason=stop_reason_sl) 
            return
            
        # 🛑 التحقق من حد الخسارة: تجاوز خطوات المضاعفة
        if current_data['current_step'] > MARTINGALE_STEPS: 
            stop_reason_step = f"SL Reached: Exceeded {MARTINGALE_STEPS} Martingale steps."
            save_session_data(email, current_data) 
            stop_bot(email, clear_data=True, stop_reason=stop_reason_step) 
            return
        
        # حساب الرهان الجديد للمضاعفة
        new_stake = calculate_martingale_stake(base_stake_used, current_data['current_step'], MARTINGALE_MULTIPLIER)
        current_data['current_stake'] = new_stake 
        
        entry_tag = f"READY FOR MARTINGALE STEP {current_data['current_step']} | Next Stake: {round(new_stake, 2):.2f}"
        print(f"🔄 [LOSS] PnL: {total_profit:.2f}. {entry_tag}")
        
    # ✅ Win or Draw Condition 
    else: 
        current_data['total_wins'] += 1 if total_profit > 0 else 0 
        current_data['current_step'] = 0 
        current_data['consecutive_losses'] = 0
        current_data['current_stake'] = base_stake_used
        
        entry_result_tag = "WIN" if total_profit > 0 else "DRAW"
        entry_tag = f"READY FOR BASE ENTRY (Stake reset to base: {base_stake_used:.2f})"
        print(f"✅ [ENTRY RESULT] {entry_result_tag}. Total PnL: {total_profit:.2f}. {entry_tag}")
    
    # تحديث الربح الكلي والتحقق من هدف الربح (TP)
    current_data['current_profit'] += total_profit
    if current_data['current_profit'] >= current_data['tp_target']:
        stop_reason_tp = "TP Reached"
        save_session_data(email, current_data) 
        stop_bot(email, clear_data=True, stop_reason=stop_reason_tp) 
        return
        
    # مسح التيكات وبدء التجميع للدخول الموقوت التالي
    last_ten_ticks[email].clear() 
    
    save_session_data(email, current_data) 
    
    is_contract_open[email] = False # السماح للبوت بالبحث عن فرصة دخول جديدة
    
    currency = current_data.get('currency', 'USD')
    print(f"[LOG {email}] PNL: {currency} {current_data['current_profit']:.2f}, Step: {current_data['current_step']}, Stake: {current_data['current_stake']:.2f} | Next Entry: {entry_tag}")
    

def handle_contract_settlement(email, contract_id, profit_loss):
    """ معالجة نتيجة عقد واحد """
    current_data = get_session_data(email)
    
    if contract_id not in current_data['open_contract_ids']:
        return 

    current_data['contract_profits'][contract_id] = profit_loss
    
    if contract_id in current_data['open_contract_ids']:
        current_data['open_contract_ids'].remove(contract_id)
        
    save_session_data(email, current_data)
    
    if not current_data['open_contract_ids']:
        apply_martingale_logic(email, profit_loss)


def start_new_single_trade(email, contract_type_param, barrier_sign):
    """ يرسل صفقة واحدة CALL/PUT بناءً على الإشارة ونوع العقد والرهان الحالي """
    global is_contract_open, BARRIER_OFFSET
    
    current_data = get_session_data(email)
    stake = current_data['current_stake'] 
    currency_to_use = current_data['currency']
        
    current_data['current_entry_id'] = time.time()
    current_data['open_contract_ids'] = [] 
    current_data['contract_profits'] = {}
    
    entry_type_tag = "BASE ENTRY" if current_data['current_step'] == 0 else f"MARTINGALE STEP {current_data['current_step']}"
    
    print(f"🧠 [TIMED BARRIER ENTRY] {entry_type_tag} | Contract: {contract_type_param}{barrier_sign}{BARRIER_OFFSET} | Stake: {round(stake, 2):.2f}")
    
    
    current_data['last_entry_time'] = int(time.time())
    current_data['last_entry_price'] = current_data.get('last_valid_tick_price', 0.0)
    current_data['last_entry_barrier_sign'] = barrier_sign 
    current_data['last_contract_type'] = contract_type_param 
    current_data['waiting_for_entry'] = True 

    save_session_data(email, current_data) 
    
    if send_trade_order(email, stake, currency_to_use, contract_type_param, barrier_sign):
        is_contract_open[email] = True 
    else:
        is_contract_open[email] = False 
        print(f"❌ [TRADE FAILED] Trade order failed to send for {email}. Initiating Martingale logic.")
        current_data['current_entry_id'] = None
        
        apply_martingale_logic(email, -current_data['current_stake'])
        return

def analyze_trend(email):
    """ يحلل اتجاه التيكات العشر الأخيرة """
    global last_ten_ticks, TICK_ANALYSIS_COUNT
    
    if email not in last_ten_ticks or len(last_ten_ticks[email]) < TICK_ANALYSIS_COUNT:
        return None 
        
    first_tick = last_ten_ticks[email][0]
    last_tick = last_ten_ticks[email][-1]
    
    open_price = float(first_tick['quote'])
    close_price = float(last_tick['quote'])
    
    if close_price > open_price:
        return "UP" 
    elif close_price < open_price:
        return "DOWN" 
    else:
        return "FLAT"


def determine_barrier_sign_for_entry(email):
    """ 🎯 يحدد نوع العقد وإشارة الحاجز للدخول (Higher -0.6 / Lower +0.6) """
    
    trend = analyze_trend(email)
    
    if trend == "UP":
        # صاعد (UP)، يدخل Higher (-0.6)
        # Higher = CALL
        return "CALL", "-", "UP_TREND"
        
    elif trend == "DOWN":
        # هابط (DOWN)، يدخل Lower (+0.6)
        # Lower = PUT
        return "PUT", "+", "DOWN_TREND"
        
    else:
        return None, None, "FLAT"
        
    
def bot_core_logic(email, token, stake, tp, currency, account_type):
    """ Core bot logic """
    
    global is_contract_open, active_ws, last_ten_ticks

    is_contract_open[email] = False 
    active_ws = {email: None}
    last_ten_ticks[email] = deque(maxlen=TICK_ANALYSIS_COUNT) 

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
        "waiting_for_entry": False,
    })
    save_session_data(email, session_data)

    while True:
        # 🚨 الفحص الحاسم: نقرأ حالة التشغيل من قاعدة البيانات في بداية كل دورة اتصال
        current_data = get_session_data(email) 
        if not current_data.get('is_running'):
            print(f"🛑 [PROCESS EXIT] Found is_running=False in DB for {email}. Exiting loop.")
            break 

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
            current_data['waiting_for_entry'] = False
            last_ten_ticks[email].clear()

        def on_message_wrapper(ws_app, message):
            data = json.loads(message)
            msg_type = data.get('msg_type')
            
            current_data = get_session_data(email)
            if not current_data.get('is_running'): return

            if msg_type == 'tick':
                tick_data = data['tick']
                current_price = float(tick_data['quote'])
                tick_epoch = tick_data['epoch']
                
                tick_datetime = datetime.fromtimestamp(tick_epoch, tz=timezone.utc)
                current_second = tick_datetime.second
                
                seconds_after_entry = current_second - ENTRY_SECOND
                
                current_data['last_valid_tick_price'] = current_price
                current_data['last_tick_data'] = tick_data
                
                if not is_contract_open.get(email):
                    last_ten_ticks[email].append(tick_data)
                    # إزالة طباعة كل تيك لتسريع البوت وتقليل اللوغ
                    # print(f"📈 [TICK] Price: {current_price:.5f}. Ticks Received: {len(last_ten_ticks[email])}/{TICK_ANALYSIS_COUNT} | Second: {current_second}") 
                
                
                # =========================================================================
                # === منطق الدخول الموقوت (عند الثانية 40) ===
                if not is_contract_open.get(email) and seconds_after_entry >= 0 and seconds_after_entry <= 5 : 
                    
                    last_entry_minute = datetime.fromtimestamp(current_data['last_entry_time']).minute if current_data['last_entry_time'] else -1
                    current_minute = tick_datetime.minute
                    
                    if current_minute != last_entry_minute: 
                    
                        if len(last_ten_ticks[email]) >= TICK_ANALYSIS_COUNT:
                            
                            contract_type_param, barrier_sign, trend_type = determine_barrier_sign_for_entry(email)
                            
                            if trend_type != "FLAT":
                                start_new_single_trade(email, contract_type_param=contract_type_param, barrier_sign=barrier_sign)
                                
                            else:
                                print(f"⏳ [FLAT TREND] Detected Flat trend. Clearing {TICK_ANALYSIS_COUNT} ticks. Waiting for next @{ENTRY_SECOND} second.")
                                last_ten_ticks[email].clear() 
                        
                        else:
                            print(f"⏳ [WAIT] Not enough ticks ({len(last_ten_ticks[email])}/{TICK_ANALYSIS_COUNT}) for Timed Entry @{ENTRY_SECOND}s. Waiting for next tick.")
                
                # =========================================================================

                save_session_data(email, current_data) 
                
            elif msg_type == 'buy':
                contract_id = data['buy']['contract_id']
                print(f"✅ [BUY SUCCESS] Contract ID: {contract_id}. Starting subscription...")
                
                current_data['open_contract_ids'].append(contract_id)
                current_data['waiting_for_entry'] = False 
                save_session_data(email, current_data)
                
                ws_app.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}))
            
            elif msg_type == 'proposal_open_contract':
                contract = data['proposal_open_contract']
                contract_id = contract.get('contract_id')
                
                if contract.get('is_sold') == 1 and contract_id:
                    print(f"💰 [SALE RECEIVED] Contract ID: {contract_id}. Profit: {contract.get('profit')}")
                    handle_contract_settlement(email, contract_id, contract.get('profit', 0.0))
                    
                    if 'subscription_id' in data: 
                        ws_app.send(json.dumps({"forget": data['subscription_id']}))
            
            elif 'error' in data:
                error_code = data['error'].get('code', 'N/A')
                error_message = data['error'].get('message', 'Unknown Error')
                print(f"❌❌ [API ERROR] Code: {error_code}, Message: {error_message}. *Trade may be disrupted.*")
                
                if data.get('req_id') and current_data['current_entry_id'] is not None and not current_data['open_contract_ids']:
                    is_contract_open[email] = False 
                    
                    print(f"💤 [API RECOVERY] API Buy Error received. Treating as a loss to initiate Martingale.")
                    current_data['current_entry_id'] = None
                    apply_martingale_logic(email, -current_data['current_stake'])

        def on_close_wrapper(ws_app, code, msg):
            print(f"⚠ [PROCESS] WS closed for {email}. *RECONNECTING IMMEDIATELY.*")
            is_contract_open[email] = False

        def on_error_wrapper(ws, err):
            error_str = str(err)
            print(f"[WS Error {email}] {error_str}")

            if 'buy' in error_str and not is_contract_open.get(email):
                current_data = get_session_data(email)
                if current_data.get('is_running') and current_data.get('current_entry_id') is not None and current_data.get('waiting_for_entry'):
                    print(f"❌ [WS BUY ERROR] Trade failed to send via WS. Treating as a loss and initiating Martingale.")
                    is_contract_open[email] = False
                    current_data['waiting_for_entry'] = False
                    current_data['current_entry_id'] = None
                    apply_martingale_logic(email, -current_data['current_stake'])
                    
            is_contract_open[email] = False

        try:
            ws = websocket.WebSocketApp(
                WSS_URL, on_open=on_open_wrapper, on_message=on_message_wrapper,
                on_error=on_error_wrapper, 
                on_close=on_close_wrapper
            )
            active_ws[email] = ws
            ws.run_forever(ping_interval=10, ping_timeout=5)
            
        except Exception as e:
            print(f"❌ [ERROR] WebSocket failed for {email}: {e}")
        
        # 🚨 الفحص الحاسم: نقرأ حالة التشغيل من قاعدة البيانات بعد فشل الاتصال مباشرة
        if get_session_data(email).get('is_running') is False:
             print(f"🛑 [PROCESS EXIT] Found is_running=False in DB for {email} after connection error. Exiting loop.")
             break
        
        print(f"💤 [PROCESS] Immediate Retrying connection for {email}...")
        time.sleep(0.5) 

    print(f"🛑 [PROCESS] Bot process loop ended for {email}.")

# --- (FLASK APP SETUP AND ROUTES - UNCHANGED) ---

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET_KEY', 'VERY_STRONG_SECRET_KEY_RENDER_BOT')
app.config['SESSION_PERMANENT'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=5) 

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
        
        {% if session_data and session_data.stop_reason and session_data.stop_reason != "Running" and session_data.stop_reason != "Displayed" %}
            <p style="color:red; font-weight:bold;">Last Reason: {{ session_data.stop_reason }}</p>
        {% endif %}
    {% endif %}
{% endwith %}


{% if session_data and session_data.is_running %}
    {% set timing_logic = "Timed Entry @ :" + entry_second|string + "s (Trend-Following " + tick_analysis_count|string + " Ticks)" %}
    {% set strategy = "UP->Higher-" + barrier_offset + " | DOWN->Lower+" + barrier_offset + " (" + symbol + " - " + timing_logic + " - x" + martingale_multiplier|string + " Martingale, Max Steps " + martingale_steps|string + ", Max Loss " + max_consecutive_losses|string + ")" %}
    
    <p class="status-running">✅ Bot is Running! (Auto-refreshing)</p>
    <p>Account Type: {{ session_data.account_type.upper() }} | Currency: {{ session_data.currency }}</p>
    <p>Net Profit: {{ session_data.currency }} {{ session_data.current_profit|round(2) }}</p>
    <p>Current Stake: {{ session_data.currency }} {{ session_data.current_stake|round(2) }}</p>
    <p>Step: {{ session_data.current_step }} / {{ martingale_steps }} (Max Loss: {{ max_consecutive_losses }})</p>
    <p style="font-weight: bold; color: green;">Total Wins: {{ session_data.total_wins }} | Total Losses: {{ session_data.total_losses }}</p>
    <p style="font-weight: bold; color: purple;">Last Tick Price: {{ session_data.last_valid_tick_price|round(5) }}</p>
    <p style="font-weight: bold; color: #007bff;">Current Strategy: {{ strategy }}</p>
    <p style="font-weight: bold; color: #ff5733;">Contracts Open: {{ session_data.open_contract_ids|length }}</p>
    
    <form method="POST" action="{{ url_for('stop_route') }}">
        <button type="submit" style="background-color: red; color: white;">🛑 Stop Bot</button>
    </form>
{% else %}
    <p class="status-stopped">🛑 Bot is Stopped. Enter settings to start a new session.</p>
    <form method="POST" action="{{ url_for('start_bot') }}">

        <label for="account_type">Account Type:</label><br>
        <select id="account_type" name="account_type" required>
            <option value="demo" selected>Demo (USD)</option>
            <option value="live">Live (tUSDT)</option>
        </select><br>

        <label for="token">Deriv API Token:</label><br>
        <input type="text" id="token" name="token" required value="{{ session_data.api_token if session_data and 'api_token' in session_data else '' }}"><br>
        
        <label for="stake">Base Stake (USD/tUSDT):</label><br>
        <input type="number" id="stake" name="stake" value="{{ session_data.base_stake|round(2) if session_data and 'base_stake' in session_data else 1.0 }}" step="0.01" min="0.35" required><br>
        
        <label for="tp">TP Target (USD/tUSDT):</label><br>
        <input type="number" id="tp" name="tp" value="{{ session_data.tp_target|round(2) if session_data and 'tp_target' in session_data else 10.0 }}" step="0.01" required><br>
        
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
        if reason.startswith("SL Reached"): flash(f"🛑 STOP: Max loss reached! ({reason.split(': ')[1]})", 'error')
        elif reason == "TP Reached": flash(f"✅ GOAL: Profit target ({session_data['tp_target']} {session_data.get('currency', 'USD')}) reached successfully! (TP Reached)", 'success')
        elif reason.startswith("API Buy Error"): flash(f"❌ API Error: {reason}. Check your token and account status.", 'error')
            
        session_data['stop_reason'] = "Displayed"
        save_session_data(email, session_data)
        
    if not session_data.get('is_running') and 'base_stake' not in session_data:
        session_data = DEFAULT_SESSION_STATE.copy()


    return render_template_string(CONTROL_FORM,
        email=email,
        session_data=session_data,
        martingale_steps=MARTINGALE_STEPS,
        max_consecutive_losses=MAX_CONSECUTIVE_LOSSES,
        martingale_multiplier=MARTINGALE_MULTIPLIER, 
        duration=DURATION,
        barrier_offset=BARRIER_OFFSET,
        symbol=SYMBOL,
        CONTRACT_TYPE_BASE=CONTRACT_TYPE_BASE,
        entry_second=ENTRY_SECOND,
        tick_analysis_count=TICK_ANALYSIS_COUNT
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
        token = request.form['token'] if request.form['token'] else current_data.get('api_token', '')
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
    
    flash(f'Bot started successfully. Strategy: UP->Higher-{BARRIER_OFFSET} / DOWN->Lower+{BARRIER_OFFSET} @ :{ENTRY_SECOND}s (x{MARTINGALE_MULTIPLIER})', 'success')
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
