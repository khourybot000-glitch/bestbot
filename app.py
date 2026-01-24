import websocket, json, time, multiprocessing, os
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
# التوكن الجديد المستبدل
TOKEN = "8433565422:AAFU4uzvwn8PJl-qfeJRzoSOd0cfOOpd900"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']

# قاموس لتخزين جلسات المستخدمين بشكل مستقل (يدعم مئات الأجهزة)
user_sessions = {}

# --- UTILS (Infinite Retry Connection) ---
def get_ws_connection(api_token):
    """يحاول الاتصال بشكل دائم حتى ينجح لضمان عدم الانقطاع"""
    while True:
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
            ws.send(json.dumps({"authorize": api_token}))
            res = json.loads(ws.recv())
            if "authorize" in res: return ws
            ws.close()
        except: pass
        time.sleep(1)

# --- TRADING LOGIC ---
def analyze_price_difference(ticks):
    if len(ticks) < 15: return None
    diff = ticks[-1] - ticks[-15]
    if diff >= 1.5: return "CALL"
    elif diff <= -1.5: return "PUT"
    return None

def trade_engine(chat_id):
    """محرك التداول المستقل لكل مستخدم/جهاز"""
    session = user_sessions.get(chat_id)
    if not session: return

    last_processed_minute = -1
    while session.get("is_running"):
        try:
            now = datetime.now()
            if now.second == 30 and now.minute != last_processed_minute:
                # التأكد من أن البريد لا يزال مفعلاً في قاعدة البيانات قبل كل صفقة
                user_db = users_col.find_one({"email": session['email']})
                if not user_db or datetime.strptime(user_db['expiry'], "%Y-%m-%d") < datetime.now():
                    bot.send_message(chat_id, "🚫 انتهى اشتراكك. تم إيقاف البوت تلقائياً.")
                    session["is_running"] = False
                    break

                # التحليل باستخدام أول توكن في الجلسة
                test_ws = get_ws_connection(session['tokens'][0])
                test_ws.send(json.dumps({"ticks_history": "R_100", "count": 15, "end": "latest", "style": "ticks"}))
                history = json.loads(test_ws.recv()).get("history", {}).get("prices", [])
                sig = analyze_price_difference(history)
                test_ws.close()

                if sig:
                    # تنفيذ الصفقات لجميع الحسابات المرتبطة بهذه الجلسة
                    for token in session['tokens']:
                        execute_trade(token, sig, chat_id)
                    last_processed_minute = now.minute
                    
                    # انتظار 40 ثانية كما طلبت لجلب النتائج
                    time.sleep(40)
                    for token in session['tokens']:
                        check_result(token, chat_id)
            
            time.sleep(1)
        except:
            time.sleep(1)

def execute_trade(token, direction, chat_id):
    session = user_sessions[chat_id]
    acc = session['accounts_data'][token]
    if acc.get("stopped"): return

    ws = get_ws_connection(token)
    amount = round(float(acc["current_stake"]), 2)
    bar = "-1.5" if direction == "CALL" else "+1.5"
    req = {"proposal": 1, "amount": amount, "basis": "stake", "contract_type": direction, "currency": "USD", "duration": 30, "duration_unit": "s", "symbol": "R_100", "barrier": bar}
    
    try:
        ws.send(json.dumps(req))
        res = json.loads(ws.recv())
        prop = res.get("proposal")
        if prop:
            ws.send(json.dumps({"buy": prop["id"], "price": amount}))
            buy_res = json.loads(ws.recv())
            if "buy" in buy_res:
                acc["active_contract"] = buy_res["buy"]["contract_id"]
        ws.close()
    except: pass

def check_result(token, chat_id):
    session = user_sessions[chat_id]
    acc = session['accounts_data'][token]
    if not acc.get("active_contract"): return

    ws = get_ws_connection(token)
    try:
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": acc["active_contract"]}))
        res = json.loads(ws.recv())
        contract = res.get("proposal_open_contract", {})
        if contract.get("is_expired") == 1:
            profit = float(contract.get("profit", 0))
            if profit > 0:
                acc["win_count"] += 1
                acc["consecutive_losses"] = 0
                acc["current_stake"] = session["initial_stake"]
                status = "✅ WIN"
            else:
                acc["loss_count"] += 1
                acc["consecutive_losses"] += 1
                acc["current_stake"] *= 19 # الضرب في 19 عند الخسارة
                status = "❌ LOSS"
            
            acc["total_profit"] += profit
            acc["active_contract"] = None
            
            # عرض النتيجة والإحصائيات
            msg = f"{status} ({profit:.2f})\n✅ Wins: {acc['win_count']} | ❌ Loss: {acc['loss_count']}\n🔄 MG: {acc['consecutive_losses']}/2\n💰 Profit: {acc['total_profit']:.2f}"
            bot.send_message(chat_id, msg)
            
            if acc["consecutive_losses"] >= 2:
                acc["stopped"] = True
                bot.send_message(chat_id, f"🛑 توقف الحساب {token[:5]}... بعد خسارتين.")
        ws.close()
    except: pass

