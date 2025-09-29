import streamlit as st
import time
import websocket
import json
import os
import decimal
import sqlite3
import multiprocessing
import traceback

# --- Strategy Configuration ---
TRADING_SYMBOL = "R_100" 
CONTRACT_DURATION = 1 
CONTRACT_DURATION_UNIT = 't' 
NET_LOSS_MULTIPLIER = 6.0 
BASE_OVER_MULTIPLIER = 2.0 
MAX_CONSECUTIVE_LOSSES = 3 
CONTRACT_EXPECTED_DURATION = 5 
CHECK_DELAY_SECONDS = CONTRACT_EXPECTED_DURATION + 1 

# --- SQLite Database Configuration ---
DB_FILE = "trading_data_unique_martingale_balance.db" 

# --- Database & Utility Functions ---
def create_connection():
    try:
        conn = sqlite3.connect(DB_FILE)
        return conn
    except sqlite3.Error:
        return None

def create_table_if_not_exists():
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
                
                base_under_amount REAL NOT NULL, 
                base_over_amount REAL NOT NULL,
                current_under_amount REAL NOT NULL,
                current_over_amount REAL NOT NULL,
                consecutive_net_losses INTEGER DEFAULT 0, 
                
                initial_balance REAL DEFAULT 0.0,
                under_contract_id TEXT, 
                over_contract_id TEXT, 
                trade_start_time REAL DEFAULT 0.0,
                balance_before_trade REAL DEFAULT 0.0,
                is_running INTEGER DEFAULT 0,
                trade_count INTEGER DEFAULT 0, 
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
            
            cursor = conn.execute("PRAGMA table_info(sessions)")
            columns = [col[1] for col in cursor.fetchall()]
            if 'under_contract_id' not in columns:
                 conn.execute("ALTER TABLE sessions ADD COLUMN under_contract_id TEXT")
            if 'over_contract_id' not in columns:
                 conn.execute("ALTER TABLE sessions ADD COLUMN over_contract_id TEXT")
            if 'balance_before_trade' not in columns: 
                 conn.execute("ALTER TABLE sessions ADD COLUMN balance_before_trade REAL DEFAULT 0.0")
            
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
    # This function assumes a user_ids.txt file exists for user authentication
    try:
        with open("user_ids.txt", "r") as file:
            active_users = [line.strip() for line in file.readlines()]
        return email in active_users
    except FileNotFoundError:
        # For testing, we might assume a default user is active if the file is missing
        return email == "test@example.com"
    except Exception:
        return False

def start_new_session_in_db(email, settings):
    conn = create_connection()
    if conn:
        try:
            base_under = settings["base_under_amount_input"]
            base_over = base_under * BASE_OVER_MULTIPLIER 
            
            max_losses = settings.get("max_consecutive_losses", MAX_CONSECUTIVE_LOSSES) 
            tp_target = settings.get("tp_target", 100.0)
            
            with conn:
                conn.execute("""
                    INSERT OR REPLACE INTO sessions 
                    (email, user_token, base_amount, tp_target, max_consecutive_losses, 
                     base_under_amount, base_over_amount, current_under_amount, current_over_amount,
                     is_running, consecutive_net_losses, trade_count, cycle_net_profit, under_contract_id, over_contract_id, balance_before_trade)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 0, 0.0, NULL, NULL, 0.0)
                    """, (email, settings["user_token"], base_under, tp_target, 
                          max_losses, base_under, base_over, 
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

def update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_under_amount, current_over_amount, consecutive_net_losses, trade_count, cycle_net_profit, initial_balance=None, under_contract_id=None, over_contract_id=None, trade_start_time=None, balance_before_trade=None):
    conn = create_connection()
    if conn:
        try:
            with conn:
                update_query = """
                UPDATE sessions SET 
                    total_wins = ?, total_losses = ?, current_under_amount = ?, current_over_amount = ?, 
                    consecutive_net_losses = ?, trade_count = ?, cycle_net_profit = ?, 
                    initial_balance = COALESCE(?, initial_balance), 
                    under_contract_id = ?, over_contract_id = ?, 
                    trade_start_time = COALESCE(?, trade_start_time),
                    balance_before_trade = COALESCE(?, balance_before_trade)
                WHERE email = ?
                """
                conn.execute(update_query, (total_wins, total_losses, current_under_amount, current_over_amount, 
                                             consecutive_net_losses, trade_count, cycle_net_profit, 
                                             initial_balance, under_contract_id, over_contract_id, trade_start_time, balance_before_trade, email))
        except sqlite3.Error as e:
            print(f"Database error in update_stats_and_trade_info_in_db: {e}")
        finally:
            conn.close()

# --- WebSocket Helper Functions ---
def connect_websocket(user_token):
    ws = websocket.WebSocket()
    try:
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=1) 
        auth_req = {"authorize": user_token}
        ws.send(json.dumps(auth_req))
        
        for _ in range(30): # Wait up to 3 seconds for auth response
            try:
                response_str = ws.recv()
                if response_str:
                    auth_response = json.loads(response_str)
                    if auth_response.get('msg_type') == 'authorize' or auth_response.get('error'): 
                        return ws
                    # Ignore ticks or other messages during auth
                    if auth_response.get('msg_type') != 'tick':
                        time.sleep(0.1)
                        
            except websocket.WebSocketTimeoutException:
                time.sleep(0.1)
                continue
            except Exception:
                break
        
        if ws and ws.connected: ws.close()
        return None
    except Exception: 
        if ws and ws.connected: ws.close()
        return None

