import time
import json
import websocket 
import threading
import os 
import sys 
import fcntl 
from flask import Flask, request, render_template_string, redirect, url_for, session, flash, g

# ==========================================================
# الإعدادات الثابتة للبوت
# ==========================================================
WSS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL = "R_100"          # زوج Volatility 100 Index
TRADE_TYPE = "DIGITUNDER" # الفوز إذا كان الرقم الأخير أقل من الحاجز (تحت 8)
BARRIER = 8               # الحاجز هو 8
DURATION = 1 
DURATION_UNIT = "t" 
MARTINGALE_STEPS = 4 
MAX_CONSECUTIVE_LOSSES = 3
RECONNECT_DELAY = 1       # فترة انتظار قبل محاولة إعادة الاتصال (1 ثانية)
USER_IDS_FILE = "user_ids.txt"
ACTIVE_SESSIONS_FILE = "active_sessions.json" 
# ==========================================================

# ==========================================================
# حالة البوت في الذاكرة (Runtime Cache)
# ==========================================================
active_threads = {} 
active_ws = {} 
is_contract_open = {} 

DEFAULT_SESSION_STATE = {
    "api_token": "",
    "base_stake": 1.0,
    "tp_target": 10.0,
    "is_running": False,
    "current_profit": 0.0,
    "current_stake": 1.0, 
    "consecutive_losses": 0,
    "current_step": 0,
    "total_wins": 0,
    "total_losses": 0
}
# ==========================================================

# ==========================================================
# دوال إدارة الحالة (الملف الثابت)
# ==========================================================
def get_file_lock(f):
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    except Exception:
        pass

def release_file_lock(f):
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass

def load_persistent_sessions():
    if not os.path.exists(ACTIVE_SESSIONS_FILE):
        return {}
    
    with open(ACTIVE_SESSIONS_FILE, 'a+') as f:
        f.seek(0)
        get_file_lock(f)
        try:
            content = f.read()
            if content:
                data = json.loads(content)
            else:
                data = {}
        except json.JSONDecodeError:
            data = {}
        finally:
            release_file_lock(f)
            return data

def save_session_data(email, session_data):
    all_sessions = load_persistent_sessions()
    all_sessions[email] = session_data
    
    with open(ACTIVE_SESSIONS_FILE, 'w') as f:
        get_file_lock(f)
        try:
            json.dump(all_sessions, f, indent=4)
        except Exception as e:
            print(f"❌ ERROR saving session data: {e}")
        finally:
            release_file_lock(f)

def delete_session_data(email):
    all_sessions = load_persistent_sessions()
    if email in all_sessions:
        del all_sessions[email]
    
    with open(ACTIVE_SESSIONS_FILE, 'w') as f:
        get_file_lock(f)
        try:
            json.dump(all_sessions, f, indent=4)
        except Exception as e:
            print(f"❌ ERROR deleting session data: {e}")
        finally:
            release_file_lock(f)

def get_session_data(email):
    all_sessions = load_persistent_sessions()
    if email in all_sessions:
        return all_sessions[email]
    
    return DEFAULT_SESSION_STATE.copy()

def load_allowed_users():
    if not os.path.exists(USER_IDS_FILE):
        print(f"❌ ERROR: Missing {USER_IDS_FILE} file.")
        return set()
    try:
        with open(USER_IDS_FILE, 'r', encoding='utf-8') as f:
            users = {line.strip().lower() for line in f if line.strip()}
        return users
    except Exception as e:
        print(f"❌ ERROR reading {USER_IDS_FILE}: {e}")
        return set()
        
