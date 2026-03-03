import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION (UPDATED TOKEN) ---
BOT_TOKEN = "8309516496:AAE8uhI2HCUTIm9Z5NA-n65n8mUgQd7544Q"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V24_Final_Signal']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

trade_locks = {}

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

# --- CORE TRADING ENGINE (10-Minute Hybrid Logic) ---
def user_trading_loop(chat_id, token):
    while True:
        try:
            session = active_sessions_col.find_one({"chat_id": chat_id, "is_running": True})
            if not session: break

            now = datetime.now()
            # Analyze at minute 9:56 of the 10-min candle
            if now.minute % 10 == 9 and now.second == 56:
                if not trade_locks.get(chat_id, False):
                    threading.Thread(target=run_trade_logic, args=(chat_id, token)).start()
                    time.sleep(5) 
            time.sleep(0.5)
        except:
            time.sleep(1)

def run_trade_logic(chat_id, token):
    trade_locks[chat_id] = True
    session = active_sessions_col.find_one({"chat_id": chat_id, "is_running": True})
    if not session:
        trade_locks[chat_id] = False
        return

    ws = None
    try:
        ws, currency = get_safe_connection(token)
        acc_data = session["accounts_data"][token]
        stake = acc_data["current_stake"]

        ws.send(json.dumps({"ticks_history": "R_100", "end": "latest", "count": 620, "style": "ticks"}))
        res = json.loads(ws.recv())

        target = None
        if "history" in res and "prices" in res["history"]:
            prices = res["history"]["prices"]
            if len(prices) >= 600:
                current_p = float(prices[-1])         
                p_start_mid = float(prices[-301])   # Start of second 5 mins (Min 5)
                p_start_10min = float(prices[-597])   # Start of 10 min candle (Min 0)

                # Check directions
                is_first_5_down = p_start_mid < p_start_10min
                is_first_5_up = p_start_mid > p_start_10min
                is_second_5_up = current_p > p_start_mid
                is_second_5_down = current_p < p_start_mid

                # --- HYBRID LOGIC ---
                # CALL if first 5m was DOWN and second 5m is UP
                if is_first_5_down and is_second_5_up:
                    target = "CALL"
                # PUT if first 5m was UP and second 5m is DOWN
                elif is_first_5_up and is_second_5_down:
                    target = "PUT"
        
        if target:
            ws.send(json.dumps({
                "buy": "1", "price": stake,
                "parameters": {
                    "amount": stake, "basis": "stake", "contract_type": target,
                    "duration": 60, "duration_unit": "s", "symbol": "R_100", "currency": currency
                }
            }))
            buy_res = json.loads(ws.recv())
            if "buy" in buy_res:
                contract_id = buy_res["buy"]["contract_id"]
                bot.send_message(chat_id, f"🔔 **Trade Placed: {target}**\nStake: ${stake}\nDuration: 1 Minute")
                ws.close()
                time.sleep(60) 
                monitor_result(chat_id, token, contract_id, target)
                return 

        if ws: ws.close()
        trade_locks[chat_id] = False
    except:
        if ws: ws.close()
        trade_locks[chat_id] = False

def monitor_result(chat_id, token, contract_id, last_type):
    ws, _ = get_safe_connection(token)
    while True:
        try:
            ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
            res = json.loads(ws.recv())
            if "proposal_open_contract" in res:
                data = res["proposal_open_contract"]
                if data.get("is_expired") or data.get("status") in ["won", "lost"]:
                    profit = float(data.get("profit", 0))
                    ws.close()
                    handle_outcome(chat_id, token, profit, last_type)
                    break
            time.sleep(1)
        except:
            ws, _ = get_safe_connection(token)
            time.sleep(1)

def handle_outcome(chat_id, token, profit, last_type):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session: return
    acc = session["accounts_data"][token]
    is_win = profit > 0
    
    new_streak = 0 if is_win else acc.get("streak", 0) + 1
    new_total_profit = round(acc["total_profit"] + profit, 2)
    new_wins = acc.get("wins", 0) + (1 if is_win else 0)
    new_losses = acc.get("losses", 0) + (0 if is_win else 1)
    new_stake = session["initial_stake"] if is_win else round(acc["current_stake"] * 2.2, 2)

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.streak": new_streak,
        f"accounts_data.{token}.total_profit": new_total_profit,
        f"accounts_data.{token}.wins": new_wins,
        f"accounts_data.{token}.losses": new_losses
    }})

    status = "✅ WIN" if is_win else "❌ LOSS"
    stats_msg = (
        f"**Result: {status}**\n"
        f"----------------------\n"
        f"Wins: {new_wins} | Losses: {new_losses}\n"
        f"Total Profit: ${new_total_profit}\n"
        f"Next Stake: ${new_stake}"
    )
    bot.send_message(chat_id, stats_msg, parse_mode="Markdown")

    if new_streak >= 4 or new_total_profit >= session["target_profit"]:
        active_sessions_col.delete_one({"chat_id": chat_id})
        bot.send_message(chat_id, "🛑 Session Closed (Target/Limit).", reply_markup=main_keyboard())
    
    trade_locks[chat_id] = False

