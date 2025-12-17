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
# BOT CONSTANT SETTINGS (FINAL)
# ==========================================================
WSS_URL_UNIFIED = "wss://blue.derivws.com/websockets/v3?app_id=16929"
# الزوج R_100
SYMBOL = "R_100"
# مدة الصفقة 1 تيك
DURATION = 5          
DURATION_UNIT = "t"
# أقصى خطوة مضاعفة 1
MARTINGALE_STEPS = 0          
# الحد الأقصى للخسائر المتتالية 2 
MAX_CONSECUTIVE_LOSSES = 1    
RECONNECT_DELAY = 1
USER_IDS_FILE = "user_ids.txt"
ACTIVE_SESSIONS_FILE = "active_sessions.json"
# تحليل 2 تيك
TICK_HISTORY_SIZE = 5   
# ✅ مُضاعِف مارتينجال 9.0
MARTINGALE_MULTIPLIER = 9.0 

# ✅ نوع العقد: DIGITUNDER 8 (بدون بارير)
TRADE_CONFIGS = {
    "CALL": {"type": "CALL", "barrier": "-0.6", "label": "CALL_STRATEGY"},
    "PUT": {"type": "PUT", "barrier": "+0.6", "label": "PUT_STRATEGY"}
}

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
    "display_t2_price": 0.0, 
    "last_trade_type": None
}

# المتغيرات العالمية لعملية Flask الرئيسية
flask_local_processes = {}
final_check_processes = {}
active_ws = {}  
is_contract_open = None  

# ----------------------------------------------------------
# Persistent State Management Functions (No Changes)
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
    if not isinstance(session_data, dict):
          print(f"❌ ERROR: Attempted to save non-dict data for {email}")
          return

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
        for key, default_val in DEFAULT_SESSION_STATE.items(): 
            if key not in data:
                data[key] = default_val
        return data

    return DEFAULT_SESSION_STATE.copy() 

def load_allowed_users():
    if not os.path.exists(USER_IDS_FILE):
        with open(USER_IDS_FILE, 'w', encoding='utf-8') as f:
            f.write("test@example.com\n")
        return {"test@example.com"}
    try:
        with open(USER_IDS_FILE, 'r', encoding='utf-8') as f:
            users = {line.strip().lower() for line in f if line.strip()}
        return users
    except Exception as e:
        return set()

def stop_bot(email, clear_data=True, stop_reason="Stopped Manually"):
    global flask_local_processes
    global final_check_processes
    global is_contract_open 
    global active_ws 

    current_data = get_session_data(email)
    current_data["is_running"] = False
    current_data["stop_reason"] = stop_reason
    
    if email in active_ws and active_ws[email] is not None:
         try:
             active_ws[email].close()
             active_ws[email] = None
             print(f"🛑 [INFO] WebSocket for {email} closed.")
         except Exception as e:
             print(f"❌ [ERROR] Could not close WS for {email}: {e}")

    if not clear_data:
        current_data["open_contract_ids"] = []

    save_session_data(email, current_data)

    if email in flask_local_processes:
        try:
            process = flask_local_processes[email]
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
            del flask_local_processes[email]
            print(f"🛑 [INFO] Main process for {email} forcefully terminated.")
        except Exception as e:
            print(f"❌ [ERROR] Could not terminate main process for {email}: {e}")

    if email in final_check_processes:
        try:
            process = final_check_processes[email]
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
            del final_check_processes[email]
            print(f"🛑 [INFO] Final check process for {email} forcefully terminated.")
        except Exception as e:
            print(f"❌ [ERROR] Could not terminate final check process for {email}: {e}")

    if is_contract_open is not None and email in is_contract_open:
        is_contract_open[email] = False 
    
    if clear_data:
        delete_session_data(email) 
        print(f"🛑 [INFO] Bot for {email} stopped ({stop_reason}) and session data cleared from file.")

        temp_data = DEFAULT_SESSION_STATE.copy()
        temp_data["stop_reason"] = stop_reason
        save_session_data(email, temp_data)
    else:
        save_session_data(email, current_data)


# ==========================================================
# TRADING BOT FUNCTIONS 
# ==========================================================

