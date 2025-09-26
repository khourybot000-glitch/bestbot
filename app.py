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

# --- Database & Utility Functions (UNCHANGED) ---
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
                                print(f"Bot process {pid} timed out. Marking as stopped.")
                                update_bot_running_status(0, 0)
                                return 0
                            else:
                                return status
                        else:
                            print(f"Bot process {pid} not found. Marking as stopped.")
                            update_bot_running_status(0, 0)
                            return 0
                    else:
                        return 0
                return 0
        except sqlite3.Error as e:
            print(f"Database error in get_bot_running_status: {e}")
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
            print(f"Database error in update_bot_running_status: {e}")
        finally:
            conn.close()

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

# --- WebSocket Helper Functions (MODIFIED: UNLIMITED RETRIES) ---
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
        ws = connect_websocket(user_token) # Connects indefinitely until success
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
    
    # Loop to check status and handle connection loss
    while True:
        try:
            if not ws or not ws.connected:
                # This should be handled by the caller, forcing a reconnect
                return None 
            
            ws.send(json.dumps(req))
            response = json.loads(ws.recv())
            return response.get('proposal_open_contract')
        
        except websocket._exceptions.WebSocketConnectionClosedException:
            # Signal the caller to handle the full reconnect and retry
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

# --- Trading Bot Logic (MODIFIED) ---
def analyse_data(df_ticks):
    """
    Analyzes tick data to generate the prediction (Last Digit)
    for the 'Differs' contract.
    """
    if df_ticks.empty:
        return "Neutral", 0, "Insufficient data."

    # Get the latest price (most recent tick)
    latest_price_str = str(df_ticks.iloc[-1]['price'])
    
    # Extract the last digit of the price
    if '.' in latest_price_str:
        last_digit_char = latest_price_str.split('.')[-1][-1]
    else:
        # Use the last digit of the integer part if no decimal
        last_digit_char = latest_price_str[-1]
    
    try:
        last_digit = int(last_digit_char)
        # Signal 'Trade' and return the last digit as the barrier/prediction
        return "Trade", last_digit, f"Ready to trade Differs from {last_digit}."
    except ValueError:
        return "Neutral", 0, "Error extracting last digit from price."

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
        # Connect initially (will retry indefinitely)
        ws = connect_websocket(user_token)
        if not ws:
            print(f"Could not connect/authorize WebSocket for {email}. Stopping attempt.")
            return

        # --- Check for completed trades (if contract_id exists) ---
        while contract_id: # Loop to check contract status until it's sold
            contract_info = check_contract_status(ws, contract_id)
            
            if contract_info is None:
                # Connection was lost or error while checking status. Reconnect and retry check.
                print(f"Reconnecting WebSocket for {email} to check contract {contract_id} status...")
                ws = connect_websocket(user_token)
                if not ws:
                    print(f"Failed to reconnect for {email}. Stopping for now.")
                    return
                continue # Retry check_contract_status with new ws

            if contract_info.get('is_sold'): # Trade has finished
                profit = float(contract_info.get('profit', 0))
                
                # Update stats and Martingale logic
                if profit > 0:
                    consecutive_losses = 0
                    total_wins += 1
                    current_amount = base_amount # Reset to base amount on win
                elif profit < 0:
                    consecutive_losses += 1
                    total_losses += 1
                    next_bet = float(current_amount) * 49
                    current_amount = max(base_amount, next_bet)
                else: 
                    consecutive_losses = 0 
                
                # Reset trade tracking after completion
                new_contract_id = None
                trade_start_time = 0.0
                
                # Check for Take Profit or Max Losses after trade completion
                new_balance, _ = get_balance_and_currency(user_token)
                if new_balance is not None:
                    current_balance_float = float(new_balance)
                    if initial_balance == 0.0:
                        initial_balance = current_balance_float
                    
                    # Update DB with final trade result
                    update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=new_contract_id, trade_start_time=trade_start_time)

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
                
                break # Break the while loop if the contract is sold
            
            # If contract is not sold, and we are in check_only mode, wait briefly
            if check_only:
                time.sleep(0.5)

        # --- Place new trade if conditions met ---
        if not check_only and not contract_id: # Place a new trade if no trade is active
            balance, currency = get_balance_and_currency(user_token)
            if balance is None:
                print(f"Failed to get balance for {email}. Skipping trade.")
                return
            if initial_balance == 0:
                initial_balance = float(balance)
                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=None, trade_start_time=None)
                
            # Get latest ticks for analysis
            req = {"ticks_history": TRADING_SYMBOL, "end": "latest", "count": 1, "style": "ticks"}
            
            # --- Ticks History Retrieval Loop with Reconnect ---
            tick_data = None
            while tick_data is None:
                try:
                    ws.send(json.dumps(req))
                    response = json.loads(ws.recv())
                    
                    if response.get('msg_type') == 'history':
                        tick_data = response
                        break
                    elif response.get('error'):
                        print(f"Error getting ticks history for {email}: {response['error']['message']}")
                        return
                except websocket._exceptions.WebSocketConnectionClosedException:
                    print(f"WebSocket closed while waiting for ticks history for {email}. Reconnecting...")
                    ws = connect_websocket(user_token) # Reconnect indefinitely
                    if not ws: return 
                except Exception as e:
                    print(f"Error receiving ticks history for {email}: {e}")
                    return

            if 'history' in tick_data and 'prices' in tick_data['history']:
                ticks = tick_data['history']['prices']
                df_ticks = pd.DataFrame({'price': ticks})
                
                # --- APPLY DIGIT DIFFERS LOGIC ---
                signal, last_digit_prediction, message = analyse_data(df_ticks) 
                print(f"User {email}: Signal = {signal}, Prediction = {last_digit_prediction}, Message = {message}")

                if signal == 'Trade':
                    contract_type = "DIGITDIFF" 
                    barrier_value = last_digit_prediction
                    amount_to_bet = max(0.35, round(float(current_amount), 2)) 

                    proposal_req = {
                        "proposal": 1, "amount": amount_to_bet, "basis": "stake",
                        "contract_type": contract_type, "currency": currency,
                        "duration": 1, "duration_unit": "t", "symbol": TRADING_SYMBOL,
                        "barrier": str(barrier_value)
                    }
                    
                    # --- Proposal Request Loop with Reconnect ---
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
                            
                            # Update DB with new trade info
                            update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=new_contract_id, trade_start_time=trade_start_time)
                        else:
                            print(f"User {email}: Failed to place order. Response: {order_response}")
                    else:
                        print(f"User {email}: No proposal received or error in proposal response. Response: {proposal_response}")
            else:
                print(f"User {email}: No tick history received or unexpected response format: {tick_data}")
    
    except Exception as e:
        print(f"An error occurred in run_trading_job_for_user for {email}: {e}")
    finally:
        if ws and ws.connected:
            ws.close()

