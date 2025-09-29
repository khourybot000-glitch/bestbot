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

# --- GLOBAL CONFIGURATION (MODIFIED) ---
DB_FILE = "trading_data0099.db"
TRADING_SYMBOL = "R_100"       
TRADE_DURATION = 2             # Changed from 5 ticks to 2 ticks
ANALYSIS_COUNT = 2             # Changed from 5 ticks to 2 ticks for analysis

# --- Database & Utility Functions (Unchanged) ---

def create_connection():
    """Create a database connection to the SQLite database specified by DB_FILE"""
    try:
        conn = sqlite3.connect(DB_FILE)
        return conn
    except sqlite3.Error as e:
        # print(f"Database connection error: {e}") 
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
            print(f"Database error during table creation: {e}") 
        finally:
            conn.close()

def get_bot_running_status():
    """Gets the global bot running status from the database. Also checks for process liveness and timeouts."""
    conn = create_connection()
    if conn:
        try:
            with conn:
                cursor = conn.execute("SELECT is_running_flag, last_heartbeat, process_pid FROM bot_status WHERE flag_id = 1")
                row = cursor.fetchone()
                if row:
                    status, heartbeat, pid = row
                    
                    if status == 1:
                        # Simple check if the heartbeat is too old
                        if (time.time() - heartbeat > 30): # Heartbeat timeout (30 seconds)
                            # print(f"Bot process {pid} timed out. Marking as stopped.")
                            update_bot_running_status(0, 0) # Mark as stopped if no heartbeat
                            return 0
                        return status # Assume running if heartbeat is recent
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

def is_user_active(email):
    """Checks if a user's email exists in the user_ids.txt file."""
    try:
        # Assuming user_ids.txt is available
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

# --- WebSocket Helper Functions (Unchanged) ---
def connect_websocket(user_token):
    """Establishes a WebSocket connection and authenticates the user."""
    ws = websocket.WebSocket()
    try:
        # Use a reliable Deriv endpoint
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
        # print(f"Error connecting to WebSocket: {e}")
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
        # print(f"Error getting balance: {e}")
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
        response = json.loads(ws.recv())
        return response.get('proposal_open_contract')
    except Exception as e:
        # print(f"Error checking contract status: {e}")
        return None

def place_order(ws, proposal_id, amount):
    """Places a trade order on Deriv."""
    if not ws or not ws.connected:
        return {"error": {"message": "WebSocket not connected."}}
    # Ensure amount is rounded correctly for the API
    amount_decimal = decimal.Decimal(str(amount)).quantize(decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_UP)
    req = {"buy": proposal_id, "price": float(amount_decimal)}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        return response
    except Exception as e:
        print(f"Error placing order: {e}")
        return {"error": {"message": "Order placement failed."}}

# --- Trading Bot Logic (MODIFIED) ---
def analyse_data(df_ticks):
    """
    Analyzes tick data to generate a trading signal based on the last 2 ticks trend.
    """
    # Need at least 2 ticks for the analysis
    if len(df_ticks) < ANALYSIS_COUNT:
        return "Neutral", "Insufficient data. Need at least 2 ticks."

    # Use the last 2 ticks
    last_2_ticks = df_ticks.tail(ANALYSIS_COUNT).copy()

    # Determine the trend based on the first and last price in the 2 ticks window
    first_price = last_2_ticks.iloc[0]['price']
    last_price = last_2_ticks.iloc[-1]['price']

    signal = "Neutral"
    message = "No clear trend detected on the last 2 ticks."

    if last_price > first_price:
        signal = "Buy"
        message = "Detected an uptrend on the last 2 ticks."
    elif last_price < first_price:
        signal = "Sell"
        message = "Detected a downtrend on the last 2 ticks."
    
    return signal, message