def calculate_martingale_stake(base_stake, current_step):
    """
    يحسب قيمة الرهان للمضاعفة باستخدام x9.0 (خطوة واحدة).
    """
    if current_step == 0:
        return base_stake

    # ✅ استخدام Martingale Multiplier 9.0
    stake = base_stake
    # الخطوة 1 هي المضاعفة
    for i in range(1, current_step + 1):
        stake *= MARTINGALE_MULTIPLIER

    # الحد الأقصى خطوة واحدة للمضاعفة (Martingale Steps = 1)
    if current_step > MARTINGALE_STEPS:
         return base_stake
    
    return round(stake, 2)


def send_trade_orders(email, base_stake, currency_code, contract_type, label, barrier, current_step, shared_is_contract_open=None):
    global final_check_processes

    if email not in active_ws or active_ws[email] is None: 
        print(f"❌ [TRADE ERROR] WebSocket not active for {email}. Cannot send trade.")
        if shared_is_contract_open is not None:
             shared_is_contract_open[email] = False
        return
        
    ws_app = active_ws[email]

    current_data = get_session_data(email)
    current_data['before_trade_balance'] = current_data['current_balance']

    if current_data['before_trade_balance'] == 0.0:
        print("⚠️ [STAKE WARNING] Before trade balance is 0.0. PNL calculation will rely heavily on the final balance check.")
        pass

    stake_per_contract = calculate_martingale_stake(current_data['base_stake'], current_step)
        
    rounded_stake = round(stake_per_contract, 2)

    current_data['current_stake'] = rounded_stake
    current_data['current_total_stake'] = rounded_stake 
    
    current_data['last_entry_price'] = current_data['last_tick_data']['price'] if current_data.get('last_tick_data') else 0.0
    
    # الحفاظ على حسابات الديجيت كما هي في كودك الأصلي بدون حذف
    entry_digits = get_target_digits(current_data['last_entry_price'])
    current_data['last_entry_d2'] = entry_digits[1] if len(entry_digits) > 1 else 'N/A' 
    
    current_data['open_contract_ids'] = []

    # تعديل نص الرسالة ليناسب نوع العملية
    entry_msg = f"MARTINGALE STEP {current_step}" if current_step > 0 else "BASE SIGNAL (Analysis)"

    # بناء طلب التداول مع الحفاظ على الهيكل الأصلي
    trade_request = {
        "buy": 1,
        "price": rounded_stake,
        "parameters": {
            "amount": rounded_stake,
            "basis": "stake",
            "currency": currency_code,
            "duration": DURATION,        
            "duration_unit": DURATION_UNIT,
            "symbol": SYMBOL,
            "contract_type": contract_type, # سيأخذ CALL أو PUT من التحليل
        }
    }
    
    # ✅ تعديل منطق البارير ليكون مرناً (يستقبل +0.6 أو -0.6 أو أي قيمة ترسلها)
    if barrier is not None:
        trade_request["parameters"]["barrier"] = str(barrier)
    
    barrier_display = barrier if barrier is not None else 'N/A'
    print(f"\n💰 [TRADE START] Stake: {current_data['current_total_stake']:.2f} ({entry_msg}) | Contract: {contract_type} @ Barrier: {barrier_display}")

    try:
        ws_app.send(json.dumps(trade_request))
        print(f"    [-- {label}] Sent {contract_type} @ {rounded_stake:.2f} {currency_code}")
        current_data['last_trade_type'] = contract_type
    except Exception as e:
        print(f"❌ [TRADE ERROR] Could not send trade order for {label}: {e}")
        if shared_is_contract_open is not None:
             shared_is_contract_open[email] = False
        return

    if shared_is_contract_open is not None:
        shared_is_contract_open[email] = True 
        
    current_data['last_entry_time'] = time.time() * 1000

    save_session_data(email, current_data)

    check_time_ms = 16000 

    final_check = multiprocessing.Process(
        target=final_check_process,
        args=(email, current_data['api_token'], current_data['last_entry_time'], check_time_ms, shared_is_contract_open)
    )
    final_check.start()
    final_check_processes[email] = final_check
    print(f"✅ [TRADE START] Final check process started in background (Waiting {check_time_ms / 1000}s).")

