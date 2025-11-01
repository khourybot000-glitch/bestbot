import time
import json
import websocket 
import os 
import sys 
import fcntl 
from flask import Flask, request, render_template_string, redirect, url_for, session, flash, g
from datetime import timedelta, datetime, timezone
# 🚀 التغيير الرئيسي: استخدام multiprocessing بدلاً من threading
from multiprocessing import Process, Manager
from threading import Lock # استخدام Lock من threading لإدارة قفل الملفات/الذاكرة المشتركة

# ==========================================================
# BOT CONSTANT SETTINGS
# ==========================================================
WSS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL = "R_100"          
DURATION = 5             # مدة العقد 5 تيكس
DURATION_UNIT = "t" 
MARTINGALE_STEPS = 4 
MAX_CONSECUTIVE_LOSSES = 5 # حد الخسارة: 5 خسارات متتالية
RECONNECT_DELAY = 1       
USER_IDS_FILE = "user_ids.txt"
ACTIVE_SESSIONS_FILE = "active_sessions.json" 
# ==========================================================

# ==========================================================
# GLOBAL STATE (Shared between processes via Manager)
# ==========================================================
# 🚨 هذه سيتم تهيئتها لاحقاً بواسطة Manager
active_processes = {}  
active_ws = {} # لا يمكن مشاركة كائنات WebSocket مباشرة
is_contract_open = {} 
# 🔒 قفل لضمان سلامة الوصول إلى active_processes و active_ws (إذا استخدمت في Flask)
PROCESS_LOCK = Lock() 
# Trading State Definitions 
TRADE_STATE_DEFAULT = {"type": "CALL"}  # CALL = Rise
TRADE_STATE_MARTINGALE = {"type": "PUT"}  # PUT = Fall 

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
    "last_entry_time": 0,          # آخر مرة دخل فيها البوت (Timestamp)
    "last_entry_price": 0.0,       # سعر النقطة الزمنية السابقة
    "last_tick_data": None         # آخر تيك كامل تم استلامه من Deriv
}
# ==========================================================

# ⚠️ ملاحظة: دوال إدارة الملفات (get_file_lock, load_persistent_sessions, save_session_data, delete_session_data)
# ستعمل كما هي لأنها تتعامل مع الملفات ولا تعتمد على Threads/Processes بشكل مباشر.

def load_persistent_sessions():
# ... (الدالة كما هي)
    if not os.path.exists(ACTIVE_SESSIONS_FILE):
        return {}
    
    with open(ACTIVE_SESSIONS_FILE, 'a+') as f:
        f.seek(0)
        try: # تم حذف قفل fcntl للتبسيط وتجنب مشاكل التوافق، ولكن يفضل إبقاؤه إذا كان الكود يعمل في بيئة Linux/Unix
            # get_file_lock(f) 
            content = f.read()
            if content:
                data = json.loads(content)
            else:
                data = {}
        except json.JSONDecodeError:
            data = {}
        finally:
            # release_file_lock(f)
            return data

def save_session_data(email, session_data):
    all_sessions = load_persistent_sessions()
    all_sessions[email] = session_data
    
    with open(ACTIVE_SESSIONS_FILE, 'w') as f:
        # get_file_lock(f)
        try:
            json.dump(all_sessions, f, indent=4)
        except Exception as e:
            print(f"❌ ERROR saving session data: {e}")
        finally:
            # release_file_lock(f)
            pass # لا تفعل شيئاً

# (بقية دوال إدارة الملفات كما هي أو مع حذف أقفال fcntl إذا لم تكن تعمل بشكل صحيح في البيئة الحالية)