def stop_bot(email, clear_data=True): 
    """ إيقاف البوت. إذا كانت clear_data=True، يتم مسح البيانات بالكامل (إيقاف صريح). """
    global is_contract_open 
    
    # 1. إغلاق اتصال WebSocket إن وجد
    if email in active_ws and active_ws[email]:
        try:
            ws = active_ws[email]
            ws.send(json.dumps({"forget": "ticks", "symbol": SYMBOL}))
            ws.close()
        except:
            pass
        if email in active_ws:
             del active_ws[email]

    # 2. إزالة تسجيل الخيط من الذاكرة 
    if email in active_threads:
        del active_threads[email]
        
    # 3. تحديث حالة العقد المفتوح
    if email in is_contract_open:
        is_contract_open[email] = False

    if clear_data:
        # 4. حذف الحالة بالكامل (إيقاف صريح من المستخدم)
        delete_session_data(email)
        print(f"🛑 [INFO] Bot for {email} stopped and session data cleared from file.")
    else:
        # 4. تحديث حالة التشغيل فقط (إيقاف تلقائي بسبب الانقطاع)
        current_data = get_session_data(email)
        if current_data.get("is_running") is True:
             current_data["is_running"] = False
             save_session_data(email, current_data)
        print(f"⚠️ [INFO] Bot for {email} stopped by disconnection. Will retry soon.")

# ==========================================================
# دوال البوت التداولي
# ==========================================================

def get_latest_price_digit(price):
    try:
        return int(str(price)[-1]) 
    except Exception:
        return -1

def send_trade_order(email, stake):
    global is_contract_open 
    if email not in active_ws: return
    ws_app = active_ws[email]
    
    trade_request = {
        "buy": 1, "price": stake,
        "parameters": {
            "amount": stake, "basis": "stake",
            "contract_type": TRADE_TYPE, "barrier": BARRIER,
            "currency": "USD", "duration": DURATION,
            "duration_unit": DURATION_UNIT, "symbol": SYMBOL
        }
    }
    try:
        ws_app.send(json.dumps(trade_request))
        is_contract_open[email] = True 
    except Exception as e:
        print(f"❌ [TRADE ERROR] Could not send trade order: {e}")
        pass
        
def check_pnl_limits(email, profit_loss):
    """
    تسجيل الربح/الخسارة وتجهيز البوت للخطوة التالية.
    لا يتم إرسال طلب تداول جديد هنا.
    """
    global is_contract_open 
    
    is_contract_open[email] = False 

    current_data = get_session_data(email)
    if not current_data.get('is_running'): return

    last_stake = current_data['current_stake'] 

    current_data['current_profit'] += profit_loss
    
    if profit_loss > 0:
        current_data['total_wins'] += 1
        current_data['current_step'] = 0  # إعادة تعيين الخطوة عند الربح
        current_data['consecutive_losses'] = 0
        current_data['current_stake'] = current_data['base_stake'] # إعادة تعيين الرهان الأساسي
    else:
        current_data['total_losses'] += 1
        current_data['consecutive_losses'] += 1
        current_data['current_step'] += 1 # زيادة الخطوة (بدون تطبيق المضاعفة فوراً)

        if current_data['consecutive_losses'] >= MAX_CONSECUTIVE_LOSSES:
            stop_bot(email, clear_data=True) 
            return 
        
    if current_data['current_profit'] >= current_data['tp_target']:
        stop_bot(email, clear_data=True) 
        return
    
    save_session_data(email, current_data)
        
    print(f"[LOG {email}] PNL: {current_data['current_profit']:.2f}, Step: {current_data['current_step']}, Last Stake: {last_stake:.2f}")


