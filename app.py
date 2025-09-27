import streamlit as st
import time
import websocket
import json
import os
import decimal
import sqlite3
import pandas as pd
from datetime import datetime
import multiprocessing

# --- Strategy Configuration (NEW) ---
SYMBOL = "R_100" # Volatility 100 Index - مناسب لاستراتيجيات الأرقام
CONTRACT_TYPE = "DIGITDIFF" 
CONTRACT_DURATION = 1 # 1 tick
CONTRACT_DURATION_UNIT = 't' # 't' for tick
TARGET_DIGIT = 5 # الرقم الهدف لـ DIGITDIFF (Differ from 5)

# --- SQLite Database Configuration ---
DB_FILE = "trading_data0099.db"

# --- Database & Utility Functions ---
def create_connection():
    """Create a database connection to the SQLite database specified by DB_FILE"""
    try:
        conn = sqlite3.connect(DB_FILE)
        return conn
    except sqlite3.Error as e:
        return None

def create_table_if_not_exists():
    """Create the sessions and bot_status tables if they do not exist."""
    conn = create_connection()
    if conn:
        try:
            # Table for session data
            sql_create_sessions_table = """
            CREATE TABLE IF NOT EXISTS sessions (
                email TEXT PRIMARY KEY,
                user_token TEXT NOT NULL,
                base_amount REAL NOT NULL,
                tp_target REAL NOT NULL,
                max_consecutive_losses INTEGER NOT NULL,
                total_wins INTEGER DEFAULT 0,
                total_losses INTEGER DEFAULT 0,
                current_amount REAL NOT NULL,
                consecutive_losses INTEGER DEFAULT 0,
                initial_balance REAL DEFAULT 0.0,
                contract_id TEXT,
                trade_start_time REAL DEFAULT 0.0,
                is_running INTEGER DEFAULT 0 
            );
            """
            # Table for global bot status and process management
            sql_create_bot_status_table = """
            CREATE TABLE IF NOT EXISTS bot_status (
                flag_id INTEGER PRIMARY KEY,
                is_running_flag INTEGER DEFAULT 0, -- 0: Stopped, 1: Running
                last_heartbeat REAL DEFAULT 0.0,
                process_pid INTEGER DEFAULT 0    -- Stores the PID of the bot process
            );
            """
            conn.execute(sql_create_sessions_table)
            conn.execute(sql_create_bot_status_table)
            
            # Check and insert the initial status row if it doesn't exist
            cursor = conn.execute("SELECT COUNT(*) FROM bot_status WHERE flag_id = 1")
            if cursor.fetchone()[0] == 0:
                conn.execute("INSERT INTO bot_status (flag_id, is_running_flag, last_heartbeat, process_pid) VALUES (1, 0, 0.0, 0)")
            
            conn.commit()
        except sqlite3.Error as e:
            print(f"Database error during table creation: {e}") # For debugging
        finally:
            conn.close()

def get_bot_running_status():
    """
    Gets the global bot running status from the database.
    Also checks for process liveness and timeouts.
    """
    conn = create_connection()
    if conn:
        try:
            with conn:
                cursor = conn.execute("SELECT is_running_flag, last_heartbeat, process_pid FROM bot_status WHERE flag_id = 1")
                row = cursor.fetchone()
                if row:
                    status, heartbeat, pid = row
                    
                    # Check if the process is supposed to be running but no heartbeat or old heartbeat
                    if status == 1:
                        # Check if the PID is valid and the process is alive
                        if pid and os.path.exists(f"/proc/{pid}"): # Linux specific check for process existence
                            if (time.time() - heartbeat > 30): # Heartbeat timeout (30 seconds)
                                print(f"Bot process {pid} timed out. Marking as stopped.")
                                update_bot_running_status(0, 0) # Mark as stopped if no heartbeat
                                return 0
                            else:
                                return status # Bot is running and heartbeat is recent
                        else:
                            # Process is not alive (or PID invalid), mark as stopped
                            print(f"Bot process {pid} not found. Marking as stopped.")
                            update_bot_running_status(0, 0)
                            return 0
                    else:
                        return 0 # Bot is explicitly stopped
                return 0 # No status found, assume stopped
        except sqlite3.Error as e:
            print(f"Database error in get_bot_running_status: {e}")
            return 0
        finally:
            conn.close()
    return 0 # Connection failed

