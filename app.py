import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
BOT_TOKEN = "8433565422:AAFhoezq7TqJ3y4PJrEMq4TXKDk4rEfOAXM"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V24_Final_Signal']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

trade_locks = {}

# --- KEYBOARDS ---
def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('START 🚀', 'STOP 🛑')
    return markup

# --- UTILITY ---
def get_safe_connection(token):
    while True:
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
            ws.send(json.dumps({"authorize": token}))
            res = json.loads(ws.recv())
            if "authorize" in res: return ws, res["authorize"].get("currency", "USD")
            ws.close()
        except: time.sleep(2)

# --- CORE ENGINE ---
def user_trading_loop(chat_id, token):
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id, "is_running": True})
        if not session: break

        now = datetime.now()
        # Strictly trigger at Second 0
        if now.second == 0 and not trade_locks.get(chat_id, False):
            acc_data = session["accounts_data"][token]
            
            # Martingale Logic
            if acc_data.get("streak", 0) > 0:
                last_type = acc_data.get("last_type")
                next_type = "CALL" if last_type == "PUT" else "PUT"
                new_stake = acc_data["current_stake"]
                threading.Thread(target=run_trade_logic, args=(chat_id, token, next_type, new_stake)).start()
            
            # Fresh Signal Analysis
            else:
                threading.Thread(target=run_trade_logic, args=(chat_id, token)).start()
            
            time.sleep(50) 
        time.sleep(0.5)

def run_trade_logic(chat_id, token, force_target=None, force_stake=None):
    trade_locks[chat_id] = True
    session = active_sessions_col.find_one({"chat_id": chat_id, "is_running": True})
    if not session:
        trade_locks[chat_id] = False
        return

    ws = None
    try:
        ws, currency = get_safe_connection(token)
        target = force_target
        stake = force_stake if force_stake else session["accounts_data"][token]["current_stake"]

        # 180 Ticks / 6 Candles Analysis
        if not target:
            ws.send(json.dumps({"ticks_history": "R_100", "count": 180, "end": "latest", "style": "ticks"}))
            res = json.loads(ws.recv())
            if "history" in res:
                p = res["history"]["prices"]
                # Closing price of one is opening of next
                c1 = "UP" if p[29] > p[0] else "DOWN"
                c2 = "UP" if p[59] > p[30] else "DOWN"
                c3 = "UP" if p[89] > p[60] else "DOWN"
                c4 = "UP" if p[119] > p[90] else "DOWN"
                c5 = "UP" if p[149] > p[120] else "DOWN"
                c6 = "UP" if p[179] > p[150] else "DOWN"
                
                pattern = [c1, c2, c3, c4, c5, c6]
                
                # YOUR SPECIFIC SIGNAL LOGIC
                if pattern == ["UP", "DOWN", "UP", "DOWN", "UP", "DOWN"]:
                    target = "PUT"
                elif pattern == ["DOWN", "UP", "DOWN", "UP", "DOWN", "UP"]:
                    target = "CALL"

        if target:
            ws.send(json.dumps({
                "buy": "1", "price": stake,
                "parameters": {
                    "amount": stake, "basis": "stake", "contract_type": target,
                    "duration": 54, "duration_unit": "s", "symbol": "R_100", "currency": currency
                }
            }))
            buy_res = json.loads(ws.recv())
            if "buy" in buy_res:
                contract_id = buy_res["buy"]["contract_id"]
                bot.send_message(chat_id, f"🚀 **Order Sent at Sec 0**\nSignal: `{target}` | Stake: `${stake}`\nPattern: 6 Candles (180 Ticks)")
                time.sleep(54)
                monitor_result(chat_id, token, contract_id, target)
            else: trade_locks[chat_id] = False
        else:
            trade_locks[chat_id] = False # No pattern matched
        
        if ws: ws.close()
    except:
        trade_locks[chat_id] = False

def monitor_result(chat_id, token, contract_id, last_type):
    time.sleep(2)
    while True:
        try:
            ws, _ = get_safe_connection(token)
            ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
            res = json.loads(ws.recv())
            if "proposal_open_contract" in res:
                data = res["proposal_open_contract"]
                if data.get("is_expired"):
                    profit = float(data.get("profit", 0))
                    ws.close()
                    handle_outcome(chat_id, token, profit, last_type)
                    break
            ws.close()
            time.sleep(1)
        except: time.sleep(1)

