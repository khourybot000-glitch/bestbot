import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
# New Token Updated
BOT_TOKEN = "8433565422:AAGwXsTMYFt6hzUh7kc4d46naJaHAY_SmQY"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V17_90Ticks_Final']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

# --- KEYBOARDS ---
def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('START 🚀', 'STOP 🛑')
    return markup

# --- UTILITY: RESILIENT CONNECTION ---
def get_safe_connection(token):
    while True:
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
            ws.send(json.dumps({"authorize": token}))
            res = json.loads(ws.recv())
            if "authorize" in res:
                return ws, res["authorize"].get("currency", "USD")
            ws.close()
        except:
            time.sleep(2)

# --- CORE ENGINE ---
def user_trading_loop(chat_id, token):
    """Monitors the clock and triggers analysis at Second 0"""
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id, "is_running": True})
        if not session: break

        now = datetime.now()
        # Trigger ONLY at second 0
        if now.second == 0:
            acc_data = session["accounts_data"][token]
            # Only start a new analysis if NOT in the middle of a martingale series
            if acc_data.get("streak", 0) == 0:
                threading.Thread(target=run_analysis_and_trade, args=(chat_id, token)).start()
            time.sleep(50) # Avoid double triggers
        time.sleep(0.5)

def run_analysis_and_trade(chat_id, token, force_target=None, force_stake=None):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session or not session["is_running"]: return

    ws = None
    try:
        ws, currency = get_safe_connection(token)
        # Request 90 Ticks History
        ws.send(json.dumps({"ticks_history": "R_100", "count": 90, "end": "latest", "style": "ticks"}))
        res = json.loads(ws.recv())
        
        target = force_target
        stake = force_stake if force_stake else session["accounts_data"][token]["current_stake"]

        if "history" in res and not target:
            prices = res["history"]["prices"]
            # Divide into 3 candles (30 ticks each)
            c1 = "UP" if prices[29] > prices[0] else "DOWN"
            c2 = "UP" if prices[59] > prices[30] else "DOWN"
            c3 = "UP" if prices[89] > prices[60] else "DOWN"
            
            pattern = [c1, c2, c3]
            # Logic: UP-DOWN-UP -> CALL | DOWN-UP-DOWN -> PUT
            if pattern == ["UP", "DOWN", "UP"]: target = "CALL"
            elif pattern == ["DOWN", "UP", "DOWN"]: target = "PUT"

        if target:
            ws.send(json.dumps({
                "buy": "1", "price": stake,
                "parameters": {
                    "amount": stake, "basis": "stake", "contract_type": target,
                    "duration": 1, "duration_unit": "m", "symbol": "R_100", "currency": currency
                }
            }))
            buy_res = json.loads(ws.recv())
            if "buy" in buy_res:
                contract_id = buy_res["buy"]["contract_id"]
                bot.send_message(chat_id, f"🚀 **Trade Placed!**\nType: `{target}` | Stake: `{stake} {currency}`\nBased on 90-Tick analysis.")
                
                time.sleep(60) # Trade duration (1 minute)
                monitor_result(chat_id, token, contract_id, target)
        ws.close()
    except:
        if ws: ws.close()

def monitor_result(chat_id, token, contract_id, last_type):
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
            time.sleep(2)
        except: time.sleep(2)