def update_bot_running_status(status, pid):
    """Updates the global bot running status and PID in the database."""
    conn = create_connection()
    if conn:
        try:
            with conn:
                conn.execute("UPDATE bot_status SET is_running_flag = ?, last_heartbeat = ?, process_pid = ? WHERE flag_id = 1", (status, time.time(), pid))
        except sqlite3.Error as e:
            print(f"Database error in update_bot_running_status: {e}")
        finally:
            conn.close()

def is_any_session_running():
    """Checks if there is any active session in the database."""
    conn = create_connection()
    if conn:
        try:
            with conn:
                cursor = conn.execute("SELECT COUNT(*) FROM sessions WHERE is_running = 1")
                count = cursor.fetchone()[0]
                return count > 0
        except sqlite3.Error as e:
            print(f"Database error in is_any_session_running: {e}")
            return True # Assume running to be safe
        finally:
            conn.close()
    return True # Connection failed, assume running to be safe

def is_user_active(email):
    """Checks if a user's email exists in the user_ids.txt file."""
    try:
        with open("user_ids.txt", "r") as file:
            active_users = [line.strip() for line in file.readlines()]
        return email in active_users
    except FileNotFoundError:
        return False
    except Exception as e:
        print(f"Error reading user_ids.txt: {e}")
        return False

def start_new_session_in_db(email, settings):
    """Saves or updates user settings and initializes session data in the database."""
    conn = create_connection()
    if conn:
        try:
            with conn:
                conn.execute("""
                    INSERT OR REPLACE INTO sessions 
                    (email, user_token, base_amount, tp_target, max_consecutive_losses, current_amount, is_running)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                    """, (email, settings["user_token"], settings["base_amount"], settings["tp_target"], settings["max_consecutive_losses"], settings["base_amount"]))
        except sqlite3.Error as e:
            print(f"Database error in start_new_session_in_db: {e}")
        finally:
            conn.close()

def update_is_running_status(email, status):
    """Updates the is_running status for a specific user session in the database."""
    conn = create_connection()
    if conn:
        try:
            with conn:
                conn.execute("UPDATE sessions SET is_running = ? WHERE email = ?", (status, email))
        except sqlite3.Error as e:
            print(f"Database error in update_is_running_status: {e}")
        finally:
            conn.close()

def clear_session_data(email):
    """Deletes a user's session data from the database."""
    conn = create_connection()
    if conn:
        try:
            with conn:
                conn.execute("DELETE FROM sessions WHERE email=?", (email,))
        except sqlite3.Error as e:
            print(f"Database error in clear_session_data: {e}")
        finally:
            conn.close()

def get_session_status_from_db(email):
    """Retrieves the current session status for a given email from the database."""
    conn = create_connection()
    if conn:
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM sessions WHERE email=?", (email,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
        except sqlite3.Error as e:
            print(f"Database error in get_session_status_from_db: {e}")
            return None
        finally:
            conn.close()
    return None

def get_all_active_sessions():
    """Fetches all currently active trading sessions from the database."""
    conn = create_connection()
    if conn:
        try:
            with conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT * FROM sessions WHERE is_running = 1")
                rows = cursor.fetchall()
                sessions = []
                for row in rows:
                    sessions.append(dict(row))
                return sessions
        except sqlite3.Error as e:
            print(f"Database error in get_all_active_sessions: {e}")
            return []
        finally:
            conn.close()
    return []

