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

# --- GLOBAL CONFIGURATION ---
DB_FILE = "trading_data0099.db"
APP_ID = "16929" # Deriv App ID
WS_URL = f"wss://blue.derivws.com/websockets/v3?app_id={APP_ID}"
RETRY_DELAY = 1.0 # 1 second delay for retries
TRADING_SYMBOL = "R_75" # الزوج الذي يعمل عليه البوت (Volatility 75 Index)
DIFFER_TARGET_DIGIT = 5 # الرقم الثابت الذي سنتوقع الاختلاف عنه (Differs 5)

# --- Database & Utility Functions ---
def create_connection():
    """Create a database connection to the SQLite database specified by DB_FILE"""
    try:
        conn = sqlite3.connect(DB_FILE)
        return conn
    except sqlite3.Error as e:
        # For debugging purposes only
        # print(f"Error creating database connection: {e}") 
        return None

def create_table_if_not_exists():
    """Create the sessions and bot_status tables if they do not exist."""
    conn = create_connection()
    if conn:
        try:
            # FIX: Using '--' for comments in SQL statements.
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
                is_running INTEGER DEFAULT 0,
                -- 1: Waiting for first 5, 2: Waiting for second 5, 0: Ready to trade
                waiting_for_reset INTEGER DEFAULT 1 
            );
            """
            sql_create_bot_status_table = """
            CREATE TABLE IF NOT EXISTS bot_status (
                flag_id INTEGER PRIMARY KEY,
                is_running_flag INTEGER DEFAULT 0,
                last_heartbeat REAL DEFAULT 0.0,
                process_pid INTEGER DEFAULT 0
            );
            """
            conn.execute(sql_create_sessions_table)
            conn.execute(sql_create_bot_status_table)
            
            cursor = conn.execute("SELECT COUNT(*) FROM bot_status WHERE flag_id = 1")
            if cursor.fetchone()[0] == 0:
                conn.execute("INSERT INTO bot_status (flag_id, is_running_flag, last_heartbeat, process_pid) VALUES (1, 0, 0.0, 0)")
            
            conn.commit()
        except sqlite3.Error as e:
            print(f"Database error during table creation: {e}") 
        finally:
            conn.close()

def get_bot_running_status():
    """Gets the global bot running status from the database."""
    conn = create_connection()
    if conn:
        try:
            with conn:
                cursor = conn.execute("SELECT is_running_flag, last_heartbeat, process_pid FROM bot_status WHERE flag_id = 1")
                row = cursor.fetchone()
                if row:
                    status, heartbeat, pid = row
                    
                    if status == 1:
                        if pid and os.path.exists(f"/proc/{pid}"):
                            if (time.time() - heartbeat > 30):
                                update_bot_running_status(0, 0)
                                return 0
                            else:
                                return status
                        else:
                            update_bot_running_status(0, 0)
                            return 0
                    else:
                        return 0
                return 0
        except sqlite3.Error as e:
            return 0
        finally:
            conn.close()
    return 0

def update_bot_running_status(status, pid):
    """Updates the global bot running status and PID in the database."""
    conn = create_connection()
    if conn:
        try:
            with conn:
                conn.execute("UPDATE bot_status SET is_running_flag = ?, last_heartbeat = ?, process_pid = ? WHERE flag_id = 1", (status, time.time(), pid))
        except sqlite3.Error as e:
            pass
        finally:
            conn.close()

def is_user_active(email):
    """Checks if a user's email exists in the user_ids.txt file."""
    try:
        # Assuming user_ids.txt contains one email per line
        with open("user_ids.txt", "r") as file:
            active_users = [line.strip() for line in file.readlines()]
        return email in active_users
    except FileNotFoundError:
        print("user_ids.txt not found.")
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
                # Initialize 'waiting_for_reset' to 1 (Start by waiting for the first 5)
                conn.execute("""
                    INSERT OR REPLACE INTO sessions 
                    (email, user_token, base_amount, tp_target, max_consecutive_losses, current_amount, is_running, waiting_for_reset)
                    VALUES (?, ?, ?, ?, ?, ?, 1, 1) 
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
            pass
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
            pass
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
            return []
        finally:
            conn.close()
    return []

def update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=None, contract_id=None, trade_start_time=None, waiting_for_reset=None):
    """Updates trading statistics and trade information for a user in the database."""
    conn = create_connection()
    if conn:
        try:
            with conn:
                update_query = """
                UPDATE sessions SET 
                    total_wins = ?, total_losses = ?, current_amount = ?, consecutive_losses = ?, 
                    initial_balance = COALESCE(?, initial_balance), contract_id = ?, 
                    trade_start_time = COALESCE(?, trade_start_time), 
                    waiting_for_reset = COALESCE(?, waiting_for_reset)
                WHERE email = ?
                """
                conn.execute(update_query, (total_wins, total_losses, current_amount, consecutive_losses, initial_balance, contract_id, trade_start_time, waiting_for_reset, email))
        except sqlite3.Error as e:
            print(f"Database error in update_stats_and_trade_info_in_db: {e}")
        finally:
            conn.close()

# --- WebSocket Helper Functions (UNCHANGED) ---
def connect_websocket(user_token):
    """
    Establishes a WebSocket connection and authenticates the user.
    Retries indefinitely until connection and authentication succeed.
    """
    while True:
        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect(WS_URL)
            
            # Authenticate
            auth_req = {"authorize": user_token}
            ws.send(json.dumps(auth_req))
            auth_response = json.loads(ws.recv())
            
            if auth_response.get('error'):
                print(f"WebSocket authentication error: {auth_response['error']['message']}. Retrying in {RETRY_DELAY}s...")
                ws.close()
            elif auth_response.get('msg_type') == 'authorize':
                return ws # Success: Return the connected WebSocket object
            
        except Exception as e:
            print(f"Error connecting to WebSocket: {e}. Retrying in {RETRY_DELAY}s...")
        
        # Wait a short delay before retrying
        time.sleep(RETRY_DELAY)

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
        return None, None
    finally:
        if ws and ws.connected:
            ws.close()

def check_contract_status(ws, contract_id):
    """Checks the status of an open contract."""
    req = {"proposal_open_contract": 1, "contract_id": contract_id}
    
    while True:
        try:
            if not ws or not ws.connected:
                return None 
            
            ws.send(json.dumps(req))
            response = json.loads(ws.recv())
            return response.get('proposal_open_contract')
        
        except websocket._exceptions.WebSocketConnectionClosedException:
            return None 
        
        except Exception as e:
            return None

def place_order(ws, proposal_id, amount):
    """Places a trade order on Deriv."""
    if not ws or not ws.connected:
        return {"error": {"message": "WebSocket not connected."}}
    amount_decimal = decimal.Decimal(str(amount)).quantize(decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_UP)
    req = {"buy": proposal_id, "price": float(amount_decimal)}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        return response
    except Exception as e:
        return {"error": {"message": "Order placement failed."}}

# --- Trading Bot Logic (MODIFIED for Differs 5 and Double Wait) ---
def analyse_data(df_ticks):
    """
    Always targets Differs 5. Returns prediction (5) and last digit.
    """
    if df_ticks.empty:
        return "Neutral", 0, 0, "Insufficient data."

    latest_price_str = str(df_ticks.iloc[-1]['price'])
    
    if '.' in latest_price_str:
        last_digit_char = latest_price_str.split('.')[-1][-1]
    else:
        last_digit_char = latest_price_str[-1]
    
    try:
        last_digit = int(last_digit_char)
        # Prediction is always 5 (DIFFER_TARGET_DIGIT)
        return "Trade", DIFFER_TARGET_DIGIT, last_digit, f"Ready to trade Differs {DIFFER_TARGET_DIGIT}."
    except ValueError:
        return "Neutral", 0, 0, "Error extracting last digit from price."

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
    waiting_for_reset = session_data['waiting_for_reset'] # 1, 2, or 0
    
    ws = None
    try:
        ws = connect_websocket(user_token)
        if not ws:
            print(f"Could not connect/authorize WebSocket for {email}. Stopping attempt.")
            return

        # --- 1. Check for completed trades (if contract_id exists) ---
        while contract_id: 
            contract_info = check_contract_status(ws, contract_id)
            
            if contract_info is None:
                print(f"Reconnecting WebSocket for {email} to check contract {contract_id} status...")
                ws = connect_websocket(user_token)
                if not ws:
                    print(f"Failed to reconnect for {email}. Stopping for now.")
                    return
                continue

            if contract_info.get('is_sold'): # Trade has finished
                profit = float(contract_info.get('profit', 0))
                
                # Update stats and Martingale logic
                if profit > 0:
                    consecutive_losses = 0
                    total_wins += 1
                    current_amount = base_amount # Reset to base amount on win
                    waiting_for_reset = 1 # Reset to wait for the first 5 before NEXT trade
                elif profit < 0:
                    consecutive_losses += 1
                    total_losses += 1
                    next_bet = float(current_amount) * 2.1 
                    current_amount = max(base_amount, next_bet)
                    waiting_for_reset = 1 # Reset to wait for the first 5 before compensation trade
                else: 
                    consecutive_losses = 0 
                    waiting_for_reset = 1 # Always re-wait before the next trade
                
                new_contract_id = None
                trade_start_time = 0.0
                
                new_balance, _ = get_balance_and_currency(user_token)
                if new_balance is not None:
                    current_balance_float = float(new_balance)
                    if initial_balance == 0.0:
                        initial_balance = current_balance_float
                    
                    update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, 
                                                      initial_balance=initial_balance, contract_id=new_contract_id, 
                                                      trade_start_time=trade_start_time, waiting_for_reset=waiting_for_reset)

                    # Check Stop Criteria
                    if (current_balance_float - initial_balance) >= float(tp_target):
                        print(f"User {email} reached Take Profit target. Stopping session.")
                        update_is_running_status(email, 0)
                        clear_session_data(email)
                        return
                        
                    if consecutive_losses >= max_consecutive_losses:
                        print(f"User {email} reached Max Consecutive Losses. Stopping session.")
                        update_is_running_status(email, 0)
                        clear_session_data(email)
                        return
                
                break # Contract sold, exit the while loop

            if check_only:
                time.sleep(0.1)
                
        # --- 2. Place new trade if no trade is active AND if waiting is complete ---
        if not check_only and not contract_id: 
            balance, currency = get_balance_and_currency(user_token)
            if balance is None:
                print(f"Failed to get balance for {email}. Skipping trade.")
                return
            if initial_balance == 0:
                initial_balance = float(balance)
                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=None, trade_start_time=None, waiting_for_reset=waiting_for_reset)
                
            req = {"ticks_history": TRADING_SYMBOL, "end": "latest", "count": 1, "style": "ticks"}
            
            # --- Ticks History Retrieval/Wait for Double Reset Loop with Reconnect ---
            tick_data = None
            
            while True:
                # If waiting_for_reset is 0, we exit the loop immediately and trade
                if waiting_for_reset == 0:
                    break 

                try:
                    # Request and receive the latest tick
                    ws.send(json.dumps(req))
                    response = json.loads(ws.recv())
                    
                    if response.get('msg_type') == 'history' and 'history' in response:
                        ticks = response['history']['prices']
                        last_digit = None
                        if ticks:
                            latest_price_str = str(ticks[-1])
                            if '.' in latest_price_str:
                                last_digit = int(latest_price_str.split('.')[-1][-1])
                            else:
                                last_digit = int(latest_price_str[-1])
                        
                        # --- WAIT LOGIC (If waiting_for_reset > 0) ---
                        if waiting_for_reset > 0:
                            if last_digit == DIFFER_TARGET_DIGIT:
                                # Found the target digit '5'
                                new_wait_state = waiting_for_reset + 1
                                
                                if new_wait_state == 3: # Target digit appeared for the SECOND time
                                    print(f"User {email}: Target digit ({DIFFER_TARGET_DIGIT}) appeared TWICE. Entering trade.")
                                    waiting_for_reset = 0 # Set flag to 0 (Ready)
                                    update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, waiting_for_reset=0)
                                    break # Exit tick loop and proceed to place trade
                                else: # Target digit appeared for the FIRST time (or advanced state)
                                    print(f"User {email}: Target digit ({DIFFER_TARGET_DIGIT}) appeared (Wait state: {waiting_for_reset} -> {new_wait_state}).")
                                    waiting_for_reset = new_wait_state
                                    update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, waiting_for_reset=new_wait_state)
                                    # State remains 2 (waiting for second 5) until the condition is met. 
                                    # If the next digit is also 5, it will hit new_wait_state == 3 and break immediately, fulfilling the consecutive condition.
                            
                            # If last_digit != DIFFER_TARGET_DIGIT, we do nothing and the state remains (1 or 2).
                            time.sleep(0.1)
                            continue 
                    elif response.get('error'):
                        print(f"Error getting ticks history for {email}: {response['error']['message']}")
                        return
                        
                except websocket._exceptions.WebSocketConnectionClosedException:
                    print(f"WebSocket closed while waiting/getting ticks history for {email}. Reconnecting...")
                    ws = connect_websocket(user_token) 
                    if not ws: return 
                except Exception as e:
                    print(f"Error receiving/processing ticks for {email}: {e}")
                    return
            
            # --- Place Trade (Only reached if waiting_for_reset == 0) ---
            
            # Get the very latest tick one more time just before trading
            try:
                ws.send(json.dumps(req))
                response = json.loads(ws.recv())
                if response.get('msg_type') == 'history' and 'history' in response:
                    ticks = response['history']['prices']
                else:
                    print(f"User {email}: Failed to get final tick before trading.")
                    return
            except Exception as e:
                print(f"User {email}: Error getting final tick before trading: {e}")
                return

            df_ticks = pd.DataFrame({'price': ticks})
            signal, barrier_value, _, message = analyse_data(df_ticks) 
            
            if signal == 'Trade':
                contract_type = "DIGITDIFF" 
                amount_to_bet = max(0.35, round(float(current_amount), 2)) 

                proposal_req = {
                    "proposal": 1, "amount": amount_to_bet, "basis": "stake",
                    "contract_type": contract_type, "currency": currency,
                    "duration": 1, "duration_unit": "t", "symbol": TRADING_SYMBOL,
                    "barrier": str(barrier_value)
                }
                
                proposal_response = None
                while proposal_response is None:
                    try:
                        ws.send(json.dumps(proposal_req))
                        response_str = ws.recv()
                        if response_str:
                            proposal_response = json.loads(response_str)
                            if proposal_response.get('error'):
                                print(f"Error getting proposal for {email}: {proposal_response['error']['message']}")
                                return
                            if 'proposal' in proposal_response:
                                break 
                    except websocket._exceptions.WebSocketConnectionClosedException:
                        print(f"WebSocket closed while waiting for proposal for {email}. Reconnecting...")
                        ws = connect_websocket(user_token)
                        if not ws: return 
                        proposal_response = None 
                    except Exception as e:
                        print(f"Error receiving proposal for {email}: {e}")
                        return

                if proposal_response and 'proposal' in proposal_response:
                    proposal_id = proposal_response['proposal']['id']
                    
                    # Place the order
                    order_response = place_order(ws, proposal_id, amount_to_bet)
                    
                    if 'buy' in order_response and 'contract_id' in order_response['buy']:
                        new_contract_id = order_response['buy']['contract_id']
                        trade_start_time = time.time()
                        print(f"User {email}: Placed DIGITDIFF trade {new_contract_id} (Differs {barrier_value}) with stake {amount_to_bet}.")
                        
                        # Set waiting_for_reset to 0 temporarily while trade is active
                        update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, 
                                                          initial_balance=initial_balance, contract_id=new_contract_id, 
                                                          trade_start_time=trade_start_time, waiting_for_reset=0)
                    else:
                        print(f"User {email}: Failed to place order. Response: {order_response}")
                else:
                    print(f"User {email}: No proposal received or error in proposal response. Response: {proposal_response}")
            else:
                print(f"User {email}: Analysis failed. Skipping trade.")
    
    except Exception as e:
        print(f"An error occurred in run_trading_job_for_user for {email}: {e}")
    finally:
        if ws and ws.connected:
            ws.close()

# --- Main Bot Loop Function (UNCHANGED) ---
def bot_loop():
    """Main loop that orchestrates trading jobs for all active sessions."""
    print("Bot process started. PID:", os.getpid())
    update_bot_running_status(1, os.getpid())
    
    while True:
        try:
            # Heartbeat check
            update_bot_running_status(1, os.getpid())
            active_sessions = get_all_active_sessions()
            
            if active_sessions:
                for session in active_sessions:
                    email = session['email']
                    latest_session_data = get_session_status_from_db(email)
                    
                    if not latest_session_data or latest_session_data.get('is_running') == 0:
                        continue
                        
                    contract_id = latest_session_data.get('contract_id')
                    
                    # 1. Check open contract status
                    if contract_id:
                        run_trading_job_for_user(latest_session_data, check_only=True) 
                        time.sleep(0.1) 
                        continue
                        
                    # 2. Place a new trade if ready (contract_id is None)
                    re_checked_session_data = get_session_status_from_db(email) 
                    if re_checked_session_data and re_checked_session_data.get('is_running') == 1 and not re_checked_session_data.get('contract_id'):
                        run_trading_job_for_user(re_checked_session_data, check_only=False)  
            
            time.sleep(1) 
        except Exception as e:
            print(f"Error in bot_loop main loop: {e}. Sleeping for 5 seconds before retrying.")
            time.sleep(5) 

# --- Streamlit App Configuration (UNCHANGED) ---
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

if bot_status_from_db == 0: 
    try:
        print("Attempting to start bot process...")
        bot_process = multiprocessing.Process(target=bot_loop, daemon=True)
        bot_process.start()
        update_bot_running_status(1, bot_process.pid)
        print(f"Bot process started with PID: {bot_process.pid}")
    except Exception as e:
        st.error(f"❌ Error starting bot process: {e}")
else:
    # This message will only appear if the bot successfully started and updated the DB status
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
    
    stats_data = get_session_status_from_db(st.session_state.user_email)
    st.session_state.stats = stats_data
    
    is_user_bot_running_in_db = False
    if st.session_state.stats:
        is_user_bot_running_in_db = st.session_state.stats.get('is_running', 0) == 1
    
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
        update_is_running_status(st.session_state.user_email, 0)
        st.info("⏸ Your bot session has been stopped.")
        st.rerun()

    st.markdown("---")
    st.subheader("Statistics")

    stats_placeholder = st.empty()
    
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
            
            # Translate waiting_for_reset status
            wait_status_code = stats.get('waiting_for_reset', 1)
            if wait_status_code == 0:
                wait_status_display = "Ready to Trade (Condition Met)"
            elif wait_status_code == 1:
                wait_status_display = f"Waiting for first {DIFFER_TARGET_DIGIT}"
            elif wait_status_code == 2:
                wait_status_display = f"Waiting for second {DIFFER_TARGET_DIGIT}"
            else:
                wait_status_display = "Unknown"

            col1, col2, col3, col4, col5, col6 = st.columns(6)
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
            with col6:
                st.metric(label="Wait Status", value=wait_status_display)
            
            if stats.get('contract_id'):
                st.warning("⚠ A trade is currently active. Stats will update after completion.")
            elif stats.get('waiting_for_reset', 1) > 0:
                st.info(f"⏳ Bot is waiting for the entry condition: **{wait_status_display}**.")

    else:
        with stats_placeholder.container():
            st.info("Your bot session is currently stopped or not yet configured.")
            
    # Auto-rerun to update status periodically
    time.sleep(2)
    st.rerun()