def check_pnl_limits_by_balance(email, after_trade_balance):
    global MARTINGALE_STEPS
    global MAX_CONSECUTIVE_LOSSES

    current_data = get_session_data(email)

    if not current_data.get('is_running') and current_data.get('stop_reason') != "Running":
        print(f"⚠️ [PNL] Bot stopped. Ignoring check for {email}.")
        return

    before_trade_balance = current_data.get('before_trade_balance', 0.0)
    last_total_stake = current_data['current_total_stake']

    if before_trade_balance > 0.0:
        total_profit_loss = after_trade_balance - before_trade_balance
        print(f"** [PNL Calc] After Balance: {after_trade_balance:.2f} - Before Balance: {before_trade_balance:.2f} = PL: {total_profit_loss:.2f}")
    else:
        print("⚠️ [PNL WARNING] Before trade balance is 0.0. Assuming loss equivalent to stake for safety.")
        total_profit_loss = -last_total_stake
    
    overall_loss = total_profit_loss < 0

    stop_triggered = False

    if not overall_loss:
        # 🟢 ربح: إعادة ضبط كاملة للانتظار للتحليل الأساسي الجديد
        current_data['total_wins'] += 1
        current_data['current_step'] = 0
        current_data['consecutive_losses'] = 0
        current_data['current_stake'] = current_data['base_stake']
        current_data['current_total_stake'] = current_data['base_stake'] * 1
        current_data['last_trade_type'] = None
        
        initial_balance = current_data.get('initial_starting_balance', after_trade_balance)
        net_profit_display = after_trade_balance - initial_balance
        
        if net_profit_display >= current_data['tp_target']:
            stop_triggered = "TP Reached"

    else:
        # 🔴 خسارة: الاستعداد للمضاعفة (عند التيك التالي الذي يحقق الشرط)
        current_data['total_losses'] += 1
        current_data['consecutive_losses'] += 1
        current_data['last_trade_type'] = None

        # الحد الأقصى للخسائر المتتالية 2 (خسارة الأساس + خسارة المضاعفة)
        if current_data['consecutive_losses'] >= MAX_CONSECUTIVE_LOSSES:
            stop_triggered = f"SL Reached ({MAX_CONSECUTIVE_LOSSES} Consecutive Loss)"

        else:
            current_data['current_step'] += 1
            
            # الحد الأقصى للمضاعفة 1 (خطوة واحدة)
            if current_data['current_step'] <= MARTINGALE_STEPS:
                new_stake = calculate_martingale_stake(current_data['base_stake'], current_data['current_step'])

                current_data['current_stake'] = new_stake
                current_data['current_total_stake'] = new_stake * 1 
                current_data['martingale_stake'] = new_stake
                
            else:
                # تجاوز Max Martingale Steps (1)، يتم إعادة ضبط الخطوة إلى 0
                current_data['current_stake'] = current_data['base_stake']
                current_data['current_total_stake'] = current_data['base_stake'] * 1
                current_data['current_step'] = 0

        # تفريغ سجل التيك لضمان عدم استخدام بيانات قديمة
        current_data['tick_history'] = []

    save_session_data(email, current_data)

    print(f"[LOG {email}] Last Total PL: {total_profit_loss:.2f}, Step: {current_data['current_step']}, Last Total Stake: {last_total_stake:.2f}")

    if stop_triggered:
        stop_bot(email, clear_data=False, stop_reason=stop_triggered)
        return

# ==========================================================
# UTILITY FUNCTIONS 
# ==========================================================
def get_target_digits(price):
    """
    لاستخراج رقمين عشريين (D1, D2) لـ R_100.
    """
    try:
        # استخدام 2 أرقام بعد الفاصلة
        formatted_price = "{:.2f}".format(float(price)) 
        if '.' in formatted_price:
            parts = formatted_price.split('.')
            decimal_part = parts[1] 
            # الحصول على D1, D2
            digits = [int(d) for d in decimal_part[:2] if d.isdigit()]
            while len(digits) < 2:
                 digits.append(0)
            return digits 
        return [0, 0]
    except Exception as e:
        print(f"Error calculating target digits: {e}")
        return [0, 0]

