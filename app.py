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
# BOT CONSTANT SETTINGS (R_100 | x2.2 | خطوات 4 | خسائر متتالية 5)
# ==========================================================
WSS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL = "R_100"                      # تغيير الزوج إلى R_100
DURATION = 5
DURATION_UNIT = "t"

# إعدادات المضاعفة المُحدَّثة
MARTINGALE_STEPS = 4                  # الحد الأقصى لخطوات المضاعفة
MAX_CONSECUTIVE_LOSSES = 5            # الحد الأقصى للخسائر المتتالية
MARTINGALE_MULTIPLIER = 2.2           # معامل المضاعفة الجديد

RECONNECT_DELAY = 1
USER_IDS_FILE = "user_ids.txt"
ACTIVE_SESSIONS_FILE = "active_sessions.json"

# أنواع العقود لـ Rise/Fall
CONTRACT_TYPE_HIGHER = "RISE"
CONTRACT_TYPE_LOWER = "FALL"

# ==========================================================

# ==========================================================
# GLOBAL STATE
# ==========================================================
active_processes = {}
active_ws = {}
is_contract_open = {} 
PROCESS_LOCK = Lock()
TRADE_LOCK = Lock()

# متغير جديد لتخزين آخر 5 تكات لكل مستخدم
TICK_HISTORY = {} 

DEFAULT_SESSION_STATE = {
    "api_token": "",
    "base_stake": 1.0,
    "tp_target": 10.0,
    "is_running": False,
    "current_profit": 0.0,
    "current_stake_lower": 1.0,         # الرهان الحالي المستخدم
    "current_stake_higher": 1.0,        # يتم تحديثه ليتوافق مع lower (للتناسق)
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
}

# --- Persistence functions (UNCHANGED) ---
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
            del active_processes[email]
    
    with PROCESS_LOCK:
        if email in active_ws and active_ws[email]:
            try: active_ws[email].close() 
            except: pass
            del active_ws[email]

    if email in is_contract_open: is_contract_open[email] = False

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


def send_trade_order(email, amount, currency, contract_type, barrier_value=""):
    """ إرسال طلب شراء واحد (تم تعديلها لإزالة الحاجز لعقود Rise/Fall) """
    global active_ws, DURATION, DURATION_UNIT, SYMBOL
    
    if email not in active_ws or active_ws[email] is None: 
        print(f"❌ [TRADE ERROR] Cannot send trade: WebSocket connection is inactive.")
        return None
        
    ws_app = active_ws[email]
    
    trade_request = {
        "buy": 1,
        "price": round(amount, 2),
        "parameters": {
            "amount": round(amount, 2),
            "basis": "stake",
            "contract_type": contract_type, 
            "currency": currency, 
            "duration": DURATION, 
            "duration_unit": DURATION_UNIT, 
            "symbol": SYMBOL,
        }
    }
    
    # لا نُرسل حقل barrier أبداً لعقود Rise/Fall
    # if barrier_value: trade_request['parameters']['barrier'] = barrier_value # تم حذفه
    
    try:
        ws_app.send(json.dumps(trade_request))
        return True
    except Exception as e:
        print(f"❌ [TRADE ERROR] Could not send trade order: {e}")
        return False


def apply_martingale_logic(email, total_profit):
    """ يطبق منطق المضاعفة بناءً على نتيجة الصفقة الواحدة """
    global is_contract_open, MARTINGALE_MULTIPLIER, MARTINGALE_STEPS, MAX_CONSECUTIVE_LOSSES
    current_data = get_session_data(email)
    
    if not current_data.get('is_running'): return

    current_data['current_profit'] += total_profit
    
    # 🛑 تحقق من TP
    if current_data['current_profit'] >= current_data['tp_target']:
        save_session_data(email, current_data)
        stop_bot(email, clear_data=True, stop_reason="TP Reached")
        return
        
    base_stake_used = current_data['base_stake']
    
    # ❌ Loss Condition (خسارة كاملة للرهان)
    if total_profit < 0:
        current_data['total_losses'] += 1
        current_data['consecutive_losses'] += 1
        current_data['current_step'] += 1 # الانتقال إلى الخطوة التالية (مضاعفة)
        
        # 🛑 تحقق من SL (خسائر متتالية)
        if current_data['consecutive_losses'] > MAX_CONSECUTIVE_LOSSES:
            save_session_data(email, current_data)
            stop_bot(email, clear_data=True, stop_reason=f"SL Reached: {MAX_CONSECUTIVE_LOSSES} consecutive losses.")
            return
            
        # 🛑 تحقق من SL (خطوات المضاعفة)
        if current_data['current_step'] > MARTINGALE_STEPS:
            save_session_data(email, current_data)
            stop_bot(email, clear_data=True, stop_reason=f"SL Reached: Exceeded {MARTINGALE_STEPS} Martingale steps.")
            return
            
        new_stake = calculate_martingale_stake(base_stake_used, current_data['current_step'], MARTINGALE_MULTIPLIER)
        current_data['current_stake_lower'] = new_stake
        current_data['current_stake_higher'] = new_stake
        
        print(f"🔄 [LOSS] PnL: {total_profit:.2f}. Step {current_data['current_step']}. Next Stake: {round(new_stake, 2):.2f}. Waiting for next entry.")
        
    # ✅ Win Condition
    else:
        current_data['total_wins'] += 1
        current_data['current_step'] = 0
        current_data['consecutive_losses'] = 0
        
        current_data['current_stake_lower'] = base_stake_used
        current_data['current_stake_higher'] = base_stake_used
        
        entry_result_tag = "WIN" if total_profit > 0 else "DRAW"
        print(f"✅ [ENTRY RESULT] {entry_result_tag}. Total PnL: {total_profit:.2f}. Stake reset to base: {base_stake_used:.2f}.")

    # إعادة تعيين متغيرات الدخول للسماح بالدخول الجديد
    current_data['current_entry_id'] = None
    current_data['open_contract_ids'] = []
    current_data['contract_profits'] = {}
    is_contract_open[email] = False # للسماح بالدخول عند 0 أو 20 أو 40
    
    currency = current_data.get('currency', 'USD')
    print(f"[LOG {email}] PNL: {currency} {current_data['current_profit']:.2f}, Step: {current_data['current_step']}, Stake: {current_data['current_stake_lower']:.2f}")
    
    save_session_data(email, current_data)