def handle_outcome(chat_id, token, profit, last_type):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    acc = session["accounts_data"][token]
    is_win = profit > 0
    
    new_streak = 0 if is_win else acc.get("streak", 0) + 1
    new_stake = session["initial_stake"] if is_win else round(acc["current_stake"] * 2.2, 2)
    new_total_profit = round(acc["total_profit"] + profit, 2)
    
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.streak": new_streak,
        f"accounts_data.{token}.last_type": last_type,
        f"accounts_data.{token}.total_profit": new_total_profit,
        f"accounts_data.{token}.wins": acc.get("wins", 0) + (1 if is_win else 0),
        f"accounts_data.{token}.losses": acc.get("losses", 0) + (0 if is_win else 1)
    }})

    status = "✅ PROFIT" if is_win else "❌ LOSS"
    bot.send_message(chat_id, f"{status} (${profit})\nTotal: {new_total_profit}\nStreak: {new_streak}/5")

    if new_streak >= 5 or new_total_profit >= session["target_profit"]:
        stop_session(chat_id, "Session Finished.")
    else:
        trade_locks[chat_id] = False # Unlock for next Second 0

def stop_session(chat_id, reason):
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
    trade_locks[chat_id] = False
    bot.send_message(chat_id, f"🛑 **Bot Stopped**\nReason: {reason}", reply_markup=main_keyboard())

# --- ADMIN PANEL HTML ---
@app.route('/')
def admin():
    users = list(users_col.find())
    return render_template_string("""
    <body style="background:#0f172a; color:#f8fafc; font-family:sans-serif; text-align:center; padding:50px;">
        <div style="background:#1e293b; padding:20px; border-radius:12px; display:inline-block; width:85%;">
            <h2>Sniper Bot Admin v24</h2>
            <form action="/add" method="POST">
                <input name="email" placeholder="User Email" required style="padding:10px; border-radius:5px;">
                <select name="days" style="padding:10px; border-radius:5px;">
                    <option value="1">1 Day</option>
                    <option value="30">30 Days</option>
                    <option value="36500">36500 Days (LifeTime)</option>
                </select>
                <button type="submit" style="padding:10px; background:#38bdf8; border:none; cursor:pointer; font-weight:bold;">Activate</button>
            </form>
            <table style="width:100%; margin-top:20px; text-align:left; border-collapse:collapse;">
                <tr style="border-bottom:2px solid #475569;"><th>Email</th><th>Expiry Date</th><th>Action</th></tr>
                {% for u in users %}
                <tr>
                    <td style="padding:10px; border-bottom:1px solid #334155;">{{u.email}}</td>
                    <td style="padding:10px; border-bottom:1px solid #334155;">{{u.expiry}}</td>
                    <td style="padding:10px; border-bottom:1px solid #334155;"><a href="/delete/{{u.email}}" style="color:#f43f5e;">Delete</a></td>
                </tr>
                {% endfor %}
            </table>
        </div>
    </body>""", users=users)

@app.route('/add', methods=['POST'])
def add():
    exp = (datetime.now() + timedelta(days=int(request.form.get('days')))).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower().strip()}, {"$set": {"expiry": exp}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete(email):
    users_col.delete_one({"email": email})
    return redirect('/')

# --- TELEGRAM BOT HANDLERS ---
@bot.message_handler(commands=['start'])
def welcome(m):
    trade_locks[m.chat.id] = False
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "Hello! Please enter your registered Email:", reply_markup=main_keyboard())
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "Authenticated. Please enter your API Token:")
        bot.register_next_step_handler(m, lambda msg: setup_stake(msg, msg.text.strip()))
    else: bot.send_message(m.chat.id, "🚫 Access Denied or Expired.")

def setup_stake(m, token):
    bot.send_message(m.chat.id, "Initial Stake (Amount):")
    bot.register_next_step_handler(m, lambda msg: setup_target(msg, token, float(msg.text)))

def setup_target(m, token, stake):
    bot.send_message(m.chat.id, "Target Profit ($):")
    bot.register_next_step_handler(m, lambda msg: start_engine(msg, token, stake, float(msg.text)))

def start_engine(m, token, stake, target):
    acc_data = {token: {"current_stake": stake, "total_profit": 0, "streak": 0, "wins": 0, "losses": 0}}
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {
        "is_running": True, "tokens": [token], "initial_stake": stake, "target_profit": target, "accounts_data": acc_data
    }}, upsert=True)
    bot.send_message(m.chat.id, "🛰️ Sniper V24 Started. Analyzing 180 Ticks at Second 0...")
    threading.Thread(target=user_trading_loop, args=(m.chat.id, token), daemon=True).start()

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