def get_initial_balance_sync(token):
    global WSS_URL_UNIFIED
    try:
        ws = websocket.WebSocket()
        ws.connect(WSS_URL_UNIFIED, timeout=5, sslopt={"cert_reqs": ssl.CERT_NONE})
        ws.send(json.dumps({"authorize": token}))
        auth_response = json.loads(ws.recv())
        if 'error' in auth_response:
            ws.close()
            return None, "Authorization Failed"
        ws.send(json.dumps({"balance": 1, "subscribe": 1}))
        balance_response = json.loads(ws.recv())
        ws.close()
        if balance_response.get('msg_type') == 'balance':
            balance = balance_response.get('balance', {}).get('balance')
            currency = balance_response.get('balance', {}).get('currency')
            return float(balance), currency
        return None, "Balance response invalid"
    except Exception as e:
        return None, f"Connection/Request Failed: {e}"

def get_balance_sync(token):
    global WSS_URL_UNIFIED
    try:
        ws = websocket.WebSocket()
        ws.connect(WSS_URL_UNIFIED, timeout=5, sslopt={"cert_reqs": ssl.CERT_NONE})
        ws.send(json.dumps({"authorize": token}))
        auth_response = json.loads(ws.recv())
        if 'error' in auth_response:
            ws.close()
            return None, "Authorization Failed"
        ws.send(json.dumps({"balance": 1}))
        balance_response = json.loads(ws.recv())
        ws.close()
        if balance_response.get('msg_type') == 'balance':
            balance = balance_response.get('balance', {}).get('balance')
            return float(balance), None
        return None, "Balance response invalid"
    except Exception as e:
        return None, f"Connection/Request Failed: {e}"

# ==========================================================
# عملية التحقق النهائي المنفصلة (Final Check) 
# ==========================================================

def final_check_process(email, token, start_time_ms, time_to_wait_ms, shared_is_contract_open):
    global final_check_processes

    time_since_start = (time.time() * 1000) - start_time_ms
    sleep_time = max(0, (time_to_wait_ms - time_since_start) / 1000)

    print(f"😴 [FINAL CHECK] Separate process sleeping for {sleep_time:.2f} seconds...")
    time.sleep(sleep_time)

    final_balance, error = get_balance_sync(token)

    if final_balance is not None:
        check_pnl_limits_by_balance(email, final_balance) 
        
        current_data = get_session_data(email)
        current_data['current_balance'] = final_balance
        current_data['before_trade_balance'] = final_balance
        save_session_data(email, current_data)
        
        print(f"✅ [FINAL CHECK] Result confirmed. New Balance: {final_balance:.2f}.")

    else:
        print(f"❌ [FINAL CHECK] Failed to get final balance: {error}. Cannot calculate precise PNL for this trade.")

    try:
        if shared_is_contract_open is not None and email in shared_is_contract_open:
            shared_is_contract_open[email] = False
            print(f"✅ [FINAL CHECK] Contract status successfully reset to False for {email}.")
    except Exception as reset_e:
        print(f"❌ [FINAL CHECK ERROR] Failed to reset shared_is_contract_open: {reset_e}")

    if email in final_check_processes:
        del final_check_processes[email]
        print(f"✅ [FINAL CHECK] Process finished.")


# ==========================================================
# CORE BOT LOGIC (مع تحليل 2 تيك)
# ==========================================================