def get_balance_and_currency(user_token):
    for attempt in range(3):
        ws = None
        try:
            ws = connect_websocket(user_token)
            if not ws: 
                time.sleep(0.5)
                continue 

            balance_req = {"balance": 1}
            ws.send(json.dumps(balance_req))
            
            balance_response = None
            start_time = time.time()
            # انتظار الرد مع تجاهل التيك
            while time.time() - start_time < 5: 
                try:
                    response_str = ws.recv()
                    if response_str:
                        balance_response = json.loads(response_str)
                        if balance_response.get('msg_type') == 'balance':
                            balance_info = balance_response.get('balance', {})
                            return balance_info.get('balance'), balance_info.get('currency')
                        elif balance_response.get('msg_type') == 'tick':
                            continue # تجاهل التيك
                        elif balance_response.get('error'):
                            print(f"Error getting balance: {balance_response['error']['message']}")
                            break
                    time.sleep(0.1) 
                except websocket.WebSocketTimeoutException:
                    time.sleep(0.1)
                    continue
            
            if ws and ws.connected: ws.close()
            time.sleep(0.5)

        except Exception as e: 
            print(f"Exception in get_balance_and_currency: {e}")
            if ws and ws.connected: ws.close()
            time.sleep(0.5)
            continue
        finally:
            if ws and ws.connected: ws.close()
            
    return None, None

def place_order(ws, contract_type, amount, currency, barrier):
    if not ws or not ws.connected: return {"error": {"message": "WebSocket not connected."}}
    
    # تحويل المبلغ إلى Decimal وتقريبه
    amount_decimal = decimal.Decimal(str(amount)).quantize(decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_UP)
    
    # 1. Proposal Request
    proposal_req = {
        "proposal": 1, "amount": float(amount_decimal), "basis": "stake",
        "contract_type": contract_type, 
        "currency": currency,
        "duration": CONTRACT_DURATION, "duration_unit": CONTRACT_DURATION_UNIT, 
        "symbol": TRADING_SYMBOL,
        "barrier": f"+{barrier}" if contract_type == "DIGITOVER" else f"-{barrier}"
    }
    ws.send(json.dumps(proposal_req))
    
    # 2. Await Proposal Response (Robust against ticks)
    proposal_response = None
    start_time = time.time()
    while time.time() - start_time < 5: 
        try:
            response_str = ws.recv()
            if response_str:
                response = json.loads(response_str)
                
                if response.get('msg_type') == 'proposal':
                    proposal_response = response
                    break
                elif response.get('msg_type') == 'tick':
                    continue # تجاهل رسائل التيك المستمرة
                elif response.get('error'):
                    return response # إرجاع الخطأ مباشرة
            time.sleep(0.1)
        except websocket.WebSocketTimeoutException:
            continue
        except Exception: 
            break
            
    if not (proposal_response and 'proposal' in proposal_response):
        return {"error": {"message": f"Timeout or missing proposal for {contract_type}."}}

    proposal_id = proposal_response['proposal']['id']
    
    # 3. Buy Order
    buy_req = {"buy": proposal_id, "price": float(amount_decimal)}
    ws.send(json.dumps(buy_req))
    
    # 4. Await Buy Confirmation (Robust against ticks)
    start_time = time.time()
    while time.time() - start_time < 5: 
        try:
            response_str = ws.recv()
            if response_str:
                response = json.loads(response_str)
                if response.get('msg_type') == 'buy': 
                    return response
                elif response.get('msg_type') == 'tick':
                    continue # تجاهل رسائل التيك المستمرة
                elif response.get('error'): 
                    return response
            time.sleep(0.1)
        except websocket.WebSocketTimeoutException:
            continue
        except Exception:
            return {"error": {"message": "WebSocket read error during buy confirmation."}}
            
    return {"error": {"message": "Buy confirmation timed out."}}


