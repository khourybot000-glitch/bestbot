import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
TOKEN = "8433565422:AAFWFG26DQBYj2zrr11RoEOTBf3C28y5tjk"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V3']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

def check_00(price):
    try:
        formatted = "{:.2f}".format(float(price))
        return formatted.split(".")[1] == "00"
    except: return False

def force_check_result(token, contract_id, chat_id):
    """وظيفة محسنة لضمان جلب النتيجة وعدم ضياع الإحصائيات"""
    max_attempts = 10
    for _ in range(max_attempts):
        ws = None
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
            ws.send(json.dumps({"authorize": token}))
            ws.recv()
            ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
            res = json.loads(ws.recv())
            ws.close()
            
            if "proposal_open_contract" in res:
                data = res["proposal_open_contract"]
                if data.get("is_sold"): # التأكد أن الصفقة انتهت
                    return float(data.get("profit", 0))
        except:
            if ws: ws.close()
        time.sleep(3)
    return None

def trading_process(chat_id):
    history = [] 
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
            ws.send(json.dumps({"authorize": session['api_token']}))
            ws.recv()
            ws.send(json.dumps({"ticks": "R_100", "subscribe": 1}))
            
            while True:
                session = active_sessions_col.find_one({"chat_id": chat_id})
                if not session or not session.get("is_running"): break
                
                raw = ws.recv()
                data = json.loads(raw)
                if "tick" in data:
                    t_curr = float(data["tick"]["quote"])
                    history.append(t_curr)
                    if len(history) > 5: history.pop(0) 
                    
                    # تحليل 5 تيكات عند ظهور .00 في التيك الأخير
                    if len(history) == 5 and check_00(t_curr):
                        t1, t4, t5 = history[0], history[3], history[4]
                        ctype, barrier = None, ""
                        
                        # شرط الـ PUT: (T5 > T1 صعود 5 تيكات) و (T5 > T4 صعود آخر تيك)
                        if t5 > t1 and t5 > t4:
                            ctype, barrier = "PUT", "+1"
                        # شرط الـ CALL: (T5 < T1 هبوط 5 تيكات) و (T5 < T4 هبوط آخر تيك)
                        elif t5 < t1 and t5 < t4:
                            ctype, barrier = "CALL", "-1"
                        
                        if ctype:
                            stake = session["current_stake"]
                            ws.send(json.dumps({
                                "buy": 1, "price": stake,
                                "parameters": {
                                    "amount": stake, "basis": "stake", "contract_type": ctype,
                                    "currency": "USD", "duration": 5, "duration_unit": "t",
                                    "symbol": "R_100", "barrier": barrier
                                }
                            }))
                            resp = json.loads(ws.recv())
                            if "buy" in resp:
                                c_id = resp["buy"]["contract_id"]
                                bot.send_message(chat_id, f"🎯 *Pattern Found!* {ctype} {barrier}\nPrice: `{t_curr:.2f}`")
                                ws.close() # إغلاق ستريم التيكات لتركيز المعالجة على النتيجة
                                
                                # انتظار انتهاء الـ 5 تيكات + جلب النتيجة بقوة
                                time.sleep(12) 
                                profit = force_check_result(session['api_token'], c_id, chat_id)
                                if profit is not None:
                                    handle_stats(chat_id, profit)
                                else:
                                    bot.send_message(chat_id, "⚠️ Error fetching result. Checking stats...")
                                break # العودة لفتح اتصال تيكات جديد
            if ws: ws.close()
        except:
            time.sleep(2); continue