def bot_core_logic(email, token, stake, tp, account_type, currency_code, shared_is_contract_open):
    global active_ws
    global WSS_URL_UNIFIED

    active_ws[email] = None
    
    if shared_is_contract_open is not None:
        if email not in shared_is_contract_open: shared_is_contract_open[email] = False
        else: shared_is_contract_open[email] = False

    session_data = get_session_data(email)

    try:
        initial_balance, currency_returned = get_initial_balance_sync(token)

        if initial_balance is not None:
            session_data['current_balance'] = initial_balance
            session_data['currency'] = currency_returned
            session_data['is_balance_received'] = True
            session_data['initial_starting_balance'] = initial_balance 
            session_data['before_trade_balance'] = initial_balance
            save_session_data(email, session_data)
            print(f"💰 [SYNC BALANCE] Initial balance retrieved: {initial_balance:.2f} {session_data['currency']}. Account type: {session_data['account_type'].upper()}")
        else:
            print(f"⚠️ [SYNC BALANCE] Could not retrieve initial balance. Currency: {session_data['currency']}")
            stop_bot(email, clear_data=True, stop_reason="Balance Retrieval Failed")
            return

    except Exception as e:
        print(f"❌ FATAL ERROR during sync balance retrieval: {e}")
        stop_bot(email, clear_data=True, stop_reason="Balance Retrieval Failed")
        return

    session_data = get_session_data(email)

    session_data.update({
        "api_token": token, "base_stake": stake, "tp_target": tp,
        "is_running": True,
        "current_total_stake": session_data.get("current_total_stake", stake * 1),
        "stop_reason": "Running",
    })

    save_session_data(email, session_data)
    
    
   def execute_single_trade(email, current_data, contract_type, barrier_value):
    """
    تم تعديل الدالة لاستقبال نوع العقد والبارير بناءً على نتيجة تحليل الـ 5 تيكات
    """
    current_step = current_data['current_step']
    base_stake_to_use = current_data['base_stake']
    currency_code = current_data['currency']
    
    # التسمية التوضيحية للصفقة في السجلات
    label = f"{contract_type}_STRATEGY"
    
    # إرسال الأمر مع المعايير الديناميكية (CALL+0.6 أو PUT-0.6)
    send_trade_orders(
        email, 
        base_stake_to_use, 
        currency_code, 
        contract_type,  # CALL أو PUT
        label, 
        barrier_value,  # +0.6 أو -0.6
        current_step, 
        shared_is_contract_open=shared_is_contract_open
    )

    # -------------------------------------------------------------
    # الدوال المساعدة (on_open_wrapper, on_message_wrapper, on_close_wrapper)
    # -------------------------------------------------------------

    def on_open_wrapper(ws):
        current_data = get_session_data(email)
        token = current_data.get('api_token')
        asset = SYMBOL

        ws.send(json.dumps({"authorize": token}))
        ws.send(json.dumps({"ticks": asset, "subscribe": 1}))
        if not current_data.get('is_balance_received'):
              ws.send(json.dumps({"balance": 1, "subscribe": 1}))

    def on_message_wrapper(ws_app, message):
        data = json.loads(message)
        msg_type = data.get('msg_type')

        current_data = get_session_data(email)

        if not current_data.get('is_running'):
            ws_app.close()
            return

        if msg_type == 'balance':
            current_balance = data['balance']['balance']
            currency = data['balance']['currency']

            current_data['current_balance'] = float(current_balance)
            current_data['currency'] = currency
            current_data['is_balance_received'] = True
            save_session_data(email, current_data)

       elif msg_type == 'tick':
            if current_data['is_balance_received'] == False:
                return
            
            if shared_is_contract_open.get(email, False):
                return

            current_timestamp = int(data['tick']['epoch'])
            current_price = float(data['tick']['quote'])

            tick_data = {
                "price": current_price,
                "timestamp": current_timestamp
            }
            current_data['last_tick_data'] = tick_data 

            # إضافة التيك الجديد للسجل
            current_data['tick_history'].append(tick_data)

            # تعديل حجم السجل ليحتفظ بـ 5 تيكات (TICK_HISTORY_SIZE يجب أن تكون 5 في الثوابت)
            if len(current_data['tick_history']) > 5:
                 current_data['tick_history'].pop(0)

            # تحديث قيم العرض للواجهة (اختياري لمتابعة السعرين)
            if len(current_data['tick_history']) >= 5:
                current_data['display_t1_price'] = current_data['tick_history'][0]['price']
                current_data['display_t5_price'] = current_data['tick_history'][4]['price']

            # 🚨🚨 استراتيجية الـ 5 تيكات: تحليل الفرق T5 - T1 🚨🚨
            if len(current_data['tick_history']) == 5: 
                t1_price = current_data['tick_history'][0]['price'] # التيك الأقدم
                t5_price = current_data['tick_history'][4]['price'] # التيك الأحدث
                
                # حساب الفرق
                diff = round(t5_price - t1_price, 4)
                current_data['display_diff'] = diff # لعرض الفرق في الواجهة

                # تجهيز شروط الدخول
                # 1. حالة CALL: الفرق >= +0.4
                is_call_signal = (diff >= 0.4)
                # 2. حالة PUT: الفرق <= -0.4
                is_put_signal = (diff <= -0.4)

                if is_call_signal or is_put_signal:
                    current_time_ms = time.time() * 1000
                    time_since_last_entry_ms = current_time_ms - current_data['last_entry_time']
                    
                    # احترام الفاصل الزمني بين الصفقات
                    if time_since_last_entry_ms > 100:
                        
                        # تحديد نوع العقد والـ Barrier بناءً على الإشارة
                        if is_call_signal:
                            contract_type = "CALL"
                            barrier = "-0.6"
                        else:
                            contract_type = "PUT"
                            barrier = "+0.6"

                        # تنفيذ الصفقة (يجب التأكد أن دالة execute_single_trade تستقبل الـ barrier)
                        # ملاحظة: إذا كانت دالتك القديمة لا تدعم النوع الجديد، يفضل استخدام send_trade_orders
                        execute_single_trade(email, current_data, contract_type, barrier) 
                        
                        # تفريغ السجل بعد الدخول لبدء تحليل جديد
                        current_data['tick_history'] = []
                        
                        entry_type = "BASE ENTRY" if current_data['current_step'] == 0 else f"MARTINGALE STEP {current_data['current_step']}"
                        print(f"🚀 [{entry_type} CONFIRMED] Signal: Diff {diff}. Executing {contract_type} with Barrier {barrier}")
                    else:
                        print("⚠️ [TIMING SKIP] Signal met but time gap too short.")
                
                else:
                    # تصفير أقدم تيك للسماح بدخول تيك جديد في اللفة القادمة (تحليل مستمر)
                    current_data['tick_history'].pop(0)
                    # print(f"🔄 [5-TICK ANALYSIS] Waiting... Current Diff: {diff}")

            save_session_data(email, current_data)

    def on_close_wrapper(ws_app, code, msg):
        print(f"❌ [WS Close {email}] Code: {code}, Message: {msg}")
        if email in active_ws:
            active_ws[email] = None
        
        current_data = get_session_data(email)
        if current_data.get('is_running'):
             print(f"⚠️ [WS RESTART] WS closed. Main loop will attempt to reconnect.")
        else:
             stop_bot(email, clear_data=False, stop_reason="WS Connection Lost")

    def on_ping_wrapper(ws, message):
        if not get_session_data(email).get('is_running'):
            ws.close()

    # -------------------------------------------------------------
    # الجزء الذي يشغل WebSocket
    # -------------------------------------------------------------
    while session_data.get('is_running'):
        
        is_open = shared_is_contract_open.get(email) if shared_is_contract_open is not None else False

        if active_ws.get(email) is None and is_open is False:
            
            print(f"🔗 [PROCESS] Attempting to connect for {email} to {WSS_URL_UNIFIED}...")

            try:
                ws = websocket.WebSocketApp(
                    WSS_URL_UNIFIED, on_open=on_open_wrapper, on_message=on_message_wrapper,
                    on_error=lambda ws, err: print(f"[WS Error {email}] {err}"),
                    on_close=on_close_wrapper
                )
                active_ws[email] = ws
                ws.on_ping = on_ping_wrapper
                
                ws.run_forever(ping_interval=20, ping_timeout=10, sslopt={"cert_reqs": ssl.CERT_NONE}) 

            except Exception as e:
                print(f"❌ [ERROR] WebSocket failed for {email}: {e}")

            session_data = get_session_data(email)
            if not session_data.get('is_running'):
                break

            print(f"💤 [PROCESS] Waiting {RECONNECT_DELAY} seconds before retrying connection for {email}...")
            time.sleep(RECONNECT_DELAY)
        else:
              time.sleep(0.5)
              
    print(f"🛑 [PROCESS] Bot process ended for {email}.")


# ==========================================================
# FLASK APP SETUP AND ROUTES (Minor Update to FORM)
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
