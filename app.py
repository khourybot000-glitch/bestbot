import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
BOT_TOKEN = "8433565422:AAH2vNbzE5V3ETVHvBlF20pbVTmY5NiMd9E"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V4']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

is_analyzing = {}

# --- TRADING ENGINE ---

def run_zigzag_cycle(chat_id, token):
    """Phase 1: Quick Connect -> Analyze -> Trade -> Immediate Disconnect"""
    if is_analyzing.get(chat_id): return
    is_analyzing[chat_id] = True
    
    ticks = []
    ws = None
    try:
        # اتصال سريع جداً
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": token}))
        auth_res = json.loads(ws.recv())
        currency = auth_res.get("authorize", {}).get("currency", "USD")
        
        ws.send(json.dumps({"ticks": "R_100", "subscribe": 1}))
        
        # جمع 30 تيك بسرعة (تستغرق حوالي 30 ثانية في الخلفية)
        # ملاحظة: لتحقيق طلبك بالاتصال المتقطع، البوت سيسحب آخر بيانات السوق
        while len(ticks) < 30:
            msg = json.loads(ws.recv())
            if "tick" in msg:
                ticks.append(msg["tick"]["quote"])
        
        # تقسيم الـ 30 تيك إلى 6 مجموعات (كل مجموعة 5 تيكات)
        candles = []
        for i in range(0, 30, 5):
            segment = ticks[i:i+5]
            if segment[-1] > segment[0]: candles.append("UP")
            elif segment[-1] < segment[0]: candles.append("DOWN")
            else: candles.append("NEUTRAL")

        # الأنماط المطلوبة
        put_pattern = ["UP", "DOWN", "UP", "DOWN", "UP", "DOWN"]
        call_pattern = ["DOWN", "UP", "DOWN", "UP", "DOWN", "UP"]

        contract_type = None
        if candles == put_pattern: contract_type = "PUT"
        elif candles == call_pattern: contract_type = "CALL"

        if contract_type:
            session = active_sessions_col.find_one({"chat_id": chat_id})
            acc = session["accounts_data"][token]
            stake = acc["current_stake"]

            ws.send(json.dumps({
                "buy": "1", "price": stake,
                "parameters": {
                    "amount": stake, "basis": "stake", "contract_type": contract_type,
                    "duration": 5, "duration_unit": "t",
                    "symbol": "R_100", "currency": currency
                }
            }))
            
            buy_res = json.loads(ws.recv())
            if "buy" in buy_res:
                contract_id = buy_res["buy"]["contract_id"]
                bot.send_message(chat_id, f"🎯 **Pattern Found!**\nEntering `{contract_type}` | ID: `{contract_id}`")
                
                # اقطع الاتصال فوراً بعد الشراء
                ws.close()
                time.sleep(18) # انتظار مدة الصفقة
                fetch_contract_result(chat_id, token, contract_id)
            else:
                ws.close()
        else:
            # لم يجد نمط؟ اقطع الاتصال فوراً
            ws.close()

    except Exception as e:
        if ws: ws.close()
        print(f"Connection Error: {e}")
    
    is_analyzing[chat_id] = False

def fetch_contract_result(chat_id, token, contract_id):
    """Phase 2: Quick Connect for result only"""
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": token}))
        ws.recv() 
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
        res = json.loads(ws.recv())
        ws.close() # اقطع الاتصال فوراً بعد استلام النتيجة
        
        if "proposal_open_contract" in res:
            profit = float(res["proposal_open_contract"].get("profit", 0))
            update_stats(chat_id, token, profit)
    except: pass

def update_stats(chat_id, token, profit):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session: return
    
    acc = session["accounts_data"][token]
    is_win = profit > 0
    
    new_stake = session["initial_stake"] if is_win else round(acc["current_stake"] * 2.2, 2)
    new_streak = 0 if is_win else acc.get("streak", 0) + 1
    new_total = round(acc["total_profit"] + profit, 2)
    wins = acc.get("wins_count", 0) + (1 if is_win else 0)
    losses = acc.get("losses_count", 0) + (0 if is_win else 1)

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.streak": new_streak,
        f"accounts_data.{token}.total_profit": new_total,
        f"accounts_data.{token}.wins_count": wins,
        f"accounts_data.{token}.losses_count": losses
    }})

    bot.send_message(chat_id, 
        f"{'✅ WIN' if is_win else '❌ LOSS'}\n"
        f"Profit: `{profit}$` | Total: `{new_total}$` \n"
        f"📊 Stats: W: `{wins}` | L: `{losses}` | Streak: `{new_streak}/4`"
    )
    
    if new_total >= session["target_profit"]: stop_bot(chat_id, "Target Reached!")
    elif new_streak >= 4: stop_bot(chat_id, "Stop Loss: 4 Losses!")

def stop_bot(chat_id, reason):
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
    bot.send_message(chat_id, f"🛑 **BOT STOPPED**\nReason: {reason}\n\n/start")

# --- SCHEDULER ---
def main_scheduler(chat_id):
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        # التفعيل عند كل 10 ثواني بالضبط
        if datetime.now().second % 10 == 0:
            threading.Thread(target=run_zigzag_cycle, args=(chat_id, session['tokens'][0])).start()
            time.sleep(2) # منع تشغيل أكثر من خيط في نفس الثانية
        time.sleep(0.5)

# --- BOT HANDLERS ---
@bot.message_handler(commands=['start'])
def start(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "Welcome! Enter Email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "Enter Token:")
        bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 No Access.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False}}, upsert=True)
    bot.send_message(m.chat.id, "Initial Stake:")
    bot.register_next_step_handler(m, lambda msg: save_config(msg, "initial_stake"))

def save_config(m, key):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {key: float(m.text)}})
    if key == "initial_stake":
        bot.send_message(m.chat.id, "Target Profit:")
        bot.register_next_step_handler(m, lambda msg: save_config(msg, "target_profit"))
    else: bot.send_message(m.chat.id, "Setup Done!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def run_start(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    if sess:
        accs = {sess["tokens"][0]: {
            "current_stake": sess["initial_stake"], 
            "total_profit": 0.0, "streak": 0,
            "wins_count": 0, "losses_count": 0
        }}
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
        bot.send_message(m.chat.id, "🚀 Running Sniper (Every 10s).", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=main_scheduler, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def run_stop(m): stop_bot(m.chat.id, "Manual Stop.")

# --- ADMIN PANEL ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin</title><style>
body{background:#0a0a0a; color:white; font-family:sans-serif; text-align:center; padding:50px;}
.box{background:#151515; padding:30px; border-radius:15px; border:1px solid #333; display:inline-block;}
input, select, button{padding:10px; margin:5px; border-radius:5px;}
button{background:#00ccff; color:black; font-weight:bold; cursor:pointer; border:none;}
table{width:100%; margin-top:20px; border-collapse:collapse;}
th, td{padding:10px; border-bottom:1px solid #333;}
</style></head>
<body><div class="box">
    <h2>Admin Panel</h2>
    <form action="/add" method="POST">
        <input name="email" placeholder="Email" required>
        <select name="days">
            <option value="1">1 Day</option>
            <option value="30">30 Days</option>
            <option value="365">365 Days</option>
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
