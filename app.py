import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION (UPDATED TOKEN) ---
TOKEN = "8433565422:AAHRiGexiXUVId4P3QIFIEv9hFyAwkBuA8g"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V3']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

def get_digit(price):
    try:
        val = int(round(float(price) * 100))
        return val % 10
    except: return None

def quick_check(token, contract_id):
    ws = None
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": token}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
        res = json.loads(ws.recv())
        ws.close()
        return res
    except:
        if ws: ws.close()
        return None

# --- TRADING ENGINE ---
def trading_process(chat_id):
    tick_history = [] 
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
                    price = float(data["tick"]["quote"])
                    tick_history.append(price)
                    if len(tick_history) > 3: tick_history.pop(0)
                    
                    if len(tick_history) == 3:
                        t1, t2, t3 = tick_history[0], tick_history[1], tick_history[2]
                        d2, d3 = get_digit(t2), get_digit(t3)
                        
                        ctype = None
                        barrier = ""
                        # تحليل النمط المطلوب
                        if d2 == 9 and d3 == 0:
                            if t3 > t2 and t2 > t1:
                                ctype, barrier = "PUT", "+0.5"
                            elif t3 < t2 and t2 < t1:
                                ctype, barrier = "CALL", "-0.5"
                        
                        if ctype:
                            stake = session["current_stake"]
                            ws.send(json.dumps({
                                "buy": 1, "price": stake,
                                "parameters": {
                                    "amount": stake, "basis": "stake", "contract_type": ctype,
                                    "currency": "USD", "duration": 1, "duration_unit": "t",
                                    "symbol": "R_100", "barrier": barrier
                                }
                            }))
                            resp = json.loads(ws.recv())
                            if "buy" in resp:
                                c_id = resp["buy"]["contract_id"]
                                bot.send_message(chat_id, f"🎯 *Pattern Match!* {ctype} {barrier}")
                                ws.close()
                                time.sleep(18)
                                res = quick_check(session['api_token'], c_id)
                                if res:
                                    handle_stats(chat_id, float(res["proposal_open_contract"].get("profit", 0)))
                                break 
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
        if losses >= 3:
            next_s, stop = session["initial_stake"], True
        else:
            next_s = float("{:.2f}".format(session["current_stake"] * 6))

    new_total = session.get("total_profit", 0) + profit
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        "total_profit": new_total, "current_stake": next_s,
        "win_count": session.get("win_count", 0) + (1 if is_win else 0),
        "loss_count": session.get("loss_count", 0) + (0 if is_win else 1),
        "consecutive_losses": losses
    }})

    msg = f"📊 Result: {'✅ WIN' if is_win else '❌ LOSS'}\nProfit: {profit:.2f} | Total: {new_total:.2f}\n🏆 Wins: {session.get('win_count', 0) + (1 if is_win else 0)} | 💀 Losses: {session.get('loss_count', 0) + (0 if is_win else 1)}"
    bot.send_message(chat_id, msg)

    if stop or new_total >= session["target_profit"]:
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('/start')
        bot.send_message(chat_id, "🛑 Session Ended.", reply_markup=markup)

# --- ADMIN PANEL ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin Panel</title><style>
body{font-family:sans-serif; background:#f4f7f6; padding:20px; text-align:center;}
.box{max-width:850px; margin:auto; background:white; padding:30px; border-radius:15px; box-shadow:0 5px 15px rgba(0,0,0,0.1);}
input, select{padding:10px; border-radius:5px; border:1px solid #ddd; margin:5px;}
button{background:#2ecc71; color:white; border:none; padding:10px 20px; border-radius:5px; cursor:pointer;}
table{width:100%; border-collapse:collapse; margin-top:20px;}
th,td{padding:10px; border-bottom:1px solid #eee; text-align:left;}
</style></head>
<body><div class="box">
    <h2>User Management</h2>
    <form action="/add" method="POST">
        <input type="email" name="email" placeholder="Email" required>
        <select name="days"><option value="1">1 Day</option><option value="30">30 Days</option><option value="36500">36500 Days</option></select>
        <button type="submit">Add User</button>
    </form>
    <table><tr><th>Email</th><th>Expiry</th><th>Action</th></tr>
    {% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" style="color:red;">Del</a></td></tr>{% endfor %}
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

# --- TELEGRAM ---
@bot.message_handler(commands=['start'])
def start_bot(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    bot.send_message(m.chat.id, "👋 Welcome! Enter your email:", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "Approved! Enter API Token:"); bot.register_next_step_handler(m, tk)
    else: bot.send_message(m.chat.id, "Denied Access.")
def tk(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"api_token": m.text.strip()}}, upsert=True)
    bot.send_message(m.chat.id, "Initial Stake:"); bot.register_next_step_handler(m, sk)
def sk(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text), "current_stake": float(m.text), "consecutive_losses": 0}})
    bot.send_message(m.chat.id, "Target Profit:"); bot.register_next_step_handler(m, tp)
def tp(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": float(m.text), "total_profit": 0, "win_count": 0, "loss_count": 0, "is_running": True}})
    bot.send_message(m.chat.id, "🚀 Running...", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
    threading.Thread(target=trading_process, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_btn(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    bot.send_message(m.chat.id, "🛑 Stopped.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('/start'))

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
