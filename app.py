import websocket, json, time, os, threading, queue
import pandas as pd
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION (NEW TOKEN) ---
TOKEN = "8433565422:AAFjaVi_nW1KN1cYKAvumBze-fNKptljzso"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

# --- ANALYZE 45 TICKS (REVERSED SIGNALS) ---
def analyze_instant_history(prices):
    if len(prices) < 45: return None
    
    # Big Candle (First 30 ticks)
    big_open = prices[0]
    big_close = prices[29]
    big_is_up = big_close > big_open
    big_is_down = big_close < big_open
    
    # Small Candle (Last 15 ticks)
    small_open = prices[30]
    small_close = prices[44]
    small_is_up = small_close > small_open
    small_is_down = small_close < small_open
    
    # --- REVERSED LOGIC ---
    # CALL: Big Down + Small Up
    if big_is_down and small_is_up: return "CALL"
    # PUT: Big Up + Small Down
    if big_is_up and small_is_down: return "PUT"
    
    return None

# --- TRADING ENGINE ---
def trade_engine(chat_id):
    processed_minute = -1 
    
    while True: 
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        token = session.get('api_token')
        if not token: break

        def on_message(ws, message):
            nonlocal processed_minute
            data = json.loads(message)
            now = datetime.now()

            # Trigger History at second 30
            if now.second == 30 and now.minute != processed_minute:
                ws.send(json.dumps({
                    "ticks_history": "R_100",
                    "count": 45, "end": "latest", "style": "ticks"
                }))
                processed_minute = now.minute

            # Analyze & Trade
            if "history" in data:
                prices = data["history"]["prices"]
                signal = analyze_instant_history(prices)
                
                curr_session = active_sessions_col.find_one({"chat_id": chat_id})
                if signal and not curr_session.get("active_contract"):
                    barrier = "-0.5" if signal == "CALL" else "+0.5"
                    ws.send(json.dumps({
                        "proposal": 1, "amount": curr_session.get("current_stake"), "basis": "stake",
                        "contract_type": signal, "duration": 5, "duration_unit": "t",
                        "symbol": "R_100", "barrier": barrier, "currency": "USD"
                    }))

            # Execution logic
            if "proposal" in data:
                ws.send(json.dumps({"buy": data["proposal"]["id"], "price": float(data["proposal"]["ask_price"])}))

            if "buy" in data:
                active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
                    "active_contract": data["buy"]["contract_id"],
                    "check_time": (datetime.now() + timedelta(seconds=18)).isoformat()
                }})
                bot.send_message(chat_id, f"🚀 *Trade Executed:* {data['buy']['shortcode']}")

            if "proposal_open_contract" in data:
                if data["proposal_open_contract"].get("is_expired"):
                    process_result(chat_id, data)

            # Polling result
            curr_session = active_sessions_col.find_one({"chat_id": chat_id})
            if curr_session and curr_session.get("active_contract") and curr_session.get("check_time"):
                if datetime.now() >= datetime.fromisoformat(curr_session["check_time"]):
                    ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": curr_session["active_contract"]}))

        def on_open(ws):
            ws.send(json.dumps({"authorize": token}))

        ws = websocket.WebSocketApp("wss://blue.derivws.com/websockets/v3?app_id=16929", on_open=on_open, on_message=on_message)
        ws.run_forever(ping_interval=10, ping_timeout=5)
        time.sleep(1)

def process_result(chat_id, res):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session: return
    
    profit = float(res.get("proposal_open_contract", {}).get("profit", 0))
    total_p = session.get("total_profit", 0) + profit
    
    if profit > 0:
        new_stake, losses = session["initial_stake"], 0
        status = "WIN ✅"
    else:
        new_stake = float("{:.2f}".format(session["current_stake"] * 5))
        losses = session.get("consecutive_losses", 0) + 1
        status = "LOSS ❌"

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        "current_stake": new_stake, "consecutive_losses": losses, "total_profit": total_p,
        "active_contract": None, "check_time": None
    }})
    
    bot.send_message(chat_id, f"📊 *Result:* {status}\n*Profit:* {profit}\n*Net:* {total_p:.2f}\n*Next Stake:* {new_stake}")

    if total_p >= session.get("target_profit", 9999) or losses >= 3:
        reason = "TP Reached" if total_p >= session.get("target_profit", 9999) else "SL Hit (3 Losses)"
        active_sessions_col.delete_one({"chat_id": chat_id})
        bot.send_message(chat_id, f"🛑 *BOT STOPPED*\nReason: {reason}\n\nAll data cleared.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

# --- HTML ADMIN PANEL ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin Control</title><style>
body{font-family:'Segoe UI', sans-serif; background:#f4f7f6; padding:40px; text-align:center;}
.card{max-width:850px; margin:auto; background:white; padding:30px; border-radius:15px; box-shadow:0 10px 30px rgba(0,0,0,0.1);}
h2{color:#2c3e50;}
form{display:flex; gap:10px; justify-content:center; margin-bottom:30px;}
input, select{padding:12px; border:1px solid #ddd; border-radius:8px;}
button{padding:12px 25px; background:#3498db; color:white; border:none; border-radius:8px; cursor:pointer; font-weight:bold;}
table{width:100%; border-collapse:collapse;}
th, td{padding:15px; text-align:left; border-bottom:1px solid #eee;}
th{background:#f8f9fa;}
.del-btn{color:#e74c3c; font-weight:bold; text-decoration:none;}</style></head>
<body><div class="card">
<h2>👥 User Access Management</h2>
<form action="/add" method="POST">
<input type="email" name="email" placeholder="Email" required>
<select name="days">
    <option value="1">1 Day</option>
    <option value="30">30 Days</option>
    <option value="36500">Unlimited (Lifetime)</option>
</select>
<button type="submit">Grant Access</button></form>
<table><thead><tr><th>Email</th><th>Expiry Date</th><th>Action</th></tr></thead>
<tbody>{% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" class="del-btn">Delete</a></td></tr>{% endfor %}</tbody>
</table></div></body></html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_ADMIN, users=list(users_col.find()))

@app.route('/add', methods=['POST'])
def add_user():
    days = int(request.form.get('days'))
    exp = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower().strip()}, {"$set": {"expiry": exp}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete_user(email):
    users_col.delete_one({"email": email}); return redirect('/')

# --- TELEGRAM BOT HANDLERS ---
@bot.message_handler(commands=['start'])
@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def cmd_start(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "🤖 *System Initialized*\nPlease enter your Email:", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(m, auth)

def auth(m):
    user = users_col.find_one({"email": m.text.strip().lower()})
    if user and datetime.strptime(user['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Authorized. Enter Deriv API Token:")
        bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 Access Denied.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"api_token": m.text.strip()}}, upsert=True)
    bot.send_message(m.chat.id, "Enter Initial Stake ($):")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    stake = float(m.text)
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": stake, "current_stake": stake}})
    bot.send_message(m.chat.id, "Enter Target Profit (TP):")
    bot.register_next_step_handler(m, save_tp)

def save_tp(m):
    tp = float(m.text)
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": tp, "total_profit": 0, "consecutive_losses": 0, "is_running": True}})
    bot.send_message(m.chat.id, "🚀 *Bot Online (Reversed Signals)*\nAnalysis at :30", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
    threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_btn(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "🛑 Stopped & Data Cleared.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
