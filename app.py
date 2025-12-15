import time
import json
import websocket
import multiprocessing
import os
import sys
import fcntl
import ssl
from flask import Flask, request, render_template_string, redirect, url_for, session, flash
from datetime import datetime, timezone

# ==========================================================
# BOT CONSTANT SETTINGS (FINAL)
# ==========================================================
WSS_URL_UNIFIED = "wss://blue.derivws.com/websockets/v3?app_id=16929"
# الزوج R_100
SYMBOL = "R_100"
# مدة الصفقة 1 تيك
DURATION = 1          
DURATION_UNIT = "t"
# تفعيل المضاعفة 2 خطوات
MARTINGALE_STEPS = 2          
# الحد الأقصى للخسائر المتتالية 3
MAX_CONSECUTIVE_LOSSES = 3    
RECONNECT_DELAY = 1
USER_IDS_FILE = "user_ids.txt"
ACTIVE_SESSIONS_FILE = "active_sessions.json"
# تحليل 2 تيك 
TICK_HISTORY_SIZE = 2   
# مُضاعِف مارتينجال 4.0
MARTINGALE_MULTIPLIER = 4.0 
CANDLE_TICK_SIZE = 0
SYNC_SECONDS = []

# نوع العقد: DIGITOVER مع حاجز 2 (Over 2)
TRADE_CONFIGS = [
    {"type": "DIGITOVER", "barrier": 2, "label": "OVER_2_ENTRY"}, 
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
    "pending_martingale": False,
    "is_balance_received": False,
    "stop_reason": "Stopped",
    "pending_delayed_entry": False,
    "entry_t1_d2": None,
    "display_t1_price": 0.0,
    "display_t4_price": 0.0, 
    "display_t3_price": 0.0,
    "last_trade_type": None
}