# --- UI & DATA CONTROL ---
def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton('START 🚀'), types.KeyboardButton('STOP 🛑'))
    return markup

def reset_user_data(chat_id):
    active_sessions_col.delete_one({"chat_id": chat_id})
    if chat_id in trade_locks:
        trade_locks[chat_id] = False

@bot.message_handler(commands=['start'])
def start_cmd(m):
    reset_user_data(m.chat.id)
    bot.send_message(m.chat.id, "♻️ Data Reset. Enter Registered Email:", reply_markup=main_keyboard())
    bot.register_next_step_handler(m, auth)

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_handler(m):
    reset_user_data(m.chat.id)
    bot.send_message(m.chat.id, "🛑 Bot Stopped. All session data cleared.", reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def start_button_handler(m):
    reset_user_data(m.chat.id)
    bot.send_message(m.chat.id, "♻️ Restarting... Enter Registered Email:", reply_markup=main_keyboard())
    bot.register_next_step_handler(m, auth)

def auth(m):
    if m.text == 'STOP 🛑':
        stop_handler(m)
        return
    user = users_col.find_one({"email": m.text.strip().lower()})
    if user and datetime.strptime(user['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Access Granted. Enter API Token:")
        bot.register_next_step_handler(m, lambda msg: setup_stake(msg, msg.text.strip()))
    else: 
        bot.send_message(m.chat.id, "🚫 Email not registered or expired.")

def setup_stake(m, token):
    if m.text == 'STOP 🛑':
        stop_handler(m)
        return
    bot.send_message(m.chat.id, "Initial Stake ($):")
    bot.register_next_step_handler(m, lambda msg: setup_target(msg, token, float(msg.text)))

def setup_target(m, token, stake):
    if m.text == 'STOP 🛑':
        stop_handler(m)
        return
    bot.send_message(m.chat.id, "Target Profit ($):")
    bot.register_next_step_handler(m, lambda msg: start_engine(msg, token, stake, float(msg.text)))

def start_engine(m, token, stake, target):
    if m.text == 'STOP 🛑':
        stop_handler(m)
        return
    acc_data = {token: {"current_stake": stake, "total_profit": 0, "streak": 0, "wins": 0, "losses": 0}}
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {
        "is_running": True, "initial_stake": stake, "target_profit": target, "accounts_data": acc_data
    }}, upsert=True)
    bot.send_message(m.chat.id, "🛰️ **10min Hybrid Logic Active**\nMonitoring market...", reply_markup=main_keyboard())
    threading.Thread(target=user_trading_loop, args=(m.chat.id, token), daemon=True).start()

# --- ADMIN PANEL ---
@app.route('/')
def admin():
    users = list(users_col.find())
    return render_template_string("""
    <!DOCTYPE html><html><head><title>Admin Panel</title><style>
    body { background: #0f172a; color: #f8fafc; font-family: sans-serif; text-align: center; }
    .card { background: #1e293b; padding: 20px; border-radius: 10px; display: inline-block; margin-top: 50px; width: 420px; }
    input, select { padding: 10px; margin: 5px; width: 85%; border-radius: 5px; border: none; }
    button { padding: 10px 20px; background: #38bdf8; border: none; cursor: pointer; font-weight: bold; border-radius: 5px; margin-top: 10px; }
    table { width: 100%; margin-top: 20px; border-collapse: collapse; }
    th, td { padding: 10px; border-bottom: 1px solid #334155; }
    </style></head><body><div class="card"><h2>User Management</h2>
    <form action="/add" method="POST">
        <input name="email" placeholder="User Email" required><br>
        <select name="days">
            <option value="1">1 Day</option>
            <option value="7">7 Days</option>
            <option value="30">30 Days</option>
            <option value="36500">Lifetime</option>
        </select><br>
        <button type="submit">Activate User</button>
    </form>
    <table><tr><th>Email</th><th>Expiry</th><th>Action</th></tr>
    {% for u in users %}
    <tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" style="color:#f87171">Delete</a></td></tr>
    {% endfor %}</table></div></body></html>
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
