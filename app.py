import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
BOT_TOKEN = "8433565422:AAH7xaW3Uow5x40HBXVQy1KS6iPy_j585Ms"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V15_Final_Package']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

# --- KEYBOARDS ---
def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('START 🚀', 'STOP 🛑')
    return markup

# --- UTILITY: RESILIENT WEBSOCKET ---
def get_safe_connection(token):
    while True:
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
            ws.send(json.dumps({"authorize": token}))
            res = json.loads(ws.recv())
            if "authorize" in res:
                currency = res["authorize"].get("currency", "USD")
                return ws, currency
            ws.close()
        except:
            time.sleep(2)

# --- CORE TRADING ENGINE (MULTI-THREADED) ---
def user_trading_loop(chat_id, token):
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id, "is_running": True})
        if not session: break

        now = datetime.now()
        if now.second == 0:
            acc_data = session["accounts_data"][token]
            ws = None
            try:
                ws, currency = get_safe_connection(token)
                ws.send(json.dumps({"ticks_history": "R_100", "count": 30, "end": "latest", "style": "ticks"}))
                res = json.loads(ws.recv())
                
                if "history" in res:
                    prices = res["history"]["prices"]
                    c1 = "UP" if prices[9] > prices[0] else "DOWN"
                    c2 = "UP" if prices[19] > prices[10] else "DOWN"
                    c3 = "UP" if prices[29] > prices[20] else "DOWN"
                    
                    pattern = [c1, c2, c3]
                    target = None
                    # Inverted Logic Implementation
                    if pattern == ["UP", "DOWN", "UP"]: target = "PUT"
                    elif pattern == ["DOWN", "UP", "DOWN"]: target = "CALL"
                    
                    if target:
                        stake = acc_data["current_stake"]
                        ws.send(json.dumps({
                            "buy": "1", "price": stake,
                            "parameters": {
                                "amount": stake, "basis": "stake", "contract_type": target,
                                "duration": 10, "duration_unit": "t", "symbol": "R_100", "currency": currency
                            }
                        }))
                        buy_res = json.loads(ws.recv())
                        if "buy" in buy_res:
                            contract_id = buy_res["buy"]["contract_id"]
                            bot.send_message(chat_id, f"🎯 **Signal!** Pattern: `{pattern}`\nTrade: `{target}` | Stake: `{stake} {currency}`")
                            
                            time.sleep(20)
                            while True:
                                try:
                                    ws_res, _ = get_safe_connection(token)
                                    ws_res.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
                                    contract_data = json.loads(ws_res.recv())
                                    if "proposal_open_contract" in contract_data:
                                        status = contract_data["proposal_open_contract"]
                                        if status.get("is_expired"):
                                            profit = float(status.get("profit", 0))
                                            handle_result(chat_id, token, profit)
                                            ws_res.close()
                                            break
                                    ws_res.close()
                                    time.sleep(2)
                                except: time.sleep(2)
                if ws: ws.close()
            except:
                if ws: ws.close()
            time.sleep(50)
        time.sleep(0.5)

def handle_result(chat_id, token, profit):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    acc = session["accounts_data"][token]
    is_win = profit > 0
    new_wins = acc.get("wins", 0) + (1 if is_win else 0)
    new_losses = acc.get("losses", 0) + (0 if is_win else 1)
    new_total_profit = round(acc["total_profit"] + profit, 2)
    
    if is_win:
        new_streak = 0
        new_stake = session["initial_stake"]
        msg = "✅ **WIN**"
    else:
        new_streak = acc.get("streak", 0) + 1
        new_stake = round(acc["current_stake"] * 2.2, 2)
        msg = "❌ **LOSS**"

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.streak": new_streak,
        f"accounts_data.{token}.wins": new_wins,
        f"accounts_data.{token}.losses": new_losses,
        f"accounts_data.{token}.total_profit": new_total_profit
    }})

    report = (f"{msg} ({profit})\n━━━━━━━━━━━━━━━\n"
              f"Profit: `{new_total_profit}`\n"
              f"W: `{new_wins}` | L: `{new_losses}`\n"
              f"Streak: `{new_streak}`\n━━━━━━━━━━━━━━━")
    bot.send_message(chat_id, report)

    if new_streak >= 4: stop_session(chat_id, "4 Losses Reached.")
    elif new_total_profit >= session["target_profit"]: stop_session(chat_id, "Target Reached! 🎯")

def stop_session(chat_id, reason):
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
    bot.send_message(chat_id, f"🛑 **Stopped**: {reason}", reply_markup=main_keyboard())

# --- TELEGRAM HANDLERS ---
@bot.message_handler(commands=['start'])
def welcome(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "Enter Email:", reply_markup=main_keyboard())
    bot.register_next_step_handler(m, auth)

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def b_start(m): welcome(m)

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def b_stop(m): stop_session(m.chat.id, "Manual Stop.")

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "Enter API Token:")
        bot.register_next_step_handler(m, lambda msg: setup_params(msg, "stake"))
    else: bot.send_message(m.chat.id, "🚫 Access Denied.")

def setup_params(m, step, token=None, stake=None):
    if step == "stake":
        bot.send_message(m.chat.id, "Initial Stake:")
        bot.register_next_step_handler(m, lambda msg: setup_params(msg, "target", token=m.text.strip()))
    elif step == "target":
        bot.send_message(m.chat.id, "Target Profit:")
        bot.register_next_step_handler(m, lambda msg: start_engine(msg, token, float(stake_val), float(msg.text)) if (stake_val := m.text) else None)

def start_engine(m, token, stake, target):
    acc_data = {token: {"current_stake": stake, "total_profit": 0, "streak": 0, "wins": 0, "losses": 0}}
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {
        "is_running": True, "tokens": [token], "initial_stake": stake, "target_profit": target, "accounts_data": acc_data
    }}, upsert=True)
    bot.send_message(m.chat.id, "🛰️ Sniper Active!")
    threading.Thread(target=user_trading_loop, args=(m.chat.id, token), daemon=True).start()

# --- COMPLETE HTML ADMIN ---
@app.route('/')
def admin():
    users = list(users_col.find())
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Sniper Admin</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { background: #0f172a; color: #f8fafc; font-family: sans-serif; text-align: center; padding: 20px; }
            .card { background: #1e293b; padding: 25px; border-radius: 12px; display: inline-block; box-shadow: 0 4px 15px rgba(0,0,0,0.3); }
            input, select, button { padding: 12px; margin: 8px; border-radius: 6px; border: none; }
            button { background: #38bdf8; color: #000; font-weight: bold; cursor: pointer; }
            table { width: 100%; margin-top: 20px; border-collapse: collapse; }
            th, td { padding: 12px; border-bottom: 1px solid #334155; }
            .delete { color: #f43f5e; text-decoration: none; font-weight: bold; }
        </style>
    </head>
    <body>
        <div class="card">
            <h2>User Management</h2>
            <form action="/add" method="POST">
                <input name="email" placeholder="User Email" required>
                <select name="days">
                    <option value="1">1 Day</option>
                    <option value="30">30 Days</option>
                    <option value="36500">36500 Days (LifeTime)</option>
                </select>
                <button type="submit">Activate User</button>
            </form>
            <table>
                <tr><th>Email</th><th>Expiry</th><th>Action</th></tr>
                {% for u in users %}
                <tr>
                    <td>{{u.email}}</td>
                    <td>{{u.expiry}}</td>
                    <td><a href="/delete/{{u.email}}" class="delete">Delete</a></td>
                </tr>
                {% endfor %}
            </table>
        </div>
    </body>
    </html>
    """, users=users)

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
