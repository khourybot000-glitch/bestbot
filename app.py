import websocket, json, time, os, threading, queue
import pandas as pd
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION (UPDATED TOKEN) ---
TOKEN = "8433565422:AAFDSc9FiooqxcN2_W-e9MzHnOm6gspV1BU"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

# --- CUSTOM CANDLE LOGIC (30 Ticks Big / 15 Ticks Small) ---
def analyze_custom_candles(ticks):
    if len(ticks) < 45: return None
    
    big_candle_open = ticks[0]
    big_candle_close = ticks[29]
    big_is_up = big_candle_close > big_candle_open
    big_is_down = big_candle_close < big_candle_open
    
    small_candle_open = ticks[30]
    small_candle_close = ticks[44]
    small_is_up = small_candle_close > small_candle_open
    small_is_down = small_candle_close < small_candle_open
    
    if big_is_up and small_is_down: return "CALL"
    if big_is_down and small_is_up: return "PUT"
    return None

# --- ENGINE ---
def trade_engine(chat_id):
    analysis_started = False
    
    while True: 
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        token = session.get('api_token')
        if not token: break
        
        prices_list = []

        def on_message(ws, message):
            nonlocal prices_list, analysis_started
            data = json.loads(message)
            curr_time = datetime.now()
            
            if "tick" in data:
                price = float(data["tick"]["quote"])
                
                if curr_time.second == 30 and not analysis_started:
                    prices_list = []
                    analysis_started = True
                
                if analysis_started:
                    prices_list.append(price)
                    
                    if len(prices_list) == 45:
                        analysis_started = False
                        signal = analyze_custom_candles(prices_list)
                        
                        curr_session = active_sessions_col.find_one({"chat_id": chat_id})
                        if signal and not curr_session.get("active_contract"):
                            barrier = "-0.5" if signal == "CALL" else "+0.5"
                            stake = curr_session.get("current_stake")
                            
                            ws.send(json.dumps({
                                "proposal": 1, "amount": stake, "basis": "stake",
                                "contract_type": signal, "duration": 5, "duration_unit": "t",
                                "symbol": "R_100", "barrier": barrier, "currency": "USD"
                            }))

                curr_session = active_sessions_col.find_one({"chat_id": chat_id})
                if curr_session and curr_session.get("active_contract") and curr_session.get("check_time"):
                    if datetime.now() >= datetime.fromisoformat(curr_session["check_time"]):
                        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": curr_session["active_contract"]}))

            if "proposal" in data:
                ws.send(json.dumps({"buy": data["proposal"]["id"], "price": float(data["proposal"]["ask_price"])}))

            if "buy" in data:
                target_time = (datetime.now() + timedelta(seconds=18)).isoformat()
                active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
                    "active_contract": data["buy"]["contract_id"],
                    "check_time": target_time
                }})
                bot.send_message(chat_id, f"🚀 *Trade Entered:* {data['buy']['shortcode']}")

            if "proposal_open_contract" in data:
                contract = data["proposal_open_contract"]
                if contract.get("is_expired"):
                    process_result(chat_id, data)

        def on_open(ws):
            ws.send(json.dumps({"authorize": token}))
            ws.send(json.dumps({"ticks": "R_100", "subscribe": 1}))

        ws = websocket.WebSocketApp("wss://blue.derivws.com/websockets/v3?app_id=16929", on_open=on_open, on_message=on_message)
        ws.run_forever(ping_interval=10, ping_timeout=5)
        time.sleep(1)