def update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=None, contract_id=None, trade_start_time=None):
    """Updates trading statistics and trade information for a user in the database."""
    conn = create_connection()
    if conn:
        try:
            with conn:
                update_query = """
                UPDATE sessions SET 
                    total_wins = ?, total_losses = ?, current_amount = ?, consecutive_losses = ?, 
                    initial_balance = COALESCE(?, initial_balance), contract_id = ?, trade_start_time = COALESCE(?, trade_start_time)
                WHERE email = ?
                """
                conn.execute(update_query, (total_wins, total_losses, current_amount, consecutive_losses, initial_balance, contract_id, trade_start_time, email))
        except sqlite3.Error as e:
            print(f"Database error in update_stats_and_trade_info_in_db: {e}")
        finally:
            conn.close()

# --- WebSocket Helper Functions ---
def connect_websocket(user_token):
    """Establishes a WebSocket connection and authenticates the user."""
    ws = websocket.WebSocket()
    try:
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929")
        auth_req = {"authorize": user_token}
        ws.send(json.dumps(auth_req))
        auth_response = json.loads(ws.recv())
        if auth_response.get('error'):
            print(f"WebSocket authentication error: {auth_response['error']['message']}")
            ws.close()
            return None
        return ws
    except Exception as e:
        print(f"Error connecting to WebSocket: {e}")
        return None

def get_balance_and_currency(user_token):
    """Fetches the user's current balance and currency using WebSocket."""
    ws = None
    try:
        ws = connect_websocket(user_token)
        if not ws:
            return None, None
        balance_req = {"balance": 1}
        ws.send(json.dumps(balance_req))
        balance_response = json.loads(ws.recv())
        if balance_response.get('msg_type') == 'balance':
            balance_info = balance_response.get('balance', {})
            return balance_info.get('balance'), balance_info.get('currency')
        return None, None
    except Exception as e:
        print(f"Error getting balance: {e}")
        return None, None
    finally:
        if ws and ws.connected:
            ws.close()

def check_contract_status(ws, contract_id):
    """Checks the status of an open contract."""
    if not ws or not ws.connected:
        return None
    req = {"proposal_open_contract": 1, "contract_id": contract_id}
    try:
        ws.send(json.dumps(req))
        # Receive until we get the contract status response
        while True:
            response_str = ws.recv()
            response = json.loads(response_str)
            if response.get('msg_type') == 'proposal_open_contract':
                return response.get('proposal_open_contract')
            elif response.get('error'):
                print(f"Error checking contract status: {response['error']['message']}")
                return None
    except Exception as e:
        print(f"Error checking contract status: {e}")
        return None

def place_order(ws, proposal_id, amount):
    """Places a trade order on Deriv."""
    if not ws or not ws.connected:
        return {"error": {"message": "WebSocket not connected."}}
    amount_decimal = decimal.Decimal(str(amount)).quantize(decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_UP)
    req = {"buy": proposal_id, "price": float(amount_decimal)}
    try:
        ws.send(json.dumps(req))
        # Wait for the buy response
        while True:
            response_str = ws.recv()
            response = json.loads(response_str)
            if response.get('msg_type') == 'buy':
                return response
            elif response.get('error'):
                return response
    except Exception as e:
        print(f"Error placing order: {e}")
        return {"error": {"message": "Order placement failed."}}

# --- Trading Bot Logic ---
def analyse_data(last_price):
    """
    Analyzes the last price to generate a trading signal based on the last digit being 5.
    """
    try:
        # استخراج آخر رقم من السعر
        price_str = str(last_price)
        last_digit = int(price_str[-1])
        
        if last_digit == TARGET_DIGIT: # TARGET_DIGIT = 5
            return "Trade", "Last digit is 5. Ready to enter."
        else:
            return "Wait", f"Last digit is {last_digit}. Waiting for {TARGET_DIGIT}."
    except Exception:
        return "Wait", "Invalid price format or missing TARGET_DIGIT."

