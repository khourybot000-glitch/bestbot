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
TOKEN = "8433565422:AAHNYNQyYq5p3WgaQZxcamGXrqZjLGRZyLQ"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN, threaded=True, num_threads=100)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

msg_queue = queue.Queue()

# --- MESSAGE WORKER ---
def message_worker():
    while True:
        try:
            item = msg_queue.get()
            bot.send_message(item[0], item[1], parse_mode="Markdown", reply_markup=item[2] if len(item) > 2 else None)
            msg_queue.task_done()
            time.sleep(0.05) 
        except: pass

threading.Thread(target=message_worker, daemon=True).start()

def safe_send(chat_id, text, markup=None):
    msg_queue.put((chat_id, text, markup))

# --- STRATEGY: 15 INDICATORS (70% THRESHOLD) ---
def analyze_market(prices):
    if len(prices) < 200: return "NEUTRAL"
    df = pd.DataFrame(prices, columns=['close'])
    c = df['close']
    
    votes = []
    # 1-5: Trends
    votes.append(1 if c.iloc[-1] > ta.sma(c, length=10).iloc[-1] else -1)
    votes.append(1 if c.iloc[-1] > ta.ema(c, length=20).iloc[-1] else -1)
    votes.append(1 if ta.ema(c, length=10).iloc[-1] > ta.ema(c, length=30).iloc[-1] else -1)
    votes.append(1 if c.iloc[-1] > c.rolling(50).mean().iloc[-1] else -1)
    votes.append(1 if ta.sma(c, length=5).iloc[-1] > ta.sma(c, length=15).iloc[-1] else -1)

    # 6-10: Oscillators
    rsi = ta.rsi(c, length=14).iloc[-1]
    votes.append(1 if rsi > 50 else -1)
    macd = ta.macd(c).iloc[-1]
    votes.append(1 if macd['MACD_12_26_9'] > macd['MACDs_12_26_9'] else -1)
    votes.append(1 if ta.cci(c, length=14).iloc[-1] > 0 else -1)
    stoch = ta.stoch(c, c, c).iloc[-1]
    votes.append(1 if stoch['STOCHk_14_3_3'] > 50 else -1)
    votes.append(1 if ta.willr(c, c, c).iloc[-1] > -50 else -1)

    # 11-15: Volatility & PA
    bb = ta.bbands(c, length=20)
    votes.append(1 if c.iloc[-1] > bb['BBM_20_2.0'].iloc[-1] else -1)
    votes.append(1 if c.iloc[-1] > c.iloc[-5] else -1)
    votes.append(1 if c.iloc[-1] > c.iloc[-10] else -1)
    votes.append(1 if c.diff().iloc[-1] > 0 else -1)
    votes.append(1 if (c.iloc[-1] - c.iloc[0]) > 0 else -1)

    if votes.count(1) >= 11: return "CALL"
    if votes.count(-1) >= 11: return "PUT"
    return "NEUTRAL"

