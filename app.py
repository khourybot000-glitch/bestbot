import time
import json
import websocket
import os
import sys
from flask import Flask, request, render_template_string, redirect, url_for, session, flash
from datetime import datetime, timezone 
from multiprocessing import Process, Lock 
from threading import Thread, Timer
import traceback 
from collections import Counter

# ==========================================================
# BOT CONSTANT SETTINGS
# ==========================================================
WSS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL = "R_100"          
DURATION = 1              
DURATION_UNIT = "t"       
TICK_SAMPLE_SIZE = 6           
MAX_CONSECUTIVE_LOSSES = 2    
MARTINGALE_MULTIPLIER = 19.0  
CONTRACT_TYPE = "DIGITDIFF"

RECONNECT_DELAY = 1
USER_IDS_FILE = "user_ids.txt"
ACTIVE_SESSIONS_FILE = "active_sessions.json"

# ==========================================================
# GLOBAL STATE & CONSTANTS
# ==========================================================
active_processes = {} 
active_ws = {}
is_contract_open = {} 
PROCESS_LOCK = Lock() 
TRADE_LOCK = Lock() 

PRE_TRADE_BALANCE_REQ_ID = "PRE_TRADE_BALANCE"
TIMED_SETTLEMENT_REQ_ID = "TIMED_SETTLEMENT_CHECK"
DASHBOARD_BALANCE_REQ_ID = "DASHBOARD_BALANCE_CHECK"

DEFAULT_SESSION_STATE = {
    "api_token": "", "base_stake": 0.35, "tp_target": 10.0, "is_running": False,
    "current_profit": 0.0, "current_stake": 0.35, "consecutive_losses": 0,
    "current_step": 0, "total_wins": 0, "total_losses": 0,
    "stop_reason": "Stopped Manually", "last_entry_time": 0,
    "last_entry_price": 0.0, "last_tick_data": None, "currency": "USD",
    "account_type": "demo", "last_valid_tick_price": 0.0,
    "current_entry_id": None, "open_contract_ids": [], 
    "contract_profits": {}, "last_digits_history": [],    
    "last_trade_prediction": -1,
    
    "pre_trade_balance": 0.0,    
    "current_stake_recovery": 0.0,
    "latest_balance": 0.0,
}

# --- Persistence functions ---

def load_persistent_sessions():
    if not os.path.exists(ACTIVE_SESSIONS_FILE): return {}
    try:
        with PROCESS_LOCK:
            with open(ACTIVE_SESSIONS_FILE, 'r') as f:
                content = f.read()
                return json.loads(content) if content else {}
    except Exception as e: 
        print(f"❌ [FILE ERROR] Error loading sessions: {e}")
        return {}

def save_session_data(email, session_data):
    all_sessions = load_persistent_sessions()
    all_sessions[email] = session_data
    
    try:
        with PROCESS_LOCK:
            with open(ACTIVE_SESSIONS_FILE, 'w') as f:
                json.dump(all_sessions, f, indent=4)
    except Exception as e:
        print(f"❌ [FILE ERROR] Error saving sessions: {e}")

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
    
    try:
        with PROCESS_LOCK:
            with open(ACTIVE_SESSIONS_FILE, 'w') as f:
                json.dump(all_sessions, f, indent=4)
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
    
    if stop_reason != "Running": save_session_data(email, current_data)

    if email in active_ws and active_ws[email]:
        try: active_ws[email].close() 
        except: pass

    with PROCESS_LOCK:
        if email in active_processes:
            process = active_processes[email]
            if process.is_alive():
                print(f"🛑 [INFO] Terminating Process for {email}...")
                process.terminate()
            del active_processes[email]
    
    if clear_data or not current_data.get('open_contract_ids'): 
        if email in is_contract_open: is_contract_open[email] = False

    if clear_data:
        if stop_reason not in ["API Buy Error", "Displayed"]:
            delete_session_data(email)
            print(f"🛑 [INFO] Bot for {email} stopped ({stop_reason}) and session data cleared from file.")
        else:
             print(f"🛑 [INFO] Bot for {email} stopped ({stop_reason}). Data kept for display.")
    else:
        print(f"⚠ [INFO] WS closed for {email}. Attempting immediate reconnect.")


# ==========================================================
# TRADING BOT FUNCTIONS
# ==========================================================