# --- Trading Bot Logic (The Core Logic - Max Robustness) ---

def run_trading_job_for_user(session_data, check_only=False):
    email = session_data['email']
    user_token = session_data['user_token']
    
    tp_target = session_data['tp_target']
    max_consecutive_losses = session_data['max_consecutive_losses']
    total_wins = session_data['total_wins']
    total_losses = session_data['total_losses']
    base_under_amount = session_data['base_under_amount']
    current_under_amount = session_data['current_under_amount']
    current_over_amount = session_data['current_over_amount']
    consecutive_net_losses = session_data['consecutive_net_losses']
    trade_count = session_data['trade_count']
    cycle_net_profit = session_data['cycle_net_profit'] 
    initial_balance = session_data['initial_balance']
    trade_start_time = session_data['trade_start_time']
    balance_before_trade = session_data['balance_before_trade']
    
    
    # 🌟 المرحلة 1: التحقق من النتيجة والانتهاء (trade_count = 1)
    if trade_count == 1:
        current_time = time.time()
        
        # 🛑 الانتظار (Sleep) - يخرج البوت من الدالة ويعود للدخول في الحلقة التالية
        if current_time - trade_start_time < CHECK_DELAY_SECONDS: 
            return 
        
        # ******** منطق التحقق والإغلاق الحاسم (وقت الصفقة انتهى) *********
        
        current_balance = None
        
        # 1. محاولة جلب الرصيد - محمية بـ try/except (لضمان عدم التعليق)
        try:
            current_balance, currency = get_balance_and_currency(user_token)
        except Exception as e:
            print(f"Error fetching balance for settlement: {e}")
            pass 

        # 2. حساب النتيجة الصافية للدورة بناءً على الرصيد
        if current_balance is not None:
            balance_diff = float(current_balance) - float(balance_before_trade)
            cycle_net_profit = round(balance_diff, 2)
            
            if cycle_net_profit != 0.0:
                 total_wins += 1 
                 total_losses += 1 
        else:
             # 🛑 إذا فشل جلب الرصيد (بعد مرور الوقت): نفترض خسارة الدورة لضمان المضاعفة وعدم التوقف
             total_stake = current_under_amount + current_over_amount
             cycle_net_profit = -round(total_stake, 2)
        
        # 3. منطق المضاعفة (Martingale)
        is_loss = cycle_net_profit < 0
        if is_loss:
            consecutive_net_losses += 1
            
            new_under_bet = base_under_amount * (NET_LOSS_MULTIPLIER ** consecutive_net_losses)
            current_under_amount = round(max(base_under_amount, new_under_bet), 2)
            current_over_amount = round(current_under_amount * BASE_OVER_MULTIPLIER, 2)
            
            print(f"User {email} LOST. Next stakes: Under {current_under_amount}, Over {current_over_amount}. Losses: {consecutive_net_losses}")

        else: 
            print(f"User {email} WON. Profit: {cycle_net_profit}. Resetting stake.")
            consecutive_net_losses = 0
            current_under_amount = base_under_amount 
            current_over_amount = base_over_amount
        
        # 🌟 التحديث النهائي وإعادة التهيئة للدورة الجديدة
        # trade_count=0: هذا يضمن أن البوت سيحاول الدخول في صفقة جديدة فوراً
        update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_under_amount, current_over_amount, consecutive_net_losses, 
                                          trade_count=0, cycle_net_profit=cycle_net_profit, initial_balance=initial_balance, 
                                          under_contract_id=None, over_contract_id=None, trade_start_time=0.0, balance_before_trade=0.0)
        
        # 🛑 شرط وقف الخسارة (SL) - بعد إعادة التهيئة للتأكد من حفظ حالة الخسارة المتتالية
        if consecutive_net_losses >= max_consecutive_losses:
            print(f"User {email} reached Max Consecutive Losses ({max_consecutive_losses}). Stopping session.")
            update_is_running_status(email, 0)
            return 
            
        # 🛑 شرط جني الأرباح (TP)
        if current_balance is not None and (float(current_balance) - initial_balance) >= tp_target:
            print(f"User {email} reached Take Profit target. Stopping session.")
            update_is_running_status(email, 0)
            return

        return # انتهت المرحلة 1 بنجاح
    
    # 🌟 المرحلة 2: الدخول في صفقات جديدة (trade_count = 0)
    elif trade_count == 0 and not check_only: 
        
        ws = None
        try:
            # 1. الاتصال بـ WebSocket
            ws = connect_websocket(user_token)
            if not ws: return 

            # 2. جلب الرصيد والتحقق من TP
            balance, currency = get_balance_and_currency(user_token)
            if balance is None or currency is None: 
                 return 

            # التحقق من الرصيد المبدئي و TP
            if initial_balance == 0:
                initial_balance = float(balance)
                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_under_amount, current_over_amount, consecutive_net_losses, trade_count, cycle_net_profit, initial_balance=initial_balance)
            
            current_profit_vs_initial = float(balance) - initial_balance
            if current_profit_vs_initial >= tp_target and initial_balance != 0:
                 update_is_running_status(email, 0)
                 return
            
            # 3. تخزين الرصيد الأولي قبل الدخول
            balance_before_trade = float(balance) 
            
            # --- 4. تنفيذ صفقة Under 3 ---
            amount_under = max(0.35, round(float(current_under_amount), 2))
            order_response_under = place_order(ws, "DIGITUNDER", amount_under, currency, 3)
            
            # --- 5. تنفيذ صفقة Over 3 ---
            amount_over = max(0.35, round(float(current_over_amount), 2))
            order_response_over = place_order(ws, "DIGITOVER", amount_over, currency, 3)
            
            # التحقق من نجاح الصفقتين
            if 'buy' in order_response_under and 'buy' in order_response_over:
                order_success = True
                print(f"User {email}: Successfully placed DUAL Trade (Under: {amount_under} / Over: {amount_over})")

            else:
                order_success = False
                print(f"User {email}: Failed to place one or both trades.")
                if 'error' in order_response_under:
                    print(f"  Under Error: {order_response_under['error'].get('message', 'Unknown error')}")
                if 'error' in order_response_over:
                    print(f"  Over Error: {order_response_over['error'].get('message', 'Unknown error')}")

            if order_success:
                # --- 6. حفظ بيانات الصفقتين وبدء المراقبة ---
                trade_start_time = time.time() 
                
                # الانتقال لـ trade_count=1 لبدء مرحلة المراقبة
                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_under_amount, current_over_amount, consecutive_net_losses, 
                                                  trade_count=1, cycle_net_profit=0.0, 
                                                  initial_balance=initial_balance, under_contract_id="Active", over_contract_id="Active", trade_start_time=trade_start_time,
                                                  balance_before_trade=balance_before_trade)
            
            return 

        except Exception:
             traceback.print_exc()
             return
        finally:
            # إغلاق الاتصال بعد الانتهاء
            if ws and ws.connected:
                ws.close()
    else: 
        return 