# --- FLASK ADMIN PANEL ---
@app.route('/')
def index():
    users = list(users_col.find())
    html = """
    <!DOCTYPE html><html><head><title>Admin Dashboard</title>
    <style>
        body{font-family:Arial; text-align:center; background:#f0f2f5; padding-top: 50px;} 
        table{margin:auto; border-collapse:collapse; width:80%; background:#fff; border-radius:8px; overflow:hidden; box-shadow:0 0 10px rgba(0,0,0,0.1);} 
        th,td{padding:15px; border:1px solid #ddd;} th{background:#333; color:white;}
        form{background:white; padding:20px; display:inline-block; border-radius:8px; margin-bottom:20px; box-shadow:0 0 10px rgba(0,0,0,0.1);}
        input, select, button{padding:10px; margin:5px;} button{background:#007bff; color:white; border:none; cursor:pointer;}
    </style></head>
    <body><h2>User Access Management</h2>
    <form action="/add" method="POST">
        <input type="email" name="email" placeholder="User Email" required>
        <select name="days">
            <option value="1">1 Day</option><option value="30">30 Days</option><option value="36500">Life Time</option>
        </select>
        <button type="submit">Add User</button>
    </form><br>
    <table><tr><th>User Email</th><th>Expiry Date</th><th>Action</th></tr>
    {% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" style="color:red; text-decoration:none;">Remove</a></td></tr>{% endfor %}
    </table></body></html>
    """
    return render_template_string(html, users=users)

@app.route('/add', methods=['POST'])
def add_user():
    email = request.form.get('email').lower()
    days = int(request.form.get('days'))
    expiry = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    users_col.update_one({"email": email}, {"$set": {"expiry": expiry}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete_user(email):
    users_col.delete_one({"email": email})
    return redirect('/')

# --- TELEGRAM HANDLERS (خطوة بخطوة لكل مستخدم) ---
@bot.message_handler(commands=['start'])
def start(m):
    user_sessions[m.chat.id] = {"email": "", "tokens": [], "accounts_data": {}, "is_running": False}
    bot.send_message(m.chat.id, "Welcome! Please enter your registered email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    email = m.text.strip().lower()
    user = users_col.find_one({"email": email})
    if user and datetime.strptime(user['expiry'], "%Y-%m-%d") > datetime.now():
        user_sessions[m.chat.id]["email"] = email
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰')
        bot.send_message(m.chat.id, "✅ Access Granted!", reply_markup=markup)
    else: bot.send_message(m.chat.id, "🚫 Email not registered or expired.")

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def ask_token(m):
    bot.send_message(m.chat.id, "Enter your Deriv API Token:")
    bot.register_next_step_handler(m, save_token)

def save_token(m):
    tokens = [t.strip() for t in m.text.split(",")]
    user_sessions[m.chat.id]["tokens"] = tokens
    bot.send_message(m.chat.id, "Initial Stake Amount:")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    try:
        stake = float(m.text)
        session = user_sessions[m.chat.id]
        session["initial_stake"] = stake
        session["accounts_data"] = {t: {"current_stake": stake, "win_count": 0, "loss_count": 0, "total_profit": 0.0, "consecutive_losses": 0, "active_contract": None, "stopped": False} for t in session["tokens"]}
        bot.send_message(m.chat.id, "Target Profit Amount:")
        bot.register_next_step_handler(m, run_bot)
    except: bot.send_message(m.chat.id, "Invalid. Start over.")

def run_bot(m):
    try:
        session = user_sessions[m.chat.id]
        session["is_running"] = True
        bot.send_message(m.chat.id, "🚀 Bot Started on this device!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        multiprocessing.Process(target=trade_engine, args=(m.chat.id,), daemon=True).start()
    except: pass

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop(m):
    if m.chat.id in user_sessions:
        user_sessions[m.chat.id]["is_running"] = False
    bot.send_message(m.chat.id, "🛑 Bot Stopped.")

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    # تشغيل Flask في عملية منفصلة
    multiprocessing.Process(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    bot.infinity_polling()