def find_most_frequent_digit(digits_list):
    if not digits_list: return 0 
    counts = Counter(digits_list)
    all_digits_counts = {i: counts[i] for i in range(10)}
    max_count = max(all_digits_counts.values())
    for digit in range(10):
        if all_digits_counts[digit] == max_count: return digit
    return 0 

def calculate_martingale_stake(base_stake, current_step, multiplier):
    if current_step == 0: return base_stake
    return base_stake * (multiplier ** current_step)


def send_single_trade_order(email, stake, currency, contract_type, prediction):
    global active_ws, DURATION, DURATION_UNIT, SYMBOL
    
    if email not in active_ws or active_ws[email] is None: 
        print(f"❌ [TRADE ERROR] Cannot send trade: WebSocket connection is inactive.")
        return False
        
    ws_app = active_ws[email]
    
    trade_request = {
        "buy": 1,
        "price": round(stake, 2),
        "parameters": {
            "amount": round(stake, 2),
            "basis": "stake",
            "contract_type": contract_type, 
            "currency": currency, 
            "duration": DURATION, 
            "duration_unit": DURATION_UNIT, 
            "symbol": SYMBOL,
            "barrier": prediction
        }
    }
    
    try:
        ws_app.send(json.dumps(trade_request))
        return True
    except Exception as e:
        print(f"❌ [TRADE ERROR] Could not send trade order: {e}")
        return False
        
# -------------------------------------------------------------------
# BALANCE CHECK FUNCTIONS 
# -------------------------------------------------------------------

def send_pre_trade_balance_request(email, ws_app, purpose=PRE_TRADE_BALANCE_REQ_ID):
    try:
        request_id = f"{purpose}_{email}" 
        
        ws_app.send(json.dumps({
            "balance": 1, 
            "req_id": request_id 
        })) 
        print(f"🔗 [DEBUG] Sent Balance Request with ID: {request_id}")
    except Exception as e: 
        print(f"❌ [REQUEST ERROR] Failed to send balance request for {email}: {e}")

def send_settlement_balance_request(email, ws_app):
    try:
        request_id = f"{TIMED_SETTLEMENT_REQ_ID}_{email}"
        ws_app.send(json.dumps({
            "balance": 1, 
            "req_id": request_id
        })) 
    except: pass 

def handle_contract_settlement_by_balance(email, current_balance):
    current_data = get_session_data(email)
    
    if not current_data['open_contract_ids'] or current_data['pre_trade_balance'] <= 0.0:
        return
        
    last_balance = current_data['pre_trade_balance']
    stake_used = current_data['current_stake_recovery']

    profit_loss = current_balance - last_balance
    
    if abs(profit_loss) < (stake_used * 0.25) and time.time() - current_data['last_entry_time'] < 10:
        print(f"⌛ [SETTLEMENT WAIT] PnL {profit_loss:.2f} too small or too early. Skipping settlement.")
        return 

    print(f"💰 [SETTLEMENT BY BALANCE] Contract closed. PnL: {profit_loss:.2f} (Current: {current_balance:.2f} | Pre: {last_balance:.2f})")

    contract_id = current_data['open_contract_ids'][0] 
    current_data['contract_profits'][contract_id] = profit_loss
    current_data['open_contract_ids'] = []
    
    current_data['pre_trade_balance'] = 0.0
    current_data['current_stake_recovery'] = 0.0
    save_session_data(email, current_data)
    
    apply_martingale_logic(email)

def schedule_settlement_check(email, ws_app):
    def deferred_check():
        current_data = get_session_data(email)
        if current_data['open_contract_ids']:
            send_settlement_balance_request(email, ws_app)
            
    t = Timer(8.0, deferred_check)
    t.start()
    print("⏰ [TIMER] Settlement check scheduled in 8 seconds (based on balance).")
    

