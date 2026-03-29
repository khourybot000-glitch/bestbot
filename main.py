import websocket, json, time, threading, math, requests
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
# التوكن الجديد الذي زودتني به
BOT_TOKEN = "8511172742:AAHhBb-gUEzEVGfY0quXj6OmEV6vitmxIDs"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V24_Final_Signal']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

trade_locks = {}
stop_signals = {}

# --- CORE LOGIC: Bollinger Bands ---
def calculate_bb(prices, period=60, dev=2):
    if len(prices) < period: return None, None
    sma = sum(prices[-period:]) / period
    variance = sum((x - sma) ** 2 for x in prices[-period:]) / period
    std_dev = math.sqrt(variance)
    return sma + (std_dev * dev), sma - (std_dev * dev)

# --- CONNECTION ---
def get_safe_connection(token):
    while True:
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
            ws.send(json.dumps({"authorize": token}))
            res = json.loads(ws.recv())
            if "authorize" in res: return ws, res["authorize"].get("currency", "USD")
            ws.close()
        except: time.sleep(1)

# --- TRADING LOOP ---
def user_trading_loop(chat_id, token):
    stop_signals[chat_id] = False
    print(f"✅ [SYSTEM] Thread started for user: {chat_id}")
    
    while not stop_signals.get(chat_id, False):
        try:
            session_db = active_sessions_col.find_one({"chat_id": chat_id, "is_running": True})
            if not session_db: 
                print(f"ℹ️ Session for {chat_id} not found in DB, stopping thread.")
                break 

            now = datetime.now()
            # تنفيذ المنطق كل 10 ثواني
            if now.second % 10 == 0 and not trade_locks.get(chat_id, False):
                run_trade_logic(chat_id, token)
                time.sleep(1) 
            time.sleep(0.5)
        except Exception as e:
            print(f"⚠️ Loop Error for {chat_id}: {e}")
            time.sleep(2)
            
    stop_signals[chat_id] = True
    print(f"🛑 [SYSTEM] Thread closed for user: {chat_id}")

def run_trade_logic(chat_id, token):
    trade_locks[chat_id] = True
    session_db = active_sessions_col.find_one({"chat_id": chat_id, "is_running": True})
    if not session_db: 
        trade_locks[chat_id] = False
        return

    ws = None
    try:
        ws, currency = get_safe_connection(token)
        acc = session_db["accounts_data"][token]
        stake = acc["current_stake"]

        ws.send(json.dumps({"ticks_history": "R_100", "count": 65, "style": "ticks"}))
        res = json.loads(ws.recv())
        
        if "history" in res:
            prices = [float(p) for p in res["history"]["prices"]]
            upper, lower = calculate_bb(prices, 60, 2)
            curr = prices[-1]

            target_type, barrier = (None, None)
            if curr >= upper: target_type, barrier = "PUT", "+0.6"
            elif curr <= lower: target_type, barrier = "CALL", "-0.6"

            if target_type:
                ws.send(json.dumps({
                    "buy": "1", "price": stake,
                    "parameters": {
                        "amount": stake, "basis": "stake", "contract_type": target_type, 
                        "barrier": barrier, "duration": 7, "duration_unit": "t",
                        "symbol": "R_100", "currency": currency
                    }
                }))
                buy_res = json.loads(ws.recv())
                if "buy" in buy_res:
                    bot.send_message(chat_id, f"🚀 **Signal Sent!**\nType: {target_type}\nBarrier: {barrier}\nPrice: {curr:.2f}")
                    time.sleep(20)
                    monitor_result(chat_id, token, buy_res["buy"]["contract_id"])
                else: trade_locks[chat_id] = False
            else: trade_locks[chat_id] = False
        if ws: ws.close()
    except Exception as e:
        print(f"❌ Logic Error: {e}")
        trade_locks[chat_id] = False

def monitor_result(chat_id, token, contract_id):
    ws, _ = get_safe_connection(token)
    try:
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
        while True:
            res = json.loads(ws.recv())
            if "proposal_open_contract" in res:
                data = res["proposal_open_contract"]
                if data.get("is_expired") or data.get("status") in ["won", "lost"]:
                    handle_outcome(chat_id, token, float(data.get("profit", 0)))
                    ws.close()
                    break
            time.sleep(1)
    except: trade_locks[chat_id] = False

def handle_outcome(chat_id, token, profit):
    session_db = active_sessions_col.find_one({"chat_id": chat_id})
    if not session_db: return
    acc = session_db["accounts_data"][token]
    is_win = profit > 0
    
    new_wins = acc.get("wins", 0) + (1 if is_win else 0)
    new_losses = acc.get("losses", 0) + (0 if is_win else 1)
    new_profit = round(acc["total_profit"] + profit, 2)
    streak = 0 if is_win else acc.get("loss_streak", 0) + 1
    
    # Martingale x14
    new_stake = session_db["initial_stake"] if is_win else round(acc["current_stake"] * 14.0, 2)

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.total_profit": new_profit,
        f"accounts_data.{token}.wins": new_wins,
        f"accounts_data.{token}.losses": new_losses,
        f"accounts_data.{token}.loss_streak": streak
    }})

    status_icon = "✅ WIN" if is_win else "❌ LOSS"
    bot.send_message(chat_id, f"{status_icon} (${profit})\nNet Profit: ${new_profit}\nNext Stake: ${new_stake}")

    if streak >= 2 or new_profit >= session_db["target_profit"]:
        stop_signals[chat_id] = True
        active_sessions_col.delete_one({"chat_id": chat_id})
        bot.send_message(chat_id, "🏁 **Session Ended** (TP/SL reached).", reply_markup=main_keyboard())
    
    trade_locks[chat_id] = False