def process_result(chat_id, res):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session: return
    
    profit = float(res.get("proposal_open_contract", {}).get("profit", 0))
    total_p = session.get("total_profit", 0) + profit
    
    if profit > 0:
        new_stake = session["initial_stake"]
        losses = 0
        status = "WIN ✅"
    else:
        new_stake = float("{:.2f}".format(session["current_stake"] * 5)) # 5x Martingale
        losses = session.get("consecutive_losses", 0) + 1
        status = "LOSS ❌"

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        "current_stake": new_stake, 
        "consecutive_losses": losses,
        "total_profit": total_p,
        "active_contract": None,
        "check_time": None
    }})
    
    bot.send_message(chat_id, f"📊 *Result:* {status}\n*Profit:* {profit}\n*Net:* {total_p}\n*Next Stake:* {new_stake}")

    if total_p >= session.get("target_profit", 99999) or losses >= 3:
        reason = "Target Profit Reached!" if total_p >= session.get("target_profit", 99999) else "Stop Loss Hit (3 Losses)!"
        stop_and_clear(chat_id, reason)

def stop_and_clear(chat_id, reason):
    active_sessions_col.delete_one({"chat_id": chat_id})
    bot.send_message(chat_id, f"🛑 *BOT STOPPED*\nReason: {reason}\n\n*All session data cleared. Please START again.*", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

# --- HTML ADMIN PANEL (WITH DAYS OPTIONS) ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin Control Panel</title><style>
body{font-family:'Segoe UI', sans-serif; background:#f0f2f5; padding:40px; text-align:center;}
.container{max-width:850px; margin:auto; background:white; padding:30px; border-radius:15px; box-shadow:0 8px 20px rgba(0,0,0,0.05);}
h2{color:#1a73e8;}
form{display:flex; gap:10px; justify-content:center; margin-bottom:30px;}
input, select{padding:12px; border:1px solid #ddd; border-radius:8px;}
button{padding:12px 25px; background:#1a73e8; color:white; border:none; border-radius:8px; cursor:pointer; font-weight:bold;}
table{width:100%; border-collapse:collapse;}
th, td{padding:15px; text-align:left; border-bottom:1px solid #eee;}
th{background:#f8f9fa;}
.del-btn{color:#d93025; font-weight:bold; text-decoration:none;}</style></head>
<body><div class="container">
<h2>👥 User Access Management</h2>
<form action="/add" method="POST">
<input type="email" name="email" placeholder="User Email" required>
<select name="days">
    <option value="1">1 Day</option>
    <option value="30">30 Days</option>
    <option value="36500">Unlimited (Lifetime)</option>
</select>
<button type="submit">Grant Access</button></form>
<table><thead><tr><th>Email</th><th>Expiry Date</th><th>Action</th></tr></thead>
<tbody>{% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" class="del-btn">Remove</a></td></tr>{% endfor %}</tbody>
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

# --- TELEGRAM BOT HANDLERS (ENGLISH) ---
@bot.message_handler(commands=['start'])
@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def cmd_start(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "🤖 *System Ready*\nPlease enter your Email:", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(m, auth)

def auth(m):
    user = users_col.find_one({"email": m.text.strip().lower()})
    if user and datetime.strptime(user['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Authorized. Enter *Deriv API Token*:")
        bot.register_next_step_handler(m, save_token)
    else:
        bot.send_message(m.chat.id, "🚫 Access Denied or Expired.")

def save_token(m):
    token = m.text.strip()
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"api_token": token}}, upsert=True)
    bot.send_message(m.chat.id, "Enter Initial Stake ($):")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    try:
        stake = float(m.text)
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": stake, "current_stake": stake}})
        bot.send_message(m.chat.id, "Enter Target Profit (TP):")
        bot.register_next_step_handler(m, save_tp)
    except: bot.send_message(m.chat.id, "Invalid number. Try again:")

def save_tp(m):
    try:
        tp = float(m.text)
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": tp, "total_profit": 0, "consecutive_losses": 0, "is_running": True}})
        bot.send_message(m.chat.id, "🚀 *Bot Started!*\nWaiting for second :30 to analyze...", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()
    except: bot.send_message(m.chat.id, "Invalid number. Try again:")

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_btn(m):
    stop_and_clear(m.chat.id, "User requested stop.")

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