def handle_balance_response_on_message(email, data, req_id):
    current_data = get_session_data(email)
    
    expected_pre_trade_id = f"{PRE_TRADE_BALANCE_REQ_ID}_{email}"
    expected_timed_settlement_id = f"{TIMED_SETTLEMENT_REQ_ID}_{email}"
    expected_dashboard_id = f"{DASHBOARD_BALANCE_REQ_ID}_{email}"
    
    try:
        current_balance = float(data['balance']['balance'])
        
        current_data['latest_balance'] = current_balance
        
        if req_id == expected_pre_trade_id:
            current_data['pre_trade_balance'] = current_balance
            print(f"💰 [INFO] PRE-TRADE balance recorded: {current_balance:.2f} {current_data.get('currency', 'USD')}.")
            
        elif req_id == expected_timed_settlement_id:
            if current_data['open_contract_ids']:
                handle_contract_settlement_by_balance(email, current_balance)
            
        elif req_id == expected_dashboard_id:
            print(f"💰 [INFO] Dashboard Balance updated: {current_balance:.2f} {current_data.get('currency', 'USD')}.")

        elif req_id == "AUTO_BALANCE_UPDATE":
             print(f"💰 [INFO] Auto Balance Updated: {current_balance:.2f} {current_data.get('currency', 'USD')}.")

        else:
            print(f"⚠️ [BALANCE RECEIVED] Received balance: {current_balance:.2f}, but with UNKNOWN req_id: {req_id}. Updating Dashboard only.")

        save_session_data(email, current_data)

    except Exception as e:
        print(f"❌ [BALANCE HANDLE ERROR] Could not process balance response ({req_id}): {e}")


def apply_martingale_logic(email):
    global is_contract_open, MARTINGALE_MULTIPLIER, MAX_CONSECUTIVE_LOSSES
    current_data = get_session_data(email)
    
    if not current_data.get('is_running'): return

    if not current_data['contract_profits']:
        print("❌ [MARTINGALE ERROR] No contract result found.")
        is_contract_open[email] = False
        return

    total_profit_loss = list(current_data['contract_profits'].values())[0]

    current_data['current_profit'] += total_profit_loss
    
    if current_data['current_profit'] >= current_data['tp_target']:
        save_session_data(email, current_data)
        stop_bot(email, clear_data=True, stop_reason="TP Reached")
        return
        
    base_stake_used = current_data['base_stake']
    
    if total_profit_loss < 0:
        current_data['total_losses'] += 1 
        current_data['consecutive_losses'] += 1
        current_data['current_step'] += 1
        
        if current_data['consecutive_losses'] >= MAX_CONSECUTIVE_LOSSES:
            save_session_data(email, current_data)
            stop_bot(email, clear_data=True, stop_reason="SL Reached: Consecutive losses")
            return
            
        new_stake = calculate_martingale_stake(base_stake_used, current_data['current_step'], MARTINGALE_MULTIPLIER)
        current_data['current_stake'] = new_stake
        
        print(f"🔄 [LOSS] PnL: {total_profit_loss:.2f}. Consecutive: {current_data['consecutive_losses']}. Next Stake calculated: {round(new_stake, 2):.2f}. Awaiting next **:00/:30** entry with **NEW ANALYSIS**.")
        
    else: 
        current_data['total_wins'] += 1 if total_profit_loss > 0 else 0 
        current_data['current_step'] = 0 
        current_data['consecutive_losses'] = 0
        current_data['current_stake'] = base_stake_used
        
        entry_result_tag = "WIN" if total_profit_loss > 0 else "DRAW/SPLIT"
        print(f"✅ [ENTRY RESULT] {entry_result_tag}. PnL: {total_profit_loss:.2f}. Stake reset to base: {base_stake_used:.2f}. **Awaiting new {TICK_SAMPLE_SIZE}-tick cycle and :00/:30 time slot.**")
        
        current_data['last_digits_history'] = [] 
        current_data['last_trade_prediction'] = -1 


    current_data['current_entry_id'] = None
    current_data['open_contract_ids'] = []
    current_data['contract_profits'] = {}
    
    currency = current_data.get('currency', 'USD')
    print(f"[LOG {email}] PNL: {currency} {current_data['current_profit']:.2f}, Con. Loss: {current_data['consecutive_losses']}/{MAX_CONSECUTIVE_LOSSES}, Stake: {current_data['current_stake']:.2f}, Strategy: {CONTRACT_TYPE} (R_100, x{MARTINGALE_MULTIPLIER})")
    
    save_session_data(email, current_data) 
    
    is_contract_open[email] = False

