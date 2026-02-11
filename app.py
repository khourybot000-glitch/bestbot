import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
# التوكن الجديد والمحدث
BOT_TOKEN = "8433565422:AAE9f6beYSHS1aAnG5IbTDTg2l8acW_gJzs"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

# مخزن للتحكم في الخيوط (Threads) لكل مستخدم
running_threads = {}

# --- TRADING ENGINE ---
def trading_loop(chat_id, token):
    """محرك مستقل لكل مستخدم يمنع التداخل"""
    while chat_id in running_threads:
        try:
            session = active_sessions_col.find_one({"chat_id": chat_id})
            if not session or not session.get("is_running"):
                break

            now = datetime.now()
            # التحليل يبدأ حصراً عند الثانية 40 من كل دقيقة
            if now.second == 40:
                perform_analysis(chat_id, token)
                # انتظار 20 ثانية لتجاوز الدقيقة الحالية ومنع تكرار التحليل
                time.sleep(20)
            
            time.sleep(0.5)
        except Exception as e:
            print(f"Error for user {chat_id}: {e}")
            time.sleep(1)

def perform_analysis(chat_id, token):
    ticks = []
    
    def on_message(ws, message):
        data = json.loads(message)
        if "tick" in data:
            ticks.append(data["tick"]["quote"])
            if len(ticks) >= 40:
                process_trade(chat_id, token, ticks)
                ws.close()

    ws = websocket.WebSocketApp(
        "wss://blue.derivws.com/websockets/v3?app_id=16929",
        on_open=lambda w: (w.send(json.dumps({"authorize": token})), w.send(json.dumps({"ticks": "R_100", "subscribe": 1}))),
        on_message=on_message
    )
    ws.run_forever()

def process_trade(chat_id, token, ticks):
    # منطق التحليل: أول 20 تيك مقابل ثاني 20 تيك
    f20, s20 = ticks[:20], ticks[20:]
    up1, down1 = f20[-1] > f20[0], f20[-1] < f20[0]
    up2, down2 = s20[-1] > s20[0], s20[-1] < s20[0]

    contract, barrier = None, ""
    if up1 and down2: contract, barrier = "PUT", "+1"
    elif down1 and up2: contract, barrier = "CALL", "-1"

    if not contract:
        bot.send_message(chat_id, "⏳ No 20/20 pattern detected this minute.")
        return

    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session: return
    
    acc = session["accounts_data"][token]
    stake = acc["current_stake"]

    bot.send_message(chat_id, f"🎯 Pattern Detected!\n🚀 Type: `{contract}` Barrier: `{barrier}`\n💰 Stake: `{stake}$`")

    # تنفيذ الصفقة
    ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
    ws.send(json.dumps({"authorize": token}))
    ws.send(json.dumps({
        "buy": "1", "price": stake,
        "parameters": {
            "amount": stake, "basis": "stake", "contract_type": contract,
            "barrier": barrier, "duration": 10, "duration_unit": "t",
            "symbol": "R_100", "currency": "USD"
        }
    }))
    
    # انتظار 30 ثانية للنتيجة
    time.sleep(30)
    ws.send(json.dumps({"statement": 1, "limit": 1}))
    res = json.loads(ws.recv())
    ws.close()

    if "statement" in res:
        amount = float(res["statement"]["transactions"][0]["amount"])
        update_logic(chat_id, token, amount)

def update_logic(chat_id, token, profit):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session: return
    
    acc = session["accounts_data"][token]
    is_win = profit > 0
    
    # المضاعفة x11 عند الخسارة
    new_stake = session["initial_stake"] if is_win else round(acc["current_stake"] * 11, 2)
    new_streak = 0 if is_win else acc.get("streak", 0) + 1
    new_total = round(acc["total_profit"] + profit, 2)

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.streak": new_streak,
        f"accounts_data.{token}.total_profit": new_total
    }})

    bot.send_message(chat_id, f"{'✅ WIN' if is_win else '❌ LOSS'}\nNet: `{profit}$` | Total: `{new_total}$` | Streak: `{new_streak}/2`")

    # فحص شروط التوقف التلقائي
    if new_total >= session["target_profit"]: 
        stop_with_reason(chat_id, "🎯 Target Profit Reached!")
    elif new_streak >= 2: 
        stop_with_reason(chat_id, "📉 Stop Loss (2 Consecutive Losses)!")