# دالة مخصصة لطلب الاشتراك في التيكات
def subscribe_to_ticks(ws):
    """Subscribes to the live ticks stream for the specified symbol."""
    req = {"ticks": SYMBOL}
    ws.send(json.dumps(req))

def run_trading_job_for_user(session_data, check_only=False):
    """Executes the trading logic for a specific user's session."""
    email = session_data['email']
    user_token = session_data['user_token']
    base_amount = session_data['base_amount']
    tp_target = session_data['tp_target']
    max_consecutive_losses = session_data['max_consecutive_losses']
    total_wins = session_data['total_wins']
    total_losses = session_data['total_losses']
    current_amount = session_data['current_amount']
    consecutive_losses = session_data['consecutive_losses']
    initial_balance = session_data['initial_balance']
    contract_id = session_data['contract_id']
    
    ws = None
    try:
        # **تعزيز الاتصال الفوري**
        for _ in range(3): # محاولة الاتصال 3 مرات
             ws = connect_websocket(user_token)
             if ws:
                 break
             time.sleep(0.5) 
        
        if not ws:
            print(f"Could not connect WebSocket for {email} after multiple retries.")
            return

        # --- Check for completed trades (if contract_id exists) ---
        if contract_id: # This means a trade is currently open/in progress
            contract_info = check_contract_status(ws, contract_id)
            if contract_info and contract_info.get('is_sold'): # Trade has finished
                
                # **تعديل: تأخير 5 ثواني بعد رؤية النتيجة**
                time.sleep(5) 
                
                profit = float(contract_info.get('profit', 0))
                
                if profit > 0:
                    consecutive_losses = 0
                    total_wins += 1
                    current_amount = base_amount # Reset to base amount on win
                elif profit < 0:
                    consecutive_losses += 1
                    total_losses += 1
                    # Martingale logic: double stake, but not less than base_amount
                    next_bet = float(current_amount) * 2.1 
                    current_amount = max(base_amount, next_bet)
                else: # Profit is 0 (e.g., trade ended with no change or cancelled)
                    consecutive_losses = 0 
                
                # Reset trade tracking after completion
                new_contract_id = None
                trade_start_time = 0.0
                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=new_contract_id, trade_start_time=trade_start_time)

                # Check for Take Profit or Max Losses after trade completion
                new_balance, _ = get_balance_and_currency(user_token)
                if new_balance is not None:
                    current_balance_float = float(new_balance)
                    if initial_balance == 0.0: # First time getting balance
                        initial_balance = current_balance_float
                        update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=new_contract_id, trade_start_time=trade_start_time)
                    
                    if (current_balance_float - initial_balance) >= float(tp_target):
                        print(f"User {email} reached Take Profit target. Stopping session.")
                        update_is_running_status(email, 0) # Mark session as not running
                        clear_session_data(email)          # Clear session from DB
                        return
                    
                    if consecutive_losses >= max_consecutive_losses:
                        print(f"User {email} reached Max Consecutive Losses. Stopping session.")
                        update_is_running_status(email, 0)
                        clear_session_data(email)
                        return
            
            # If contract is still open or check_only=True and not sold, just return (no new trade)
            return
        
        # --- If not in check_only mode and no trade is active, proceed to live analysis ---
        if not check_only and not contract_id: # Place a new trade if no trade is active and not in check_only mode
            balance, currency = get_balance_and_currency(user_token)
            if balance is None:
                print(f"Failed to get balance for {email}. Skipping trade.")
                return
            if initial_balance == 0: # If this is the first time setting balance
                initial_balance = float(balance)
                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=None, trade_start_time=None)
            
            # 1. **الاشتراك في التيكات المباشرة**
            subscribe_to_ticks(ws)
            print(f"User {email}: Subscribed to ticks. Starting live analysis (Digit 5 check).")
            
            # 2. **حلقة تحليل السعر المستمر (مراقبة الرقم 5)**
            trade_attempted = False
            while not trade_attempted:
                 try:
                     # استخدام مهلة زمنية صغيرة لعدم حظر الـ thread بالكامل
                     response_str = ws.recv() 
                     if not response_str:
                         continue
                         
                     response = json.loads(response_str)
                     
                     # التأكد من أن الرسالة هي تيك سعر
                     if response.get('msg_type') == 'tick':
                         last_price = response['tick']['quote']
                         
                         signal, message = analyse_data(last_price) 
                         
                         # لا تطبع في كل تيك لتقليل الحمل، فقط عند الضرورة
                         # print(f"User {email}: Last Digit: {int(str(last_price)[-1])}. Signal = {signal}") 

                         if signal == 'Trade':
                             amount_to_bet = max(0.35, round(float(current_amount), 2))
                             
                             # **طلب العقد الجديد (DIGITDIFF 5)**
                             proposal_req = {
                                 "proposal": 1, "amount": amount_to_bet, "basis": "stake",
                                 "contract_type": CONTRACT_TYPE, "currency": currency,
                                 "duration": CONTRACT_DURATION, "duration_unit": CONTRACT_DURATION_UNIT, 
                                 "symbol": SYMBOL,
                                 "target": TARGET_DIGIT 
                             }
                             ws.send(json.dumps(proposal_req))
                             
                             # منطق انتظار الـ proposal
                             proposal_response = None
                             while proposal_response is None:
                                 try:
                                     response_str_prop = ws.recv()
                                     if response_str_prop:
                                         proposal_response = json.loads(response_str_prop)
                                         if proposal_response.get('error'):
                                              print(f"Error getting proposal: {proposal_response['error']['message']}")
                                              return
                                         if 'proposal' in proposal_response:
                                             break
                                 except Exception:
                                      continue
                             
                             if proposal_response and 'proposal' in proposal_response:
                                 proposal_id = proposal_response['proposal']['id']
                                 
                                 # Place the order
                                 order_response = place_order(ws, proposal_id, amount_to_bet)
                                 
                                 if 'buy' in order_response and 'contract_id' in order_response['buy']:
                                     new_contract_id = order_response['buy']['contract_id']
                                     trade_start_time = time.time()
                                     print(f"User {email}: Placed trade {new_contract_id} (Digit Diff 5).")
                                     
                                     trade_attempted = True 
                                     update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=new_contract_id, trade_start_time=trade_start_time)
                                     
                                     # **إلغاء الاشتراك في التيكات فورا**
                                     ws.send(json.dumps({"forget_all": "ticks"}))
                                     
                                     # **تطبيق شرط انتظار 5 ثواني بعد الدخول**
                                     print(f"User {email}: Waiting 5 seconds before checking trade result...")
                                     time.sleep(5) 
                                     break 
                                 else:
                                     print(f"User {email}: Failed to place order. Response: {order_response}")
                                     trade_attempted = True
                                     break
                             
                     elif response.get('msg_type') == 'error':
                          print(f"Error receiving from stream: {response['error']['message']}")
                          # نعود للمحاولة مجدداً أو نخرج
                          break
                         
                 except websocket._exceptions.WebSocketConnectionClosedException:
                     print(f"WebSocket closed during live analysis for {email}. **Reconnecting immediately...**")
                     # يجب أن نخرج لكي يقوم bot_loop بإعادة تشغيل run_trading_job_for_user الذي سيقوم بإعادة الاتصال
                     return 
                 except Exception as e:
                     print(f"Error during live analysis for {email}: {e}")
                     return 

    except Exception as e:
        print(f"An error occurred in run_trading_job_for_user for {email}: {e}")
    finally:
        if ws and ws.connected:
            # التأكد من إلغاء الاشتراك في حال الخروج غير النظامي
            try:
                ws.send(json.dumps({"forget_all": "ticks"}))
            except:
                pass
            ws.close()

