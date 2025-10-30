import time
import json
import websocket 
import threading
import os 
import sys 
import fcntl # مطلوب لقفل الملفات (لبيئة الإنتاج)
from flask import Flask, request, render_template_string, redirect, url_for, session, flash, g

# ==========================================================
# الإعدادات الثابتة للبوت
# ==========================================================
WSS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"
SYMBOL = "R_10"
TRADE_TYPE = "DIGITUNDER"
BARRIER = 8
DURATION = 1 
DURATION_UNIT = "t" 
MARTINGALE_STEPS = 4 
MAX_CONSECUTIVE_LOSSES = 3
USER_IDS_FILE = "user_ids.txt"
ACTIVE_SESSIONS_FILE = "active_sessions.json" # قاعدة البيانات الدائمة

# ==========================================================
# حالة البوت في الذاكرة (Runtime Cache)
# ==========================================================
active_threads = {} 
active_ws = {} 

# القالب الافتراضي لجلسة مستخدم جديد
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
# دوال إدارة الحالة (الملف الثابت)
# ==========================================================

def get_file_lock(f):
    """ تطبيق قفل حصري للكتابة على الملف """
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    except Exception:
        pass

def release_file_lock(f):
    """ تحرير قفل الملف """
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass

def load_persistent_sessions():
    """ تحميل جميع الجلسات المحفوظة من الملف """
    if not os.path.exists(ACTIVE_SESSIONS_FILE):
        return {}
    
    # استخدام 'a+' بدلاً من 'r' لإنشاء الملف إذا لم يكن موجوداً
    with open(ACTIVE_SESSIONS_FILE, 'a+') as f:
        f.seek(0) # العودة إلى بداية الملف للقراءة
        get_file_lock(f)
        try:
            content = f.read()
            if content:
                data = json.loads(content)
            else:
                data = {} # ملف فارغ
        except json.JSONDecodeError:
            data = {}
        finally:
            release_file_lock(f)
            return data

def save_session_data(email, session_data):
    """ حفظ حالة جلسة مستخدم واحد إلى الملف """
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
    """ حذف جلسة مستخدم بالكامل من الملف """
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

# ==========================================================
# دوال إدارة الحالة والمنطق
# ==========================================================

def get_session_data(email):
    """ جلب بيانات الجلسة من الملف الثابت """
    all_sessions = load_persistent_sessions()
    if email in all_sessions:
        return all_sessions[email]
    
    return DEFAULT_SESSION_STATE.copy()

def load_allowed_users():
    """ تحميل الإيميلات المسموح بها من user_ids.txt """
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

def stop_bot(email):
    """ إيقاف البوت وحذف جميع بيانات الجلسة من الملف الثابت """
    
    # 1. إيقاف الاتصال والخيوط
    if email in active_ws and active_ws[email]:
        try:
            ws = active_ws[email]
            ws.send(json.dumps({"forget": "ticks", "symbol": SYMBOL}))
            ws.close()
        except:
            pass
        if email in active_ws:
             del active_ws[email]

    # 2. إزالة تسجيل الخيط من الذاكرة (للسماح ببدء خيط جديد)
    if email in active_threads:
        del active_threads[email]

    # 3. حذف حالة المستخدم بالكامل من الملف الثابت (Clean Slate)
    delete_session_data(email)
    
    print(f"🛑 [INFO] Bot for {email} stopped and session data cleared from file.")

# ==========================================================
# دوال البوت التداولي
# ==========================================================

def get_latest_price_digit(price):
    try:
        # الرقم الأخير من السعر
        return int(str(price)[-1]) 
    except Exception:
        return -1

def send_trade_order(email, stake):
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
    except:
        pass
        
def check_pnl_limits(email, profit_loss):
    current_data = get_session_data(email)
    if not current_data.get('is_running'): return

    # تحديث البيانات محلياً
    current_data['current_profit'] += profit_loss
    
    if profit_loss > 0:
        current_data['total_wins'] += 1
        current_data['current_step'] = 0
        current_data['current_stake'] = current_data['base_stake']
        current_data['consecutive_losses'] = 0
    else:
        current_data['total_losses'] += 1
        current_data['consecutive_losses'] += 1
        
        # حالة Max Loss: يستدعي stop_bot
        if current_data['consecutive_losses'] >= MAX_CONSECUTIVE_LOSSES:
            stop_bot(email)
            return 
        
        current_data['current_step'] += 1
        
        # تطبيق المارتينجال
        if current_data['current_step'] < MARTINGALE_STEPS:
            current_data['current_stake'] *= 7
            send_trade_order(email, current_data['current_stake']) 
        else:
            # إعادة التعيين بعد تجاوز خطوات المارتينجال
            current_data['current_step'] = 0
            current_data['current_stake'] = current_data['base_stake']
            send_trade_order(email, current_data['current_stake'])

    # حالة TP Target: يستدعي stop_bot
    if current_data['current_profit'] >= current_data['tp_target']:
        stop_bot(email)
        return
    
    # حفظ التحديث إلى الملف الثابت
    save_session_data(email, current_data)
        
    print(f"[LOG {email}] PNL: {current_data['current_profit']:.2f}, Stake: {current_data['current_stake']:.2f}")

