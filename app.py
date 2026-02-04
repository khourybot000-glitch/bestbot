import websocket, json, time, os, threading, queue
import pandas as pd
import pandas_ta as ta
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
TOKEN = "8433565422:AAF5aKUymaToeehXMbhJLeWCSodO9TkJZ14"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN, threaded=True, num_threads=100)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

msg_queue = queue.Queue()

def message_worker():
    while True:
        try:
            item = msg_queue.get()
            chat_id, text, markup = item[0], item[1], item[2] if len(item) > 2 else None
            bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)
            msg_queue.task_done()
            time.sleep(0.05) 
        except: pass

threading.Thread(target=message_worker, daemon=True).start()

def safe_send(chat_id, text, markup=None):
    msg_queue.put((chat_id, text, markup))

# --- STRATEGY LOGIC: EMA Crossover + RSI Filter ---
def get_signals(prices):
    df = pd.DataFrame(prices, columns=['close'])
    ema10 = ta.ema(df['close'], length=10)
    ema30 = ta.ema(df['close'], length=30)
    rsi = ta.rsi(df['close'], length=14)
    if ema10 is None or ema30 is None or rsi is None: return None
    
    curr_e10, prev_e10 = ema10.iloc[-1], ema10.iloc[-2]
    curr_e30, prev_e30 = ema30.iloc[-1], ema30.iloc[-2]
    curr_rsi = rsi.iloc[-1]
    
    # CALL: EMA10 crosses above EMA30 AND RSI is between 50-70
    if prev_e10 <= prev_e30 and curr_e10 > curr_e30 and 50 < curr_rsi < 70: return "CALL"
    # PUT: EMA10 crosses below EMA30 AND RSI is between 30-50
    if prev_e10 >= prev_e30 and curr_e10 < curr_e30 and 30 < curr_rsi < 50: return "PUT"
    return None

# --- TRADING ENGINE: SINGLE WEBSOCKET STRUCTURE ---
def trade_engine(chat_id):
    while True: 
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        token = session['tokens'][0]
        prices_list = []

        def on_message(ws, message):
            nonlocal prices_list
            data = json.loads(message)
            
            curr_session = active_sessions_col.find_one({"chat_id": chat_id})
            if not curr_session or not curr_session.get("is_running"):
                ws.close(); return

            # 1. Process Incoming Ticks
            if "tick" in data:
                prices_list.append(float(data["tick"]["quote"]))
                if len(prices_list) > 100: prices_list.pop(0)

                if len(prices_list) >= 40:
                    # Check status of active contracts
                    for t, acc in curr_session.get("accounts_data", {}).items():
                        if acc.get("active_contract") and acc.get("target_check_time"):
                            if datetime.now() >= datetime.fromisoformat(acc["target_check_time"]):
                                ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": acc["active_contract"]}))

                    # Signal Detection
                    if not any(acc.get("active_contract") for acc in curr_session.get("accounts_data", {}).values()):
                        signal = get_signals(prices_list)
                        if signal:
                            barrier = "-0.5" if signal == "CALL" else "+0.5"
                            acc = curr_session["accounts_data"][token]
                            payload = {
                                "proposal": 1, "amount": acc["current_stake"], "basis": "stake",
                                "contract_type": signal, "duration": 5, "duration_unit": "t",
                                "symbol": "R_100", "barrier": barrier, "currency": acc["currency"]
                            }
                            ws.send(json.dumps(payload))

            # 2. Handle Proposal & Execute Buy
            if "proposal" in data:
                ws.send(json.dumps({"buy": data["proposal"]["id"], "price": float(data["proposal"]["ask_price"])}))

            # 3. Buy Confirmation
            if "buy" in data:
                target_time = (datetime.now() + timedelta(seconds=18)).isoformat()
                active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
                    f"accounts_data.{token}.active_contract": data["buy"]["contract_id"],
                    f"accounts_data.{token}.target_check_time": target_time
                }})
                safe_send(chat_id, "🚀 *Trade Executed!* Monitoring result...")

            # 4. Result Settlement
            if "proposal_open_contract" in data:
                contract = data["proposal_open_contract"]
                if contract.get("is_expired"):
                    process_result(chat_id, token, data)

        def on_open(ws):
            ws.send(json.dumps({"authorize": token}))
            ws.send(json.dumps({"ticks_history": "R_100", "count": 100, "end": "latest", "style": "ticks"}))
            ws.send(json.dumps({"ticks": "R_100", "subscribe": 1}))

        ws = websocket.WebSocketApp("wss://blue.derivws.com/websockets/v3?app_id=16929", 
                                    on_open=on_open, on_message=on_message)
        ws.run_forever(ping_interval=10, ping_timeout=5)
        
        time.sleep(1) # Reconnect Delay
        if not active_sessions_col.find_one({"chat_id": chat_id, "is_running": True}): break