def stop_bot(email, clear_data=True, stop_reason="Stopped Manually"): 
    """ Stop the bot process and clear WebSocket connection. """
    global is_contract_open, active_processes
    
    # 1. Close WebSocket connection (هذا يعمل فقط إذا كان WebSocket موجوداً في عملية Flask الرئيسية)
    # بما أن WS موجود في العملية الفرعية، يجب الاعتماد على تحديث 'is_running' لإنهاء الحلقة في العملية الفرعية.
    # سنبقي رمز ws.close كرمز محاولة (قد يفشل).
    with PROCESS_LOCK:
        if email in active_ws and active_ws[email]:
            try:
                ws = active_ws[email]
                ws.send(json.dumps({"forget": "ticks", "symbol": SYMBOL}))
                ws.close()
            except:
                pass
            if email in active_ws:
                del active_ws[email]

    # 2. Update is_running state (CRUCIAL for stopping the subprocess loop)
    current_data = get_session_data(email)
    if current_data.get("is_running") is True:
        current_data["is_running"] = False
        current_data["stop_reason"] = stop_reason 
        save_session_data(email, current_data) # Save stop state

    # 3. Terminate process (إجباري إذا لم يتوقف loop بسرعة)
    with PROCESS_LOCK:
        if email in active_processes:
            process = active_processes[email]
            if process.is_alive():
                print(f"🛑 [INFO] Terminating Process for {email}...")
                process.terminate()
                process.join() # الانتظار للتوقف التام
            del active_processes[email]
        
    if email in is_contract_open:
        is_contract_open[email] = False

    if clear_data:
        if stop_reason in ["SL Reached", "TP Reached"]:
             print(f"🛑 [INFO] Bot for {email} stopped ({stop_reason}). Data kept for display.")
        else:
             delete_session_data(email)
             print(f"🛑 [INFO] Bot for {email} stopped ({stop_reason}) and session data cleared from file.")
    else:
        print(f"⚠️ [INFO] WS closed for {email}. Attempting immediate reconnect.")

# ==========================================================
# TRADING BOT FUNCTIONS (No changes needed here for trading logic)
# ==========================================================

def calculate_martingale_stake(base_stake, current_stake, current_step):
    """ Martingale logic: multiply the losing stake by 2.2 """
    if current_step == 0:
        return base_stake
        
    if current_step <= MARTINGALE_STEPS:
        return current_stake * 2.2 
    else:
        return base_stake

# ⚠️ تم تعديل هذه الدالة لاستخدام القواميس المشتركة 
def send_trade_order(email, stake, contract_type):
    """ 
    Send the actual trade order using the Rise/Fall contract type. 
    Ensures stake is rounded to 2 decimal places.
    """
    global is_contract_open, active_ws
    
    # يجب أن يكون active_ws موجوداً في العملية الحالية
    if email not in active_ws: return
    ws_app = active_ws[email]
    
    rounded_stake = round(stake, 2)
    
    trade_request = {
        "buy": 1, 
        "price": rounded_stake,  # استخدام القيمة المقربة
        "parameters": {
            "amount": rounded_stake, # استخدام القيمة المقربة
            "basis": "stake",
            "contract_type": contract_type, 
            "currency": "USD", "duration": DURATION,
            "duration_unit": DURATION_UNIT, "symbol": SYMBOL
        }
    }
    try:
        ws_app.send(json.dumps(trade_request))
        is_contract_open[email] = True 
        print(f"💰 [TRADE] Sent {contract_type} with rounded stake: {rounded_stake:.2f}")
    except Exception as e:
        print(f"❌ [TRADE ERROR] Could not send trade order: {e}")
        pass

def re_enter_immediately(email, last_loss_stake):
# ... (الدالة كما هي)
    current_data = get_session_data(email)
    
    new_stake = calculate_martingale_stake(
        current_data['base_stake'],
        last_loss_stake,
        current_data['current_step'] 
    )

    current_data['current_stake'] = new_stake
    current_data['current_trade_state'] = TRADE_STATE_DEFAULT 
    save_session_data(email, current_data)


def check_pnl_limits(email, profit_loss):
# ... (الدالة كما هي)
    global is_contract_open 
    
    is_contract_open[email] = False 

    current_data = get_session_data(email)
    if not current_data.get('is_running'): return

    last_stake = current_data['current_stake'] 

    current_data['current_profit'] += profit_loss
    
    if profit_loss > 0:
        # 1. Win: Reset
        current_data['total_wins'] += 1
        current_data['current_step'] = 0 
        current_data['consecutive_losses'] = 0
        current_data['current_stake'] = current_data['base_stake']
        current_data['current_trade_state'] = TRADE_STATE_DEFAULT
        
    else:
        # 2. Loss: Martingale setup
        current_data['total_losses'] += 1
        current_data['consecutive_losses'] += 1
        current_data['current_step'] += 1
        
        # 2.1. Check Stop Loss (SL) limits
        if current_data['consecutive_losses'] >= MAX_CONSECUTIVE_LOSSES: 
            stop_bot(email, clear_data=True, stop_reason="SL Reached") 
            return 
        
        # 2.2. Immediate re-entry preparation
        save_session_data(email, current_data) 
        re_enter_immediately(email, last_stake) 
        return

    # 3. Check Take Profit (TP)
    if current_data['current_profit'] >= current_data['tp_target']:
        stop_bot(email, clear_data=True, stop_reason="TP Reached") 
        return
    
    save_session_data(email, current_data)
        
    state = current_data['current_trade_state']
    rounded_last_stake = round(last_stake, 2)
    print(f"[LOG {email}] PNL: {current_data['current_profit']:.2f}, Step: {current_data['current_step']}, Last Stake: {rounded_last_stake:.2f}, State: {state['type']}")


