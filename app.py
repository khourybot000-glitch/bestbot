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
import re

# --- Configuration Constants (UPDATED) ---
MARTINGALE_MULTIPLIER = 15.0
SYMBOL = "R_75"
CONTRACT_TYPE = "DIGITDIFF" 
BARRIER = 5 
TICKS_COUNT = 10
APP_ID = 16929 

# --- SQLite Database Configuration ---
DB_FILE = "trading_data0099.db"

# --- Database & Utility Functions (Functions not changed) ---
def create_connection():
    try:
        conn = sqlite3.connect(DB_FILE)
        return conn
    except sqlite3.Error as e:
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
                contract_id TEXT, trade_start_time REAL DEFAULT 0.0, is_running INTEGER DEFAULT 0 
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
        # افتراضي: إذا كان الملف غير موجود، نعتبره نشطًا مؤقتًا
        with open("user_ids.txt", "r") as file:
            active_users = [line.strip() for line in file.readlines()]
        return email in active_users
    except FileNotFoundError:
        return True 

def start_new_session_in_db(email, settings):
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
                conn.execute("""
                    UPDATE sessions SET is_running = 0, contract_id = NULL, trade_start_time = 0.0 
                    WHERE email=?
                """, (email,))
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
                return dict(row)
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
                    sessions.append(dict(row))
                return sessions
        except sqlite3.Error as e:
            print(f"Database error in get_all_active_sessions: {e}")
            return []
        finally:
            conn.close()
    return []

def update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=None, contract_id=None, trade_start_time=None):
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

def connect_websocket(user_token):
    ws = websocket.WebSocket()
    try:
        ws.connect(f"wss://blue.derivws.com/websockets/v3?app_id={APP_ID}")
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
        while True:
            response_str = ws.recv()
            response = json.loads(response_str)
            if response.get('msg_type') == 'balance':
                balance_info = response.get('balance', {})
                return balance_info.get('balance'), balance_info.get('currency')
            if response.get('error'):
                print(f"Error getting balance: {response['error']}")
                return None, None
    except Exception as e:
        return None, None
    finally:
        if ws and ws.connected:
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