# --- Main Bot Loop Function ---
def bot_loop():
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
                        
                    run_trading_job_for_user(latest_session_data, check_only=False) 
            
            time.sleep(0.1) 
        except Exception:
            time.sleep(5)

# --- Streamlit App Configuration ---
st.set_page_config(page_title="Khoury Bot", layout="wide")
st.title("Khoury Bot 🤖")

if "logged_in" not in st.session_state: st.session_state.logged_in = False
if "user_email" not in st.session_state: st.session_state.user_email = ""
if "stats" not in st.session_state: st.session_state.stats = None
    
create_table_if_not_exists()

# Start Bot Process
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
        
        user_token_val = ""
        base_under_amount_val = 0.35 
        tp_target_val = 100.0
        
        if st.session_state.stats:
            user_token_val = st.session_state.stats.get('user_token', '')
            base_under_amount_val = st.session_state.stats.get('base_under_amount', 0.35)
            tp_target_val = st.session_state.stats.get('tp_target', 100.0)
        
        user_token = st.text_input("Deriv API Token", type="password", value=user_token_val, disabled=is_user_bot_running_in_db)
        
        base_under_amount_input = st.number_input("Base Bet Amount for **Under 3** (min $0.35)", min_value=0.35, value=base_under_amount_val, step=0.1, disabled=is_user_bot_running_in_db)
        
        tp_target = st.number_input("Take Profit Target ($)", min_value=10.0, value=tp_target_val, step=10.0, disabled=is_user_bot_running_in_db)
        
        st.caption(f"Strategy: **Simultaneous** Trades. Over 3 bet is **{BASE_OVER_MULTIPLIER}x** the Under 3 bet. Martingale $\times {NET_LOSS_MULTIPLIER}$ on **Net Loss**. **Stop Loss (SL) is fixed at {MAX_CONSECUTIVE_LOSSES} consecutive net losses.**")
        
        col_start, col_stop = st.columns(2)
        with col_start:
            start_button = st.form_submit_button("Start Bot", disabled=is_user_bot_running_in_db)
        with col_stop:
            stop_button = st.form_submit_button("Stop Bot", disabled=not is_user_bot_running_in_db)
    
    if start_button:
        if not user_token: st.error("Please enter a Deriv API Token to start the bot.")
        else:
            settings = {"user_token": user_token, "base_under_amount_input": base_under_amount_input, "tp_target": tp_target, "max_consecutive_losses": MAX_CONSECUTIVE_LOSSES}
            start_new_session_in_db(st.session_state.user_email, settings)
            st.success("✅ Bot session started successfully!")
            st.rerun()

    if stop_button:
        update_is_running_status(st.session_state.user_email, 0)
        st.info("⏸ Your bot session has been stopped.")
        st.rerun()

    st.markdown("---")
    st.subheader("Performance Monitor")
    
    stats_placeholder = st.empty()
    
    current_global_bot_status = get_bot_running_status()
    if current_global_bot_status == 1:
        st.success("🟢 *Global Bot Service is RUNNING*.")
    else:
        st.error("🔴 *Global Bot Service is STOPPED*.")

    if st.session_state.stats:
        with stats_placeholder.container():
            stats = st.session_state.stats
            
            initial_balance = stats.get('initial_balance', 0.0)
            balance, _ = get_balance_and_currency(stats.get('user_token'))
            current_profit = 0.0
            current_balance_value = 0.0
            if balance is not None:
                 current_balance_value = float(balance)
                 if initial_balance != 0.0:
                    current_profit = current_balance_value - initial_balance
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric(label="Total Wins", value=stats.get('total_wins', 0))
            with col2:
                st.metric(label="Total Losses", value=stats.get('total_losses', 0))
            with col3:
                st.metric(label="Total Profit ($)", value=f"${current_profit:.2f}")

            st.metric(label="Current Balance", value=f"${current_balance_value:.2f}")
            
            trade_count_status = stats.get('trade_count', 0)
            
            if is_user_bot_running_in_db:
                if trade_count_status == 1:
                    st.warning(f"⚠ **Trade Active:** Monitoring result. (Checking balance after {CHECK_DELAY_SECONDS} seconds)")
                else: 
                    st.success(f"✅ **Ready for Trade:** Placing new bets now.")
            else:
                 st.info("⏸ Bot is Stopped. Ready to receive new settings.")

    else:
        with stats_placeholder.container():
            st.info("Please enter your settings and press 'Start Bot' to begin.")
            
    time.sleep(2) 
    st.rerun()