def start_new_single_trade(email, contract_type, prediction):
    global is_contract_open
    
    current_data = get_session_data(email)
    stake = current_data['current_stake']
    currency_to_use = current_data['currency']
    
    if current_data['consecutive_losses'] >= MAX_CONSECUTIVE_LOSSES:
          stop_bot(email, clear_data=True, stop_reason=f"SL Reached: Max {MAX_CONSECUTIVE_LOSSES} Consecutive Losses reached.")
          return
        
    current_data['current_entry_id'] = time.time()
    current_data['open_contract_ids'] = []
    current_data['contract_profits'] = {}
    current_data['last_trade_prediction'] = prediction 
    current_data['current_stake_recovery'] = stake
    
    ws_app = active_ws.get(email)
    if ws_app:
        send_pre_trade_balance_request(email, ws_app, purpose=PRE_TRADE_BALANCE_REQ_ID)
    else:
        print("❌ [TRADE ABORT] WS is inactive. Cannot proceed with trade.")
        is_contract_open[email] = False
        return

    start_wait_time = time.time()
    balance_recorded = False
    
    while time.time() - start_wait_time < 5.0: 
        current_data = get_session_data(email) 
        if current_data['pre_trade_balance'] > 0.0:
            balance_recorded = True
            break
        time.sleep(0.1) 

    current_data = get_session_data(email) 
    
    if not balance_recorded:
        print("❌ [TRADE ABORT] Failed to record Pre-Trade Balance in time (5.0s timeout). Aborting trade.")
        is_contract_open[email] = False
        current_data['pre_trade_balance'] = 0.0
        current_data['current_stake_recovery'] = 0.0
        save_session_data(email, current_data)
        return
        
    entry_tag = f"Martingale Step {current_data['consecutive_losses']}" if current_data['consecutive_losses'] > 0 else "Base Stake Entry"
    print(f"🧠 [{entry_tag} - {contract_type}] Digit: {prediction} | Stake: {round(stake, 2):.2f}.")
    
    if send_single_trade_order(email, stake, currency_to_use, contract_type, prediction): 
        is_contract_open[email] = True
        current_data['open_contract_ids'] = [f"ENTRY_{int(time.time())}"] 
        current_data['last_entry_time'] = int(time.time())
        
        if ws_app:
            schedule_settlement_check(email, ws_app) 
    else:
        is_contract_open[email] = False 
        current_data['pre_trade_balance'] = 0.0
        current_data['current_stake_recovery'] = 0.0


    save_session_data(email, current_data)

# ----------------------------------------------------------------------
# WebSocket Core (bot_core_logic)
# ----------------------------------------------------------------------

