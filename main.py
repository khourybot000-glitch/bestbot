import websocket, json, time, threading, math
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
BOT_TOKEN = "8511172742:AAHrndbzodFw4GMjbW_yO3LQq8M8gFdozOA"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V24_Final_Signal']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

trade_locks = {}
stop_signals = {} # قاموس للتحكم في إيقاف الـ Threads

# --- UTILS: Bollinger Bands ---
def calculate_bb(prices, period=60, dev=2):
    if len(prices) < period:
        return None, None
    sma = sum(prices[-period:]) / period
    variance = sum((x - sma) ** 2 for x in prices[-period:]) / period
    std_dev = math.sqrt(variance)
    upper = sma + (std_dev * dev)
    lower = sma - (std_dev * dev)
    return upper, lower

# --- CONNECTION MANAGER ---
def get_safe_connection(token):
    while True:
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
            ws.send(json.dumps({"authorize": token}))
            res = json.loads(ws.recv())
            if "authorize" in res:
                return ws, res["authorize"].get("currency", "USD")
            ws.close()
        except:
            time.sleep(1)

# --- TRADING ENGINE ---
def user_trading_loop(chat_id, token):
    stop_signals[chat_id] = False # إعادة ضبط إشارة التوقف عند البدء
    print(f"DEBUG: New Thread started for {chat_id}")
    
    try:
        while not stop_signals.get(chat_id, False):
            session_db = active_sessions_col.find_one({"chat_id": chat_id, "is_running": True})
            
            # إذا تم حذف الجلسة من الداتابيز أو أرسلنا إشارة إيقاف
            if not session_db or stop_signals.get(chat_id, False):
                break 

            now = datetime.now()
            if now.second % 10 == 0 and not trade_locks.get(chat_id, False):
                run_trade_logic(chat_id, token)
                time.sleep(1) 
            time.sleep(0.2)
            
    except Exception as e:
        print(f"Thread Error for {chat_id}: {e}")
    finally:
        stop_signals[chat_id] = True # التأكد من حالة التوقف
        print(f"DEBUG: Thread permanently closed for {chat_id}")

def run_trade_logic(chat_id, token):
    trade_locks[chat_id] = True
    session_db = active_sessions_col.find_one({"chat_id": chat_id, "is_running": True})
    if not session_db:
        trade_locks[chat_id] = False
        return

    ws = None
    try:
        ws, currency = get_safe_connection(token)
        acc_data = session_db["accounts_data"][token]
        stake = acc_data["current_stake"]

        ws.send(json.dumps({"ticks_history": "R_100", "end": "latest", "count": 65, "style": "ticks"}))
        res = json.loads(ws.recv())
        
        if "history" in res:
            prices = [float(p) for p in res["history"]["prices"]]
            upper, lower = calculate_bb(prices, 60, 2)
            current_price = prices[-1]

            target_type = None
            barrier = None

            if current_price >= upper:
                target_type = "PUT"
                barrier = "+0.6"
            elif current_price <= lower:
                target_type = "CALL"
                barrier = "-0.6"

            if target_type:
                ws.send(json.dumps({
                    "buy": "1", "price": stake,
                    "parameters": {
                        "amount": stake, "basis": "stake",
                        "contract_type": target_type, "barrier": barrier,
                        "duration": 7, "duration_unit": "t",
                        "symbol": "R_100", "currency": currency
                    }
                }))
                buy_res = json.loads(ws.recv())
                if "buy" in buy_res:
                    contract_id = buy_res["buy"]["contract_id"]
                    bot.send_message(chat_id, f"🚀 Signal: {target_type} ({barrier})\nPrice: {current_price:.2f}")
                    time.sleep(20)
                    monitor_result(chat_id, token, contract_id)
                else:
                    trade_locks[chat_id] = False
            else:
                trade_locks[chat_id] = False
        if ws: ws.close()
    except:
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
                    profit = float(data.get("profit", 0))
                    ws.close()
                    handle_outcome(chat_id, token, profit)
                    break
            time.sleep(0.5)
    except:
        trade_locks[chat_id] = False