def handle_contract_settlement(email, contract_id, profit_loss):
    """ معالجة نتيجة العقد الواحد """
    current_data = get_session_data(email)
    
    # إزالة العقد من القائمة المفتوحة (بالرغم من أنها ستكون فارغة دائماً)
    if contract_id in current_data['open_contract_ids']:
        current_data['open_contract_ids'].remove(contract_id)
        
    # استدعاء منطق المضاعفة بالنتيجة مباشرة
    apply_martingale_logic(email, profit_loss)


def start_new_trade(email, direction, current_second_used):
    """ يرسل صفقة واحدة (Rise أو Fall) بناءً على الاتجاه المُحلل """
    global is_contract_open, CONTRACT_TYPE_HIGHER, CONTRACT_TYPE_LOWER
    
    current_data = get_session_data(email)
    stake = current_data['current_stake_lower']
    currency_to_use = current_data['currency']
    
    if current_data['current_step'] > MARTINGALE_STEPS:
        stop_bot(email, clear_data=True, stop_reason=f"SL Reached: Max {MARTINGALE_STEPS} Martingale steps reached.")
        return

    # تحديد نوع العقد
    if direction == "Higher":
        contract_type = CONTRACT_TYPE_HIGHER # "RISE"
        entry_tag = "RISE"
    else: # direction == "Lower"
        contract_type = CONTRACT_TYPE_LOWER # "FALL"
        entry_tag = "FALL"
        
    entry_type_tag = "BASE ENTRY" if current_data['current_step'] == 0 else f"MARTINGALE STEP {current_data['current_step']}"
    entry_timing_tag = f"@ SEC {current_second_used}"
    
    print(f"🧠 [SINGLE ENTRY - {entry_timing_tag}] {entry_type_tag} | Direction: {entry_tag}. Stake: {round(stake, 2):.2f}")
    
    # إرسال طلب شراء واحد (بدون حاجز)
    if send_trade_order(email, stake, currency_to_use, contract_type, barrier_value=""):
        current_data['current_entry_id'] = time.time()
        # نستخدم ID وهمي لحين الحصول على الحقيقي من رسالة 'buy'
        current_data['open_contract_ids'] = ['temp_contract_id'] 
        current_data['contract_profits'] = {}
        is_contract_open[email] = True
        current_data['last_entry_time'] = int(time.time())
        current_data['last_entry_price'] = current_data.get('last_valid_tick_price', 0.0)
        save_session_data(email, current_data)
    else:
        print("❌ [TRADE FAILURE] Could not send single trade order.")