# --- Main Bot Loop Function ---
def bot_loop():
    """Main loop that orchestrates trading jobs for all active sessions."""
    print("Bot process started. PID:", os.getpid())
    update_bot_running_status(1, os.getpid()) # Mark as running with current PID
    
    while True:
        try:
            # Update heartbeat to show this process is alive and well
            update_bot_running_status(1, os.getpid())
            
            active_sessions = get_all_active_sessions() # Fetch sessions marked as running (is_running = 1)
            
            if active_sessions:
                for session in active_sessions:
                    email = session['email']
                    
                    # Get the most up-to-date session data from DB
                    latest_session_data = get_session_status_from_db(email)
                    if not latest_session_data or latest_session_data.get('is_running') == 0:
                        continue
                        
                    contract_id = latest_session_data.get('contract_id')
                    
                    # 1. Logic to check and close active trades
                    if contract_id:
                        # check_only=True: يتحقق من النتيجة ويطبق قاعدة Martingale و TP/SL
                        run_trading_job_for_user(latest_session_data, check_only=True)
                    
                    # 2. **Logic to place new trades (مراقبة مستمرة للتيكات)**
                    elif not contract_id:
                        # check_only=False: يبدأ حلقة مراقبة التيكات (آخر رقم 5)
                        run_trading_job_for_user(latest_session_data, check_only=False) 
            
            time.sleep(1) # Wait for 1 second before the next iteration of the loop
        except Exception as e:
            print(f"Error in bot_loop main loop: {e}. Sleeping for 5 seconds before retrying.")
            time.sleep(5) # Sleep longer if an error occurs to avoid rapid failure

