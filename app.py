import streamlit as st
import time
import websocket
import json
import os
import decimal
import sqlite3
import pandas as pd
from datetime import datetime, timezone 
import multiprocessing
import random

# --- SQLite Database Configuration ---
DB_FILE = "trading_data0099.db"
# --- Global Trading Configuration ---
SYMBOL = "R_100" # Volatility 100 Index is preferred for digit trading
CONTRACT_TYPE = "DIGITDIFF" 
DIFFER_PREDICTION = 5 # The bot bets the last digit will NOT be 5
DURATION_VALUE = 1
DURATION_UNIT = "t"
MARTINGALE_MULTIPLIER = 19.0 # Per user request for the "old bot" style (Caution: This is a very high and dangerous multiplier)
TICK_COLLECTION_COUNT = 5 # The number of sequential ticks to collect
COLLECTION_TIMEOUT = 30 # Max wait 30 seconds for 5 ticks

# --- Database & Utility Functions (Functions not changed) ---
def create_connection():
    try:
        conn = sqlite3.connect(DB_FILE)
        return conn
    except sqlite3.Error as e:
        print(f"Error connecting to database: {e}")
        return None

def create_table_if_not_exists():
    conn = create_connection()
    if conn:
        try:
            sql_create_sessions_table = """
            CREATE TABLE IF NOT EXISTS sessions (
                email TEXT PRIMARY KEY, user_token TEXT NOT NULL, base_amount REAL NOT NULL, tp_target REAL NOT NULL, 
                max_consecutive_losses INTEGER NOT NULL, total_wins INTEGER DEFAULT 0, total_losses INTEGER DEFAULT 0, 
                current_amount REAL NOT NULL, consecutive_losses INTEGER DEFAULT 0, initial_balance REAL DEFAULT 0.0,
                contract_id TEXT, trade_start_time REAL DEFAULT 0.0, is_running INTEGER DEFAULT 0,
                is_collecting INTEGER DEFAULT 1, collected_digits_json TEXT DEFAULT '[]' 
            );
            """
            sql_create_bot_status_table = """
            CREATE TABLE IF NOT EXISTS bot_status (
                flag_id INTEGER PRIMARY KEY, is_running_flag INTEGER DEFAULT 0, 
                last_heartbeat REAL DEFAULT 0.0, process_pid INTEGER DEFAULT 0
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
    conn = create_connection()
    if conn:
        try:
            with conn:
                cursor = conn.execute("SELECT is_running_flag, last_heartbeat, process_pid FROM bot_status WHERE flag_id = 1")
                row = cursor.fetchone()
                if row:
                    status, heartbeat, pid = row
                    is_process_alive = False
                    if pid and pid != 0:
                        try:
                            os.kill(pid, 0) 
                            is_process_alive = True
                        except OSError:
                            is_process_alive = False
                    if status == 1:
                        if is_process_alive:
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
            print(f"Database error in get_bot_running_status: {e}")
            return 0
        finally:
            if conn: conn.close()
    return 0

def update_bot_running_status(status, pid):
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
    try:
        with open("user_ids.txt", "r") as file:
            active_users = [line.strip() for line in file.readlines()]
        return email in active_users
    except FileNotFoundError:
        # Create the file if it doesn't exist for the admin to add users
        with open("user_ids.txt", "w") as file:
            pass
        return False
    except Exception as e:
        return False

def start_new_session_in_db(email, settings):
    conn = create_connection()
    if conn:
        try:
            # Initialize analysis state to start collection
            initial_collected_digits_json = json.dumps([])
            
            with conn:
                conn.execute("""
                    INSERT OR REPLACE INTO sessions 
                    (email, user_token, base_amount, tp_target, max_consecutive_losses, current_amount, is_running, is_collecting, collected_digits_json)
                    VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?)
                    """, (email, settings["user_token"], settings["base_amount"], settings["tp_target"], settings["max_consecutive_losses"], settings["base_amount"], initial_collected_digits_json))
        except sqlite3.Error as e:
            print(f"Database error in start_new_session_in_db: {e}")
        finally:
            conn.close()

def update_is_running_status(email, status):
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
    conn = create_connection()
    if conn:
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM sessions WHERE email=?", (email,))
            row = cursor.fetchone()
            if row:
                data = dict(row)
                # Parse collected_digits_json back into a list
                try:
                    data['collected_digits'] = json.loads(data.get('collected_digits_json', '[]'))
                except json.JSONDecodeError:
                    data['collected_digits'] = []
                return data
            return None
        except sqlite3.Error as e:
            print(f"Database error in get_session_status_from_db: {e}")
            return None
        finally:
            conn.close()
    return None

def get_all_active_sessions():
    conn = create_connection()
    if conn:
        try:
            with conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT * FROM sessions WHERE is_running = 1")
                rows = cursor.fetchall()
                sessions = []
                for row in rows:
                    session = dict(row)
                    # Parse collected_digits_json back into a list
                    try:
                        session['collected_digits'] = json.loads(session.get('collected_digits_json', '[]'))
                    except json.JSONDecodeError:
                        session['collected_digits'] = []
                    sessions.append(session)
                return sessions
        except sqlite3.Error as e:
            print(f"Database error in get_all_active_sessions: {e}")
            return []
        finally:
            conn.close()
    return []

def update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=None, contract_id=None, trade_start_time=None, is_collecting=None, collected_digits_json=None):
    conn = create_connection()
    if conn:
        try:
            with conn:
                update_query = """
                UPDATE sessions SET 
                    total_wins = ?, total_losses = ?, current_amount = ?, consecutive_losses = ?, 
                    initial_balance = COALESCE(?, initial_balance), contract_id = ?, trade_start_time = COALESCE(?, trade_start_time),
                    is_collecting = COALESCE(?, is_collecting), collected_digits_json = COALESCE(?, collected_digits_json)
                WHERE email = ?
                """
                conn.execute(update_query, (total_wins, total_losses, current_amount, consecutive_losses, 
                                            initial_balance, contract_id, trade_start_time, is_collecting, collected_digits_json, email))
        except sqlite3.Error as e:
            print(f"Database error in update_stats_and_trade_info_in_db: {e}")
        finally:
            conn.close()

def connect_websocket(user_token):
    ws = websocket.WebSocket()
    try:
        # Use a random app_id to avoid potential blocking issues if 16929 is overused
        app_id = random.randint(10000, 30000) 
        ws.connect(f"wss://blue.derivws.com/websockets/v3?app_id=16929")
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
            # Send forget_all to clean up before closing
            try:
                ws.send(json.dumps({"forget_all": "balance"}))
            except:
                pass 
            ws.close()

def check_contract_status(ws, contract_id):
    if not ws or not ws.connected:
        return None
    req = {"proposal_open_contract": 1, "contract_id": contract_id}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        return response.get('proposal_open_contract')
    except Exception as e:
        print(f"Error checking contract status: {e}")
        return None

def place_order(ws, proposal_id, amount):
    if not ws or not ws.connected:
        return {"error": {"message": "WebSocket not connected."}}
    amount_decimal = decimal.Decimal(str(amount)).quantize(decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_UP)
    req = {"buy": proposal_id, "price": float(amount_decimal)}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        return response
    except Exception as e:
        print(f"Error placing order: {e}")
        return {"error": {"message": "Order placement failed."}}


# --- Trading Bot Logic (Differ 5, 1 Tick, Real-time 5-Tick Analysis) ---
def run_trading_job_for_user(session_data, check_only=False):
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
    is_collecting = session_data.get('is_collecting', 1) 
    collected_digits = session_data.get('collected_digits', []) 
    
    ws = None
    
    try:
        ws = connect_websocket(user_token)
        if not ws:
            print(f"Could not connect WebSocket for {email}")
            return

        # --- Phase 1: Check for completed trades (if contract_id exists) ---
        if contract_id:
            contract_info = check_contract_status(ws, contract_id)
            if contract_info and contract_info.get('is_sold'):
                profit = float(contract_info.get('profit', 0))
                
                # --- After Purchase: Update Stats & Martingale ---
                if profit > 0:
                    print(f"User {email}: ✅ WIN! Profit: {profit:.2f}")
                    consecutive_losses = 0
                    total_wins += 1
                    current_amount = base_amount # Reset to base amount
                elif profit < 0:
                    print(f"User {email}: ❌ LOSS! Loss: {profit:.2f}")
                    consecutive_losses += 1
                    total_losses += 1
                    # Martingale logic (Factor 19 requested)
                    next_bet = float(current_amount) * MARTINGALE_MULTIPLIER 
                    current_amount = max(base_amount, next_bet)
                else: 
                    consecutive_losses = 0 
                
                # After any trade, reset the collection process
                is_collecting = 1
                collected_digits = []
                new_contract_id = None
                trade_start_time = 0.0
                
                collected_digits_json = json.dumps(collected_digits)
                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, 
                                                   initial_balance=initial_balance, contract_id=new_contract_id, 
                                                   trade_start_time=trade_start_time, is_collecting=is_collecting, 
                                                   collected_digits_json=collected_digits_json)

                # Check for Take Profit or Max Losses after trade completion
                new_balance, _ = get_balance_and_currency(user_token)
                if new_balance is not None:
                    current_balance_float = float(new_balance)
                    
                    if initial_balance == 0.0:
                        initial_balance = current_balance_float
                        update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, 
                                                           initial_balance=initial_balance, contract_id=new_contract_id, 
                                                           trade_start_time=trade_start_time, is_collecting=is_collecting, 
                                                           collected_digits_json=collected_digits_json)
                        
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

            if contract_id and not (contract_info and contract_info.get('is_sold')):
                # Trade is still active
                return

        # --- Phase 2: Collect New Ticks Sequentially (Logic for user's request) ---
        if is_collecting == 1:
            print(f"User {email}: 🕒 Starting new collection cycle for {TICK_COLLECTION_COUNT} sequential ticks on {SYMBOL}.")
            
            # 1. Subscribe to the tick stream
            # Forget all previous subscriptions (especially ticks) before subscribing to the new one
            ws.send(json.dumps({"forget_all": "ticks"})) 
            
            req_ticks = {"ticks": SYMBOL}
            ws.send(json.dumps(req_ticks))
            
            collected_digits = []
            start_time = time.time()
            
            # Block and wait for 5 new ticks in real-time
            while len(collected_digits) < TICK_COLLECTION_COUNT and (time.time() - start_time) < COLLECTION_TIMEOUT:
                try:
                    response_str = ws.recv()
                    response = json.loads(response_str)
                    
                    if response.get('msg_type') == 'tick':
                        price_str = response['tick']['quote']
                        try:
                            # Get the last digit of the price
                            last_digit = int(price_str[-1])
                            collected_digits.append(last_digit)
                            print(f"User {email}: Ticks collected: {len(collected_digits)}/{TICK_COLLECTION_COUNT}. Last digit: {last_digit}")
                        except (ValueError, IndexError):
                            continue
                    
                except websocket._exceptions.WebSocketConnectionClosedException:
                    print(f"User {email}: WebSocket closed during tick collection.")
                    return # Exit and rely on the next loop to reconnect
                except Exception as e:
                    # Handle JSON decode errors or other unexpected issues gracefully
                    continue 

            # 2. Unsubscribe (forget) ticks before proceeding
            ws.send(json.dumps({"forget_all": "ticks"}))
            
            # 3. Analyze the collected ticks
            if len(collected_digits) == TICK_COLLECTION_COUNT:
                # Check the condition: if 5 is NOT in the collected digits
                if DIFFER_PREDICTION not in collected_digits:
                    print(f"User {email}: 🟢 Analysis Complete. {TICK_COLLECTION_COUNT} Ticks collected. No '{DIFFER_PREDICTION}' found. READY TO TRADE.")
                    is_collecting = 0 # Ready to trade
                else:
                    print(f"User {email}: 🔴 Analysis Complete. '{DIFFER_PREDICTION}' found in {collected_digits}. Restarting collection in next cycle.")
                    is_collecting = 1 # Keep collecting (ready to start a new 5-tick sequence next cycle)
                    collected_digits = [] # Clear the failed collection
            else:
                print(f"User {email}: ⚠ Failed to collect {TICK_COLLECTION_COUNT} ticks within timeout. Collected {len(collected_digits)}/{TICK_COLLECTION_COUNT}. Retrying.")
                # Keep is_collecting = 1 and try again in the next loop cycle
            
            collected_digits_json = json.dumps(collected_digits)
            update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, 
                                               initial_balance=initial_balance, contract_id=contract_id, 
                                               trade_start_time=session_data.get('trade_start_time'), 
                                               is_collecting=is_collecting, collected_digits_json=collected_digits_json)
            return # Always return after analysis/collection phase

        
        # --- Phase 3: Place a new trade (is_collecting == 0) ---
        if not check_only and not contract_id and is_collecting == 0:
            
            # Re-check condition before placing trade (safety)
            if DIFFER_PREDICTION in collected_digits:
                 # Should not happen if previous logic worked, but if it does, reset and do not trade
                print(f"User {email}: ⚠ Safety Check Failed. {DIFFER_PREDICTION} found. Resetting state.")
                is_collecting = 1
                collected_digits = []
                collected_digits_json = json.dumps(collected_digits)
                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, 
                                                   initial_balance=initial_balance, contract_id=contract_id, 
                                                   trade_start_time=session_data.get('trade_start_time'), 
                                                   is_collecting=is_collecting, collected_digits_json=collected_digits_json)
                return

            # Proceed to trade only if condition is met
            balance, currency = get_balance_and_currency(user_token)
            if balance is None:
                print(f"Failed to get balance for {email}. Skipping trade.")
                return
            if initial_balance == 0:
                initial_balance = float(balance)
                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=None, trade_start_time=None)
            
            
            amount_to_bet = max(0.35, round(float(current_amount), 2))
            
            # --- DIFFER 5 CONFIGURATION (1 TICK) ---
            print(f"User {email}: Preparing to place {CONTRACT_TYPE} trade with amount {amount_to_bet} on {SYMBOL}.")

            # 1. Get proposal for the trade
            proposal_req = {
                "proposal": 1, "amount": amount_to_bet, "basis": "stake",
                "contract_type": CONTRACT_TYPE, "currency": currency,
                "duration": DURATION_VALUE, "duration_unit": DURATION_UNIT, 
                "symbol": SYMBOL, 
                "digit_prediction": DIFFER_PREDICTION 
            }
            
            # Forget all proposals before sending a new one (Deriv API requirement)
            ws.send(json.dumps({"forget_all": "proposal"}))
            ws.send(json.dumps(proposal_req))
            
            time.sleep(0.05) # Small delay for API response
            
            proposal_response = None
            start_wait = time.time()
            
            # Attempt to read proposal response
            while proposal_response is None and (time.time() - start_wait < 5): # 5 second timeout
                try:
                    response_str = ws.recv()
                    if response_str:
                        response = json.loads(response_str)
                        
                        if response.get('error'):
                            print(f"\n🚨🚨 PROPOSAL ERROR for {email}: 🚨🚨")
                            print(f"    Request Sent: {proposal_req}")
                            print(f"    Error Response: {response['error']}")
                            return
                            
                        if response.get('msg_type') == 'proposal':
                             proposal_response = response
                             break
                        
                except websocket._exceptions.WebSocketConnectionClosedException:
                    print(f"WebSocket closed while waiting for proposal for {email}")
                    return
                except Exception as e:
                    # Catch and ignore non-JSON/unwanted messages from API
                    pass

            if proposal_response and 'proposal' in proposal_response:
                print(f"User {email}: ✅ Proposal Received! ID: {proposal_response['proposal']['id']}")
                
                proposal_id = proposal_response['proposal']['id']
                
                # 2. Place the order
                order_response = place_order(ws, proposal_id, amount_to_bet)
                
                if order_response.get('error'):
                    print(f"\n🚨🚨 ORDER (BUY) ERROR for {email}: 🚨🚨")
                    print(f"    Error Response: {order_response['error']}")
                    return
                
                if 'buy' in order_response and 'contract_id' in order_response['buy']:
                    new_contract_id = order_response['buy']['contract_id']
                    trade_start_time = time.time()
                    print(f"User {email}: Placed trade {new_contract_id} (Stake {amount_to_bet:.2f}).")
                    
                    # Reset collected_digits upon successful trade, ready for next collection
                    is_collecting = 1
                    collected_digits = []
                    
                    collected_digits_json = json.dumps(collected_digits)
                    update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, 
                                                       initial_balance=initial_balance, contract_id=new_contract_id, 
                                                       trade_start_time=trade_start_time, is_collecting=is_collecting, 
                                                       collected_digits_json=collected_digits_json)
                else:
                    print(f"User {email}: Failed to place order (Unknown issue). Full response: {order_response}")
            else:
                print(f"User {email}: No proposal received or error in proposal response.")
    
    except websocket._exceptions.WebSocketConnectionClosedException:
        print(f"WebSocket connection lost for user {email}. Will try to reconnect next iteration.")
        if contract_id:
             update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=contract_id, trade_start_time=session_data.get('trade_start_time'))

    except Exception as e:
        print(f"An error occurred in run_trading_job_for_user for {email}: {e}")
    finally:
        if ws and ws.connected:
            # Clean up all subscriptions before closing
            try:
                ws.send(json.dumps({"forget_all": "all"}))
            except:
                pass 
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
                    trade_start_time = latest_session_data.get('trade_start_time', 0.0)
                    is_collecting = latest_session_data.get('is_collecting', 1)
                    
                    # 1. Check/close active trades 
                    if contract_id:
                        # Check status only after a short delay (1-tick trade duration is very fast, check after 2 seconds)
                        if (time.time() - trade_start_time) >= 2: 
                             run_trading_job_for_user(latest_session_data, check_only=True)
                    # 2. Perform collection/analysis or place trade if ready
                    elif is_collecting == 1 or (is_collecting == 0 and not contract_id):
                        run_trading_job_for_user(latest_session_data, check_only=False) 
            
            # Sleep for a short duration
            time.sleep(0.5) 
            
        except Exception as e:
            print(f"Error in bot_loop main loop: {e}. Sleeping for 5 seconds before retrying.")
            time.sleep(5)

# --- Streamlit App Configuration ---
st.set_page_config(page_title="Khoury Bot (Differ 5 - Sequential Tick Analysis)", layout="wide")
st.title("Khoury Bot 🤖 (Differ 5 Strategy)")
st.caption(f"Trading on *{SYMBOL}, **{DURATION_VALUE} {DURATION_UNIT}* duration, using *{CONTRACT_TYPE} {DIFFER_PREDICTION}* with Martingale Multiplier *{MARTINGALE_MULTIPLIER}x*.")

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "user_email" not in st.session_state:
    st.session_state.user_email = ""
if "stats" not in st.session_state:
    st.session_state.stats = None
    
create_table_if_not_exists()

bot_status_from_db = get_bot_running_status()

if bot_status_from_db == 0:
    try:
        print("Attempting to start bot process...")
        # Check if the process needs a fresh start
        update_bot_running_status(0, 0) 
        bot_process = multiprocessing.Process(target=bot_loop, daemon=True)
        bot_process.start()
        print(f"Bot process started with PID: {bot_process.pid}")
        time.sleep(1) 
        st.rerun()
    except Exception as e:
        st.error(f"❌ Error starting bot process: {e}")
else:
    print("Bot process is already running (status from DB).")

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

if st.session_state.logged_in:
    st.markdown("---")
    st.subheader(f"Welcome, {st.session_state.user_email}")
    
    stats_data = get_session_status_from_db(st.session_state.user_email)
    st.session_state.stats = stats_data
    
    is_user_bot_running_in_db = False
    is_collecting_state = True
    
    if st.session_state.stats:
        is_user_bot_running_in_db = st.session_state.stats.get('is_running', 0) == 1
        is_collecting_state = st.session_state.stats.get('is_collecting', 1) == 1
        
    global_bot_status = get_bot_running_status() 

    with st.form("settings_and_control"):
        st.subheader("Bot Settings and Control")
        user_token_val = ""
        base_amount_val = 5.0 # Increased default base amount to 5.0 to match typical Differ trades
        tp_target_val = 10.0
        max_consecutive_losses_val = 3
        
        if st.session_state.stats:
            user_token_val = st.session_state.stats.get('user_token', '')
            base_amount_val = st.session_state.stats.get('base_amount', 5.0)
            tp_target_val = st.session_state.stats.get('tp_target', 10.0)
            max_consecutive_losses_val = st.session_state.stats.get('max_consecutive_losses', 3)
        
        user_token = st.text_input("Deriv API Token", type="password", value=user_token_val, disabled=is_user_bot_running_in_db)
        base_amount = st.number_input("Base Bet Amount (First Stake)", min_value=0.35, value=base_amount_val, step=0.1, disabled=is_user_bot_running_in_db)
        st.warning(f"Note: Martingale Multiplier is fixed at {MARTINGALE_MULTIPLIER}x per your request (Highly Risky).")
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
            st.success("✅ Bot session started successfully! Initializing tick collection.")
            st.rerun()

    if stop_button:
        update_is_running_status(st.session_state.user_email, 0)
        st.info("⏸ Your bot session has been stopped. To fully reset stats, click start again.")
        st.rerun()

    st.markdown("---")
    st.subheader("Statistics")

    stats_placeholder = st.empty()
    
    if global_bot_status == 1:
        st.success(f"🟢 Global Bot Service is RUNNING.")
    else:
        st.error("🔴 Global Bot Service is STOPPED or Crashed.")

    if st.session_state.user_email:
        session_data = get_session_status_from_db(st.session_state.user_email)
        if session_data:
            user_token_for_balance = session_data.get('user_token')
            balance, currency = get_balance_and_currency(user_token_for_balance)
            if balance is not None:
                st.metric(label=f"Current Balance ({currency or 'USD'})", value=f"${float(balance):.2f}")

    if st.session_state.stats:
        with stats_placeholder.container():
            stats = st.session_state.stats
            col1, col2, col3, col4, col5 = st.columns(5)
            
            # --- Status Indicator ---
            if is_user_bot_running_in_db:
                if stats.get('contract_id'):
                    status_text = "⚠ Trade Active"
                    status_color = 'orange'
                elif is_collecting_state:
                    status_text = "🕒 Collecting Ticks..."
                    status_color = 'blue'
                else:
                    status_text = "🟢 Ready to Trade"
                    status_color = 'green'
            else:
                status_text = "🔴 Stopped"
                status_color = 'red'

            st.markdown(f"*Bot State:* <span style='color:{status_color}; font-size: 20px;'>{status_text}</span>", unsafe_allow_html=True)
            
            with col1:
                st.metric(label="Current Bet Amount", value=f"${stats.get('current_amount', 0.0):.2f}")
            with col2:
                initial_balance = stats.get('initial_balance', 0.0)
                current_balance_float = float(balance or 0.0)
                current_profit = current_balance_float - initial_balance if initial_balance > 0 else 0.0
                st.metric(label="Net Profit/Loss", value=f"${current_profit:.2f}", delta=f"{current_profit:.2f}")

            with col3:
                st.metric(label="Total Wins", value=stats.get('total_wins', 0))
            with col4:
                st.metric(label="Total Losses", value=stats.get('total_losses', 0))
            with col5:
                st.metric(label="Consecutive Losses", value=stats.get('consecutive_losses', 0), delta=f"-{stats.get('max_consecutive_losses', 0) - stats.get('consecutive_losses', 0)} to Stop")
            
            
            # --- Collection Status ---
            if is_user_bot_running_in_db and is_collecting_state:
                digits_collected = stats.get('collected_digits', [])
                st.info(f"Analysis: Collected {len(digits_collected)} out of {TICK_COLLECTION_COUNT} sequential digits. Digits: {digits_collected}")
                
            
    else:
        with stats_placeholder.container():
            st.info("Your bot session is currently stopped or not yet configured.")
            
    time.sleep(1)
    st.rerun()
