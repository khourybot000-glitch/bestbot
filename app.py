import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
BOT_TOKEN = "8433565422:AAFHZj00Qc74iie1cGeSDTMYWs_3tqx9CNA"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

running_locks = {}

# --- TRADING ENGINE (10 vs 10 Logic) ---

def run_demand_cycle(chat_id, token):
    if running_locks.get(chat_id): return
    running_locks[chat_id] = True
    
    ticks = []
    
    def on_message(ws_app, message):
        data = json.loads(message)
        
        if "tick" in data:
            ticks.append(data["tick"]["quote"])
            
            # تحليل 20 تيك (10 ضد 10)
            if len(ticks) >= 20:
                f10, s10 = ticks[:10], ticks[10:]
                up1, down1 = f10[-1] > f10[0], f10[-1] < f10[0]
                up2, down2 = s10[-1] > s10[0], s10[-1] < s10[0]
                
                contract, barrier = None, ""
                if up1 and down2: contract, barrier = "PUT", "+1"
                elif down1 and up2: contract, barrier = "CALL", "-1"
                
                if contract:
                    execute_trade(chat_id, token, ws_app, contract, barrier)
                else:
                    # إغلاق صامت دون إرسال رسالة
                    ws_app.close()

        if "buy" in data:
            # تم حذف رسالة تأكيد الشراء لتقليل الإزعاج، يكتفي برسالة التنفيذ
            threading.Timer(2, ws_app.close).start()

    ws = websocket.WebSocketApp(
        "wss://blue.derivws.com/websockets/v3?app_id=16929",
        on_open=lambda w: (w.send(json.dumps({"authorize": token})), w.send(json.dumps({"ticks": "R_100", "subscribe": 1}))),
        on_message=on_message
    )
    ws.run_forever()
    running_locks[chat_id] = False

def execute_trade(chat_id, token, ws_app, contract, barrier):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session: return
    acc = session["accounts_data"][token]
    stake = acc["current_stake"]

    ws_app.send(json.dumps({
        "buy": "1", "price": stake,
        "parameters": {
            "amount": stake, "basis": "stake", "contract_type": contract,
            "barrier": barrier, "duration": 10, "duration_unit": "t",
            "symbol": "R_100", "currency": "USD"
        }
    }))
    bot.send_message(chat_id, f"🚀 **Entering Trade**\nType: `{contract}` | Stake: `{stake}$`")
    threading.Thread(target=wait_for_result, args=(chat_id, token)).start()

def wait_for_result(chat_id, token):
    time.sleep(35)
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": token}))
        ws.send(json.dumps({"statement": 1, "limit": 1}))
        res = json.loads(ws.recv())
        ws.close()
        
        if "statement" in res:
            profit = float(res["statement"]["transactions"][0]["amount"])
            update_logic(chat_id, token, profit)
    except: pass

def update_logic(chat_id, token, profit):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    acc = session["accounts_data"][token]
    is_win = profit > 0
    
    # مضاعفة x11 عند الخسارة
    new_stake = session["initial_stake"] if is_win else round(acc["current_stake"] * 11, 2)
    new_streak = 0 if is_win else acc.get("streak", 0) + 1
    new_total = round(acc["total_profit"] + profit, 2)

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.streak": new_streak,
        f"accounts_data.{token}.total_profit": new_total
    }})

    bot.send_message(chat_id, f"{'✅ WIN' if is_win else '❌ LOSS'}\nProfit: `{profit}$` | Total: `{new_total}$` | Streak: `{new_streak}/2`")
    
    if new_total >= session["target_profit"]: 
        stop_with_reason(chat_id, "🎯 Target Profit Reached!")
    elif new_streak >= 2: 
        stop_with_reason(chat_id, "📉 Stop Loss (2 Losses)!")

def stop_with_reason(chat_id, reason):
    active_sessions_col.delete_one({"chat_id": chat_id})
    bot.send_message(chat_id, f"🛑 **BOT STOPPED**\nReason: {reason}\n\nClick /start to begin a new session.", reply_markup=types.ReplyKeyboardRemove())

# --- SCHEDULER & HANDLERS ---
def scheduler_engine(chat_id):
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        if datetime.now().second == 40:
            threading.Thread(target=run_demand_cycle, args=(chat_id, session['tokens'][0])).start()
            time.sleep(20)
        time.sleep(0.5)

@bot.message_handler(commands=['start'])
def start(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "📧 Enter Registered Email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ OK. Enter API Token:")
        bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 No access.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False}}, upsert=True)
    bot.send_message(m.chat.id, "Initial Stake:")
    bot.register_next_step_handler(m, lambda msg: save_config(msg, "initial_stake"))

def save_config(m, key):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {key: float(m.text)}})
    if key == "initial_stake":
        bot.send_message(m.chat.id, "Target Profit:")
        bot.register_next_step_handler(m, lambda msg: save_config(msg, "target_profit"))
    else: bot.send_message(m.chat.id, "Ready!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def run_start(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    if sess:
        accs = {sess["tokens"][0]: {"current_stake": sess["initial_stake"], "total_profit": 0.0, "streak": 0}}
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
        bot.send_message(m.chat.id, "🛰️ Silent Mode Active. Bot will only message when a trade is entered.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=scheduler_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def run_stop(m): stop_with_reason(m.chat.id, "Manual Stop.")

# --- ADMIN PANEL ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin</title><style>
body{background:#0a0a0a; color:white; font-family:sans-serif; text-align:center; padding:50px;}
.box{background:#151515; padding:30px; border-radius:15px; border:1px solid #333; display:inline-block;}
input, select, button{padding:10px; margin:5px; border-radius:5px;}
button{background:#00ff88; color:black; font-weight:bold; cursor:pointer; border:none;}
table{width:100%; margin-top:20px; border-collapse:collapse;}
th, td{padding:10px; border-bottom:1px solid #333;}
</style></head>
<body><div class="box">
    <h2>Admin Panel</h2>
    <form action="/add" method="POST">
        <input name="email" placeholder="Email" required>
        <select name="days"><option value="1">1 Day</option><option value="30">30 Days</option><option value="36500">36500 Days</option></select>
        <button type="submit">Grant Access</button>
    </form>
    <table><tr><th>User</th><th>Expiry</th><th>Action</th></tr>
    {% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" style="color:red">Delete</a></td></tr>{% endfor %}
    </table>
</div></body></html>
"""

@app.route('/')
def admin(): return render_template_string(HTML_ADMIN, users=list(users_col.find()))

@app.route('/add', methods=['POST'])
def add():
    exp = (datetime.now() + timedelta(days=int(request.form.get('days')))).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower().strip()}, {"$set": {"expiry": exp}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete(email): users_col.delete_one({"email": email}); return redirect('/')

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