# --- Trading Bot Logic (APPLYING NEW STRATEGY) ---
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
    
    ws = None
    try:
        ws = connect_websocket(user_token)
        if not ws:
            print(f"Could not connect WebSocket for {email}")
            return

        # --- Check for completed trades (if contract_id exists) ---
        if contract_id:
            contract_info = check_contract_status(ws, contract_id)
            if contract_info and contract_info.get('is_sold'):
                profit = float(contract_info.get('profit', 0))
                
                # Update Stats
                if profit > 0:
                    consecutive_losses = 0
                    total_wins += 1
                    current_amount = base_amount 
                elif profit < 0:
                    consecutive_losses += 1
                    total_losses += 1
                    # تحديث منطق Martingale: x15.0
                    next_bet = float(current_amount) * MARTINGALE_MULTIPLIER 
                    current_amount = max(base_amount, next_bet)
                else: 
                    consecutive_losses = 0 
                    
                new_contract_id = None
                trade_start_time = 0.0
                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=new_contract_id, trade_start_time=trade_start_time)

                # Check for Take Profit or Max Losses after trade completion
                new_balance, _ = get_balance_and_currency(user_token) 
                if new_balance is not None:
                    current_balance_float = float(new_balance)
                    
                    if initial_balance == 0.0:
                        initial_balance = current_balance_float
                        update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=new_contract_id, trade_start_time=trade_start_time)
                    
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
                return


        # --- Place a new trade ---
        if not check_only and not contract_id: 
            
            ws.close()
            ws = connect_websocket(user_token)
            if not ws: return

            balance, currency = get_balance_and_currency(user_token) 
            if balance is None:
                print(f"Failed to get balance for {email}. Skipping trade.")
                return
            if initial_balance == 0:
                initial_balance = float(balance)
                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=None, trade_start_time=None)
            
            amount_to_bet = max(0.35, round(float(current_amount), 2))
            
            # 1. منطق فحص التيكات الجديد (مُدمج مباشرة)
            ticks_history_req = {
              "ticks_history": SYMBOL, # R_75
              "end": "latest",
              "count": TICKS_COUNT, # 10
              "style": "ticks",
              "adjust_start_time": 1
            }
            ws.send(json.dumps(ticks_history_req))

            ticks_response = None
            start_wait = time.time()
            while ticks_response is None and (time.time() - start_wait < 5):
                try:
                    response_str = ws.recv()
                    if response_str:
                        data = json.loads(response_str)
                        if data.get('msg_type') == 'history':
                            ticks_response = data
                            break
                        if data.get('msg_type') in ['proposal', 'balance', 'ping']: continue 
                except Exception:
                    break

            fives_count = 0
            if ticks_response and 'history' in ticks_response:
                prices = ticks_response['history']['prices']
                for price_str in prices:
                    price_str = str(price_str)
                    # استخراج آخر رقم عشري
                    if '.' in price_str:
                        last_digit = price_str.split('.')[-1][-1]
                    else:
                        last_digit = price_str[-1] 
                        
                    if last_digit == '5':
                        fives_count += 1
            
            # 2. شرط الدخول: الشراء إذا كان عدد الخمسات >= 2
            if fives_count < 2:
                print(f"User {email}: Waiting. Found only {fives_count}/{TICKS_COUNT} ticks ending in '5'.")
                return # لا يتم الشراء

            # 3. TRADE CONFIGURATION: Digits Differs 5
            duration_value = 5 
            duration_unit = "t"
            
            print(f"User {email}: ✅ Condition met! Placing {CONTRACT_TYPE} trade with amount {amount_to_bet}.")

            # 4. Get proposal for the trade
            proposal_req = {
                "proposal": 1, "amount": amount_to_bet, "basis": "stake",
                "contract_type": CONTRACT_TYPE, "currency": currency,
                "duration": duration_value, "duration_unit": duration_unit, 
                "symbol": SYMBOL, 
                "barrier": BARRIER # ⬅ الرقم 5 لعقد Differs
            }
            
            ws.send(json.dumps(proposal_req))
            
            time.sleep(0.05)
            
            proposal_response = None
            start_wait = time.time()
            while proposal_response is None and (time.time() - start_wait < 15):
                try:
                    response_str = ws.recv()
                    if response_str:
                        response = json.loads(response_str)
                        if response.get('error'):
                            print(f"\n🚨🚨 PROPOSAL ERROR for {email}: 🚨🚨")
                            print(f"   Error Response: {response['error']}")
                            return
                        if response.get('msg_type') == 'proposal':
                            proposal_response = response
                            break
                except Exception:
                    pass

            if proposal_response and 'proposal' in proposal_response:
                print(f"User {email}: ✅ Proposal Received! ID: {proposal_response['proposal']['id']}")
                
                proposal_id = proposal_response['proposal']['id']
                
                # 5. Place the order
                order_response = place_order(ws, proposal_id, amount_to_bet)
                
                if order_response.get('error'):
                    print(f"\n🚨🚨 ORDER (BUY) ERROR for {email}: 🚨🚨")
                    print(f"   Error Response: {order_response['error']}")
                    return
                
                if 'buy' in order_response and 'contract_id' in order_response['buy']:
                    new_contract_id = order_response['buy']['contract_id']
                    trade_start_time = time.time()
                    print(f"User {email}: Placed trade {new_contract_id} (Stake {amount_to_bet}).")
                    
                    update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=new_contract_id, trade_start_time=trade_start_time)
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
            ws.close()