def process_result(chat_id, token, res):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session: return
    acc = session['accounts_data'].get(token)
    profit = float(res.get("proposal_open_contract", {}).get("profit", 0))
    
    new_total = acc["total_profit"] + profit
    new_wins = acc.get("win_count", 0) + (1 if profit > 0 else 0)
    new_losses = acc.get("loss_count", 0) + (1 if profit <= 0 else 0)
    
    if profit > 0:
        new_stake, new_streak, status = session["initial_stake"], 0, "✅ *WIN*"
    else:
        new_stake = float("{:.2f}".format(acc["current_stake"] * 14)) # 14x Multiplier
        new_streak = acc.get("consecutive_losses", 0) + 1
        status = "❌ *LOSS*"

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake, 
        f"accounts_data.{token}.consecutive_losses": new_streak, 
        f"accounts_data.{token}.total_profit": new_total, 
        f"accounts_data.{token}.win_count": new_wins, 
        f"accounts_data.{token}.loss_count": new_losses, 
        f"accounts_data.{token}.active_contract": None, 
        f"accounts_data.{token}.target_check_time": None
    }})
    
    stats_msg = f"📊 *Trade Stats:*\nStatus: {status}\nWins: `{new_wins}` | Losses: `{new_losses}`\nNet Profit: `{new_total:.2f}`\nNext Stake: `{new_stake}`"

    if new_total >= session.get("target_profit", 999999) or new_streak >= 2:
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}, "$unset": {"accounts_data": ""}})
        msg = "🎯 Target Profit Reached!" if new_total >= session.get("target_profit", 999999) else "🛑 Stopped after 2 Consecutive Losses."
        safe_send(chat_id, stats_msg + f"\n\n{msg}", types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))
    else:
        safe_send(chat_id, stats_msg)

# --- HTML ADMIN PANEL ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Bot Admin Panel</title><style>
body{font-family:'Segoe UI', sans-serif; background:#f4f7f9; padding:40px; text-align:center;}
.card{max-width:850px; margin:auto; background:white; padding:30px; border-radius:15px; box-shadow:0 8px 20px rgba(0,0,0,0.05);}
h2{color:#2c3e50;}
form{display:flex; gap:10px; justify-content:center; margin-bottom:30px;}
input, select{padding:12px; border:1px solid #ddd; border-radius:8px;}
button{padding:12px 25px; background:#3498db; color:white; border:none; border-radius:8px; cursor:pointer; font-weight:bold;}
table{width:100%; border-collapse:collapse;}
th, td{padding:15px; text-align:left; border-bottom:1px solid #eee;}
th{background:#f9f9f9;}
.del-link{color:#e74c3c; font-weight:bold; text-decoration:none;}</style></head>
<body><div class="card">
<h2>👥 User Access Management</h2>
<form action="/add" method="POST">
<input type="email" name="email" placeholder="User Email" required>
<select name="days"><option value="1">1 Day</option><option value="30">30 Days</option><option value="36500">Unlimited</option></select>
<button type="submit">Grant Access</button></form>
<table><thead><tr><th>Email</th><th>Expiry Date</th><th>Action</th></tr></thead>
<tbody>{% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" class="del-link">Remove</a></td></tr>{% endfor %}</tbody>
</table></div></body></html>
"""

@app.route('/')
def index():
    users = list(users_col.find())
    return render_template_string(HTML_ADMIN, users=users)

@app.route('/add', methods=['POST'])
def add_user():
    days = int(request.form.get('days'))
    exp = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower().strip()}, {"$set": {"expiry": exp}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete_user(email):
    users_col.delete_one({"email": email}); return redirect('/')

# --- TELEGRAM COMMANDS ---
@bot.message_handler(commands=['start'])
def cmd_start(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "🤖 *System Ready*\nPlease enter your registered Email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Access Authorized. Please enter your Deriv Token:")
        bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 Access Denied or Expired.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False}}, upsert=True)
    bot.send_message(m.chat.id, "Enter Initial Stake:")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text)}})
    bot.send_message(m.chat.id, "Enter Target Profit (TP):")
    bot.register_next_step_handler(m, save_tp)

def save_tp(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": float(m.text)}})
    bot.send_message(m.chat.id, "Setup Finished.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def run_bot(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    if sess:
        accs = {sess["tokens"][0]: {"current_stake": sess["initial_stake"], "total_profit": 0.0, "consecutive_losses": 0, "win_count": 0, "loss_count": 0, "active_contract": None, "target_check_time": None, "currency": "USD"}}
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
        bot.send_message(m.chat.id, "🚀 *Bot Started...*", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_bot(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}, "$unset": {"accounts_data": ""}})
    bot.send_message(m.chat.id, "🛑 *Bot Stopped.*", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