def bot_core_logic(email, token, stake, tp):
    # عند البدء، نقوم بتهيئة الحالة في الملف الثابت
    session_data = DEFAULT_SESSION_STATE.copy()
    session_data.update({
        "api_token": token, "base_stake": stake, "tp_target": tp,
        "is_running": True, "current_stake": stake
    })
    
    # حفظ حالة البدء (Running) إلى الملف الثابت
    save_session_data(email, session_data)

    def on_open_wrapper(ws_app):
        ws_app.send(json.dumps({"authorize": token}))
        ws_app.send(json.dumps({"ticks": SYMBOL, "subscribe": 1}))

    def on_message_wrapper(ws_app, message):
        data = json.loads(message)
        current_data = get_session_data(email) 
        
        if not current_data.get('is_running'):
            ws_app.close()
            return
            
        if data.get('msg_type') == 'tick':
            last_digit = get_latest_price_digit(data['tick']['quote'])
            
            # شرط الدخول: الدخول بـ Base Stake فقط إذا كانت الخسائر المتتالية صفر والرقم الأخير هو 9
            if current_data.get('is_running') and current_data['consecutive_losses'] == 0 and last_digit == 9: 
                 send_trade_order(email, current_data['current_stake'])

        # 🛑🛑🛑 تم تصحيح الخطأ في هذا السطر (إزالة القوس الزائد) 🛑🛑🛑
        elif data.get('msg_type') == 'buy':
            contract_id = data['buy']['contract_id']
            ws_app.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}))
        elif data.get('msg_type') == 'proposal_open_contract':
            contract = data['proposal_open_contract']
            if contract.get('is_sold') == 1:
                check_pnl_limits(email, contract['profit']) 
                if 'subscription_id' in data: ws_app.send(json.dumps({"forget": data['subscription_id']}))

    try:
        ws = websocket.WebSocketApp(
            WSS_URL, on_open=on_open_wrapper, on_message=on_message_wrapper, 
            on_error=lambda ws, err: print(f"[WS Error {email}] {err}"),
            on_close=lambda ws, code, msg: stop_bot(email)
        )
        active_ws[email] = ws
        ws.run_forever(ping_interval=20, ping_timeout=10) 
        
    except Exception as e:
        print(f"❌ [ERROR] Bot failed for {email}: {e}")
        stop_bot(email)
    
    stop_bot(email) 


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

# قوالب HTML (CONTROL_FORM) - تم إزالة التحديث التلقائي
CONTROL_FORM = """
<!doctype html>
<title>Control Panel</title>
{# ⚠️ ملاحظة: تم إزالة التحديث التلقائي لتتمكن من إدخال الـ Token، يجب تحديث الصفحة يدوياً (F5) #}
<h1>لوحة تحكم البوت | المستخدم: {{ email }}</h1>
<hr>

{% if session_data and session_data.is_running %}
    <p style="color: green; font-size: 1.2em;">✅ البوت قيد التشغيل! (يرجى التحديث يدوياً)</p>
    <p>صافي الربح الكلي: ${{ session_data.current_profit|round(2) }}</p>
    <p>الرهان الحالي: ${{ session_data.current_stake|round(2) }}</p>
    <p>الخطوة: {{ session_data.current_step + 1 }} / {{ martingale_steps }}</p>
    <p>الإحصائيات: {{ session_data.total_wins }} رابح | {{ session_data.total_losses }} خاسر</p>
    
    <form method="POST" action="{{ url_for('stop_route') }}">
        <button type="submit" style="background-color: red; color: white; padding: 10px;">🛑 إيقاف البوت</button>
    </form>
{% else %}
    <p style="color: red; font-size: 1.2em;">🛑 البوت متوقف. يرجى إدخال الإعدادات لبدء جلسة جديدة.</p>
    <form method="POST" action="{{ url_for('start_bot') }}">
        <label for="token">Deriv API Token:</label><br>
        <input type="text" id="token" name="token" size="50" required value=""><br><br>
        
        <label for="stake">Base Stake (USD):</label><br>
        <input type="number" id="stake" name="stake" value="{{ session_data.base_stake|round(2) if session_data else 1.0 }}" step="0.01" required><br><br>
        
        <label for="tp">TP Target (USD):</label><br>
        <input type="number" id="tp" name="tp" value="{{ session_data.tp_target|round(2) if session_data else 10.0 }}" step="0.01" required><br><br>
        
        <button type="submit" style="background-color: green; color: white; padding: 10px;">🚀 بدء التشغيل</button>
    </form>
{% endif %}
<hr>
<a href="{{ url_for('logout') }}">تسجيل الخروج</a>
"""


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
    
    if email in active_threads:
        flash('البوت يعمل بالفعل. يرجى إيقافه أولاً.', 'info')
        return redirect(url_for('index'))
        
    try:
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
    
    flash('بدأ تشغيل البوت بنجاح.', 'success')
    return redirect(url_for('index'))

@app.route('/stop', methods=['POST'])
def stop_route():
    if 'email' not in session:
        return redirect(url_for('auth_page'))
    
    stop_bot(session['email'])
    flash('تم إيقاف البوت ومسح بيانات الجلسة.', 'success')
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('email', None)
    flash('تم تسجيل الخروج.', 'success')
    return redirect(url_for('auth_page'))


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    # هذا الجزء سيتم تجاوزه إذا استخدمت Gunicorn (الموصى به)
    app.run(host='0.0.0.0', port=port, debug=False)