def handle_outcome(chat_id, token, profit, last_type):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    acc = session["accounts_data"][token]
    is_win = profit > 0
    
    if is_win:
        new_streak = 0
        new_stake = session["initial_stake"]
        status_msg = f"✅ **PROFIT (+{profit})**"
    else:
        new_streak = acc.get("streak", 0) + 1
        new_stake = round(acc["current_stake"] * 2.2, 2)
        status_msg = f"❌ **LOSS ({profit})**"

    # Update Statistics
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.streak": new_streak,
        f"accounts_data.{token}.total_profit": round(acc["total_profit"] + profit, 2),
        f"accounts_data.{token}.wins": acc.get("wins", 0) + (1 if is_win else 0),
        f"accounts_data.{token}.losses": acc.get("losses", 0) + (0 if is_win else 1)
    }})

    # Updated Acc Data for message
    updated_acc = active_sessions_col.find_one({"chat_id": chat_id})["accounts_data"][token]
    stats = (f"{status_msg}\n━━━━━━━━━━━━━━━\n"
             f"Total Profit: `{updated_acc['total_profit']}`\n"
             f"Wins: `{updated_acc['wins']}` | Losses: `{updated_acc['losses']}`\n"
             f"Streak: `{new_streak}/5`\n━━━━━━━━━━━━━━━")
    bot.send_message(chat_id, stats)

    if new_streak >= 5:
        stop_session(chat_id, "Stop Loss Triggered (5 Consecutive Losses).")
    elif updated_acc['total_profit'] >= session["target_profit"]:
        stop_session(chat_id, "Target Profit Reached! 🎯")
    elif not is_win:
        # Immediate Reverse Martingale
        next_type = "CALL" if last_type == "PUT" else "PUT"
        bot.send_message(chat_id, f"🔄 **Immediate Martingale (Reverse):** `{next_type}` with `{new_stake}`")
        threading.Thread(target=run_analysis_and_trade, args=(chat_id, token, next_type, new_stake)).start()

def stop_session(chat_id, reason):
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
    bot.send_message(chat_id, f"🛑 **Bot Stopped**\nReason: {reason}", reply_markup=main_keyboard())

# --- TELEGRAM HANDLERS (ENGLISH) ---
@bot.message_handler(commands=['start'])
def welcome(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "Welcome! Please enter your registered Email:", reply_markup=main_keyboard())
    bot.register_next_step_handler(m, auth)

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def b_start(m): welcome(m)

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def b_stop(m): stop_session(m.chat.id, "Manual Stop requested.")

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "Step 2: Enter your API Token:")
        bot.register_next_step_handler(m, lambda msg: setup_stake(msg, msg.text.strip()))
    else: bot.send_message(m.chat.id, "🚫 Access Denied or Expired.")

def setup_stake(m, token):
    bot.send_message(m.chat.id, "Step 3: Enter Initial Stake:")
    bot.register_next_step_handler(m, lambda msg: setup_target(msg, token, float(msg.text)))

def setup_target(m, token, stake):
    bot.send_message(m.chat.id, "Step 4: Enter Target Profit:")
    bot.register_next_step_handler(m, lambda msg: start_engine(msg, token, stake, float(msg.text)))

def start_engine(m, token, stake, target):
    acc_data = {token: {"current_stake": stake, "total_profit": 0, "streak": 0, "wins": 0, "losses": 0}}
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {
        "is_running": True, "tokens": [token], "initial_stake": stake, "target_profit": target, "accounts_data": acc_data
    }}, upsert=True)
    bot.send_message(m.chat.id, "🛰️ Sniper Initialized (90-Tick Analysis). Waiting for Second 0...")
    threading.Thread(target=user_trading_loop, args=(m.chat.id, token), daemon=True).start()

# --- HTML ADMIN PANEL ---
@app.route('/')
def admin():
    users = list(users_col.find())
    return render_template_string("""
    <body style="background:#0f172a; color:#f8fafc; font-family:sans-serif; text-align:center; padding:50px;">
        <div style="background:#1e293b; padding:20px; border-radius:12px; display:inline-block;">
            <h2>Sniper Admin Panel v17</h2>
            <form action="/add" method="POST">
                <input name="email" placeholder="Email" required style="padding:10px;">
                <select name="days" style="padding:10px;">
                    <option value="1">1 Day</option>
                    <option value="30">30 Days</option>
                    <option value="36500">LifeTime</option>
                </select>
                <button type="submit" style="padding:10px; background:#38bdf8;">Activate</button>
            </form>
            <table style="width:100%; margin-top:20px; border-collapse:collapse;">
                <tr><th>Email</th><th>Expiry</th><th>Action</th></tr>
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

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
