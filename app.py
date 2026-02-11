import websocket, json, time, threading, queue
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
BOT_TOKEN = "8433565422:AAGCAD-nYIEI7HcqVLCGBUdGWFPAjunAIwk"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

# أقفال لمنع التكرار
locks = {}

# --- TRADING ENGINE ---
def run_analysis_cycle(chat_id, token):
    if locks.get(chat_id): return
    locks[chat_id] = True
    
    ticks_collected = []
    
    def on_message(ws, message):
        data = json.loads(message)
        if "tick" in data:
            ticks_collected.append(data["tick"]["quote"])
            if len(ticks_collected) >= 40:
                # التحليل 20/20
                first_20 = ticks_collected[:20]
                second_20 = ticks_collected[20:]
                
                up_first = first_20[-1] > first_20[0]
                down_first = first_20[-1] < first_20[0]
                up_second = second_20[-1] > second_20[0]
                down_second = second_20[-1] < second_20[0]
                
                contract_type = None
                barrier = ""
                
                if up_first and down_second:
                    contract_type = "PUT"; barrier = "+1"
                elif down_first and up_second:
                    contract_type = "CALL"; barrier = "-1"
                
                if contract_type:
                    session = active_sessions_col.find_one({"chat_id": chat_id})
                    if not session: 
                        ws.close()
                        return
                    
                    acc = session["accounts_data"][token]
                    stake = acc["current_stake"]
                    
                    bot.send_message(chat_id, f"🎯 Pattern Found! Entering `{contract_type}` with stake `{stake}`")
                    
                    ws.send(json.dumps({
                        "buy": "1", "price": stake,
                        "parameters": {
                            "amount": stake, "basis": "stake",
                            "contract_type": contract_type, "barrier": barrier,
                            "duration": 10, "duration_unit": "t",
                            "symbol": "R_100", "currency": "USD"
                        }
                    }))
                    # انتظار النتيجة وإغلاق الدورة
                    threading.Timer(30, lambda: check_result_and_close(chat_id, token, ws)).start()
                else:
                    bot.send_message(chat_id, "⏳ No pattern found. Standby for next minute.")
                    ws.close()
                    locks[chat_id] = False

    def on_open(ws):
        ws.send(json.dumps({"authorize": token}))
        ws.send(json.dumps({"ticks": "R_100", "subscribe": 1}))

    ws = websocket.WebSocketApp("wss://blue.derivws.com/websockets/v3?app_id=16929", on_open=on_open, on_message=on_message)
    ws.run_forever()

def check_result_and_close(chat_id, token, ws):
    # جلب النتيجة النهائية
    def on_msg_res(ws_res, msg_res):
        data = json.loads(msg_res)
        if "statement" in data:
            trade = data["statement"]["transactions"][0]
            process_final_logic(chat_id, token, float(trade["amount"]))
            ws_res.close()
            
    ws_res = websocket.WebSocketApp("wss://blue.derivws.com/websockets/v3?app_id=16929", 
                                    on_open=lambda w: w.send(json.dumps({"authorize": token, "statement": 1, "limit": 1})),
                                    on_message=on_msg_res)
    ws_res.run_forever()
    ws.close()
    locks[chat_id] = False # فتح القفل للدورة القادمة

def process_final_logic(chat_id, token, profit):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session: return
    
    acc = session["accounts_data"][token]
    is_win = profit > 0
    
    if is_win:
        new_stake = session["initial_stake"]
        new_streak = 0
        status = "✅ WIN"
    else:
        new_stake = round(acc["current_stake"] * 11, 2)
        new_streak = acc.get("streak", 0) + 1
        status = "❌ LOSS"

    new_total = round(acc["total_profit"] + profit, 2)
    
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.streak": new_streak,
        f"accounts_data.{token}.total_profit": new_total
    }})

    bot.send_message(chat_id, f"{status}\nProfit: `{profit}$` | Total: `{new_total}$` | Streak: `{new_streak}/2`")

    if new_total >= session["target_profit"]: stop_and_notify(chat_id, "🎯 Target Profit Reached!")
    elif new_streak >= 2: stop_and_notify(chat_id, "📉 Stop Loss Hit (2 Losses)!")

def stop_and_notify(chat_id, reason):
    active_sessions_col.delete_one({"chat_id": chat_id}) # حذف كامل البيانات
    locks[chat_id] = False
    bot.send_message(chat_id, f"🛑 **BOT STOPPED**\nReason: {reason}\n\nTo start again, click /start", reply_markup=types.ReplyKeyboardRemove())

def scheduler(chat_id):
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        if datetime.now().second == 40 and not locks.get(chat_id):
            threading.Thread(target=run_analysis_cycle, args=(chat_id, session['tokens'][0])).start()
            time.sleep(30) # تأمين عدم التكرار في نفس الدقيقة
        time.sleep(0.5)

# --- BOT HANDLERS ---
@bot.message_handler(commands=['start'])
def cmd_start(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "📧 Enter Registered Email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Access Granted. Enter Deriv Token:")
        bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 No active subscription.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False}}, upsert=True)
    bot.send_message(m.chat.id, "Initial Stake:")
    bot.register_next_step_handler(m, lambda msg: save_config(msg, "initial_stake"))

def save_config(m, key):
    try:
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {key: float(m.text)}})
        if key == "initial_stake":
            bot.send_message(m.chat.id, "Target Profit:")
            bot.register_next_step_handler(m, lambda msg: save_config(msg, "target_profit"))
        else:
            bot.send_message(m.chat.id, "Ready!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))
    except: bot.send_message(m.chat.id, "Please enter a number.")

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def start_trading(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    if sess:
        accs = {sess["tokens"][0]: {"current_stake": sess["initial_stake"], "total_profit": 0.0, "streak": 0}}
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
        bot.send_message(m.chat.id, "🛰️ Analyzing at :40s...", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=scheduler, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def btn_stop(m):
    stop_and_notify(m.chat.id, "User requested manual stop.")

# --- ADMIN PANEL HTML ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin</title><style>
body{background:#0f0f0f; color:white; font-family:sans-serif; text-align:center; padding:50px;}
.card{background:#1a1a1a; padding:20px; border-radius:15px; display:inline-block; border:1px solid #333;}
input, select, button{padding:10px; margin:5px; border-radius:5px; border:none;}
button{background:#00ff88; font-weight:bold; cursor:pointer;}
table{width:100%; margin-top:20px; border-collapse:collapse;}
th, td{padding:10px; border-bottom:1px solid #333;}
</style></head>
<body><div class="card">
    <h2>Admin Panel</h2>
    <form action="/add" method="POST">
        <input name="email" placeholder="Email" required>
        <select name="days">
            <option value="1">1 Day</option>
            <option value="30">30 Days</option>
            <option value="36500">36500 Days</option>
        </select>
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