def run_trading_job_for_user(session_data, check_only=False):
    """Executes the trading logic for a specific user's session with immediate reconnection logic."""
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
        ws = connect_websocket(user_token)
        if not ws:
            # print(f"Could not connect WebSocket for {email}")
            return
            
        # --- Helper function for receiving data with immediate reconnection ---
        def safe_ws_recv(ws_obj, email, user_token):
            """Receives data from WebSocket, attempting immediate reconnection on failure."""
            nonlocal ws # Allow modification of the outer 'ws' variable
            try:
                # Attempt to receive data
                response_str = ws_obj.recv()
                if response_str:
                    return json.loads(response_str)
            except websocket._exceptions.WebSocketConnectionClosedException:
                print(f"User {email}: WebSocket closed. Attempting immediate reconnection.")
                ws_obj.close()
                new_ws = connect_websocket(user_token)
                if new_ws:
                    print(f"User {email}: Successfully reconnected.")
                    ws = new_ws # Update the main ws object to the new connection
                    # After reconnecting, we should retry the receive operation in the main loop
                    raise ConnectionRefusedError("Reconnected, please retry operation.")
                else:
                    print(f"User {email}: Failed to reconnect permanently.")
                    raise ConnectionRefusedError("Permanent connection failure.")
            except Exception as e:
                # Handle non-connection errors (e.g. JSON decode error)
                print(f"User {email}: Error during ws.recv or JSON load: {e}.")
                return None
            return None # Should be handled by the outer loop if response_str is None

        # --- Check for completed trades (if contract_id exists) ---
        if contract_id: 
            # Reconnection logic is inside safe_ws_recv, but we use check_contract_status here.
            # We must wrap the call to check_contract_status with a retry loop manually
            contract_info = None
            for _ in range(3): # Max 3 retries for contract status check
                try:
                    # check_contract_status sends a message and waits for one response.
                    # If it fails, the outer try/except catches the error.
                    contract_info = check_contract_status(ws, contract_id)
                    if contract_info:
                        break # Success
                except Exception as e:
                    print(f"User {email}: Error during contract status check. Attempting reconnect.")
                    ws.close()
                    new_ws = connect_websocket(user_token)
                    if new_ws:
                        ws = new_ws # Update ws
                        continue # Retry contract check
                    else:
                        print(f"User {email}: Failed to reconnect for contract check.")
                        return # Exit the function
            
            if contract_info and contract_info.get('is_sold'): # Trade has finished
                # ... (Martingale/Stats Logic - UNCHANGED) ...
                profit = float(contract_info.get('profit', 0))
                
                # Martingale/Stats Logic
                if profit > 0:
                    consecutive_losses = 0
                    total_wins += 1
                    current_amount = base_amount # Reset to base amount on win
                elif profit < 0:
                    consecutive_losses += 1
                    total_losses += 1
                    next_bet = float(current_amount) * 2.1  # Martingale factor
                    current_amount = max(base_amount, next_bet)

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
                    
                    # Take Profit Check
                    if (current_balance_float - initial_balance) >= float(tp_target):
                        print(f"User {email} reached Take Profit target. Stopping session.")
                        update_is_running_status(email, 0)
                        clear_session_data(email)   
                        return
                    
                    # Max Losses Check
                    if consecutive_losses >= max_consecutive_losses:
                        print(f"User {email} reached Max Consecutive Losses. Stopping session.")
                        update_is_running_status(email, 0)
                        clear_session_data(email)
                        return
            
            return # If check_only=True and contract is still open, we exit here.

        # --- If not in check_only mode and no trade is active, proceed to place a new trade ---
        if not check_only and not contract_id: 
            balance, currency = get_balance_and_currency(user_token)
            if balance is None:
                print(f"Failed to get balance for {email}. Skipping trade.")
                return
            if initial_balance == 0: 
                initial_balance = float(balance)
                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=None, trade_start_time=None)
            
            # 1. (MODIFIED) Subscribe to Ticks and get initial history (need 2 ticks)
            req = {"ticks_history": TRADING_SYMBOL, "end": "latest", "count": ANALYSIS_COUNT, "subscribe": 1, "style": "ticks"}
            ws.send(json.dumps(req))
            
            tick_data = None
            
            # Wait for initial history response and the first new tick
            while True:
                response = None
                
                # --- Immediate Reconnection Logic ---
                try:
                    response_str = ws.recv()
                    if response_str:
                        response = json.loads(response_str)
                except websocket._exceptions.WebSocketConnectionClosedException:
                    print(f"User {email}: WebSocket closed during tick reception. Attempting immediate reconnection.")
                    ws.close()
                    new_ws = connect_websocket(user_token)
                    if new_ws:
                        print(f"User {email}: Successfully reconnected. Retrying tick reception.")
                        ws = new_ws 
                        # Re-send the subscription request after reconnecting
                        ws.send(json.dumps(req))
                        continue 
                    else:
                        print(f"User {email}: Failed to reconnect permanently for ticks.")
                        return 
                except Exception as e:
                    print(f"User {email}: Error receiving tick: {e}")
                    return 
                # --- End Reconnection Logic ---


                if response is None:
                    continue # Try receiving again if response was empty or error occurred

                # Response is the initial history
                if response.get('msg_type') == 'history':
                    tick_data = response
                    
                # Response is a live tick
                elif response.get('msg_type') == 'tick':
                    current_tick_price = response['tick']['quote']
                    
                    # 2. (REMOVED CONDITION) Removed the 'price ends with 0' condition. Trade immediately on the first received tick after history.
                    
                    if 'history' in tick_data and 'prices' in tick_data['history']:
                        # Add the current tick to the history list for analysis
                        tick_data['history']['prices'].append(current_tick_price)
                        # Keep only the required number of ticks
                        if len(tick_data['history']['prices']) > ANALYSIS_COUNT:
                            tick_data['history']['prices'] = tick_data['history']['prices'][-ANALYSIS_COUNT:]
                        
                        # Once we have history data, and a new tick, we can proceed.
                        if len(tick_data['history']['prices']) >= ANALYSIS_COUNT:
                            break # Exit loop and place trade
                        
                    
                elif response.get('error'):
                    print(f"Error getting ticks/history for {email}: {response['error']['message']}")
                    ws.send(json.dumps({"forget_all": "ticks"})) # Forget subscription
                    return
            
            # 3. Stop subscription immediately after we have the required ticks
            ws.send(json.dumps({"forget_all": "ticks"}))
            
            if 'history' in tick_data and 'prices' in tick_data['history']:
                ticks = tick_data['history']['prices']
                df_ticks = pd.DataFrame({'price': ticks})
                
                # Analysis (on 2 ticks)
                signal, message = analyse_data(df_ticks)
                print(f"User {email}: Signal = {signal}, Message = {message}")

                if signal in ['Buy', 'Sell']:
                    contract_type = "CALL" if signal == 'Buy' else "PUT"
                    amount_to_bet = max(0.35, round(float(current_amount), 2)) 

                    # 4. (MODIFIED) Get proposal for the trade on R_100 for 2 ticks
                    proposal_req = {
                        "proposal": 1, "amount": amount_to_bet, "basis": "stake",
                        "contract_type": contract_type, "currency": currency,
                        "duration": TRADE_DURATION, "duration_unit": "t", "symbol": TRADING_SYMBOL
                    }
                    ws.send(json.dumps(proposal_req))
                    
                    proposal_response = None
                    # Wait for proposal response with immediate reconnection
                    while proposal_response is None:
                        response = None
                        # --- Immediate Reconnection Logic ---
                        try:
                            response_str = ws.recv()
                            if response_str:
                                response = json.loads(response_str)
                        except websocket._exceptions.WebSocketConnectionClosedException:
                            print(f"User {email}: WebSocket closed during proposal reception. Attempting immediate reconnection.")
                            ws.close()
                            new_ws = connect_websocket(user_token)
                            if new_ws:
                                print(f"User {email}: Successfully reconnected. Re-sending proposal request.")
                                ws = new_ws 
                                ws.send(json.dumps(proposal_req)) # Re-send proposal request
                                continue 
                            else:
                                print(f"User {email}: Failed to reconnect permanently for proposal.")
                                return 
                        except Exception as e:
                            print(f"Error receiving proposal for {email}: {e}")
                            return 
                        # --- End Reconnection Logic ---
                        
                        if response:
                            proposal_response = response
                            if proposal_response.get('error'):
                                print(f"Error getting proposal for {email}: {proposal_response['error']['message']}")
                                return
                            if 'proposal' in proposal_response:
                                break 
                    

                    if proposal_response and 'proposal' in proposal_response:
                        proposal_id = proposal_response['proposal']['id']
                        # Place the order
                        order_response = place_order(ws, proposal_id, amount_to_bet)
                        
                        if 'buy' in order_response and 'contract_id' in order_response['buy']:
                            new_contract_id = order_response['buy']['contract_id']
                            trade_start_time = time.time()
                            print(f"User {email}: Placed trade {new_contract_id} ({contract_type} for {TRADE_DURATION}T) with stake {amount_to_bet:.2f} on {TRADING_SYMBOL}.")
                            # Update DB with new trade info
                            update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=new_contract_id, trade_start_time=trade_start_time)
                        else:
                            print(f"User {email}: Failed to place order. Response: {order_response}")
                    else:
                        print(f"User {email}: No proposal received or error in proposal response. Response: {proposal_response}")
                else:
                    print(f"User {email}: No trade signal generated.")
            else:
                print(f"User {email}: No tick history received or unexpected response format: {tick_data}")
    
    except websocket._exceptions.WebSocketConnectionClosedException:
        print(f"WebSocket connection lost for user {email} (Outer Catch). Will try to reconnect in next loop.")
        if contract_id: 
             update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=contract_id, trade_start_time=session_data.get('trade_start_time'))

    except Exception as e:
        print(f"An error occurred in run_trading_job_for_user for {email}: {e}")
    finally:
        if ws and ws.connected:
            try:
                ws.send(json.dumps({"forget_all": "ticks"})) 
            except:
                pass 
            ws.close()

