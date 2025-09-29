import streamlit as st
import time
import websocket
import json
import os
import decimal
import sqlite3
import multiprocessing

# --- Strategy Configuration ---
TRADING_SYMBOL = "R_100"      
CONTRACT_DURATION = 2         
CONTRACT_DURATION_UNIT = 't'  
NET_LOSS_MULTIPLIER = 12.0     
BASE_OVER_MULTIPLIER = 2.5    
MAX_CONSECUTIVE_LOSSES = 3    
CONTRACT_EXPECTED_DURATION = 7
CHECK_DELAY_SECONDS = CONTRACT_EXPECTED_DURATION + 1 # 6 seconds

# --- SQLite Database Configuration ---
DB_FILE = "trading_data_unique_martingale_balance.db" 

# --- Database & Utility Functions (No changes needed here) ---
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
            
            # Ensure columns exist (for robustness)
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
    try:
        with open("user_ids.txt", "r") as file:
            active_users = [line.strip() for line in file.readlines()]
        return email in active_users
    except FileNotFoundError:
        return False
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

# --- WebSocket Helper Functions (No changes needed here) ---
def connect_websocket(user_token):
    ws = websocket.WebSocket()
    try:
        # A smaller timeout to allow quicker retry
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=1) 
        auth_req = {"authorize": user_token}
        ws.send(json.dumps(auth_req))
        # Wait up to 3 seconds for auth response
        for _ in range(30):
            try:
                response_str = ws.recv()
                if response_str:
                    auth_response = json.loads(response_str)
                    if auth_response.get('msg_type') == 'authorize' or auth_response.get('error'): 
                        return ws
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
    # نستخدم 3 محاولات لجلب الرصيد لزيادة المتانة
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
            for _ in range(5): 
                response_str = ws.recv()
                if response_str:
                    balance_response = json.loads(response_str)
                    if balance_response.get('msg_type') == 'balance':
                        balance_info = balance_response.get('balance', {})
                        return balance_info.get('balance'), balance_info.get('currency')
                    elif balance_response.get('error'):
                        break
                time.sleep(0.2) 
            
            if ws and ws.connected: ws.close()
            time.sleep(0.5)

        except Exception: 
            if ws and ws.connected: ws.close()
            time.sleep(0.5)
            continue
        finally:
            if ws and ws.connected: ws.close()
            
    return None, None

def place_order(ws, contract_type, amount, currency, barrier):
    if not ws or not ws.connected: return {"error": {"message": "WebSocket not connected."}}
    amount_decimal = decimal.Decimal(str(amount)).quantize(decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_UP)
    
    # 1. Proposal
    proposal_req = {
        "proposal": 1, "amount": float(amount_decimal), "basis": "stake",
        "contract_type": contract_type,  
        "currency": currency,
        "duration": CONTRACT_DURATION, "duration_unit": CONTRACT_DURATION_UNIT, 
        "symbol": TRADING_SYMBOL,
        "barrier": barrier 
    }
    ws.send(json.dumps(proposal_req))
    
    # 2. Await Proposal Response 
    proposal_response = None
    for i in range(5):
        try:
            response_str = ws.recv()
            if response_str:
                proposal_response = json.loads(response_str)
                if proposal_response.get('error'): return proposal_response
                if 'proposal' in proposal_response: break
            time.sleep(0.1) 
        except Exception: continue
    
    if proposal_response and 'proposal' in proposal_response:
        proposal_id = proposal_response['proposal']['id']
        
        # 3. Buy Order
        buy_req = {"buy": proposal_id, "price": float(amount_decimal)}
        ws.send(json.dumps(buy_req))
        
        # نستخدم مهلة زمنية للخروج من حلقة الـ recv لضمان عدم التعليق اللانهائي
        start_time = time.time()
        while time.time() - start_time < 5: 
            try:
                response_str = ws.recv()
                if response_str:
                    response = json.loads(response_str)
                    if response.get('msg_type') == 'buy': return response
                    elif response.get('error'): return response
                time.sleep(0.1)
            except Exception:
                return {"error": {"message": "WebSocket read error during buy confirmation."}}
    
    return {"error": {"message": "Order placement failed or proposal missing."}}


# --- Trading Bot Logic (The Core Logic - Max Robustness) ---

