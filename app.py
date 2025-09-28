import streamlit as st
import time
import websocket
import json
import os
import decimal
import sqlite3
import multiprocessing

# --- Strategy Configuration ---
TRADING_SYMBOL = "R_100"       # Volatility 100 Index
CONTRACT_DURATION = 1          # 1 tick (مدة الصفقة)
CONTRACT_DURATION_UNIT = 't'   # 't' for tick
MIN_CHECK_DELAY_SECONDS = 5    # الانتظار 5 ثواني قبل بدء الفحص المستمر
NET_LOSS_MULTIPLIER = 2.0      # المضاعف المشترك عند الخسارة الصافية (x2)
BASE_OVER_MULTIPLIER = 3.0     # مضاعف Over 3 بالنسبة لـ Under (3x)

# --- SQLite Database Configuration ---
DB_FILE = "trading_data_unique_martingale.db" # تغيير اسم الملف لتجنب تداخل السكيما

# --- Database & Utility Functions ---
def create_connection():
    """Create a database connection."""
    try:
        conn = sqlite3.connect(DB_FILE)
        return conn
    except sqlite3.Error:
        return None

def create_table_if_not_exists():
    """Create the sessions and bot_status tables if they do not exist (Final Schema)."""
    conn = create_connection()
    if conn:
        try:
            sql_create_sessions_table = """
            CREATE TABLE IF NOT EXISTS sessions (
                email TEXT PRIMARY KEY,
                user_token TEXT NOT NULL,
                -- base_amount هو قيمة Under 3
                base_amount REAL NOT NULL, 
                tp_target REAL NOT NULL,
                max_consecutive_losses INTEGER NOT NULL,
                total_wins INTEGER DEFAULT 0,
                total_losses INTEGER DEFAULT 0,
                
                -- متغيرات المضاعفة المنفصلة
                base_under_amount REAL NOT NULL, 
                base_over_amount REAL NOT NULL,
                current_under_amount REAL NOT NULL,
                current_over_amount REAL NOT NULL,
                consecutive_net_losses INTEGER DEFAULT 0, -- الخسائر المتتالية للدورة الصافية
                
                initial_balance REAL DEFAULT 0.0,
                contract_id TEXT,
                trade_start_time REAL DEFAULT 0.0,
                is_running INTEGER DEFAULT 0,
                
                -- لتعقب حالة الدورة (0: ابدأ الدورة، 1: Under تم الدخول، 2: Over تم الدخول)
                trade_count INTEGER DEFAULT 0, 
                -- لتسجيل صافي الربح/الخسارة داخل الدورة
                cycle_net_profit REAL DEFAULT 0.0 
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
    conn = create_connection()
    if conn:
        try:
            with conn:
                cursor = conn.execute("SELECT is_running_flag, last_heartbeat, process_pid FROM bot_status WHERE flag_id = 1")
                row = cursor.fetchone()
                if row:
                    status, heartbeat, pid = row
                    if status == 1:
                        if (time.time() - heartbeat > 30): 
                            update_bot_running_status(0, 0)
                            return 0
                        return status
                    else:
                        return 0
                return 0
        except sqlite3.Error:
            return 0
        finally:
            conn.close()
    return 0

def update_bot_running_status(status, pid):
    conn = create_connection()
    if conn:
        try:
            with conn:
                conn.execute("UPDATE bot_status SET is_running_flag = ?, last_heartbeat = ?, process_pid = ? WHERE flag_id = 1", (status, time.time(), pid))
        except sqlite3.Error:
            pass
        finally:
            conn.close()

def is_user_active(email):
    try:
        with open("user_ids.txt", "r") as file:
            active_users = [line.strip() for line in file.readlines()]
        return email in active_users
    except FileNotFoundError:
        return False
    except Exception:
        return False

def start_new_session_in_db(email, settings):
    """Saves or updates user settings and initializes session data in the database."""
    conn = create_connection()
    if conn:
        try:
            # Base amount for Under 3 is the user input
            base_under = settings["base_under_amount_input"]
            # Base amount for Over 3 is automatically calculated (x3)
            base_over = base_under * BASE_OVER_MULTIPLIER 
            
            with conn:
                conn.execute("""
                    INSERT OR REPLACE INTO sessions 
                    (email, user_token, base_amount, tp_target, max_consecutive_losses, 
                     base_under_amount, base_over_amount, current_under_amount, current_over_amount,
                     is_running, consecutive_net_losses, trade_count, cycle_net_profit)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 0, 0.0)
                    """, (email, settings["user_token"], base_under, settings["tp_target"], 
                          settings["max_consecutive_losses"], base_under, base_over, 
                          base_under, base_over))
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
        except sqlite3.Error:
            pass
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
        except sqlite3.Error:
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
        except sqlite3.Error:
            return []
        finally:
            conn.close()
    return []

def update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_under_amount, current_over_amount, consecutive_net_losses, trade_count, cycle_net_profit, initial_balance=None, contract_id=None, trade_start_time=None):
    """Updates trading statistics and trade information for a user in the database (Updated variables)."""
    conn = create_connection()
    if conn:
        try:
            with conn:
                update_query = """
                UPDATE sessions SET 
                    total_wins = ?, total_losses = ?, current_under_amount = ?, current_over_amount = ?, 
                    consecutive_net_losses = ?, trade_count = ?, cycle_net_profit = ?, 
                    initial_balance = COALESCE(?, initial_balance), 
                    contract_id = ?, trade_start_time = COALESCE(?, trade_start_time)
                WHERE email = ?
                """
                conn.execute(update_query, (total_wins, total_losses, current_under_amount, current_over_amount, 
                                            consecutive_net_losses, trade_count, cycle_net_profit, 
                                            initial_balance, contract_id, trade_start_time, email))
        except sqlite3.Error as e:
            print(f"Database error in update_stats_and_trade_info_in_db: {e}")
        finally:
            conn.close()

# --- WebSocket Helper Functions ---
def connect_websocket(user_token):
    ws = websocket.WebSocket()
    try:
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929") 
        auth_req = {"authorize": user_token}
        ws.send(json.dumps(auth_req))
        while True:
            auth_response = json.loads(ws.recv())
            if auth_response.get('msg_type') == 'authorize' or auth_response.get('error'):
                 break
        if auth_response.get('error'):
            ws.close()
            return None
        return ws
    except Exception:
        return None

def get_balance_and_currency(user_token):
    ws = None
    try:
        ws = connect_websocket(user_token)
        if not ws: return None, None
        balance_req = {"balance": 1}
        ws.send(json.dumps(balance_req))
        while True:
            balance_response = json.loads(ws.recv())
            if balance_response.get('msg_type') == 'balance' or balance_response.get('error'): break
        if balance_response.get('msg_type') == 'balance':
            balance_info = balance_response.get('balance', {})
            return balance_info.get('balance'), balance_info.get('currency')
        return None, None
    except Exception: return None, None
    finally:
        if ws and ws.connected: ws.close()

def check_contract_status(ws, contract_id):
    if not ws or not ws.connected: return None
    req = {"proposal_open_contract": 1, "contract_id": contract_id}
    try:
        ws.send(json.dumps(req))
        response = None
        for _ in range(3): 
            try:
                response_str = ws.recv()
                response = json.loads(response_str)
                if response and (response.get('msg_type') == 'proposal_open_contract' or response.get('error')): break
            except Exception: time.sleep(0.1); continue
        if response and response.get('msg_type') == 'proposal_open_contract': return response.get('proposal_open_contract')
        return None
    except Exception: return None

def place_order(ws, proposal_id, amount):
    if not ws or not ws.connected: return {"error": {"message": "WebSocket not connected."}}
    amount_decimal = decimal.Decimal(str(amount)).quantize(decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_UP)
    req = {"buy": proposal_id, "price": float(amount_decimal)}
    try:
        ws.send(json.dumps(req))
        while True:
            response_str = ws.recv()
            response = json.loads(response_str)
            if response.get('msg_type') == 'buy': return response
            elif response.get('error'): return response
    except Exception: return {"error": {"message": "Order placement failed."}}

# --- Trading Bot Logic (Final Unique Martingale) ---

def run_trading_job_for_user(session_data, check_only=False):
    """Executes the trading logic for a specific user's session."""
    email = session_data['email']
    user_token = session_data['user_token']
    tp_target = session_data['tp_target']
    max_consecutive_losses = session_data['max_consecutive_losses']
    total_wins = session_data['total_wins']
    total_losses = session_data['total_losses']
    
    # 🌟 المتغيرات الجديدة/المحدثة
    base_under_amount = session_data['base_under_amount']
    base_over_amount = session_data['base_over_amount']
    current_under_amount = session_data['current_under_amount']
    current_over_amount = session_data['current_over_amount']
    consecutive_net_losses = session_data['consecutive_net_losses']
    trade_count = session_data['trade_count']
    cycle_net_profit = session_data['cycle_net_profit'] 
    
    initial_balance = session_data['initial_balance']
    contract_id = session_data['contract_id']
    trade_start_time = session_data['trade_start_time']
    
    ws = None
    try:
        for _ in range(3): 
             ws = connect_websocket(user_token)
             if ws: break
             time.sleep(0.5) 
        if not ws: return

        # --- 1. Check for completed trades ---
        if contract_id: 
            elapsed_time = time.time() - trade_start_time
            # الانتظار 5 ثواني كحد أدنى للمراقبة (التعديل هنا)
            if elapsed_time < MIN_CHECK_DELAY_SECONDS: return 
            
            trade_type = "Under 3" if trade_count == 1 else "Over 3"
            
            while True:
                contract_info = check_contract_status(ws, contract_id)
                
                if contract_info and contract_info.get('is_sold'): # الصفقة أغلقت
                    
                    profit = float(contract_info.get('profit', 0))
                    
                    # 📈 تحديث إحصائيات الصفقة
                    cycle_net_profit += profit
                    total_wins += 1 if profit > 0 else 0
                    total_losses += 1 if profit < 0 else 0
                    
                    
                    # 1.1 إذا كانت الصفقة الأولى (Under) هي التي انتهت:
                    if trade_count == 1:
                        print(f"User {email}: Trade 1 (Under 3) completed. Profit: {profit:.2f}. Cycle Net Profit: {cycle_net_profit:.2f}. Proceeding to Trade 2 (Over 3).")
                        
                        # تحديث الحالة للمتابعة فوراً للصفقة الثانية
                        update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_under_amount, current_over_amount, consecutive_net_losses, 
                                                          trade_count=2, cycle_net_profit=cycle_net_profit, 
                                                          initial_balance=initial_balance, contract_id=None, trade_start_time=0.0)
                        return # نخرج لبدء الصفقة الثانية

                    # 1.2 إذا كانت الصفقة الثانية (Over) هي التي انتهت: (نهاية الدورة)
                    elif trade_count == 2:
                        print(f"User {email}: Trade 2 (Over 3) completed. Profit: {profit:.2f}. Cycle Net Profit: {cycle_net_profit:.2f} (Cycle Finished).")
                        
                        # 💰 قرار المضاعفة المشروط (بناءً على صافي الخسارة للدورة)
                        if cycle_net_profit < 0:
                            # ❌ خسارة صافية: نضاعف ونزيد عدد الخسائر المتتالية
                            consecutive_net_losses += 1
                            
                            # مضاعفة مشتركة: Under * 2 و Over * 2
                            next_under_bet = float(current_under_amount) * NET_LOSS_MULTIPLIER 
                            next_over_bet = float(current_over_amount) * NET_LOSS_MULTIPLIER
                            
                            current_under_amount = max(base_under_amount, next_under_bet)
                            current_over_amount = max(base_over_amount, next_over_bet)

                            print(f"User {email}: CYCLE NET LOSS. Net Losses: {consecutive_net_losses}. Next Under Bet: {current_under_amount:.2f} (x2). Next Over Bet: {current_over_amount:.2f} (x2).")
                        else:
                            # ✅ ربح أو تعادل صافي: نعود إلى مبلغ الأساس الثابت
                            consecutive_net_losses = 0
                            current_under_amount = base_under_amount 
                            current_over_amount = base_over_amount
                            print(f"User {email}: CYCLE NET WIN/BREAK EVEN. Resetting bets to Under {base_under_amount:.2f} and Over {base_over_amount:.2f}")
                        
                        # 🛑 التحقق من حدود الإيقاف
                        if consecutive_net_losses >= max_consecutive_losses:
                            print(f"User {email} reached Max Consecutive Losses. Stopping session.")
                            update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_under_amount, current_over_amount, consecutive_net_losses, trade_count=0, cycle_net_profit=0.0, initial_balance=initial_balance)
                            update_is_running_status(email, 0)
                            return

                        # تحديث قاعدة البيانات وإعادة تهيئة الدورة الجديدة
                        update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_under_amount, current_over_amount, consecutive_net_losses, 
                                                          trade_count=0, cycle_net_profit=0.0, initial_balance=initial_balance, 
                                                          contract_id=None, trade_start_time=0.0)
                        return 
                
                time.sleep(1)


        # --- 2. Entry Logic (If no trade is active) ---
        if not check_only and not contract_id: 
            
            balance, currency = get_balance_and_currency(user_token)
            if balance is None: return
            if initial_balance == 0:
                initial_balance = float(balance)
                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_under_amount, current_over_amount, consecutive_net_losses, trade_count, cycle_net_profit, initial_balance=initial_balance)
            
            
            # 🔄 تحديد نوع الصفقة ومبلغ الرهان (Trade 1: Under 3, Trade 2: Over 3)
            if trade_count == 0:
                # بدء دورة جديدة: الدخول في Under 3
                new_contract_type = "DIGITUNDER"
                new_trade_count = 1
                amount_to_bet = current_under_amount # استخدام المبلغ الحالي لـ Under
            elif trade_count == 1:
                # الصفقة الثانية في الدورة: الدخول في Over 3
                new_contract_type = "DIGITOVER"
                new_trade_count = 2
                amount_to_bet = current_over_amount # استخدام المبلغ الحالي لـ Over
            else:
                return 

            print(f"User {email}: Entering Trade {new_trade_count} ({new_contract_type}). Stake: {amount_to_bet:.2f}")

            amount_to_bet = max(0.35, round(float(amount_to_bet), 2))
                             
            # 1. طلب الاقتراح (Proposal)
            proposal_req = {
                "proposal": 1, "amount": amount_to_bet, "basis": "stake",
                "contract_type": new_contract_type,  
                "currency": currency,
                "duration": CONTRACT_DURATION, "duration_unit": CONTRACT_DURATION_UNIT, 
                "symbol": TRADING_SYMBOL,
                "barrier": 3 # الرقم المستهدف لـ Over/Under
            }
            ws.send(json.dumps(proposal_req))
            
            # 2. انتظار الرد على الاقتراح 
            proposal_response = None
            for i in range(5):
                try:
                    response_str = ws.recv()
                    if response_str:
                        proposal_response = json.loads(response_str)
                        if proposal_response.get('error'): return
                        if 'proposal' in proposal_response: break
                    time.sleep(0.5)
                except Exception: continue
            
            if proposal_response and 'proposal' in proposal_response:
                proposal_id = proposal_response['proposal']['id']
                
                # 3. وضع أمر الشراء (Buy)
                order_response = place_order(ws, proposal_id, amount_to_bet)
                
                if 'buy' in order_response and 'contract_id' in order_response['buy']:
                    new_contract_id = order_response['buy']['contract_id']
                    trade_start_time = time.time() 
                    
                    # 💾 تحديث حالة الصفقة الجديدة في DB
                    update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_under_amount, current_over_amount, consecutive_net_losses, 
                                                      trade_count=new_trade_count, cycle_net_profit=cycle_net_profit, 
                                                      initial_balance=initial_balance, contract_id=new_contract_id, trade_start_time=trade_start_time)
                else:
                    print(f"User {email}: Failed to place order. Response: {order_response}")
            else:
                 print(f"User {email}: Failed to receive valid proposal after waiting.")

    except Exception as e:
        print(f"An error occurred in run_trading_job_for_user for {email}: {e}")
    finally:
        if ws and ws.connected:
            ws.close()

