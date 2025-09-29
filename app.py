import streamlit as st
import sqlite3
import os
import json
import websocket
import time
from datetime import datetime
import multiprocessing
import pandas as pd

# --- Database & Utility Functions (Mocked for completeness) ---

# Mock the database connection
def create_connection():
    """Create a database connection to the SQLite database specified by db_file."""
    # Use in-memory database for Streamlit example or replace with actual file
    conn = None
    try:
        conn = sqlite3.connect('trading_bot.db') 
        return conn
    except sqlite3.Error as e:
        print(f"Error connecting to database: {e}")
    return conn

def create_table_if_not_exists():
    """Create database tables if they don't exist, including the new column."""
    conn = create_connection()
    if conn:
        try:
            with conn:
                # Table for active user sessions
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
                    balance_before_trade REAL DEFAULT 0.0 -- **العمود الجديد لإدارة الصفقات المزدوجة**
                );
                """
                # Table for bot process status
                sql_create_bot_status_table = """
                CREATE TABLE IF NOT EXISTS bot_status (
                    id INTEGER PRIMARY KEY,
                    is_running INTEGER DEFAULT 0,
                    pid INTEGER DEFAULT 0,
                    last_heartbeat REAL DEFAULT 0.0
                );
                """
                conn.execute(sql_create_sessions_table)
                conn.execute(sql_create_bot_status_table)
        except sqlite3.Error as e:
            print(f"Database error in create_table_if_not_exists: {e}")
        finally:
            conn.close()

def update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=None, contract_id=None, trade_start_time=None, balance_before_trade=None):
    """Updates trading statistics and trade information for a user in the database, including balance_before_trade."""
    conn = create_connection()
    if conn:
        try:
            with conn:
                update_query = """
                UPDATE sessions SET 
                    total_wins = ?, total_losses = ?, current_amount = ?, consecutive_losses = ?, 
                    initial_balance = COALESCE(?, initial_balance), 
                    contract_id = ?, 
                    trade_start_time = COALESCE(?, trade_start_time),
                    balance_before_trade = COALESCE(?, balance_before_trade) 
                WHERE email = ?
                """
                conn.execute(update_query, (total_wins, total_losses, current_amount, consecutive_losses, initial_balance, contract_id, trade_start_time, balance_before_trade, email))
        except sqlite3.Error as e:
            print(f"Database error in update_stats_and_trade_info_in_db: {e}")
        finally:
            conn.close()

def get_session_status_from_db(email):
    # Mock data fetching or implement actual SQLite retrieval
    conn = create_connection()
    if conn:
        try:
            with conn:
                cursor = conn.execute("SELECT * FROM sessions WHERE email=?", (email,))
                row = cursor.fetchone()
                if row:
                    # Map the column names to the fetched row data
                    columns = [description[0] for description in cursor.description]
                    return dict(zip(columns, row))
        except sqlite3.Error as e:
            print(f"Database error in get_session_status_from_db: {e}")
        finally:
            conn.close()
    return None

def get_all_active_sessions():
    conn = create_connection()
    if conn:
        try:
            with conn:
                cursor = conn.execute("SELECT * FROM sessions WHERE is_running=1")
                rows = cursor.fetchall()
                if rows:
                    columns = [description[0] for description in cursor.description]
                    return [dict(zip(columns, row)) for row in rows]
        except sqlite3.Error as e:
            print(f"Database error in get_all_active_sessions: {e}")
        finally:
            conn.close()
    return []

def start_new_session_in_db(email, settings):
    conn = create_connection()
    if conn:
        try:
            with conn:
                conn.execute("""
                    INSERT OR REPLACE INTO sessions (email, user_token, base_amount, tp_target, max_consecutive_losses, current_amount, is_running)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                """, (
                    email, settings['user_token'], settings['base_amount'], settings['tp_target'], 
                    3, # Max Consecutive Losses is now fixed at 3
                    settings['base_amount']
                ))
        except sqlite3.Error as e:
            print(f"Database error in start_new_session_in_db: {e}")
        finally:
            conn.close()

def update_is_running_status(email, status):
    conn = create_connection()
    if conn:
        try:
            with conn:
                conn.execute("UPDATE sessions SET is_running = ?, contract_id = NULL, trade_start_time = 0.0 WHERE email = ?", (status, email))
        except sqlite3.Error as e:
            print(f"Database error in update_is_running_status: {e}")
        finally:
            conn.close()

def clear_session_data(email):
    # Only clears trade-related fields, keeps settings
    conn = create_connection()
    if conn:
        try:
            with conn:
                conn.execute("UPDATE sessions SET total_wins = 0, total_losses = 0, current_amount = base_amount, consecutive_losses = 0, initial_balance = 0.0, contract_id = NULL, trade_start_time = 0.0, balance_before_trade = 0.0 WHERE email = ?", (email,))
        except sqlite3.Error as e:
            print(f"Database error in clear_session_data: {e}")
        finally:
            conn.close()

def get_bot_running_status():
    conn = create_connection()
    if conn:
        try:
            with conn:
                cursor = conn.execute("SELECT is_running, pid, last_heartbeat FROM bot_status WHERE id=1")
                row = cursor.fetchone()
                if row:
                    is_running, pid, last_heartbeat = row
                    # Check for heartbeat (if no update in 30 seconds, assume crashed)
                    if is_running == 1 and (time.time() - last_heartbeat) > 30:
                        print(f"Bot process (PID: {pid}) timed out.")
                        conn.execute("UPDATE bot_status SET is_running = 0, pid = 0, last_heartbeat = ? WHERE id = 1", (time.time(),))
                        return 0 # Timed out
                    return is_running
                else:
                    conn.execute("INSERT INTO bot_status (id, is_running, pid, last_heartbeat) VALUES (1, 0, 0, 0.0)")
        except sqlite3.Error as e:
            print(f"Database error in get_bot_running_status: {e}")
        finally:
            conn.close()
    return 0

def update_bot_running_status(status, pid):
    conn = create_connection()
    if conn:
        try:
            with conn:
                conn.execute("""
                    INSERT OR REPLACE INTO bot_status (id, is_running, pid, last_heartbeat) 
                    VALUES (1, ?, ?, ?)
                """, (status, pid, time.time()))
        except sqlite3.Error as e:
            print(f"Database error in update_bot_running_status: {e}")
        finally:
            conn.close()

def is_user_active(email):
    # Mock function to check if user exists (replace with actual user validation)
    return True 

# --- WebSocket & API Functions (Mocked or simplified) ---

DERIV_WS_URL = "wss://ws.binaryws.com/websockets/v3?app_id=1089"  # Use a mock URL or actual Deriv URL

def connect_ws(token):
    try:
        ws = websocket.create_connection(DERIV_WS_URL)
        auth_req = {"authorize": token}
        ws.send(json.dumps(auth_req))
        response = json.loads(ws.recv())
        if response.get('error'):
            print(f"Authorization error: {response['error']['message']}")
            ws.close()
            return None
        return ws
    except Exception as e:
        print(f"WebSocket connection error: {e}")
        return None

def get_balance_and_currency(token):
    ws = connect_ws(token)
    if not ws:
        return None, None
    try:
        ws.send(json.dumps({"balance": 1}))
        response = json.loads(ws.recv())
        if response.get('error'):
            print(f"Balance error: {response['error']['message']}")
            return None, None
        balance = response['balance']['balance']
        currency = response['balance']['currency']
        return balance, currency
    except Exception as e:
        print(f"Error getting balance: {e}")
        return None, None
    finally:
        if ws and ws.connected:
            ws.close()

def check_contract_status(ws, contract_id):
    req = {"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}
    ws.send(json.dumps(req))
    
    # Wait for the contract status response (could be multiple responses if subscribed)
    try:
        # We try to receive multiple messages until 'is_sold' is present
        while True:
            response_str = ws.recv()
            if response_str:
                response = json.loads(response_str)
                if response.get('msg_type') == 'proposal_open_contract':
                    contract_info = response['proposal_open_contract']
                    # Unsubscribe to avoid receiving more updates
                    ws.send(json.dumps({"forget": response['echo_req']['contract_id']}))
                    return contract_info
                elif response.get('error'):
                    print(f"Error checking contract status: {response['error']['message']}")
                    return None
            time.sleep(0.1)
    except Exception as e:
        print(f"Error receiving contract status: {e}")
        return None

def place_order(ws, proposal_id, amount):
    buy_req = {"buy": proposal_id, "price": amount}
    ws.send(json.dumps(buy_req))
    
    # Wait for the buy response
    try:
        while True:
            response_str = ws.recv()
            if response_str:
                response = json.loads(response_str)
                if response.get('msg_type') == 'buy':
                    return response
                elif response.get('error'):
                    return response
            time.sleep(0.1)
    except Exception as e:
        print(f"Error receiving buy response: {e}")
        return {"error": {"message": str(e)}}

# --- Trading Bot Logic ---
def analyse_data(df_ticks):
    """
    Analyzes tick data, now simplified to always return 'Trade' for Over/Under strategy.
    """
    # لا نحتاج لتحليل معقد في استراتيجية الدخول المزدوج (Hedge)
    if len(df_ticks) < 10: 
        return "Neutral", "Insufficient data."

    # الإشارة الجديدة: Trade
    return "Trade", "Ready to execute simultaneous Over/Under trades."


# --- Core Trading Job Function ---
def run_trading_job_for_user(session_data, check_only=False):
    email = session_data['email']
    user_token = session_data['user_token']
    base_amount = session_data['base_amount']
    tp_target = session_data['tp_target']
    
    # ************* تثبيت حد الخسارة التلقائي *************
    max_consecutive_losses = 3 
    # ***************************************************
    
    total_wins = session_data.get('total_wins', 0)
    total_losses = session_data.get('total_losses', 0)
    current_amount = session_data.get('current_amount', base_amount)
    consecutive_losses = session_data.get('consecutive_losses', 0)
    initial_balance = session_data.get('initial_balance', 0.0)
    contract_id = session_data.get('contract_id')
    trade_start_time = session_data.get('trade_start_time', 0.0)
    balance_before_trade = session_data.get('balance_before_trade', 0.0)

    ws = connect_ws(user_token)
    if not ws:
        print(f"Failed to connect WebSocket for user {email}")
        return

    try:
        # --- Check for completed trades (if contract_id exists) ---
        if contract_id: # This means a dual trade is currently open/in progress
            
            # ************* التعديل لانتظار 5 ثواني قبل فحص النتيجة *************
            time_elapsed = time.time() - trade_start_time
            if time_elapsed < 5 and check_only is False: 
                # إذا كان وقت الفحص أقل من 5 ثواني ولم يكن فحص إلزامي، انتظر أو تخطى
                print(f"User {email}: Trade {contract_id} is active ({time_elapsed:.2f}s). Waiting...")
                return
            
            # فحص حالة العقد (العقد المزدوج الأول هو علامة العملية)
            contract_info = check_contract_status(ws, contract_id)
            
            if contract_info and contract_info.get('is_sold'): # Trade has finished

                # 1. جلب الرصيد الجديد والرصيد السابق
                new_balance, _ = get_balance_and_currency(user_token)
                
                if new_balance is None or balance_before_trade == 0.0:
                    print(f"Could not fetch new balance or previous balance for {email}. Cannot calculate result.")
                    return # لا يمكن حساب النتيجة

                current_balance_float = float(new_balance)
                
                # 2. حساب صافي الربح/الخسارة للعملية المزدوجة
                net_profit_loss = current_balance_float - balance_before_trade
                
                if net_profit_loss > 0:
                    # صافي ربح للعملية المزدوجة
                    print(f"User {email}: DUAL TRADE WIN. Net Profit: {net_profit_loss:.2f}")
                    consecutive_losses = 0
                    total_wins += 1
                    current_amount = base_amount # Reset to base amount on win
                
                elif net_profit_loss < 0:
                    # صافي خسارة للعملية المزدوجة - تطبيق مضاعف 6x
                    print(f"User {email}: DUAL TRADE LOSS. Net Loss: {net_profit_loss:.2f}")
                    consecutive_losses += 1
                    total_losses += 1
                    
                    # ************** تعديل Martingale 6x **************
                    next_bet = float(current_amount) * 6.0 
                    current_amount = max(base_amount, round(next_bet, 2))
                    # *************************************************
                
                else:
                    # تعادل (نادر الحدوث)
                    print(f"User {email}: DUAL TRADE DRAW. Stake is reset.")
                    consecutive_losses = 0 # التعامل مع التعادل كصفقة ناجحة لإلغاء Martingale
                    
                # 3. إعادة ضبط التتبع في قاعدة البيانات
                new_contract_id = None
                trade_start_time = 0.0
                
                # تحديث كافة الإحصائيات وإعادة ضبط تتبع الصفقة
                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=new_contract_id, trade_start_time=trade_start_time, balance_before_trade=0.0)

                # 4. التحقق من شروط الإيقاف (TP/ML)
                if (current_balance_float - initial_balance) >= float(tp_target):
                    print(f"User {email} reached Take Profit target. Stopping session.")
                    update_is_running_status(email, 0)
                    clear_session_data(email)
                    return
                        
                if consecutive_losses >= max_consecutive_losses:
                    print(f"User {email} reached Max Consecutive Losses ({max_consecutive_losses}). Stopping session.")
                    update_is_running_status(email, 0)
                    clear_session_data(email)
                    return
                
            else:
                # Contract is still open or failed to check status
                return

        # --- Logic to place new trades (if not in check_only mode and no contract is active) ---
        if not check_only and not contract_id: # Place a new trade if no trade is active and not in check_only mode
            
            balance, currency = get_balance_and_currency(user_token)
            
            if balance is None:
                print(f"Failed to get balance for {email}. Skipping trade.")
                return
            
            # ************* تخزين الرصيد الحالي قبل الدخول في الصفقتين *************
            balance_before_trade = float(balance) 
            
            if initial_balance == 0: 
                initial_balance = balance_before_trade
                
            # التحديث في DB يتم بعد التنفيذ الناجح، لكن هنا نحتاج لـ initial_balance
            
            # Get latest ticks for analysis
            req = {"ticks_history": "R_75", "end": "latest", "count": 28, "style": "ticks"}
            ws.send(json.dumps(req))
            tick_data = None
            # Wait for the ticks history response
            while True:
                try:
                    response = json.loads(ws.recv())
                    if response.get('msg_type') == 'history':
                        tick_data = response
                        break
                    elif response.get('error'):
                        print(f"Error getting ticks history for {email}: {response['error']['message']}")
                        return
                except websocket._exceptions.WebSocketConnectionClosedException:
                    print(f"WebSocket closed while waiting for ticks history for {email}")
                    return
                except Exception as e:
                    print(f"Error receiving ticks history for {email}: {e}")
                    return

            if 'history' in tick_data and 'prices' in tick_data['history']:
                ticks = tick_data['history']['prices']
                df_ticks = pd.DataFrame({'price': ticks})
                signal, message = analyse_data(df_ticks) 
                print(f"User {email}: Signal = {signal}, Message = {message}")

                if signal == 'Trade':
                    
                    # 1. تهيئة المتغيرات لصفقتي Over/Under
                    # 'current_amount' هو مبلغ الرهان للصفقة الأساسية (Under 3)
                    under_stake = max(0.35, round(float(current_amount), 2))
                    
                    # مبلغ رهان Over 3 هو ضعف مبلغ Under 3
                    over_stake = round(under_stake * 2.0, 2) 

                    # المتغيرات الثابتة
                    symbol_used = "R_75"
                    duration_unit = "t"
                    duration = 1
                    barrier_offset = 3 

                    trade_results = []
                    
                    # 2. تنفيذ صفقتي (UNDER) ثم (OVER)
                    for contract_type in ["UNDER", "OVER"]:
                        
                        # تحديد مبلغ الرهان بناءً على نوع العقد
                        amount_to_bet = under_stake if contract_type == "UNDER" else over_stake
                        
                        print(f"User {email}: Preparing {contract_type} with stake: {amount_to_bet}")

                        # (A) الحصول على اقتراح السعر (Proposal)
                        proposal_req = {
                            "proposal": 1, "amount": amount_to_bet, "basis": "stake",
                            "contract_type": contract_type, "currency": currency,
                            "duration": duration, "duration_unit": duration_unit, "symbol": symbol_used,
                            "barrier": f"+{barrier_offset}" if contract_type == "OVER" else f"-{barrier_offset}"
                        }
                        ws.send(json.dumps(proposal_req))
                        
                        # انتظار الرد على الاقتراح
                        proposal_response = None
                        try:
                            while proposal_response is None:
                                response_str = ws.recv()
                                if response_str:
                                    proposal_response = json.loads(response_str)
                                    if proposal_response.get('msg_type') == 'proposal':
                                        break
                                    elif proposal_response.get('error'):
                                        print(f"Error getting {contract_type} proposal for {email}: {proposal_response['error']['message']}")
                                        proposal_response = {"error": True} 
                                        break
                        except Exception:
                             print(f"Error receiving {contract_type} proposal.")
                             proposal_response = {"error": True} 

                        # (B) تنفيذ الطلب (Place Order)
                        if proposal_response and 'proposal' in proposal_response:
                            proposal_id = proposal_response['proposal']['id']
                            order_response = place_order(ws, proposal_id, amount_to_bet)
                            trade_results.append((contract_type, order_response))
                        else:
                            trade_results.append((contract_type, {"error": {"message": f"No {contract_type} proposal received."}}))


                    # 3. تحديث قاعدة البيانات بعقد واحد فقط كعلامة للعملية المزدوجة
                    first_successful_buy = None
                    for contract_type, res in trade_results:
                        if 'buy' in res and 'contract_id' in res['buy']:
                            first_successful_buy = res
                            break 
                    
                    if first_successful_buy:
                        new_contract_id = first_successful_buy['buy']['contract_id']
                        trade_start_time = time.time()
                        print(f"User {email}: Placed dual trade (UNDER {under_stake}/OVER {over_stake}). Tracking ID: {new_contract_id}.")
                        
                        # تحديث DB بمعرف العقد والرصيد قبل العملية
                        update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=new_contract_id, trade_start_time=trade_start_time, balance_before_trade=balance_before_trade)
                    else:
                        print(f"User {email}: Failed to place one or both orders. Responses: {trade_results}")
                
                else:
                    print(f"User {email}: Analysis signal is Neutral, waiting...")
            else:
                print(f"User {email}: No tick history received or unexpected response format: {tick_data}")
    
    except websocket._exceptions.WebSocketConnectionClosedException:
        print(f"WebSocket connection lost for user {email}. Will try to reconnect.")
        if contract_id: 
             update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=contract_id, trade_start_time=session_data.get('trade_start_time'), balance_before_trade=balance_before_trade)

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
            now = datetime.now()
            
            # Update heartbeat
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
                    # Check if trade duration exceeds the 5-second mandatory check
                    if contract_id:
                        if (time.time() - trade_start_time) >= 5: # 5 seconds to ensure the 1-tick trade is finalized
                            # This ensures we don't miss closing an open trade 
                            run_trading_job_for_user(latest_session_data, check_only=False) # check_only=False will process the trade
                        else:
                            # Still waiting for the mandatory 5 seconds, but we can do a quick check
                            run_trading_job_for_user(latest_session_data, check_only=True)
                            
                    # --- Logic to place new trades ---
                    # The second == 0 condition is removed for instant entry (every second)
                    if not contract_id: 
                        re_checked_session_data = get_session_status_from_db(email) 
                        if re_checked_session_data and re_checked_session_data.get('is_running') == 1 and not re_checked_session_data.get('contract_id'):
                            run_trading_job_for_user(re_checked_session_data, check_only=False) 
            
            time.sleep(1) # Wait for 1 second before the next iteration of the loop
        except Exception as e:
            print(f"Error in bot_loop main loop: {e}. Sleeping for 5 seconds before retrying.")
            time.sleep(5) 

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
        
        if st.session_state.stats:
            user_token_val = st.session_state.stats.get('user_token', '')
            base_amount_val = st.session_state.stats.get('base_amount', 0.35)
            tp_target_val = st.session_state.stats.get('tp_target', 10.0)
            
        user_token = st.text_input("Deriv API Token", type="password", value=user_token_val, disabled=is_user_bot_running_in_db)
        # Note: Base Bet Amount is the stake for the UNDER 3 contract (1x)
        base_amount = st.number_input("Base Stake (UNDER 3)", min_value=0.35, value=base_amount_val, step=0.1, disabled=is_user_bot_running_in_db)
        tp_target = st.number_input("Take Profit Target", min_value=10.0, value=tp_target_val, step=3.0, disabled=is_user_bot_running_in_db)
        
        # Max Consecutive Losses is FIXED at 3 and hidden from user input.
        st.info("⚠️ **Max Consecutive Losses (Stop Loss) is fixed at 3 consecutive losses.**")
        
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
                "tp_target": tp_target
            }
            start_new_session_in_db(st.session_state.user_email, settings)
            st.success("✅ Bot session started successfully!")
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
        if session_data and session_data.get('user_token'):
            balance, _ = get_balance_and_currency(session_data.get('user_token'))
            if balance is not None:
                st.metric(label="Current Balance", value=f"${float(balance):.2f}")

    if st.session_state.stats:
        with stats_placeholder.container():
            stats = st.session_state.stats
            
            # Calculate the current stake for OVER 3
            current_under_stake = stats.get('current_amount', 0.0)
            current_over_stake = round(current_under_stake * 2.0, 2)
            
            col1, col2, col3, col4, col5 = st.columns(5)
            with col1:
                st.metric(label="UNDER 3 Stake", value=f"${current_under_stake:.2f}")
            with col2:
                st.metric(label="OVER 3 Stake (2x)", value=f"${current_over_stake:.2f}")
            with col3:
                st.metric(label="Total Wins", value=stats.get('total_wins', 0))
            with col4:
                st.metric(label="Total Losses", value=stats.get('total_losses', 0))
            with col5:
                sl_limit_text = f"{stats.get('consecutive_losses', 0)} / 3 (Auto SL)"
                st.metric(label="Consecutive Losses", value=sl_limit_text)
            
            if stats.get('contract_id'):
                time_since_trade = time.time() - stats.get('trade_start_time', time.time())
                st.warning(f"⚠ A trade is currently active ({time_since_trade:.1f}s). Stats will update after completion.")
    else:
        with stats_placeholder.container():
            st.info("Your bot session is currently stopped or not yet configured.")
            
    # Auto-refresh for statistics
    time.sleep(2) 
    st.rerun()