def run_trading_job_for_user(session_data, check_only=False):
    email = session_data['email']
    user_token = session_data['user_token']
    
    tp_target = session_data['tp_target']
    max_consecutive_losses = session_data['max_consecutive_losses']
    total_wins = session_data['total_wins']
    total_losses = session_data['total_losses']
    base_under_amount = session_data['base_under_amount']
    base_over_amount = session_data['base_over_amount']
    current_under_amount = session_data['current_under_amount']
    current_over_amount = session_data['current_over_amount']
    consecutive_net_losses = session_data['consecutive_net_losses']
    trade_count = session_data['trade_count']
    cycle_net_profit = session_data['cycle_net_profit'] 
    initial_balance = session_data['initial_balance']
    trade_start_time = session_data['trade_start_time']
    balance_before_trade = session_data['balance_before_trade']
    
    
    # 🌟 المرحلة 1: التحقق من النتيجة وإعادة التعيين (trade_count = 1)
    if trade_count == 1:
        current_time = time.time()
        
        # 🛑 الانتظار (Sleep) - يخرج البوت من الدالة ويعود للدخول في الحلقة التالية
        if current_time - trade_start_time < CHECK_DELAY_SECONDS: 
            return 
        
        # ******** منطق التحقق والإغلاق الحاسم (وقت الصفقة انتهى) *********
        
        current_balance = None
        
        # 1. محاولة جلب الرصيد
        try:
            current_balance, currency = get_balance_and_currency(user_token)
        except Exception:
            pass 

        # 2. حساب النتيجة الصافية وتحديث الإحصائيات 
        balance_before_trade_float = float(balance_before_trade) if balance_before_trade is not None else 0.0
        
        if current_balance is not None and balance_before_trade_float != 0.0:
            balance_diff = float(current_balance) - balance_before_trade_float
            cycle_net_profit = round(balance_diff, 2)
            
            # 💡 التعديل هنا: نعتبر الدورة صفقة واحدة (ربح صافي أو خسارة صافية)
            if cycle_net_profit > 0:
                total_wins += 1    
            elif cycle_net_profit < 0:
                total_losses += 1  
        else:
            # 🛑 إذا فشل جلب الرصيد: نفترض خسارة الدورة للحماية والمضاعفة
            cycle_net_profit = -round(current_under_amount + current_over_amount, 2)
            total_losses += 1 # نزيد الخسارة ونضاعف

        # 3. منطق المضاعفة (Martingale)
        if cycle_net_profit < 0:
            consecutive_net_losses += 1
            
            # حساب المبلغ الجديد للمضاعفة
            new_under_bet = base_under_amount * (NET_LOSS_MULTIPLIER ** consecutive_net_losses)
            current_under_amount = round(max(base_under_amount, new_under_bet), 2)
            current_over_amount = round(current_under_amount * BASE_OVER_MULTIPLIER, 2)
            
        else:
            # ربح صافي: نعود إلى الرهان الأساسي ونصفر الخسائر المتتالية
            consecutive_net_losses = 0
            current_under_amount = base_under_amount
            current_over_amount = base_over_amount
        
        # 🛑 شرط وقف الخسارة (SL) 
        if consecutive_net_losses >= max_consecutive_losses:
             # تحديث الحالة النهائية
             update_stats_and_trade_info_in_db(email, total_wins, total_losses, base_under_amount, base_over_amount, consecutive_net_losses, trade_count=0, cycle_net_profit=cycle_net_profit, initial_balance=initial_balance)
             update_is_running_status(email, 0) # إيقاف البوت
             return 

        # 🌟 التحديث النهائي: إعادة تهيئة الدورة الجديدة
        # الانتقال لـ trade_count=0 لتمكين الدخول الفوري في الصفقة التالية
        update_stats_and_trade_info_in_db(
            email, total_wins, total_losses, current_under_amount, current_over_amount, consecutive_net_losses, 
            trade_count=0, # 🔥 الأهم: إعادة تعيين الحالة لـ 0
            cycle_net_profit=cycle_net_profit, # نحتفظ بالربح الصافي الأخير للعرض
            initial_balance=initial_balance, 
            under_contract_id=None, over_contract_id=None, 
            trade_start_time=0.0, balance_before_trade=0.0
        )
        
        return # انتهت المرحلة 1 بنجاح
    
    # 🌟 المرحلة 2: الدخول في صفقات جديدة (trade_count = 0)
    elif trade_count == 0 and not check_only: 
        
        ws = None
        try:
            # 1. الاتصال بـ WebSocket لـ جلب الرصيد ووضع الصفقة
            ws = connect_websocket(user_token)
            if not ws: return 

            # 2. جلب الرصيد والتحقق من TP
            balance, currency = get_balance_and_currency(user_token)
            if balance is None: 
                return 

            # التحقق من الرصيد المبدئي و TP
            if initial_balance == 0:
                initial_balance = float(balance)
                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_under_amount, current_over_amount, consecutive_net_losses, trade_count, cycle_net_profit, initial_balance=initial_balance)
            
            current_profit_vs_initial = float(balance) - initial_balance
            if current_profit_vs_initial >= tp_target and initial_balance != 0:
                 update_is_running_status(email, 0) # تحقيق الهدف
                 return
            
            # 3. تخزين الرصيد الأولي قبل الدخول
            balance_before_trade = float(balance) 
            
            order_success = False
            
            # --- 4. تنفيذ صفقة Under 3 ---
            amount_under = max(0.35, round(float(current_under_amount), 2))
            order_response_under = place_order(ws, "DIGITUNDER", amount_under, currency, 3)
            
            # --- 5. تنفيذ صفقة Over 3 ---
            amount_over = max(0.35, round(float(current_over_amount), 2))
            order_response_over = place_order(ws, "DIGITOVER", amount_over, currency, 3)
            
            # التحقق من نجاح الصفقتين
            if 'buy' in order_response_under and 'buy' in order_response_over:
                order_success = True

            if order_success:
                # --- 6. حفظ بيانات الصفقتين وبدء المراقبة ---
                trade_start_time = time.time() 
                
                # الانتقال لـ trade_count=1 (للتوقف والانتظار)
                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_under_amount, current_over_amount, consecutive_net_losses, 
                                                  trade_count=1, cycle_net_profit=0.0, 
                                                  initial_balance=initial_balance, under_contract_id="Active", over_contract_id="Active", trade_start_time=trade_start_time,
                                                  balance_before_trade=balance_before_trade)
                
                return 

        except Exception as e:
            # يمكن إضافة طباعة الخطأ هنا للمساعدة في التشخيص
            # print(f"Error in Phase 2: {e}")
            return
        finally:
            # إغلاق الاتصال بعد الانتهاء
            if ws and ws.connected:
                ws.close()
    else: 
        return 