# --- Main Bot Loop Function (Unchanged) ---
def bot_loop():
    """Main loop that orchestrates trading jobs for all active sessions."""
    print("Bot process started. PID:", os.getpid())
    update_bot_running_status(1, os.getpid()) # Mark as running with current PID
    
    while True:
        try:
            now = datetime.now()
            
            # Update heartbeat to show this process is alive and well
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
                    
                    # --- Logic to check and close active trades ---
                    if contract_id:
                        # Allow a maximum of 5 seconds for a 2-tick trade to be checked and processed
                        if (time.time() - trade_start_time) >= 5: 
                             print(f"User {email}: Trade {contract_id} time limit exceeded, checking status...")
                        
                        run_trading_job_for_user(latest_session_data, check_only=True) # check_only=True to only process completed trades and stop criteria
                    
                    # --- Logic to place new trades ---
                    elif not contract_id:
                        re_checked_session_data = get_session_status_from_db(email) 
                        if re_checked_session_data and re_checked_session_data.get('is_running') == 1 and not re_checked_session_data.get('contract_id'):
                            # check_only=False ensures it will attempt to place a new trade immediately
                            run_trading_job_for_user(re_checked_session_data, check_only=False) 
            
            time.sleep(1) # Wait for 1 second before the next iteration
        except Exception as e:
            print(f"Error in bot_loop main loop: {e}. Sleeping for 5 seconds before retrying.")
            time.sleep(5) 