# المتغيرات العالمية لعملية Flask الرئيسية
flask_local_processes = {}
final_check_processes = {}
active_ws = {}  
is_contract_open = None  
pending_martingale_restart = None 
# ==========================================================


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
    global pending_martingale_restart 
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
    
    if pending_martingale_restart is not None and email in pending_martingale_restart:
        pending_martingale_restart[email] = False 

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
    يحسب قيمة الرهان للمضاعفة باستخدام x4.0.
    """
    if current_step == 0:
        return base_stake

    # استخدام Martingale Multiplier 4.0 
    if current_step <= MARTINGALE_STEPS: 
        stake = base_stake
        for i in range(current_step):
            stake = stake * MARTINGALE_MULTIPLIER
        return stake
    else:
        return base_stake


def send_trade_orders(email, base_stake, currency_code, contract_type, label, barrier, is_martingale=False, shared_is_contract_open=None):
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

    if is_martingale:
        stake_per_contract = calculate_martingale_stake(current_data['base_stake'], current_data['current_step'])
    else:
        stake_per_contract = current_data['base_stake']
        
    rounded_stake = round(stake_per_contract, 2)

    current_data['current_stake'] = rounded_stake
    current_data['current_total_stake'] = rounded_stake 
    
    current_data['last_entry_price'] = current_data['last_tick_data']['price'] if current_data.get('last_tick_data') else 0.0
    entry_digits = get_target_digits(current_data['last_entry_price'])
    current_data['last_entry_d2'] = entry_digits[1] if len(entry_digits) > 1 else 'N/A' 
    current_data['open_contract_ids'] = []

    entry_msg = f"MARTINGALE STEP {current_data['current_step']} (Immediate)" if is_martingale else "BASE SIGNAL (Analysis)"

    t1_d2_entry = 'N/A' 
    t2_d2_entry = 'N/A'

    barrier_display = barrier if barrier is not None else 'N/A'

    print(f"\n💰 [TRADE START] T1 D2: {t1_d2_entry} | T2 D2: {t2_d2_entry} | Stake: {current_data['current_total_stake']:.2f} ({entry_msg}) | Contract: {contract_type} @ Barrier: {barrier_display}")

    # بناء طلب التداول للصفقة الواحدة
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
            "contract_type": contract_type,
        }
    }
    
    if barrier is not None:
        trade_request["parameters"]["barrier"] = str(barrier)


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

    check_time_ms = 6000 

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
    global pending_martingale_restart 

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
        current_data['tick_history'] = []
        current_data['last_trade_type'] = None
        
        # 💡 نوقف المضاعفة الفورية في حالة الربح
        if pending_martingale_restart is not None:
             pending_martingale_restart[email] = False

        initial_balance = current_data.get('initial_starting_balance', after_trade_balance)
        net_profit_display = after_trade_balance - initial_balance
        
        if net_profit_display >= current_data['tp_target']:
            stop_triggered = "TP Reached"

    else:
        # 🔴 خسارة: الاستعداد للمضاعفة الفورية
        current_data['total_losses'] += 1
        current_data['consecutive_losses'] += 1
        current_data['last_trade_type'] = None

        if current_data['consecutive_losses'] >= MAX_CONSECUTIVE_LOSSES:
            stop_triggered = f"SL Reached ({MAX_CONSECUTIVE_LOSSES} Consecutive Loss)"

        else:
            current_data['current_step'] += 1
            
            if current_data['current_step'] <= MARTINGALE_STEPS:
                new_stake = calculate_martingale_stake(current_data['base_stake'], current_data['current_step'])

                current_data['current_stake'] = new_stake
                current_data['current_total_stake'] = new_stake * 1 
                current_data['martingale_stake'] = new_stake
                
                # 🚨 تفعيل إشارة المضاعفة الفورية (الدخول بدون انتظار تيك جديد)
                if pending_martingale_restart is not None:
                     pending_martingale_restart[email] = True 
                     print(f"🚨 [IMMEDIATE MARTINGALE] Loss detected. Setting restart flag for Step {current_data['current_step']}.")


            else:
                # تجاوز Max Martingale Steps (2)، يتم إعادة ضبط الخطوة إلى 0
                current_data['current_stake'] = current_data['base_stake']
                current_data['current_total_stake'] = current_data['base_stake'] * 1
                current_data['current_step'] = 0

        # تفريغ سجل التيك لضمان عدم استخدام بيانات قديمة
        current_data['tick_history'] = []

    current_data['pending_delayed_entry'] = False
    current_data['entry_t1_d2'] = None

    save_session_data(email, current_data)

    print(f"[LOG {email}] Last Total PL: {total_profit_loss:.2f}, Step: {current_data['current_step']}, Last Total Stake: {last_total_stake:.2f}")

    if stop_triggered:
        stop_bot(email, clear_data=True, stop_reason=stop_triggered)
        return

# ==========================================================
# UTILITY FUNCTIONS 
# ==========================================================
# (get_target_digits, get_initial_balance_sync, get_balance_sync remain the same)
def get_target_digits(price):
    try:
        formatted_price = "{:.2f}".format(float(price)) 
        if '.' in formatted_price:
            parts = formatted_price.split('.')
            decimal_part = parts[1] 
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
        ws.connect(WSS_URL_UNIFIED, timeout=5)
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
        ws.connect(WSS_URL_UNIFIED, timeout=5)
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
        # هذا هو المكان الذي يتم فيه تحديد ما إذا كانت هناك خسارة وتفعيل pending_martingale_restart
        check_pnl_limits_by_balance(email, final_balance) 
        
        current_data = get_session_data(email)
        current_data['current_balance'] = final_balance
        current_data['before_trade_balance'] = final_balance
        save_session_data(email, current_data)
        
        print(f"✅ [FINAL CHECK] Result confirmed. New Balance: {final_balance:.2f}. Martingale restart flag status: {pending_martingale_restart.get(email, False)}")

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
# CORE BOT LOGIC 
# ==========================================================

def bot_core_logic(email, token, stake, tp, account_type, currency_code, shared_is_contract_open, shared_pending_martingale_restart):
    global active_ws
    global WSS_URL_UNIFIED

    active_ws[email] = None
    
    # تهيئة الحالة المشتركة
    if shared_is_contract_open is not None:
        if email not in shared_is_contract_open: shared_is_contract_open[email] = False
        else: shared_is_contract_open[email] = False
            
    if shared_pending_martingale_restart is not None:
        if email not in shared_pending_martingale_restart: shared_pending_martingale_restart[email] = False
        else: shared_pending_martingale_restart[email] = False

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
    
    
    # 💡 حلقة خارجية تسمح بإعادة التشغيل الفوري بعد الخسارة
    while session_data.get('is_running'):
        
        is_open = shared_is_contract_open.get(email) if shared_is_contract_open is not None else False
        is_immediate_restart = shared_pending_martingale_restart.get(email) if shared_pending_martingale_restart is not None else False

        # 🚨🚨 منطق المضاعفة الفورية (الأولوية القصوى) 🚨🚨
        if is_immediate_restart and is_open is False:
            current_data = get_session_data(email) 
            
            # التأكد من أن الخطوة > 0 (لأننا لا نريد المضاعفة عند الربح أو بعد الفشل في خطوات المضاعفة)
            if current_data['current_step'] > 0 and current_data['current_step'] <= MARTINGALE_STEPS:

                print(f"🚀 [IMMEDIATE MARTINGALE TRIGGERED] Executing Step {current_data['current_step']} trade immediately after loss confirmation.")
                
                # تنفيذ الصفقة المضاعفة فوراً
                execute_single_trade(email, current_data) 
                
                # إلغاء علم إعادة التشغيل الفوري بعد التنفيذ الناجح
                shared_pending_martingale_restart[email] = False
                
            else:
                 shared_pending_martingale_restart[email] = False # إعادة ضبط العلامة إذا كانت خاطئة
            
            # العودة إلى بداية حلقة 'while True' للانتظار حتى انتهاء الصفقة أو محاولة فتح WS
            time.sleep(0.5)
            session_data = get_session_data(email)
            continue 


        def execute_single_trade(email, current_data):
            is_martingale = current_data['current_step'] > 0
            base_stake_to_use = current_data['base_stake']
            currency_code = current_data['currency']
            
            config = TRADE_CONFIGS[0]
            send_trade_orders(email, base_stake_to_use, currency_code, config['type'], config['label'], config['barrier'], is_martingale=is_martingale, shared_is_contract_open=shared_is_contract_open)

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
                
                # 🛑 التجاهل: إذا كانت هناك مضاعفة معلقة أو عقد مفتوح، لا نحلل التيك
                if shared_pending_martingale_restart.get(email, False) or shared_is_contract_open.get(email, False):
                    return

                current_timestamp = int(data['tick']['epoch'])
                current_price = float(data['tick']['quote'])

                tick_data = {
                    "price": current_price,
                    "timestamp": current_timestamp
                }
                current_data['last_tick_data'] = tick_data 

                current_data['tick_history'].append(tick_data)

                if len(current_data['tick_history']) > TICK_HISTORY_SIZE:
                     current_data['tick_history'].pop(0)

                
                current_data['display_t1_price'] = current_data['tick_history'][0]['price'] if len(current_data['tick_history']) >= 1 else 0.0
                current_data['display_t4_price'] = current_data['tick_history'][1]['price'] if len(current_data['tick_history']) >= 2 else 0.0
                current_data['display_t3_price'] = 0.0


                # 🚨 شرط الدخول الأساسي: يتم الدخول فقط إذا كان current_step = 0 (بداية صفقة جديدة)
                if current_data['current_step'] == 0:

                    current_time_ms = time.time() * 1000
                    time_since_last_entry_ms = current_time_ms - current_data['last_entry_time']
                    
                    is_time_gap_respected = time_since_last_entry_ms > 5000 
                    
                    if not is_time_gap_respected:
                        save_session_data(email, current_data)
                        return

                    # 🚨🚨 شروط الدخول: T1 D2 = 9 أو 8 و T2 D2 = 9 أو 8 🚨🚨
                    if len(current_data['tick_history']) == TICK_HISTORY_SIZE:

                        tick_T1_price = current_data['tick_history'][0]['price']
                        tick_T2_price = current_data['tick_history'][1]['price']
                        
                        digits_T1 = get_target_digits(tick_T1_price)
                        digits_T2 = get_target_digits(tick_T2_price)
                        
                        T1_D2 = digits_T1[1] if len(digits_T1) >= 2 else None 
                        T2_D2 = digits_T2[1] if len(digits_T2) >= 2 else None 

                        condition_entry = (T1_D2 in [9, 8]) and (T2_D2 in [9, 8])

                        if condition_entry:
                            execute_single_trade(email, current_data) 
                            current_data['tick_history'] = []
                            print(f"🚀 [BASE ENTRY CONFIRMED] Signal: T1 D2={T1_D2} AND T2 D2={T2_D2}. Executing OVER 2 (Step: {current_data['current_step']}).")

                        else:
                            if len(current_data['tick_history']) == TICK_HISTORY_SIZE:
                                current_data['tick_history'].pop(0)

                            print(f"🔄 [2-TICK ANALYSIS] Condition not met. T1 D2:{T1_D2}, T2 D2:{T2_D2}. Waiting for signal.")


                save_session_data(email, current_data)

        def on_close_wrapper(ws_app, code, msg):
            print(f"❌ [WS Close {email}] Code: {code}, Message: {msg}")
            if email in active_ws:
                active_ws[email] = None
            
            # إذا كان هناك إعادة تشغيل مضاعفة معلقة، نترك الحلقة الرئيسية هي من تعيد الاتصال
            if shared_pending_martingale_restart.get(email, False):
                 print(f"⚠️ [WS RESTART] WS closed. Immediate martingale restart pending. Main loop will handle reconnect/trade.")
            else:
                 current_data = get_session_data(email)
                 if current_data.get('is_running'):
                      stop_bot(email, clear_data=False, stop_reason="WS Connection Lost")


        def on_ping_wrapper(ws, message):
            if not get_session_data(email).get('is_running'):
                ws.close()

        # -------------------------------------------------------------
        # الجزء الذي يشغل WebSocket
        # -------------------------------------------------------------
        current_data = get_session_data(email)
        is_open = shared_is_contract_open.get(email) if shared_is_contract_open is not None else False

        # لا تحاول فتح WS إذا كان مفتوحاً أو كان هناك عقد قيد المراجعة
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
                
                ws.run_forever(ping_interval=20, ping_timeout=10, sslopt={"cert_reqs": ssl.CERT_NONE} if sys.version_info < (3, 7) else {}) 

            except Exception as e:
                print(f"❌ [ERROR] WebSocket failed for {email}: {e}")

            session_data = get_session_data(email)
            if not session_data.get('is_running'):
                break

            if not shared_pending_martingale_restart.get(email, False):
                 print(f"💤 [PROCESS] Waiting {RECONNECT_DELAY} seconds before retrying connection for {email}...")
                 time.sleep(RECONNECT_DELAY)
        else:
              time.sleep(0.5)
              
    print(f"🛑 [PROCESS] Bot process ended for {email}.")

# ... (بقية كود Flask كما هو) ...


# ==========================================================
# FLASK APP SETUP AND ROUTES 
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

{% if session_data and session_data.stop_reason and session_data.stop_reason != "Running" and session_data.stop_reason != "Stopped Manually" %}
    <p style="color:red; font-weight:bold;">Last Session Ended: {{ session_data.stop_reason }}</p>
{% endif %}


{% if session_data and session_data.is_running %}
    {% set strategy = '2 Ticks (R_100, D2 analysis) -> Entry: DIGITOVER 2 (if T1 D2 & T2 D2 are 9 or 8) | Ticks: ' + TICK_HISTORY_SIZE|string + ' | Duration: ' + DURATION|string + ' Ticks | Martingale: ' + MARTINGALE_STEPS|string + ' Steps (x' + MARTINGALE_MULTIPLIER|string + ') | Max Losses: ' + max_consecutive_losses|string + (' | Immediate Martingale: ON (Only on Loss)' if True else '') %}

    <p class="status-running">✅ Bot is Running! (Auto-refreshing)</p>

    {# Display T1 Price and T2 Price & D2 Digit #}
    <div class="tick-box">
        {# T1 Display #}
        <div>
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
            <p style="font-weight: normal; font-size: 0.8em; color: {% if d2_digit_t1 in ['9', '8'] %}green{% else %}red{% endif %};">
                Condition: T1 D2 = 9 or 8
            </p>
        </div>
        
        {# T2 Display #}
        <div>
            <span class="info-label">T2 Price (Latest):</span> <b>{% if session_data.display_t4_price %}{{ "%0.2f"|format(session_data.display_t4_price) }}{% else %}N/A{% endif %}</b>
            <br>
            <span class="info-label">T2 D2 (Latest):</span>
            <b class="current-digit">
            {% set price_str_t2 = "%0.2f"|format(session_data.display_t4_price) %}
            {% set price_parts_t2 = price_str_t2.split('.') %}
            {% set d2_digit_t2 = 'N/A' %}
            {% if price_parts_t2|length > 1 and price_parts_t2[-1]|length >= 2 %}{% set d2_digit_t2 = price_parts_t2[-1][1] %}{% endif %}
            {{ d2_digit_t2 }}
            </b>
            
            <p style="font-weight: normal; font-size: 0.8em; color: {% if d2_digit_t2 in ['9', '8'] %}green{% else %}red{% endif %};">
                Condition: T2 D2 = 9 or 8
            </p>
            <p style="font-weight: normal; font-size: 0.8em; color: {% if d2_digit_t1 in ['9', '8'] and d2_digit_t2 in ['9', '8'] %}green{% else %}red{% endif %};">
                Signal (Base Entry): 
                {% if d2_digit_t1 in ['9', '8'] and d2_digit_t2 in ['9', '8'] %}
                    OVER 2 (T1 & T2 D2 met)
                {% else %}
                    NONE (Waiting for analysis)
                {% endif %}
            </p>
        </div>

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
                {% if pending_martingale_restart.get(email) %}
                    🔴 IMMEDIATE MARTINGALE PENDING! (Executing Trade...)
                {% else %}
                    0 (Ready for Base Signal/Analysis)
                {% endif %}
            {% endif %}
            </b>
        </p>

        <p style="font-weight: bold; color: {% if session_data.current_step > 0 %}#ff5733{% else %}#555{% endif %};">
            Trade Status:
            <b>
                {% set is_open = is_contract_open.get(email) if is_contract_open is not none else false %}
                {% if is_open %}
                    Awaiting PNL Check (Stake: {{ session_data.current_total_stake|round(2) }})
                {% else %}
                    {% if pending_martingale_restart.get(email) %}
                        MARTINGALE STEP {{ session_data.current_step }} @ Stake: {{ session_data.current_total_stake|round(2) }} (IMMEDIATE ENTRY)
                    {% elif session_data.current_step > 0 %}
                        Awaiting Immediate Martingale Entry (Stake: {{ session_data.current_total_stake|round(2) }})
                    {% else %}
                        BASE STAKE @ Stake: {{ session_data.current_stake|round(2) }} (Searching 2-Tick Signal)
                    {% endif %}
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
        pending_martingale_restart=pending_martingale_restart, 
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

        if is_contract_open is None or pending_martingale_restart is None:
              flash("Bot initialization failed (Shared state manager not ready). Please restart the service.", 'error')
              return redirect(url_for('control_panel'))
        
        process = multiprocessing.Process(
            target=bot_core_logic,
            args=(email, token, stake, tp, account_type, currency, is_contract_open, pending_martingale_restart) 
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
    pending_martingale_restart = manager.dict() 
except Exception as e:
    print(f"⚠️ [MANAGER INIT] Multiprocessing Manager failed to initialize: {e}. Defaulting shared state to None.")
    is_contract_open = None
    pending_martingale_restart = None

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