def handle_stats(chat_id, profit):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    is_win = profit > 0
    losses = session.get("consecutive_losses", 0)
    
    stop = False
    if is_win:
        next_s, losses = session["initial_stake"], 0
    else:
        losses += 1
        if losses >= 2: # التوقف بعد خسارتين
            next_s, stop = session["initial_stake"], True
        else:
            next_s = float("{:.2f}".format(session["current_stake"] * 24)) # مضاعفة x24

    new_total = session.get("total_profit", 0) + profit
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        "total_profit": new_total, "current_stake": next_s,
        "win_count": session.get("win_count", 0) + (1 if is_win else 0),
        "loss_count": session.get("loss_count", 0) + (0 if is_win else 1),
        "consecutive_losses": losses
    }})

    status = "✅ WIN" if is_win else "❌ LOSS"
    msg = (f"📊 *Trade Result:* {status}\n"
           f"💰 Profit: `{profit:.2f}` | Total: `{new_total:.2f}`\n"
           f"🏆 W: `{session.get('win_count', 0) + (1 if is_win else 0)}` | 💀 L: `{session.get('loss_count', 0) + (0 if is_win else 1)}` | Seq L: `{losses}`")
    bot.send_message(chat_id, msg)

    if stop or new_total >= session["target_profit"]:
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
        reason = "Target Profit Reached!" if not stop else "2 Consecutive Losses!"
        bot.send_message(chat_id, f"🛑 *Bot Stopped*\nReason: {reason}", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('/start'))

# --- ADMIN PANEL HTML ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin Panel</title><style>
body{font-family:sans-serif; background:#f4f7f6; padding:20px; text-align:center;}
.box{max-width:850px; margin:auto; background:white; padding:30px; border-radius:15px; box-shadow:0 5px 15px rgba(0,0,0,0.1);}
.form-box{display:flex; gap:10px; margin-bottom:25px;}
input, select{padding:12px; border-radius:6px; border:1px solid #ddd; flex:1;}
button{background:#1a73e8; color:white; border:none; padding:12px 24px; border-radius:6px; cursor:pointer; font-weight:bold;}
table{width:100%; border-collapse:collapse;}
th,td{padding:12px; border-bottom:1px solid #eee; text-align:left;}
</style></head>
<body><div class="box">
    <h2>👤 User Access Control</h2>
    <form class="form-box" action="/add" method="POST">
        <input type="email" name="email" placeholder="Email" required>
        <select name="days">
            <option value="1">1 Day</option>
            <option value="30">30 Days</option>
            <option value="36500">36500 Days (Lifetime)</option>
        </select>
        <button type="submit">Add User</button>
    </form>
    <table><tr><th>Email</th><th>Expiry</th><th>Action</th></tr>
    {% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" style="color:red;font-weight:bold;">Remove</a></td></tr>{% endfor %}
    </table>
</div></body></html>
"""

@app.route('/')
def index(): return render_template_string(HTML_ADMIN, users=list(users_col.find()))
@app.route('/add', methods=['POST'])
def add_user():
    e, d = request.form.get('email').lower().strip(), int(request.form.get('days'))
    exp = (datetime.now() + timedelta(days=d)).strftime("%Y-%m-%d")
    users_col.update_one({"email": e}, {"$set": {"expiry": exp}}, upsert=True); return redirect('/')
@app.route('/delete/<email>')
def delete_user(email): users_col.delete_one({"email": email}); return redirect('/')

@bot.message_handler(commands=['start'])
def start_bot(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    bot.send_message(m.chat.id, "👋 Welcome! Enter your email:", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Authorized! Enter API Token:"); bot.register_next_step_handler(m, tk)
    else: bot.send_message(m.chat.id, "🚫 Access Denied.")
def tk(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"api_token": m.text.strip()}}, upsert=True)
    bot.send_message(m.chat.id, "Initial Stake:"); bot.register_next_step_handler(m, sk)
def sk(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text), "current_stake": float(m.text), "consecutive_losses": 0}})
    bot.send_message(m.chat.id, "Target Profit:"); bot.register_next_step_handler(m, tp)
def tp(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": float(m.text), "total_profit": 0, "win_count": 0, "loss_count": 0, "is_running": True}})
    bot.send_message(m.chat.id, "🚀 Running Logic: 5-Ticks Sequence .00", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
    threading.Thread(target=trading_process, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_btn(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    bot.send_message(m.chat.id, "🛑 Stopped.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('/start'))

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