def handle_outcome(chat_id, token, profit):
    session_db = active_sessions_col.find_one({"chat_id": chat_id})
    if not session_db: return
    acc = session_db["accounts_data"][token]
    is_win = profit > 0
    
    new_wins = acc.get("wins", 0) + (1 if is_win else 0)
    new_losses = acc.get("losses", 0) + (0 if is_win else 1)
    new_total_profit = round(acc["total_profit"] + profit, 2)
    current_loss_streak = 0 if is_win else acc.get("loss_streak", 0) + 1
    
    new_stake = session_db["initial_stake"] if is_win else round(acc["current_stake"] * 14.0, 2)

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.total_profit": new_total_profit,
        f"accounts_data.{token}.wins": new_wins,
        f"accounts_data.{token}.losses": new_losses,
        f"accounts_data.{token}.loss_streak": current_loss_streak
    }})

    status = "✅ WIN" if is_win else "❌ LOSS"
    bot.send_message(chat_id, f"Result: {status} (${profit})\nWins: {new_wins} | Losses: {new_losses}\nNet: ${new_total_profit}")

    # التوقف عند TP أو SL
    if current_loss_streak >= 2:
        stop_signals[chat_id] = True # إرسال إشارة إيقاف للـ Thread
        active_sessions_col.delete_one({"chat_id": chat_id})
        bot.send_message(chat_id, "⚠️ STOP LOSS (2 Losses). Thread Killed.", reply_markup=main_keyboard())
    elif new_total_profit >= session_db["target_profit"]:
        stop_signals[chat_id] = True # إرسال إشارة إيقاف للـ Thread
        active_sessions_col.delete_one({"chat_id": chat_id})
        bot.send_message(chat_id, "🎯 Target Profit Reached! Thread Killed.", reply_markup=main_keyboard())
    
    trade_locks[chat_id] = False

# --- UI & BOT COMMANDS ---
def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton('START 🚀'), types.KeyboardButton('STOP 🛑'))
    return markup

def kill_and_clean(chat_id):
    """وظيفة لقتل الـ Thread القديم وتنظيف البيانات"""
    stop_signals[chat_id] = True # إشارة للـ Thread القديم بالخروج
    active_sessions_col.delete_one({"chat_id": chat_id})
    trade_locks[chat_id] = False
    time.sleep(0.5) # وقت قصير للتأكد من خروج الـ Thread

@bot.message_handler(commands=['start'])
def start_cmd(m):
    kill_and_clean(m.chat.id)
    bot.send_message(m.chat.id, "♻️ System Cleaned. Enter Registered Email:", reply_markup=main_keyboard())
    bot.register_next_step_handler(m, auth)

@bot.message_handler(func=lambda m: m.text in ['STOP 🛑', 'START 🚀'])
def menu_handler(m):
    if m.text == 'STOP 🛑':
        kill_and_clean(m.chat.id)
        bot.send_message(m.chat.id, "🛑 Thread Killed. Session Stopped.", reply_markup=main_keyboard())
    else:
        kill_and_clean(m.chat.id)
        bot.send_message(m.chat.id, "♻️ Resetting... Enter Email:", reply_markup=main_keyboard())
        bot.register_next_step_handler(m, auth)

def auth(m):
    user = users_col.find_one({"email": m.text.strip().lower()})
    if user and datetime.strptime(user['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "API Token:")
        bot.register_next_step_handler(m, lambda msg: setup_stake(msg, msg.text.strip()))
    else: bot.send_message(m.chat.id, "🚫 No Access.")

def setup_stake(m, token):
    bot.send_message(m.chat.id, "Initial Stake:")
    bot.register_next_step_handler(m, lambda msg: setup_target(msg, token, float(msg.text)))

def setup_target(m, token, stake):
    bot.send_message(m.chat.id, "Target Profit ($):")
    bot.register_next_step_handler(m, lambda msg: start_engine(msg, token, stake, float(msg.text)))

def start_engine(m, token, stake, target):
    # التأكد من قتل أي Thread قديم قبل البدء
    stop_signals[m.chat.id] = True
    time.sleep(0.5)
    
    acc_data = {token: {"current_stake": stake, "total_profit": 0, "wins": 0, "losses": 0, "loss_streak": 0}}
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {
        "is_running": True, "initial_stake": stake, "target_profit": target, "accounts_data": acc_data
    }}, upsert=True)
    
    bot.send_message(m.chat.id, "🛰️ Starting New Thread (BB 60/2)...", reply_markup=main_keyboard())
    
    # تشغيل Thread جديد نظيف
    t = threading.Thread(target=user_trading_loop, args=(m.chat.id, token), daemon=True)
    t.start()

# --- ADMIN PANEL (Flask) ---
@app.route('/')
def admin():
    users = list(users_col.find())
    return render_template_string("""
    <!DOCTYPE html>
    <html><head><title>Admin Panel</title><style>body{background:#0f172a;color:#f8fafc;font-family:sans-serif;text-align:center;}table{width:80%;margin:20px auto;border-collapse:collapse;}th,td{padding:10px;border:1px solid #334155;}button{padding:10px;background:#38bdf8;border:none;border-radius:5px;cursor:pointer;}</style></head>
    <body>
        <h2>User Management</h2>
        <form action="/add" method="POST">
            <input name="email" placeholder="Email" required>
            <select name="days"><option value="1">1 Day</option><option value="30">30 Days</option><option value="36500">Lifetime</option></select>
            <button type="submit">Add User</button>
        </form>
        <table><tr><th>Email</th><th>Expiry</th><th>Action</th></tr>
        {% for u in users %}
        <tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" style="color:red">Remove</a></td></tr>
        {% endfor %}
        </table>
    </body></html>
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

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