def bot_core_logic(email, token, stake, tp):
    """ Main bot logic with auto-reconnect loop running in a separate process. """
    
    # 💡 يجب تعريف المتغيرات العامة مرة أخرى داخل العملية لكي تكون خاصة بها
    global is_contract_open, active_ws

    # تهيئة is_contract_open و active_ws لهذه العملية فقط
    is_contract_open = {email: False}
    active_ws = {email: None}

    session_data = get_session_data(email)
    # ... (تحديث البيانات الأولية)
    session_data.update({
        "api_token": token, "base_stake": stake, "tp_target": tp,
        "is_running": True, "current_stake": stake,
        "current_trade_state": TRADE_STATE_DEFAULT,
        "stop_reason": "Running",
        "last_entry_time": 0,
        "last_entry_price": 0.0,
        "last_tick_data": None
    })
    save_session_data(email, session_data)

    while True: 
        current_data = get_session_data(email)
        
        if not current_data.get('is_running'):
            break

        print(f"🔗 [PROCESS] Attempting to connect for {email}...")

        def on_open_wrapper(ws_app):
            # استخدام current_data المحدثة من الملف
            current_data = get_session_data(email) 
            ws_app.send(json.dumps({"authorize": current_data['api_token']})) 
            ws_app.send(json.dumps({"ticks": SYMBOL, "subscribe": 1}))
            running_data = get_session_data(email)
            running_data['is_running'] = True
            save_session_data(email, running_data)
            print(f"✅ [PROCESS] Connection established for {email}.")
            is_contract_open[email] = False 

        def on_message_wrapper(ws_app, message):
            # ... (منطق on_message كما هو)
            data = json.loads(message)
            msg_type = data.get('msg_type')
            
            current_data = get_session_data(email) 
            if not current_data.get('is_running'):
                ws_app.close()
                return
                
            if msg_type == 'tick':
                current_timestamp = int(data['tick']['epoch'])
                current_price = float(data['tick']['quote'])
                
                # 1. تحديث آخر تيك تم استلامه دائمًا
                current_data['last_tick_data'] = {
                    "price": current_price,
                    "timestamp": current_timestamp
                }
                save_session_data(email, current_data) 
                
                # إذا كان العقد مفتوح، ننتظر
                if is_contract_open.get(email) is True: 
                    return 
                    
                # منطق فحص وقت الدخول
                entry_seconds = [0, 15, 30, 45]
                current_second = datetime.fromtimestamp(current_timestamp, tz=timezone.utc).second
                is_entry_time = current_second in entry_seconds
                time_since_last_entry = current_timestamp - current_data['last_entry_time']
                
                if time_since_last_entry >= 14 and is_entry_time: 
                    
                    tick_to_use = current_data['last_tick_data']
                    if tick_to_use is None: return

                    entry_price = tick_to_use['price']
                    last_price = current_data.get('last_entry_price', 0.0)
                    
                    if last_price == 0.0 or entry_price > last_price:
                        contract_type_to_use = "CALL" 
                    elif entry_price < last_price:
                        contract_type_to_use = "PUT"
                    else:
                        contract_type_to_use = current_data['current_trade_state']['type']

                    stake_to_use = current_data['current_stake']
                    
                    # حفظ بيانات نقطة الدخول الجديدة
                    current_data['last_entry_price'] = entry_price
                    current_data['last_entry_time'] = current_timestamp # تحديث وقت الدخول
                    current_data['current_trade_state']['type'] = contract_type_to_use 
                    save_session_data(email, current_data)

                    # إرسال الصفقة
                    send_trade_order(email, stake_to_use, contract_type_to_use)
                    
            elif msg_type == 'buy':
                contract_id = data['buy']['contract_id']
                ws_app.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}))
            elif msg_type == 'proposal_open_contract':
                contract = data['proposal_open_contract']
                if contract.get('is_sold') == 1:
                    check_pnl_limits(email, contract['profit']) 
                    if 'subscription_id' in data: ws_app.send(json.dumps({"forget": data['subscription_id']}))

        def on_close_wrapper(ws_app, code, msg):
             # هنا نعتمد على أن stop_bot ستقوم بتحديث is_running في الملف
             print(f"⚠️ [PROCESS] WS closed for {email}. Stopping for auto-retry.")
             # لا نستخدم stop_bot هنا لأنه سيحاول إنهاء العملية، وهذا يسبب مشاكل في الاتصال المتكرر.
             # بدلاً من ذلك، نترك حلقة while True في الأسفل لتأمين إعادة الاتصال
             is_contract_open[email] = False # إغلاق محلي


        try:
            ws = websocket.WebSocketApp(
                WSS_URL, on_open=on_open_wrapper, on_message=on_message_wrapper, 
                on_error=lambda ws, err: print(f"[WS Error {email}] {err}"),
                on_close=on_close_wrapper 
            )
            # حفظ كائن ws محلياً في القاموس الخاص بالعملية
            active_ws[email] = ws
            ws.run_forever(ping_interval=20, ping_timeout=10) 
            
        except Exception as e:
            print(f"❌ [ERROR] WebSocket failed for {email}: {e}")
        
        if get_session_data(email).get('is_running') is False:
             break
        
        print(f"💤 [PROCESS] Waiting {RECONNECT_DELAY} seconds before retrying connection for {email}...")
        time.sleep(RECONNECT_DELAY)

    print(f"🛑 [PROCESS] Bot process loop ended for {email}.")
    # لا داعي لحذف العملية من active_processes هنا، سيتم إجراؤه في stop_bot (من عملية Flask الرئيسية)