# --- Main Bot Loop Function (No changes needed here) ---
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
        except Exception as e:
            time.sleep(5)

# --- Streamlit App Configuration (No changes needed here) ---
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
        
        base_under_amount_input = st.number_input("Base Bet Amount for *Under 3* (min $0.35)", min_value=0.35, value=base_under_amount_val, step=0.1, disabled=is_user_bot_running_in_db)
        
        tp_target = st.number_input("Take Profit Target ($)", min_value=10.0, value=tp_target_val, step=10.0, disabled=is_user_bot_running_in_db)
        
        st.caption(f"Strategy: *Simultaneous* Trades. Over 3 bet is *{BASE_OVER_MULTIPLIER}x* the Under 3 bet. Martingale $\times *{NET_LOSS_MULTIPLIER}$ on **Net Loss. **Stop Loss (SL) is fixed at {MAX_CONSECUTIVE_LOSSES} consecutive net losses.*")
        
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
        st.success("🟢 Global Bot Service is RUNNING.")
    else:
        st.error("🔴 Global Bot Service is STOPPED.")

    if st.session_state.stats:
        with stats_placeholder.container():
            stats = st.session_state.stats
            
            initial_balance = stats.get('initial_balance', 0.0)
            balance, _ = get_balance_and_currency(stats.get('user_token'))
            current_profit = 0.0
            if balance is not None and initial_balance != 0.0:
                 current_profit = float(balance) - initial_balance
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric(label="Total Wins", value=stats.get('total_wins', 0))
            with col2:
                st.metric(label="Total Losses", value=stats.get('total_losses', 0))
            with col3:
                st.metric(label="Total Profit ($)", value=f"${current_profit:.2f}")

            if balance is not None:
                 st.metric(label="Current Balance", value=f"${float(balance):.2f}")
            
            trade_count_status = stats.get('trade_count', 0)
            
            # تحديث عرض حالة الصفقة ليتناسب مع طلبك
            if trade_count_status == 1:
                st.warning(f"⚠ *Trade Active:* Monitoring result. (Checking balance after {CHECK_DELAY_SECONDS} seconds)")
            else: 
                st.success(f"✅ *Ready for Trade:* Placing new bets now.")
            
    else:
        with stats_placeholder.container():
            st.info("Please enter your settings and press 'Start Bot' to begin.")
            
    time.sleep(2) 
    st.rerun()