# --- Streamlit App Configuration (Unchanged) ---
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
        # Start the bot loop in a separate process
        bot_process = multiprocessing.Process(target=bot_loop, daemon=True)
        bot_process.start()
        # Update DB with new status and PID
        update_bot_running_status(1, bot_process.pid)
        print(f"Bot process started with PID: {bot_process.pid}")
    except Exception as e:
        st.error(f"❌ Error starting bot process: {e}")
else:
    # Bot is already running 
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
    st.subheader(f"Welcome, {st.session_state.user_email} (Trading on {TRADING_SYMBOL} for {TRADE_DURATION} Ticks)")
    
    # Fetch current session data for the logged-in user
    stats_data = get_session_status_from_db(st.session_state.user_email)
    st.session_state.stats = stats_data
    
    is_user_bot_running_in_db = False
    if st.session_state.stats:
        is_user_bot_running_in_db = st.session_state.stats.get('is_running', 0) == 1
    
    # Check bot global status
    global_bot_status = get_bot_running_status() 
    
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
            st.success("✅ Bot session started successfully! It will now place trades based on the 2-tick trend.")
            st.rerun()

    if stop_button:
        update_is_running_status(st.session_state.user_email, 0) # Mark session as not running
        st.info("⏸ Your bot session has been stopped.")
        st.rerun()

    st.markdown("---")
    st.subheader("Statistics")

    stats_placeholder = st.empty()
    
    # Display bot's overall status from DB
    current_global_bot_status = get_bot_running_status()
    if current_global_bot_status == 1:
        st.success("🟢 Global Bot Service is RUNNING.")
    else:
        st.error("🔴 Global Bot Service is STOPPED.")

    # Display Balance
    if st.session_state.user_email:
        session_data = get_session_status_from_db(st.session_state.user_email)
        if session_data:
            user_token_for_balance = session_data.get('user_token')
            # Check if user_token is set before fetching balance (to avoid errors)
            if user_token_for_balance and len(user_token_for_balance) > 10: 
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
                # Calculate current profit if initial balance is set
                initial_balance_val = stats.get('initial_balance', 0.0)
                if initial_balance_val > 0 and balance is not None:
                    profit = float(balance) - initial_balance_val
                    st.metric(label="Total Profit/Loss", value=f"${profit:.2f}")
                else:
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
    time.sleep(2) 
    st.rerun()