# --- TRADING ENGINE ---
def trade_engine(chat_id):
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        token = session['tokens'][0]
        prices_list = []
        waiting_for_rebound = False
        acc_currency = "USD"

        def on_message(ws, message):
            nonlocal prices_list, waiting_for_rebound, acc_currency
            data = json.loads(message)
            
            if "authorize" in data:
                acc_currency = data["authorize"].get("currency", "USD")
                active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {f"accounts_data.{token}.currency": acc_currency}})

            if "tick" in data:
                prices_list.append(float(data["tick"]["quote"]))
                if len(prices_list) > 1000: prices_list.pop(0)
                
                now = datetime.now()
                if now.second == 54 and not waiting_for_rebound:
                    signal = analyze_market(prices_list)
                    if signal != "NEUTRAL":
                        waiting_for_rebound = True
                        last_30_ticks = prices_list[-30:]
                        safe_send(chat_id, f"🔍 *Signal:* `{signal}` (70%+)\nWaiting 60s for Rebound check...")
                        threading.Timer(60, lambda: execute_reverse_trade(ws, chat_id, token, signal, last_30_ticks)).start()

            if "proposal_open_contract" in data:
                if data["proposal_open_contract"].get("is_sold"):
                    process_result(chat_id, token, data)

        def execute_reverse_trade(ws, chat_id, token, signal, old_ticks):
            nonlocal waiting_for_rebound
            curr_sess = active_sessions_col.find_one({"chat_id": chat_id})
            if not curr_sess or not curr_sess.get("is_running"): return
            
            trend = prices_list[-1] - old_ticks[0]
            is_rebound = (signal == "CALL" and trend < 0) or (signal == "PUT" and trend > 0)
            
            if is_rebound:
                rev_type = "PUT" if signal == "CALL" else "CALL"
                acc = curr_sess["accounts_data"][token]
                payload = {
                    "buy": "1", "price": acc["current_stake"],
                    "parameters": {
                        "amount": acc["current_stake"], "basis": "stake",
                        "contract_type": rev_type, "duration": 1, "duration_unit": "m",
                        "symbol": "R_100", "currency": acc_currency
                    }
                }
                ws.send(json.dumps(payload))
                safe_send(chat_id, f"⚡ *Rebound Confirmed!* Entering: `{rev_type}`")
            else:
                safe_send(chat_id, "⏭️ *No Rebound.* Signal cancelled.")
            waiting_for_rebound = False

        def on_open(ws):
            ws.send(json.dumps({"authorize": token}))
            ws.send(json.dumps({"ticks": "R_100", "subscribe": 1}))

        ws = websocket.WebSocketApp("wss://blue.derivws.com/websockets/v3?app_id=16929", on_open=on_open, on_message=on_message)
        ws.run_forever(ping_interval=10, ping_timeout=5)
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
        new_stake, new_streak, status = session["initial_stake"], 0, "✅ *PROFIT*"
    else:
        new_stake = float("{:.2f}".format(acc["current_stake"] * 2.2))
        new_streak = acc.get("consecutive_losses", 0) + 1
        status = "❌ *LOSS*"

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake, f"accounts_data.{token}.consecutive_losses": new_streak, 
        f"accounts_data.{token}.total_profit": new_total, f"accounts_data.{token}.win_count": new_wins, f"accounts_data.{token}.loss_count": new_losses
    }})
    
    safe_send(chat_id, f"{status}\n💰 Net: `{new_total:.2f}$` | Wins: `{new_wins}` | Loss: `{new_losses}`\n⚠️ Streak: `{new_streak}/4` | Next: `{new_stake}$`")

    if new_total >= session.get("target_profit", 10) or new_streak >= 4:
        active_sessions_col.delete_one({"chat_id": chat_id})
        safe_send(chat_id, "🛑 *Session Reset.* Target reached or 4 losses hit.")

# --- HTML ADMIN PANEL (UPDATED OPTIONS) ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin Panel</title><style>
body{font-family:sans-serif; background:#0f0f0f; color:#fff; padding:40px; text-align:center;}
.card{max-width:800px; margin:auto; background:#181818; padding:30px; border-radius:15px; border:1px solid #333;}
input, select, button{padding:12px; margin:5px; border-radius:8px; border:1px solid #444; background:#222; color:#fff;}
button{background:#00ffa6; color:#000; font-weight:bold; cursor:pointer; border:none;}
table{width:100%; margin-top:20px; border-collapse:collapse;}
th, td{padding:15px; text-align:left; border-bottom:1px solid #333;}
a{color:#ff4444; text-decoration:none; font-weight:bold;}
</style></head>
<body><div class="card">
    <h2>Access Control Manager</h2>
    <form action="/add" method="POST">
        <input type="email" name="email" placeholder="User Email" required>
        <select name="days">
            <option value="1">1 Day</option>
            <option value="30">30 Days</option>
            <option value="36500">Life Time</option>
        </select>
        <button type="submit">Add User</button>
    </form>
    <table><tr><th>Email</th><th>Expiry Date</th><th>Action</th></tr>
    {% for u in users %}
    <tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}">Remove</a></td></tr>
    {% endfor %}
    </table>
</div></body></html>
"""

@app.route('/')
def index():
    users = list(users_col.find())
    return render_template_string(HTML_ADMIN, users=users)

@app.route('/add', methods=['POST'])
def add_user():
    exp = (datetime.now() + timedelta(days=int(request.form.get('days')))).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower().strip()}, {"$set": {"expiry": exp}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete_user(email):
    users_col.delete_one({"email": email}); return redirect('/')

# --- TELEGRAM HANDLERS ---
@bot.message_handler(commands=['start'])
def cmd_start(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "📧 Enter registered Email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ *Authorized.* Enter Deriv Token:")
        bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 *Denied.* Contact admin.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False}}, upsert=True)
    bot.send_message(m.chat.id, "Stake:")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text)}})
    bot.send_message(m.chat.id, "Target Profit:")
    bot.register_next_step_handler(m, save_tp)

def save_tp(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": float(m.text)}})
    bot.send_message(m.chat.id, "Ready!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def run_bot(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    if sess:
        accs = {sess["tokens"][0]: {"current_stake": sess["initial_stake"], "total_profit": 0.0, "consecutive_losses": 0, "win_count": 0, "loss_count": 0}}
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
        bot.send_message(m.chat.id, "🚀 *Bot Started*", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_bot(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "🛑 *Bot Reset.*", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
