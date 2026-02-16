import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
BOT_TOKEN = "8433565422:AAHmYyeMm9MjjQ_FBQe7gwpv4EEzM6upWZI"
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

# --- CORE TRADING ENGINE ---
def user_trading_loop(chat_id, token):
    while True:
        try:
            session = active_sessions_col.find_one({"chat_id": chat_id, "is_running": True})
            if not session: break

            now = datetime.now()
            if now.second == 0 and not trade_locks.get(chat_id, False):
                acc_data = session["accounts_data"][token]
                # Check for pattern only on streak 0 or 2 (start of a new "opportunity")
                if acc_data.get("streak", 0) % 2 == 0:
                    threading.Thread(target=run_trade_logic, args=(chat_id, token)).start()
                    time.sleep(50) 
            time.sleep(0.5)
        except:
            time.sleep(1)

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

        if not target:
            # Analyze last 6 minutes candles
            end_t = int(time.time())
            start_t = end_t - 360
            ws.send(json.dumps({"ticks_history": "R_100", "start": start_t, "end": end_t, "style": "ticks"}))
            res = json.loads(ws.recv())
            if "history" in res:
                prices, times = res["history"]["prices"], res["history"]["times"]
                candles = []
                for i in range(6):
                    m_s, m_e = start_t + (i*60), start_t + ((i+1)*60)
                    m_p = [p for p, t in zip(prices, times) if m_s <= t < m_e]
                    if len(m_p) >= 2: candles.append("UP" if m_p[-1] > m_p[0] else "DOWN")
                    else: candles.append("NEUTRAL")

                if len(candles) == 6 and "NEUTRAL" not in candles:
                    # Check for alternating pattern
                    if all(candles[i] != candles[i+1] for i in range(5)):
                        target = "CALL" if candles[5] == "UP" else "PUT"

        if target:
            ws.send(json.dumps({
                "buy": "1", "price": stake,
                "parameters": {
                    "amount": stake, "basis": "stake", "contract_type": target,
                    "duration": 56, "duration_unit": "s", "symbol": "R_100", "currency": currency
                }
            }))
            buy_res = json.loads(ws.recv())
            if "buy" in buy_res:
                contract_id = buy_res["buy"]["contract_id"]
                ws.close() # DISCONNECT immediately for 56s
                bot.send_message(chat_id, f"🔔 Trade Placed\nStake: ${stake}")
                
                time.sleep(56) # Trade duration wait
                monitor_result(chat_id, token, contract_id, target)
            else:
                if ws: ws.close()
                trade_locks[chat_id] = False
        else:
            if ws: ws.close()
            trade_locks[chat_id] = False
    except:
        trade_locks[chat_id] = False

def monitor_result(chat_id, token, contract_id, last_type):
    # RECONNECT to monitor result
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
            time.sleep(1) # Poll every 1s
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
        f"accounts_data.{token}.last_type": last_type,
        f"accounts_data.{token}.total_profit": new_total_profit,
        f"accounts_data.{token}.wins": new_wins,
        f"accounts_data.{token}.losses": new_losses
    }})

    status = "✅ WIN" if is_win else "❌ LOSS"
    stats_msg = (
        f"Result: {status}\n"
        f"----------------------\n"
        f"Wins: {new_wins} | Losses: {new_losses}\n"
        f"Total Profit: ${new_total_profit}\n"
        f"Next Stake: ${new_stake}"
    )
    bot.send_message(chat_id, stats_msg)

    # Termination check: 4 losses (2 opportunities) or Target Profit reached
    if new_streak >= 4 or new_total_profit >= session["target_profit"]:
        active_sessions_col.delete_one({"chat_id": chat_id})
        trade_locks[chat_id] = False
        bot.send_message(chat_id, "🛑 Session Finished. Data Cleared.", reply_markup=main_keyboard())
    else:
        if not is_win and (new_streak == 1 or new_streak == 3):
            # Immediate Martingale in opposite direction
            next_type = "PUT" if last_type == "CALL" else "CALL"
            threading.Thread(target=run_trade_logic, args=(chat_id, token, next_type, new_stake)).start()
        else:
            # Opportunity lost or reset, wait for next minute analysis
            trade_locks[chat_id] = False

# --- UI & ADMIN ---
def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton('START 🚀'), types.KeyboardButton('STOP 🛑'))
    return markup

@bot.message_handler(commands=['start'])
def start_cmd(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    trade_locks[m.chat.id] = False
    bot.send_message(m.chat.id, "♻️ System Reset. Enter Email:", reply_markup=main_keyboard())
    bot.register_next_step_handler(m, auth)

@bot.message_handler(func=lambda m: m.text in ['STOP 🛑', 'START 🚀'])
def menu_handler(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    trade_locks[m.chat.id] = False
    if m.text == 'STOP 🛑':
        bot.send_message(m.chat.id, "🛑 Stopped. Session Deleted.", reply_markup=main_keyboard())
    else:
        bot.send_message(m.chat.id, "♻️ Reset. Enter Email:", reply_markup=main_keyboard())
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
    acc_data = {token: {"current_stake": stake, "total_profit": 0, "streak": 0, "wins": 0, "losses": 0}}
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {
        "is_running": True, "initial_stake": stake, "target_profit": target, "accounts_data": acc_data
    }}, upsert=True)
    bot.send_message(m.chat.id, "🛰️ System Active...", reply_markup=main_keyboard())
    threading.Thread(target=user_trading_loop, args=(m.chat.id, token), daemon=True).start()

# --- ADMIN PANEL HTML ---
@app.route('/')
def admin():
    users = list(users_col.find())
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Control Panel</title>
        <style>
            body { background: #0f172a; color: #f8fafc; font-family: sans-serif; text-align: center; padding: 20px; }
            .card { background: #1e293b; padding: 30px; border-radius: 15px; display: inline-block; width: 100%; max-width: 500px; }
            input, select { padding: 12px; margin: 5px; border-radius: 8px; border: 1px solid #334155; background: #0f172a; color: white; width: 85%; }
            button { padding: 12px 25px; background: #38bdf8; border: none; border-radius: 8px; font-weight: bold; cursor: pointer; margin-top:10px; }
            table { width: 100%; margin-top: 30px; border-collapse: collapse; }
            th { background: #334155; padding: 12px; }
            td { padding: 12px; border-bottom: 1px solid #334155; }
            .del { color: #f87171; text-decoration: none; }
        </style>
    </head>
    <body>
        <div class="card">
            <h2>User Management</h2>
            <form action="/add" method="POST">
                <input name="email" placeholder="Email" required><br>
                <select name="days">
                    <option value="1">1 Day</option>
                    <option value="7">7 Days</option>
                    <option value="30">30 Days</option>
                    <option value="36500">Lifetime</option>
                </select><br>
                <button type="submit">Activate User</button>
            </form>
            <table>
                <tr><th>Email</th><th>Expiry</th><th>Action</th></tr>
                {% for u in users %}
                <tr>
                    <td>{{u.email}}</td><td>{{u.expiry}}</td>
                    <td><a href="/delete/{{u.email}}" class="del">Remove</a></td>
                </tr>
                {% endfor %}
            </table>
        </div>
    </body>
    </html>
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