def bot_core_logic(email, token, stake, tp):
    """ منطق التشغيل الأساسي مع حلقة إعادة الاتصال المستمرة. """
    global is_contract_open 

    is_contract_open[email] = False

    session_data = get_session_data(email)
    session_data.update({
        "api_token": token, "base_stake": stake, "tp_target": tp,
        "is_running": True, "current_stake": stake 
    })
    save_session_data(email, session_data)

    while True: # 👈 حلقة إعادة الاتصال الرئيسية
        current_data = get_session_data(email)
        
        if not current_data.get('is_running'):
            break

        print(f"🔗 [THREAD] Attempting to connect for {email}...")

        def on_open_wrapper(ws_app):
            ws_app.send(json.dumps({"authorize": current_data['api_token']})) 
            ws_app.send(json.dumps({"ticks": SYMBOL, "subscribe": 1}))
            running_data = get_session_data(email)
            running_data['is_running'] = True
            save_session_data(email, running_data)
            print(f"✅ [THREAD] Connection established for {email}.")
            is_contract_open[email] = False 

        def on_message_wrapper(ws_app, message):
            data = json.loads(message)
            msg_type = data.get('msg_type')
            
            current_data = get_session_data(email) 
            if not current_data.get('is_running'):
                ws_app.close()
                return
                
            if msg_type == 'tick':
                # منع الدخول إذا كانت هناك صفقة مفتوحة بالفعل
                if is_contract_open.get(email) is True: 
                    return 

                last_digit = get_latest_price_digit(data['tick']['quote'])
                
                # شرط الدخول: الدخول إذا كان الرقم الأخير هو 2 
                if last_digit == 2: 
                    
                    # --- منطق المضاعفة عند الفرصة القادمة ---
                    current_stake = current_data['base_stake']
                    
                    if current_data['current_step'] > 0:
                        # الرهان الخاسر الأخير محفوظ في current_stake 
                        last_loss_stake = current_data['current_stake'] 
                        
                        if current_data['current_step'] <= MARTINGALE_STEPS:
                            # المضاعفة: الرهان الخاسر * 7
                            current_stake = last_loss_stake * 7
                        else:
                            # العودة للأساسي
                            current_stake = current_data['base_stake'] 

                    # تحديث الـ stake المحسوب
                    current_data['current_stake'] = current_stake
                    save_session_data(email, current_data)

                    send_trade_order(email, current_stake)
                    # ----------------------------------------

            elif msg_type == 'buy':
                contract_id = data['buy']['contract_id']
                ws_app.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}))
            elif msg_type == 'proposal_open_contract':
                contract = data['proposal_open_contract']
                if contract.get('is_sold') == 1:
                    check_pnl_limits(email, contract['profit']) 
                    if 'subscription_id' in data: ws_app.send(json.dumps({"forget": data['subscription_id']}))

        def on_close_wrapper(ws_app, code, msg):
             # عند الإغلاق التلقائي، يتم إيقاف تشغيل الخيط مع الاحتفاظ بالبيانات (clear_data=False)
             stop_bot(email, clear_data=False) 

        try:
            ws = websocket.WebSocketApp(
                WSS_URL, on_open=on_open_wrapper, on_message=on_message_wrapper, 
                on_error=lambda ws, err: print(f"[WS Error {email}] {err}"),
                on_close=on_close_wrapper 
            )
            active_ws[email] = ws
            ws.run_forever(ping_interval=20, ping_timeout=10) 
            
        except Exception as e:
            print(f"❌ [ERROR] WebSocket failed for {email}: {e}")
        
        # الانتظار قبل المحاولة التالية لإعادة الاتصال
        if get_session_data(email).get('is_running') is False:
             break
        
        print(f"💤 [THREAD] Waiting {RECONNECT_DELAY} seconds before retrying connection for {email}...")
        time.sleep(RECONNECT_DELAY)

    if email in active_threads:
        del active_threads[email] 
    print(f"🛑 [THREAD] Bot process ended for {email}.")


# ==========================================================
# إعداد تطبيق FLASK ومساراته
# ==========================================================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET_KEY', 'VERY_STRONG_SECRET_KEY_RENDER_BOT')

# قوالب HTML (AUTH_FORM)
AUTH_FORM = """
<!doctype html>
<title>Login - Deriv Bot</title>
<h1>تسجيل الدخول - بوت Deriv</h1>
<p>يرجى إدخال بريدك الإلكتروني المفعّل:</p>
{% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
        {% for category, message in messages %}
            <p style="color:red;">{{ message }}</p>
        {% endfor %}
    {% endif %}
{% endwith %}
<form method="POST" action="{{ url_for('login') }}">
    <label for="email">البريد الإلكتروني:</label><br>
    <input type="email" id="email" name="email" size="50" required><br><br>
    <button type="submit" style="background-color: blue; color: white; padding: 10px;">دخول</button>
</form>
"""