# ==========================================================
# FLASK APP SETUP AND ROUTES
# ==========================================================
# ... (بقية كود Flask و HTML كما هي)
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET_KEY', 'VERY_STRONG_SECRET_KEY_RENDER_BOT')
app.config['SESSION_PERMANENT'] = False 

# ... (AUTH_FORM و CONTROL_FORM كما هي)
# ... (الدوال المساعدة: get_file_lock, release_file_lock, load_allowed_users)

# ... (Routes: check_user_status, index, login, auth_page, stop_route, logout)

# ⚠️ التعديل في دالة start_bot لاستخدام Process
@app.route('/start', methods=['POST'])
def start_bot():
    global active_processes # استخدام القاموس الخاص بالعمليات
    
    if 'email' not in session:
        return redirect(url_for('auth_page'))
    
    email = session['email']
    
    with PROCESS_LOCK:
        # التأكد من أن العملية لا تعمل بالفعل
        if email in active_processes and active_processes[email].is_alive():
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
        
    # 🚀 استخدام multiprocessing.Process بدلاً من threading.Thread
    process = Process(target=bot_core_logic, args=(email, token, stake, tp))
    process.daemon = True
    process.start()
    
    with PROCESS_LOCK:
        active_processes[email] = process
    
    flash('Bot started successfully. It will attempt to connect and auto-reconnect.', 'success')
    return redirect(url_for('index'))


@app.route('/stop', methods=['POST'])
def stop_route():
# ... (الدالة كما هي)
    if 'email' not in session:
        return redirect(url_for('auth_page'))
    
    # stop_bot ستقوم بإنهاء العملية الفرعية باستخدام .terminate()
    stop_bot(session['email'], clear_data=True, stop_reason="Stopped Manually") 
    flash('Bot stopped and session data cleared.', 'success')
    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    session.pop('email', None)
    flash('Logged out successfully.', 'success')
    return redirect(url_for('auth_page'))

# ⚠️ التعديل في الدالة الرئيسية للتشغيل
if __name__ == '__main__':
    # 🛑 ملاحظة هامة: في بيئات Windows، يجب أن تكون نقطة الدخول في main
    # لحسن الحظ، الكود يعتمد على قراءة/كتابة حالة الجلسة في ملف (ACTIVE_SESSIONS_FILE)
    # وهذا يجعله يعمل بشكل جيد مع Multiprocessing.

    # 🚫 لا نستخدم Manager هنا لأن قراءة/كتابة الملف كافية لمشاركة الحالة
    # بين عملية Flask الرئيسية والعمليات الفرعية.
    # إذا أردت مشاركة حالة الذاكرة، استخدم Manager() هنا لتهيئة القواميس المشتركة.

    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