# --- Main Bot Loop Function ---
def bot_loop():
    """Main loop that orchestrates trading jobs for all active sessions."""
    print("Bot process started. PID:", os.getpid())
    update_bot_running_status(1, os.getpid())
    
    while True:
        try:
            now = datetime.now(timezone.utc)
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
                    
                    # 1. Check/close active trades 
                    if contract_id and (time.time() - trade_start_time) >= 10: 
                        run_trading_job_for_user(latest_session_data, check_only=True)

                    # 2. Logic to place new trades - يدخل في الثواني 0, 1, 2, 3, 4 (UTC)
                    if now.second >= 0 and now.second <= 4 and not contract_id: 
                        re_checked_session_data = get_session_status_from_db(email)
                        if re_checked_session_data and re_checked_session_data.get('is_running') == 1 and not re_checked_session_data.get('contract_id'):
                            run_trading_job_for_user(re_checked_session_data, check_only=False) 
            
            time.sleep(1)
        except Exception as e:
            print(f"Error in bot_loop main loop: {e}. Sleeping for 5 seconds before retrying.")
            time.sleep(5)

# --- Streamlit App Configuration (Corrected) ---
st.set_page_config(page_title="Khoury Bot", layout="wide")
st.title("Khoury Bot 🤖")

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
        bot_process = multiprocessing.Process(target=bot_loop, daemon=True)
        bot_process.start()
        print(f"Bot process started with PID: {bot_process.pid}")
        time.sleep(1) 
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
    if st.session_state.stats:
        is_user_bot_running_in_db = st.session_state.stats.get('is_running', 0) == 1
    
    global_bot_status = get_bot_running_status() 

    with st.form("settings_and_control"):
        st.subheader("Bot Settings and Control")
        
        # 🚨 تصحيح الأسطر التي تحتوي على رموز LaTeX/f-string
        st.markdown(f"*Current Strategy:* $\\mathbf{{\\text{{R}}\\_75}}$, Digits Differs $\\mathbf{{5}}$ (Entry on $\\mathbf{{2+}}$ '5's in last 10 Ticks).")
        st.markdown(f"*Martingale Multiplier:* $\\mathbf{{\\times {MARTINGALE\_MULTIPLIER:.1f}}}$ (High Risk)")

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
            st.success("✅ Bot session started successfully! Waiting for the next trade cycle.")
            st.rerun()

    if stop_button:
        update_is_running_status(st.session_state.user_email, 0)
        st.info("⏸ Your bot session has been stopped.")
        st.rerun()

    st.markdown("---")
    st.subheader("Statistics")

    stats_placeholder = st.empty()
    
    if global_bot_status == 1:
        st.success(f"🟢 Global Bot Service is RUNNING.")
    else:
        st.error("🔴 Global Bot Service is STOPPED or Crashed.")

    balance = None
    currency = None
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
            with col1:
                st.metric(label="Current Bet Amount", value=f"${stats.get('current_amount', 0.0):.2f}")
            with col2:
                initial_balance = stats.get('initial_balance', 0.0)
                current_profit = float(balance or 0.0) - initial_balance if initial_balance > 0 and balance is not None else 0.0
                st.metric(label="Net Profit/Loss", value=f"${current_profit:.2f}", delta=f"{current_profit:.2f}")

            with col3:
                st.metric(label="Total Wins", value=stats.get('total_wins', 0))
            with col4:
                st.metric(label="Total Losses", value=stats.get('total_losses', 0))
            with col5:
                losses_remaining = stats.get('max_consecutive_losses', 0) - stats.get('consecutive_losses', 0)
                st.metric(label="Consecutive Losses", value=stats.get('consecutive_losses', 0), delta=f"-{losses_remaining} to Stop")
            
            if stats.get('contract_id'):
                st.warning(f"⚠ Trade Active: {stats.get('contract_id')}. Stats update after completion.")
            elif stats.get('is_running') == 1:
                 st.info(f"🕒 Waiting for next trade cycle on $\\text{{R}}\\_75$.")

    else:
        with stats_placeholder.container():
            st.info("Your bot session is currently stopped or not yet configured.")
            
    time.sleep(1)
    st.rerun()