# --- Main Bot Loop Function ---
def bot_loop():
    """Main loop that orchestrates trading jobs for all active sessions."""
    print("Bot process started. PID:", os.getpid())
    update_bot_running_status(1, os.getpid())
    
    while True:
        try:
            update_bot_running_status(1, os.getpid())
            active_sessions = get_all_active_sessions()
            
            if active_sessions:
                for session in active_sessions:
                    email = session['email']
                    latest_session_data = get_session_status_from_db(email)
                    
                    if not latest_session_data or latest_session_data.get('is_running') == 0:
                        continue
                        
                    contract_id = latest_session_data.get('contract_id')
                    trade_start_time = latest_session_data.get('trade_start_time')
                    
                    # --- Check and Close Active Trades ---
                    if contract_id:
                        # Safety check: if trade exceeds 5 seconds, force a check.
                        if (time.time() - trade_start_time) >= 5: 
                            run_trading_job_for_user(latest_session_data, check_only=True) 
                        else:
                            time.sleep(0.1) 
                        continue
                        
                    # --- Place New Trades (Continuous Loop) ---
                    re_checked_session_data = get_session_status_from_db(email) 
                    if re_checked_session_data and re_checked_session_data.get('is_running') == 1 and not re_checked_session_data.get('contract_id'):
                        
                        # Apply the 5-second wait period between trades
                        print(f"User {email}: Trade finished. Waiting 5 seconds before next trade...")
                        time.sleep(5)
                        
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
            
    time.sleep(2)
    st.rerun()