def bot_core_logic(email, token, stake, tp, currency, account_type):
    
    print(f"🚀🚀 [CORE START] Bot logic started for {email} in new process.") 
    
    global is_contract_open, active_ws

    is_contract_open[email] = False
    active_ws[email] = None

    session_data = get_session_data(email)
    session_data.update({
        "api_token": token, "base_stake": stake, "tp_target": tp, "is_running": True, 
        "current_stake": stake, "stop_reason": "Running", "last_entry_time": 0,
        "pre_trade_balance": 0.0, "current_stake_recovery": 0.0
    })
    save_session_data(email, session_data)

    try:
        while True:
            current_data = get_session_data(email)
            
            if not current_data.get('is_running'): break

            print(f"🔗 [PROCESS] Attempting to connect for {email} ({account_type.upper()}/{currency})...")

            def on_open_wrapper(ws_app):
                current_data = get_session_data(email) 
                ws_app.send(json.dumps({"authorize": current_data['api_token']}))
                
                running_data = get_session_data(email)
                running_data['is_running'] = True
                save_session_data(email, running_data)
                print(f"✅ [PROCESS] Connection established for {email}. Awaiting authorization confirmation...")

            def on_message_wrapper(ws_app, message):
                data = json.loads(message)
                msg_type = data.get('msg_type')
                
                current_data = get_session_data(email)
                if not current_data.get('is_running'): return

                if msg_type == 'authorize':
                    if data.get('error'):
                        error_msg = data['error'].get('message', 'Authorization failed')
                        print(f"❌ [AUTHORIZE FAIL] Token failed: {error_msg}")
                        stop_bot(email, clear_data=True, stop_reason=f"Token Failed: {error_msg}")
                        return
                    
                    print(f"✅ [AUTHORIZE SUCCESS] Token validated. Requesting data...")
                    
                    ws_app.send(json.dumps({"ticks": SYMBOL, "subscribe": 1}))
                    send_pre_trade_balance_request(email, ws_app, purpose=DASHBOARD_BALANCE_REQ_ID)
                    
                    account_info = data.get('authorize', {})
                    current_data['currency'] = account_info.get('currency', 'USD')
                    current_data['account_type'] = account_info.get('account_type', 'demo')
                    save_session_data(email, current_data)
                    return
                        
                elif msg_type == 'tick':
                    try:
                        current_price = float(data['tick']['quote'])
                        tick_time_epoch = int(data['tick']['epoch'])
                    except (KeyError, ValueError): return
                        
                    T1 = int(str(current_price)[-1]) 
                    dt_object = datetime.fromtimestamp(tick_time_epoch, tz=timezone.utc)
                    current_second = dt_object.second
                    is_time_to_trade = (current_second == 0) or (current_second == 30)
                    
                    current_data['last_digits_history'].append(T1)
                    if len(current_data['last_digits_history']) > TICK_SAMPLE_SIZE:
                        current_data['last_digits_history'].pop(0)

                    current_data['last_valid_tick_price'] = current_price
                    current_data['last_tick_data'] = data['tick']
                    
                    
                    if not is_contract_open.get(email):
                        if is_time_to_trade:
                            if len(current_data['last_digits_history']) == TICK_SAMPLE_SIZE:
                                target_prediction = find_most_frequent_digit(current_data['last_digits_history'])
                                start_new_single_trade(email, CONTRACT_TYPE, target_prediction)
                                
                    save_session_data(email, current_data)

                elif msg_type == 'buy':
                    pass 
                
                elif msg_type == 'balance':
                    req_id = data.get('req_id', '') 
                    
                    if not req_id:
                        handle_balance_response_on_message(email, data, "AUTO_BALANCE_UPDATE")
                        return

                    if req_id:
                         handle_balance_response_on_message(email, data, req_id)
                         return
                
                elif 'error' in data:
                    error_message = data['error'].get('message', 'Unknown Error')
                    print(f"❌❌ [API ERROR] Message: {error_message}. Trade failed.")
                    
                    if is_contract_open.get(email):
                        time.sleep(1) 
                        is_contract_open[email] = False 
                        current_data['current_entry_id'] = None
                        current_data['pre_trade_balance'] = 0.0 
                        current_data['current_stake_recovery'] = 0.0
                        save_session_data(email, current_data)
                        stop_bot(email, clear_data=True, stop_reason=f"API Buy Error: {error_message}")


            def on_close_wrapper(ws_app, code, msg):
                print(f"⚠ [PROCESS] WS closed for {email}. RECONNECTING IMMEDIATELY. Code: {code}")

            def on_error_wrapper(ws_app, err):
                print(f"❌ [WS Critical Error {email}] {err}") 

            try:
                ws = websocket.WebSocketApp(
                    WSS_URL, on_open=on_open_wrapper, on_message=on_message_wrapper,
                    on_error=on_error_wrapper, 
                    on_close=on_close_wrapper
                )
                active_ws[email] = ws
                
                ws_thread = Thread(target=ws.run_forever, kwargs={
                    'ping_interval': 10, 'ping_timeout': 5, 'http_proxy_host': None, 'http_proxy_port': None
                })
                ws_thread.daemon = True
                ws_thread.start()
                
                ws_thread.join()
                
            except Exception as e:
                print(f"❌ [ERROR] WebSocket failed for {email}: {e}")
            
            if get_session_data(email).get('is_running') is False: break
            
            print(f"💤 [PROCESS] Immediate Retrying connection for {email}...")
            time.sleep(0.5) 

        print(f"🛑 [PROCESS] Bot process loop ended for {email}.")
        
    except Exception as process_error:
        print(f"\n\n💥💥 [CRITICAL PROCESS CRASH] The entire bot process for {email} failed: {process_error}")
        traceback.print_exc()
        stop_bot(email, clear_data=True, stop_reason="Critical Python Crash")
        
    finally:
        if email in active_ws: del active_ws[email]