# --- Streamlit App Configuration ---
st.set_page_config(page_title="Khoury Bot", layout="wide")
st.title("Khoury Bot 🤖")

# --- Initialize Session State ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "user_email" not in st.session_state:
    st.session_state.user_email = ""
if "stats" not in st.session_state:
    st.session_state.stats = None
    
# Ensure database tables exist when the app starts
create_table_if_not_exists()

# --- Bot Process Management ---
bot_status_from_db = get_bot_running_status()

if bot_status_from_db == 0: # Bot is not running or was timed out/crashed
    try:
        print("Attempting to start bot process...")
        bot_process = multiprocessing.Process(target=bot_loop, daemon=True)
        bot_process.start()
        # Update DB with new status and PID
        update_bot_running_status(1, bot_process.pid)
        print(f"Bot process started with PID: {bot_process.pid}")
    except Exception as e:
        st.error(f"❌ Error starting bot process: {e}")
else:
    # Bot is already running (status is 1)
    print("Bot process is already running (status from DB).")

# --- Login Section ---
if not st.session_state.logged_in:
    st.markdown("---")
    st.subheader("Login")
    login_form = st.form("login_form")
    email_input = login_form.text_input("Email")
    submit_button = login_form.form_submit_button("Login")
    
    if submit_button:
        if is_user_active(email_input):
            st.session_state.logged_in = True
            st.session_state.user_email = email_input
            st.rerun()
        else:
            st.error("❌ This email is not active. Please contact the administrator.")