# قوالب HTML (CONTROL_FORM)
CONTROL_FORM = """
<!doctype html>
<title>Control Panel</title>
<h1>لوحة تحكم البوت | المستخدم: {{ email }}</h1>
<hr>

{% if session_data and session_data.is_running %}
    <p style="color: green; font-size: 1.2em;">✅ البوت قيد التشغيل! (يرجى التحديث يدوياً)</p>
    <p>صافي الربح الكلي: ${{ session_data.current_profit|round(2) }}</p>
    <p>الرهان الحالي: ${{ session_data.current_stake|round(2) }}</p>
    <p>الخطوة: {{ session_data.current_step }} / {{ martingale_steps }}</p>
    <p>الإحصائيات: {{ session_data.total_wins }} رابح | {{ session_data.total_losses }} خاسر</p>
    
    <form method="POST" action="{{ url_for('stop_route') }}">
        <button type="submit" style="background-color: red; color: white; padding: 10px;">🛑 إيقاف البوت</button>
    </form>
{% else %}
    <p style="color: red; font-size: 1.2em;">🛑 البوت متوقف. يرجى إدخال الإعدادات لبدء جلسة جديدة.</p>
    <form method="POST" action="{{ url_for('start_bot') }}">
        <label for="token">Deriv API Token:</label><br>
        <input type="text" id="token" name="token" size="50" required value="{{ session_data.api_token if session_data else '' }}" {% if session_data and session_data.api_token and session_data.is_running is not none %}readonly{% endif %}><br><br>
        
        <label for="stake">Base Stake (USD):</label><br>
        <input type="number" id="stake" name="stake" value="{{ session_data.base_stake|round(2) if session_data else 0.35 }}" step="0.01" min="0.35" required><br><br>
        
        <label for="tp">TP Target (USD):</label><br>
        <input type="number" id="tp" name="tp" value="{{ session_data.tp_target|round(2) if session_data else 10.0 }}" step="0.01" required><br><br>
        
        <button type="submit" style="background-color: green; color: white; padding: 10px;">🚀 بدء التشغيل</button>
    </form>
{% endif %}
<hr>
<a href="{{ url_for('logout') }}">تسجيل الخروج</a>
"""

# ==========================================================
# مسارات FLASK (Routes)
# ==========================================================

@app.route('/')
def index():
    if 'email' not in session:
        return redirect(url_for('auth_page'))
    
    email = session['email']
    session_data = get_session_data(email)

    return render_template_string(CONTROL_FORM, 
        email=email,
        session_data=session_data,
        martingale_steps=MARTINGALE_STEPS
    )

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].lower()
        allowed_users = load_allowed_users()
        
        if email in allowed_users:
            session['email'] = email
            flash('تم الدخول بنجاح.', 'success')
            return redirect(url_for('index'))
        else:
            flash('البريد الإلكتروني غير مفعل.', 'error')
            return redirect(url_for('auth_page'))
    
    return redirect(url_for('auth_page'))

@app.route('/auth')
def auth_page():
    if 'email' in session:
        return redirect(url_for('index'))
    return render_template_string(AUTH_FORM)

@app.route('/start', methods=['POST'])
def start_bot():
    if 'email' not in session:
        return redirect(url_for('auth_page'))
    
    email = session['email']
    
    if email in active_threads and active_threads[email].is_alive():
        flash('البوت يعمل بالفعل.', 'info')
        return redirect(url_for('index'))
        
    try:
        current_data = get_session_data(email)
        if current_data.get('api_token') and request.form.get('token') == current_data['api_token']:
            token = current_data['api_token']
        else:
            token = request.form['token']

        stake = float(request.form['stake'])
        tp = float(request.form['tp'])
    except ValueError:
        flash("قيمة غير صحيحة للرهان أو TP.", 'error')
        return redirect(url_for('index'))
        
    thread = threading.Thread(target=bot_core_logic, args=(email, token, stake, tp))
    thread.daemon = True
    thread.start()
    active_threads[email] = thread
    
    flash('بدأ تشغيل البوت بنجاح. سيحاول الاتصال وإعادة الاتصال تلقائياً.', 'success')
    return redirect(url_for('index'))

@app.route('/stop', methods=['POST'])
def stop_route():
    if 'email' not in session:
        return redirect(url_for('auth_page'))
    
    stop_bot(session['email'], clear_data=True) 
    flash('تم إيقاف البوت ومسح بيانات الجلسة.', 'success')
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('email', None)
    flash('تم تسجيل الخروج.', 'success')
    return redirect(url_for('auth_page'))


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