# ----------------------------------------------------------------------
# FLASK APP SETUP AND ROUTES
# ----------------------------------------------------------------------

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET_KEY', 'VERY_STRONG_SECRET_KEY_RENDER_BOT')
app.config['SESSION_PERMANENT'] = False

AUTH_FORM = """
<!doctype html>
<title>Login</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
    body { font-family: Arial, sans-serif; padding: 20px; max-width: 400px; margin: auto; text-align: center; }
    input[type="email"] { width: 95%; padding: 10px; margin: 10px 0; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }
    button { background-color: #007bff; color: white; padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer; font-size: 1.1em; }
    h1 { color: #007bff; }
</style>
<h1>Bot Login</h1>
{% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
        {% for category, message in messages %}
            <p style="color:{{ 'red' if category == 'error' else 'green' }};">{{ message }}</p>
        {% endfor %}
    {% endif %}
{% endwith %}
<form method="POST" action="/login">
    <label for="email">Email:</label><br>
    <input type="email" id="email" name="email" required><br>
    <button type="submit">Login</button>
</form>
"""

CONTROL_FORM = f"""
<!doctype html>
<title>Control Panel</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
    body {{
        font-family: Arial, sans-serif;
        padding: 10px;
        max-width: 600px;
        margin: auto;
        direction: ltr;
        text-align: left;
    }}
    h1 {{
        color: #007bff;
        font-size: 1.8em;
        border-bottom: 2px solid #eee;
        padding-bottom: 10px;
    }}
    .status-running {{
        color: green;
        font-weight: bold;
        font-size: 1.3em;
    }}
    .status-stopped {{
        color: red;
        font-weight: bold;
        font-size: 1.3em;
    }}
    input[type="text"], input[type="number"], select {{
        width: 98%;
        padding: 10px;
        margin-top: 5px;
        margin-bottom: 10px;
        border: 1px solid #ccc;
        border-radius: 4px;
        box-sizing: border-box;
        text-align: left;
    }}
    form button {{
        padding: 12px 20px;
        border: none;
        border-radius: 5px;
        cursor: pointer;
        font-size: 1.1em;
        margin-top: 15px;
        width: 100%;
    }}
</style>
<h1>Bot Control Panel | User: {{ email }}</h1>
<hr>

{{ '{{' }} with messages = get_flashed_messages(with_categories=true) }}
    {{ '{{' }} if messages }}
        {{ '{{' }} for category, message in messages }}
            <p style="color:{{ 'green' if category == 'success' else ('blue' if category == 'info' else 'red') }};">{{ '{{' }} message }}</p>
        {{ '{{' }} endfor }}
        
        {{ '{{' }} if session_data and session_data.stop_reason and session_data.stop_reason != "Running" and session_data.stop_reason != "Stopped Manually" and session_data.stop_reason != "Disconnected (Auto-Retry)" }}
            <p style="color:red; font-weight:bold;">Last Reason: {{ '{{' }} session_data.stop_reason }}</p>
        {{ '{{' }} endif }}
    {{ '{{' }} endif }}
{{ '{{' }} endwith }}


{{ '{{' }} if session_data and session_data.is_running }}
    {{ '{{' }} set strategy = 'Digit Differ (R_100 - Base Entry: Most Frequent Digit in Last ' + tick_sample_size|string + ' Ticks AND Time :00/:30 / Martingale: DELAYED with NEW ANALYSIS - x' + martingale_multiplier|string + ' Martingale, Max ' + max_consecutive_losses|string + ' Losses, ' + duration|string + ' Tick) - SETTLEMENT BY BALANCE ONLY' }}
    
    <p class="status-running">✅ Bot is Running! (Auto-refreshing)</p>
    <p>Account Type: {{ '{{' }} session_data.account_type.upper() }} | Currency: {{ '{{' }} session_data.currency }}</p>
    
    <p style="font-weight: bold; color: blue;">💰 Current Balance: {{ '{{' }} session_data.currency }} {{ '{{' }} session_data.latest_balance|round(2) }}</p>
    
    <p>Net Profit: {{ '{{' }} session_data.currency }} {{ '{{' }} session_data.current_profit|round(2) }}</p>
    <p>Current Stake: {{ '{{' }} session_data.currency }} {{ '{{' }} session_data.current_stake|round(2) }}</p>
    <p>Consecutive Losses: {{ '{{' }} session_data.consecutive_losses }} / {{ '{{' }} max_consecutive_losses }}</p>
    <p style="font-weight: bold; color: green;">Total Wins: {{ '{{' }} session_data.total_wins }} | Total Losses: {{ '{{' }} session_data.total_losses }}</p>
    <p style="font-weight: bold; color: purple;">Collected Digits: {{ '{{' }} session_data.last_digits_history|length }} / {{ '{{' }} tick_sample_size }}</p>
    <p style="font-weight: bold; color: #007bff;">Current Strategy: {{ '{{' }} strategy }}</p>
    <p style="font-weight: bold; color: #ff5733;">Contracts Open: {{ '{{' }} session_data.open_contract_ids|length }}</p>
    
    <form method="POST" action="{{ '{{' }} url_for('stop_route') }}">
        <button type="submit" style="background-color: red; color: white;">🛑 Stop Bot</button>
    </form>
{{ '{{' }} else }}
    <p class="status-stopped">🛑 Bot is Stopped. Enter settings to start a new session.</p>
    <form method="POST" action="{{ '{{' }} url_for('start_bot') }}">

        <label for="account_type">Account Type:</label><br>
        <select id="account_type" name="account_type" required>
            <option value="demo" selected>Demo (USD)</option>
            <option value="live">Live (tUSDT)</option>
        </select><br>

        <label for="token">Deriv API Token:</label><br>
        <input type="text" id="token" name="token" required value="{{ '{{' }} session_data.api_token if session_data else '' }}" {{ '{{' }} 'readonly' if session_data and session_data.api_token and session_data.is_running is not none else '' }}><br>
        
        <label for="stake">Base Stake (USD/tUSDT):</label><br>
        <input type="number" id="stake" name="stake" value="{{ '{{' }} session_data.base_stake|round(2) if session_data else 0.35 }}" step="0.01" min="0.35" required><br>
        
        <label for="tp">TP Target (USD/tUSDT):</label><br>
        <input type="number" id="tp" name="tp" value="{{ '{{' }} session_data.tp_target|round(2) if session_data else 10.0 }}" step="0.01" required><br>
        
        <button type="submit" style="background-color: green; color: white;">🚀 Start Bot</button>
    </form>
{{ '{{' }} endif }}
<hr>
<a href="{{ '{{' }} url_for('logout') }}" style="display: block; text-align: center; margin-top: 15px; font-size: 1.1em;">Logout</a>

<script>
    function autoRefresh() {
        // تم استخدام 'let' بدلاً من 'var' لتجنب الخطأ التركيبي في Python F-string
        let isRunning = "{{ 'true' if session_data and session_data.is_running else 'false' }}"; 
        
        if (isRunning === 'true') {
            setTimeout(function() {
                window.location.reload();
            }, 1000); // 1000 milliseconds = 1 second
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

    if not session_data.get('is_running') and "stop_reason" in session_data and session_data["stop_reason"].startswith("API Buy Error"):
        flash(f"❌ API Error: {session_data['stop_reason']}. Check your token and account status.", 'error')
        session_data['stop_reason'] = "Displayed"
        save_session_data(email, session_data)
        
    contract_type_name = "Digit Differ"

    return render_template_string(CONTROL_FORM,
        email=email,
        session_data=session_data,
        max_consecutive_losses=MAX_CONSECUTIVE_LOSSES,
        martingale_multiplier=MARTINGALE_MULTIPLIER, 
        duration=DURATION,
        tick_sample_size=TICK_SAMPLE_SIZE,
        symbol=SYMBOL,
        contract_type_name=contract_type_name
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
    
    flash(f'Bot started successfully. Strategy: Digit Differ (Base Entry: Time :00/:30 & {TICK_SAMPLE_SIZE} Ticks | Martingale: Delayed with NEW Analysis) with x{MARTINGALE_MULTIPLIER} Conditional Martingale (Max {MAX_CONSECUTIVE_LOSSES} Losses, 1 Tick). **Settlement is now done via Balance Check only.**', 'success')
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
        if all_sessions[email].get('stop_reason') == "Disconnected (Auto-Retry)":
             stop_bot(email, clear_data=False, stop_reason="Disconnected (Auto-Retry)")
        else:
             stop_bot(email, clear_data=True, stop_reason="Stopped Manually")
        
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