# --- Main Application Section (after login) ---
if st.session_state.logged_in:
    st.markdown("---")
    st.subheader(f"Welcome, {st.session_state.user_email}")
    
    # Fetch current session data for the logged-in user
    stats_data = get_session_status_from_db(st.session_state.user_email)
    st.session_state.stats = stats_data
    
    is_user_bot_running_in_db = False
    if st.session_state.stats:
        is_user_bot_running_in_db = st.session_state.stats.get('is_running', 0) == 1
    
    # Check if the bot process from DB is actually running
    global_bot_status = get_bot_running_status() # This function checks PID and heartbeat
    
    # The UI should reflect the session's running status, not the global bot status

    with st.form("settings_and_control"):
        st.subheader("Bot Settings and Control")
        user_token_val = ""
        base_amount_val = 0.35
        tp_target_val = 10.0
        max_consecutive_losses_val = 3
        
        if st.session_state.stats:
            user_token_val = st.session_state.stats.get('user_token', '')
            base_amount_val = st.session_state.stats.get('base_amount', 0.35)
            tp_target_val = st.session_state.stats.get('tp_target', 10.0)
            max_consecutive_losses_val = st.session_state.stats.get('max_consecutive_losses', 3)
        
        user_token = st.text_input("Deriv API Token", type="password", value=user_token_val, disabled=is_user_bot_running_in_db)
        base_amount = st.number_input("Base Bet Amount", min_value=0.35, value=base_amount_val, step=0.1, disabled=is_user_bot_running_in_db)
        tp_target = st.number_input("Take Profit Target", min_value=10.0, value=tp_target_val, step=3.0, disabled=is_user_bot_running_in_db)
        max_consecutive_losses = st.number_input("Max Consecutive Losses", min_value=1, value=max_consecutive_losses_val, step=1, disabled=is_user_bot_running_in_db)
        
        col_start, col_stop = st.columns(2)
        with col_start:
            start_button = st.form_submit_button("Start Bot", disabled=is_user_bot_running_in_db)
        with col_stop:
            stop_button = st.form_submit_button("Stop Bot", disabled=not is_user_bot_running_in_db)
    
    if start_button:
        if not user_token:
            st.error("Please enter a Deriv API Token to start the bot.")
        else:
            settings = {
                "user_token": user_token,
                "base_amount": base_amount,
                "tp_target": tp_target,
                "max_consecutive_losses": max_consecutive_losses
            }
            start_new_session_in_db(st.session_state.user_email, settings)
            st.success("✅ Bot session started successfully! Please wait for the stats to update.")
            st.rerun()

    if stop_button:
        update_is_running_status(st.session_state.user_email, 0) # Mark session as not running
        # clear_session_data(st.session_state.user_email)  
        st.info("⏸ Your bot session has been stopped.")
        st.rerun()

    st.markdown("---")
    st.subheader("Statistics")

    stats_placeholder = st.empty()
    
    # Display bot's overall status from DB
    current_global_bot_status = get_bot_running_status()
    if current_global_bot_status == 1:
        st.success("🟢 *Global Bot Service is RUNNING*.")
    else:
        st.error("🔴 *Global Bot Service is STOPPED*.")

    

    if st.session_state.user_email:
        session_data = get_session_status_from_db(st.session_state.user_email)
        if session_data:
            user_token_for_balance = session_data.get('user_token')
            if user_token_for_balance:
                balance, _ = get_balance_and_currency(user_token_for_balance)
                if balance is not None:
                    st.metric(label="Current Balance", value=f"${float(balance):.2f}")

    if st.session_state.stats:
        with stats_placeholder.container():
            stats = st.session_state.stats
            col1, col2, col3, col4, col5 = st.columns(5)
            with col1:
                st.metric(label="Current Bet Amount", value=f"${stats.get('current_amount', 0.0):.2f}")
            with col2:
                st.metric(label="Profit Target", value=f"${stats.get('tp_target', 0.0):.2f}")
            with col3:
                st.metric(label="Total Wins", value=stats.get('total_wins', 0))
            with col4:
                st.metric(label="Total Losses", value=stats.get('total_losses', 0))
            with col5:
                st.metric(label="Consecutive Losses", value=stats.get('consecutive_losses', 0))
            
            if stats.get('contract_id'):
                st.warning("⚠ A trade is currently active. Stats will update after completion.")
    else:
        with stats_placeholder.container():
            st.info("Your bot session is currently stopped or not yet configured.")
            
    # Auto-refresh for statistics
    time.sleep(2) # Refresh every 2 seconds to show updated stats
    st.rerun()