def bot_core_logic(email, token, stake, tp, currency, account_type):
    """ Core bot logic """
    
    global is_contract_open, active_ws, TICK_HISTORY

    is_contract_open = {email: False}
    active_ws = {email: None}

    session_data = get_session_data(email)
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
                
                current_data['last_valid_tick_price'] = current_price
                current_data['last_tick_data'] = data['tick']
                save_session_data(email, current_data) 

                # 1. تحديث سجل التكات (TICK_HISTORY)
                if email not in TICK_HISTORY: TICK_HISTORY[email] = []
                
                # إضافة السعر الحالي وإزالة أقدم تيك للحفاظ على آخر 5 تكات
                TICK_HISTORY[email].append(current_price)
                if len(TICK_HISTORY[email]) > 5:
                    TICK_HISTORY[email].pop(0)
                
                # 2. منطق الدخول الموحد (يُنفذ عند الثانية 0 أو 20 أو 40)
                if not is_contract_open.get(email):
                    # نتحقق مما إذا كانت الثانية الحالية هي 0 أو 20 أو 40
                    if current_second in [0, 20, 40] and len(TICK_HISTORY.get(email, [])) >= 5:
                        
                        # --- تحليل الاتجاه (Trend Analysis) ---
                        tick_history = TICK_HISTORY[email]
                        first_tick = tick_history[0]   # سعر أول تيك في السجل (الأقدم)
                        last_tick = tick_history[-1]   # سعر آخر تيك في السجل (الأحدث)
                        
                        direction = None
                        if last_tick > first_tick:
                            # السعر صعد: السعر الأحدث أكبر من الأقدم
                            direction = "Higher"
                        elif last_tick < first_tick:
                            # السعر هبط: السعر الأحدث أصغر من الأقدم
                            direction = "Lower"
                        
                        if direction:
                            # إذا كان الاتجاه واضحاً، ابدأ الصفقة (باستخدام الرهان المُحدَّث تلقائياً)
                            start_new_trade(email, direction, current_second)
                        else:
                            print(f"🟡 [ANALYZE @ SEC {current_second}] Trend is flat. Skipping entry.")


            elif msg_type == 'buy':
                contract_id = data['buy']['contract_id']
                current_data['open_contract_ids'] = [contract_id] # تعديل: صفقة واحدة فقط
                save_session_data(email, current_data)
                
                ws_app.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}))
            
            elif 'error' in data:
                error_code = data['error'].get('code', 'N/A')
                error_message = data['error'].get('message', 'Unknown Error')
                print(f"❌❌ [API ERROR] Code: {error_code}, Message: {error_message}. Trade may be disrupted.")
                
                if is_contract_open.get(email):
                    # في حالة وجود خطأ بعد محاولة الشراء، نعتبر العقد مفتوحاً وننتظر الإغلاق أو نعيد المنطق
                    time.sleep(1) 
                    if not current_data['open_contract_ids'] or current_data['open_contract_ids'] == ['temp_contract_id']:
                         # إذا لم يتم الحصول على contract_id، نعتبر العملية فاشلة وننتقل للمضاعفة
                        print("⚠ [TRADE FAILURE] Buy failed without contract_id. Applying Martingale logic (as loss).")
                        apply_martingale_logic(email, -current_data['current_stake_lower']) 

            elif msg_type == 'proposal_open_contract':
                contract = data['proposal_open_contract']
                if contract.get('is_sold') == 1:
                    contract_id = contract['contract_id']
                    # نستخدم الآن handle_contract_settlement المُعدلة للصفقة الواحدة
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
    {% set timing_logic = "0, 20, 40 Seconds" %}
    {% set analysis = "5 Ticks Trend Analysis" %}
    {% set strategy = "SINGLE RISE/FALL (" + symbol + " - " + analysis + " - @ " + timing_logic + " - x" + martingale_multiplier|string + " Martingale, Max Steps " + martingale_steps|string + ")" %}
    
    <p class="status-running">✅ Bot is Running! (Auto-refreshing)</p>
    <p>Account Type: {{ session_data.account_type.upper() }} | Currency: {{ session_data.currency }}</p>
    <p>Net Profit: {{ session_data.currency }} {{ session_data.current_profit|round(2) }}</p>
    <p>Current Stake: {{ session_data.currency }} {{ session_data.current_stake_lower|round(2) }}</p>
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
        if reason.startswith("SL Reached"): flash(f"🛑 STOP: Max loss reached! ({reason.split(': ')[1]})", 'error')
        elif reason == "TP Reached": flash(f"✅ GOAL: Profit target ({session_data['tp_target']} {session_data.get('currency', 'USD')}) reached successfully! (TP Reached)", 'success')
        elif reason.startswith("API Buy Error"): flash(f"❌ API Error: {reason}. Check your token and account status.", 'error')
            
        session_data['stop_reason'] = "Displayed"
        save_session_data(email, session_data)
        delete_session_data(email)

    return render_template_string(CONTROL_FORM,
        email=email,
        session_data=session_data,
        martingale_steps=MARTINGALE_STEPS,
        max_consecutive_losses=MAX_CONSECUTIVE_LOSSES,
        martingale_multiplier=MARTINGALE_MULTIPLIER, 
        duration=DURATION,
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
    
    flash(f'Bot started successfully. Currency: {currency}. Account: {account_type.upper()}. Strategy: RISE/FALL (R_100 - @ 0, 20, 40 Sec) with x{MARTINGALE_MULTIPLIER} Martingale (Max {MARTINGALE_STEPS} Steps, Max {MAX_CONSECUTIVE_LOSSES} Losses)', 'success')
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