# --- 🔥 AUTO RECOVERY ENGINE 🔥 ---
def recover_sessions():
    print("🔍 [SYSTEM] Checking MongoDB for active sessions to recover...")
    try:
        active_list = list(active_sessions_col.find({"is_running": True}))
        for s in active_list:
            chat_id = s['chat_id']
            token = list(s['accounts_data'].keys())[0]
            if chat_id not in stop_signals or stop_signals[chat_id] == True:
                t = threading.Thread(target=user_trading_loop, args=(chat_id, token), daemon=True)
                t.start()
                print(f"🔄 Recovered session for: {chat_id}")
    except Exception as e:
        print(f"❌ Recovery Error: {e}")

# --- WEB ROUTES (Anti-Sleep) ---

@app.route('/ping')
def ping():
    # هذا المسار مخصص لـ Cron-job.org
    return "ALIVE", 200

@app.route('/')
def admin():
    users = list(users_col.find())
    return render_template_string("""
    <!DOCTYPE html><html><head><title>Bot Admin</title><style>
    body{background:#0f172a;color:#f8fafc;font-family:sans-serif;text-align:center;padding:20px;}
    .card{background:#1e293b;padding:30px;border-radius:15px;display:inline-block;width:100%;max-width:600px;}
    input,select{padding:12px;margin:5px;border-radius:8px;border:1px solid #334155;background:#0f172a;color:white;width:80%;}
    button{padding:12px 25px;background:#38bdf8;border:none;border-radius:8px;font-weight:bold;cursor:pointer;color:white;}
    table{width:100%;margin-top:30px;border-collapse:collapse;}
    th,td{padding:12px;border-bottom:1px solid #334155;text-align:left;}
    a{color:#f87171;text-decoration:none;font-weight:bold;}
    </style></head><body><div class="card"><h2>Admin Panel</h2>
    <form action="/add" method="POST"><input name="email" placeholder="User Email" required><br>
    <select name="days"><option value="1">1 Day</option><option value="30" selected>30 Days</option><option value="36500">Lifetime</option></select><br>
    <button type="submit">Activate Account</button></form><table><tr><th>Email</th><th>Expiry</th><th>Action</th></tr>
    {% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}">Remove</a></td></tr>{% endfor %}
    </table></div></body></html>
    """, users=users)

@app.route('/add', methods=['POST'])
def add():
    days = int(request.form.get('days', 30))
    exp = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower().strip()}, {"$set": {"expiry": exp}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete(email):
    users_col.delete_one({"email": email})
    return redirect('/')

# --- BOT INTERFACE ---
def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton('START 🚀'), types.KeyboardButton('STOP 🛑'))
    return markup

@bot.message_handler(commands=['start'])
def start_cmd(m):
    stop_signals[m.chat.id] = True
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "♻️ **System Reset**. Enter Registered Email:", reply_markup=main_keyboard())
    bot.register_next_step_handler(m, auth)

@bot.message_handler(func=lambda m: m.text in ['STOP 🛑', 'START 🚀'])
def menu_handler(m):
    if m.text == 'STOP 🛑':
        stop_signals[m.chat.id] = True
        active_sessions_col.delete_one({"chat_id": m.chat.id})
        bot.send_message(m.chat.id, "🛑 Trading Stopped.", reply_markup=main_keyboard())
    else:
        bot.send_message(m.chat.id, "♻️ Resetting... Enter Email:", reply_markup=main_keyboard())
        bot.register_next_step_handler(m, auth)

def auth(m):
    user = users_col.find_one({"email": m.text.strip().lower()})
    if user and datetime.strptime(user['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Access Granted. Enter **API Token**:")
        bot.register_next_step_handler(m, lambda msg: setup_params(msg, msg.text.strip()))
    else: bot.send_message(m.chat.id, "🚫 No active subscription found for this email.")

def setup_params(m, token):
    bot.send_message(m.chat.id, "Enter Initial Stake ($):")
    bot.register_next_step_handler(m, lambda msg: setup_target(msg, token, float(msg.text)))

def setup_target(m, token, stake):
    bot.send_message(m.chat.id, "Enter Target Profit ($):")
    bot.register_next_step_handler(m, lambda msg: start_engine(msg, token, stake, float(msg.text)))

def start_engine(m, token, stake, target):
    acc = {token: {"current_stake": stake, "total_profit": 0, "wins": 0, "losses": 0, "loss_streak": 0}}
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {
        "is_running": True, "initial_stake": stake, "target_profit": target, "accounts_data": acc
    }}, upsert=True)
    bot.send_message(m.chat.id, "🛰️ **Engine Started!** Monitoring signals...")
    threading.Thread(target=user_trading_loop, args=(m.chat.id, token), daemon=True).start()

# --- RUN ENGINE ---
if __name__ == '__main__':
    # تشغيل Flask لتلقي طلبات الـ Ping (المنبه)
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    
    # استعادة الجلسات فور التشغيل
    recover_sessions()
    
    # تشغيل التليجرام
    print("🤖 Bot is Online...")
    bot.infinity_polling()