def stop_with_reason(chat_id, reason):
    active_sessions_col.delete_one({"chat_id": chat_id})
    if chat_id in running_threads:
        del running_threads[chat_id]
    bot.send_message(chat_id, f"🛑 **BOT STOPPED**\nReason: {reason}\n\nTo start again, click /start", reply_markup=types.ReplyKeyboardRemove())

# --- BOT HANDLERS ---
@bot.message_handler(commands=['start'])
def start(m):
    # مسح أي جلسة سابقة عالقة
    if m.chat.id in running_threads:
        del running_threads[m.chat.id]
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    
    bot.send_message(m.chat.id, "📧 Enter Registered Email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Authorized. Enter Deriv API Token:")
        bot.register_next_step_handler(m, save_token)
    else: 
        bot.send_message(m.chat.id, "🚫 No active subscription found.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False}}, upsert=True)
    bot.send_message(m.chat.id, "Enter Initial Stake:")
    bot.register_next_step_handler(m, lambda msg: save_config(msg, "initial_stake"))

def save_config(m, key):
    try:
        val = float(m.text)
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {key: val}})
        if key == "initial_stake":
            bot.send_message(m.chat.id, "Enter Target Profit ($):")
            bot.register_next_step_handler(m, lambda msg: save_config(msg, "target_profit"))
        else:
            bot.send_message(m.chat.id, "Setup Complete!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))
    except:
        bot.send_message(m.chat.id, "Please enter a valid number.")

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def run_start(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    if sess:
        accs = {sess["tokens"][0]: {"current_stake": sess["initial_stake"], "total_profit": 0.0, "streak": 0}}
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
        bot.send_message(m.chat.id, "🛰️ Bot is active. Analyzing every minute at second :40", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        
        running_threads[m.chat.id] = True
        threading.Thread(target=trading_loop, args=(m.chat.id, sess['tokens'][0]), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def run_stop(m):
    stop_with_reason(m.chat.id, "User requested manual stop.")

# --- ADMIN PANEL HTML ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin Panel</title><style>
body{background:#0a0a0a; color:white; font-family:sans-serif; text-align:center; padding:50px;}
.container{background:#151515; padding:30px; border-radius:15px; border:1px solid #333; display:inline-block; min-width:400px;}
input, select, button{padding:12px; margin:5px; border-radius:8px; border:1px solid #444; background:#111; color:white;}
button{background:#00ff88; color:black; font-weight:bold; cursor:pointer; border:none;}
table{width:100%; margin-top:30px; border-collapse:collapse;}
th, td{padding:12px; border-bottom:1px solid #333; text-align:center;}
.stats{margin-bottom:20px; color:#00ff88; font-size:1.2em;}
</style></head>
<body><div class="container">
    <h2>Admin Management</h2>
    <div class="stats">Active Trading Sessions: <b>{{ active_count }}</b></div>
    <form action="/add" method="POST">
        <input name="email" placeholder="User Email" required>
        <select name="days">
            <option value="1">1 Day</option>
            <option value="30">30 Days</option>
            <option value="36500">36500 Days (Lifetime)</option>
        </select>
        <button type="submit">Add User</button>
    </form>
    <table><tr><th>Email</th><th>Expiry</th><th>Action</th></tr>
    {% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" style="color:#ff4444; text-decoration:none;">Delete</a></td></tr>{% endfor %}
    </table>
</div></body></html>
"""

@app.route('/')
def admin():
    active_count = active_sessions_col.count_documents({"is_running": True})
    return render_template_string(HTML_ADMIN, users=list(users_col.find()), active_count=active_count)

@app.route('/add', methods=['POST'])
def add():
    exp = (datetime.now() + timedelta(days=int(request.form.get('days')))).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower().strip()}, {"$set": {"expiry": exp}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete(email):
    users_col.delete_one({"email": email})
    return redirect('/')

if __name__ == '__main__':
    # تشغيل Flask و Telegram Bot معاً
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