# --- Main Bot Loop Function (Unchanged) ---
def bot_loop():
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
                    
                    if contract_id:
                        run_trading_job_for_user(latest_session_data, check_only=True)
                    
                    elif not contract_id:
                        run_trading_job_for_user(latest_session_data, check_only=False) 
            
            time.sleep(1) 
        except Exception as e:
            time.sleep(5)

# --- Streamlit App Configuration (Updated Stats Display) ---
st.set_page_config(page_title="Khoury Bot", layout="wide")
st.title("Khoury Bot 🤖")

if "logged_in" not in st.session_state: st.session_state.logged_in = False
if "user_email" not in st.session_state: st.session_state.user_email = ""
if "stats" not in st.session_state: st.session_state.stats = None
    
create_table_if_not_exists()

bot_status_from_db = get_bot_running_status()

if bot_status_from_db == 0: 
    try:
        bot_process = multiprocessing.Process(target=bot_loop, daemon=True)
        bot_process.start()
        update_bot_running_status(1, bot_process.pid)
    except Exception as e:
        st.error(f"❌ Error starting bot process: {e}")

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
    
    with st.form("settings_and_control"):
        st.subheader("Bot Settings and Control")
        st.info(f"**Current Strategy:** **Under 3** followed by **Over 3**. **Duration:** **1 Tick**. **Martingale:** **$\times 2$** on Net Cycle Loss. ")
        
        user_token_val = ""
        base_under_amount_val = 0.35 
        tp_target_val = 10.0
        max_consecutive_losses_val = 3
        
        if st.session_state.stats:
            user_token_val = st.session_state.stats.get('user_token', '')
            base_under_amount_val = st.session_state.stats.get('base_under_amount', 0.35)
            tp_target_val = st.session_state.stats.get('tp_target', 10.0)
            max_consecutive_losses_val = st.session_state.stats.get('max_consecutive_losses', 3)
        
        user_token = st.text_input("Deriv API Token", type="password", value=user_token_val, disabled=is_user_bot_running_in_db)
        # 🌟 تم تعديل إدخال المستخدم ليكون لـ Under 3 فقط
        base_under_amount_input = st.number_input("Base Bet Amount for **Under 3** (min $0.35)", min_value=0.35, value=base_under_amount_val, step=0.1, disabled=is_user_bot_running_in_db)
        
        # عرض مبلغ Over 3 المحسوب تلقائياً
        calculated_over_amount = round(base_under_amount_input * BASE_OVER_MULTIPLIER, 2)
        st.markdown(f"**Calculated Base Bet for Over 3 (x3):** **${calculated_over_amount:.2f}**")
        
        tp_target = st.number_input("Take Profit Target", min_value=10.0, value=tp_target_val, step=3.0, disabled=is_user_bot_running_in_db)
        max_consecutive_losses = st.number_input("Max Consecutive Losses (Net Cycles)", min_value=1, value=max_consecutive_losses_val, step=1, disabled=is_user_bot_running_in_db)
        
        col_start, col_stop = st.columns(2)
        with col_start:
            start_button = st.form_submit_button("Start Bot", disabled=is_user_bot_running_in_db)
        with col_stop:
            stop_button = st.form_submit_button("Stop Bot", disabled=not is_user_bot_running_in_db)
    
    if start_button:
        if not user_token: st.error("Please enter a Deriv API Token to start the bot.")
        else:
            settings = {"user_token": user_token, "base_under_amount_input": base_under_amount_input, "tp_target": tp_target, "max_consecutive_losses": max_consecutive_losses}
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
        if session_data:
            user_token_for_balance = session_data.get('user_token')
            if user_token_for_balance:
                balance, _ = get_balance_and_currency(user_token_for_balance)
                if balance is not None:
                    st.metric(label="Current Balance", value=f"${float(balance):.2f}")

    if st.session_state.stats:
        with stats_placeholder.container():
            stats = st.session_state.stats
            
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric(label="Total Wins", value=stats.get('total_wins', 0))
            with col2:
                st.metric(label="Total Losses", value=stats.get('total_losses', 0))
            with col3:
                st.metric(label="Net Losses Cycle", value=stats.get('consecutive_net_losses', 0))
            with col4:
                st.metric(label="Net Profit Cycle", value=f"${stats.get('cycle_net_profit', 0.0):.2f}")
            
            st.markdown("---")
            st.markdown("##### 📈 Bets for Next Cycle")
            
            col5, col6 = st.columns(2)
            with col5:
                st.metric(label=f"Under 3 Bet", value=f"${stats.get('current_under_amount', 0.0):.2f}")
            with col6:
                st.metric(label=f"Over 3 Bet", value=f"${stats.get('current_over_amount', 0.0):.2f}")
            
            trade_type = "N/A"
            if stats.get('trade_count', 0) == 1: trade_type = "Under 3"
            elif stats.get('trade_count', 0) == 2: trade_type = "Over 3"
            
            if stats.get('contract_id'):
                st.warning(f"⚠ A **{trade_type}** trade is currently active (Trade {stats.get('trade_count')} of 2).")
            elif stats.get('trade_count') == 1:
                 st.info(f"✅ Trade 1 is complete. Ready to enter Trade 2 (**Over 3**).")
            elif stats.get('trade_count') == 0:
                 st.info(f"✅ Cycle complete. Ready to start new Cycle (**Under 3**).")
            
    else:
        with stats_placeholder.container():
            st.info("Your bot session is currently stopped or not yet configured.")
            
    time.sleep(2) 
    st.rerun()
