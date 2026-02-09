import websocket, json, time, os, threading, queue
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
# Updated with your newest token
BOT_TOKEN = "8433565422:AAGTNmwMt2l1UCILXoPC-FT-CE7ODdRAJqM"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=100)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

msg_queue = queue.Queue()
trading_lock = {} # Prevents multiple overlapping trades

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

# --- TRADING ENGINE (1 TICK / 8s WAIT / LOCK ENABLED) ---
def trade_engine(chat_id):
    if chat_id not in trading_lock:
        trading_lock[chat_id] = False

    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"):
            break
            
        token = session['tokens'][0]

        def on_message(ws, message):
            data = json.loads(message)
            
            # 1. Ignore ticks if we are already in a trade or waiting for result
            if trading_lock.get(chat_id):
                return

            if "tick" in data:
                last_digit = int(str(data["tick"]["quote"])[-1])
                
                # 2. Entry condition: Last digit 4
                if last_digit == 4:
                    # Activate lock immediately
                    trading_lock[chat_id] = True
                    
                    # Get fresh stake from DB (in case of martingale)
                    current_sess = active_sessions_col.find_one({"chat_id": chat_id})
                    if not current_sess: return
                    
                    acc = current_sess["accounts_data"][token]
                    stake = acc["current_stake"]
                    
                    safe_send(chat_id, f"🎯 Last Digit 4! Executing dual trades with `${stake}`")
                    
                    # Execute Dual Trades (1 Tick duration)
                    for c_type, barrier in [("DIGITOVER", "5"), ("DIGITUNDER", "4")]:
                        ws.send(json.dumps({
                            "buy": "1", "price": stake,
                            "parameters": {
                                "amount": stake, "basis": "stake",
                                "contract_type": c_type, "barrier": barrier,
                                "duration": 1, "duration_unit": "t",
                                "symbol": "R_100", "currency": "USD"
                            }
                        }))
                    
                    # Start 8-second timer to fetch combined results
                    threading.Timer(8, lambda: check_combined_results(chat_id, token)).start()

        def on_open(ws):
            ws.send(json.dumps({"authorize": token}))
            ws.send(json.dumps({"ticks": "R_100", "subscribe": 1}))

        ws = websocket.WebSocketApp("wss://blue.derivws.com/websockets/v3?app_id=16929", 
                                    on_open=on_open, on_message=on_message)
        ws.run_forever()
        time.sleep(1)

def check_combined_results(chat_id, token):
    # Fetch the last 2 transactions to calculate group profit
    def on_message(ws, message):
        data = json.loads(message)
        if "authorize" in data:
            ws.send(json.dumps({"statement": 1, "description": 1, "limit": 2}))
        
        if "statement" in data:
            trades = data["statement"]["transactions"]
            if len(trades) >= 2:
                process_group_result(chat_id, token, trades[:2])
                ws.close()

    def on_open(ws): ws.send(json.dumps({"authorize": token}))
    ws = websocket.WebSocketApp("wss://blue.derivws.com/websockets/v3?app_id=16929", on_open=on_open, on_message=on_message)
    ws.run_forever()

def process_group_result(chat_id, token, trades):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session: 
        trading_lock[chat_id] = False
        return

    acc = session['accounts_data'].get(token)
    
    # Calculate combined net profit
    group_profit = sum(float(t.get("amount", 0)) for t in trades)
    is_win = group_profit > 0
    
    if is_win:
        new_stake = session["initial_stake"]
        new_streak = 0
        status = "✅ *GROUP WIN*"
    else:
        # Multiplier x5
        new_stake = round(acc["current_stake"] * 5, 2)
        new_streak = acc.get("consecutive_losses", 0) + 1
        status = "❌ *GROUP LOSS*"
        
    new_wins = acc.get("win_count", 0) + (1 if is_win else 0)
    new_losses = acc.get("loss_count", 0) + (1 if not is_win else 0)
    new_total_net = acc.get("total_profit", 0) + group_profit
    
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake, 
        f"accounts_data.{token}.consecutive_losses": new_streak, 
        f"accounts_data.{token}.total_profit": new_total_net, 
        f"accounts_data.{token}.win_count": new_wins, 
        f"accounts_data.{token}.loss_count": new_losses
    }})
    
    msg = (f"{status}\n💰 Net: `{group_profit:.2f}$` | Total: `{new_total_net:.2f}$`\n"
           f"🟢 Wins: `{new_wins}` | 🔴 Losses: `{new_losses}`\n"
           f"⚠️ Streak: `{new_streak}/3` | Next Stake: `{new_stake}$`")
    safe_send(chat_id, msg)
    
    # Release Lock to allow the next trade
    trading_lock[chat_id] = False

    if new_total_net >= session.get("target_profit", 10) or new_streak >= 3:
        active_sessions_col.delete_one({"chat_id": chat_id})
        safe_send(chat_id, "🛑 *Session Finished (Target or StopLoss reached).*")

# --- HTML ADMIN PANEL ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin Panel</title><style>
body{font-family:sans-serif; background:#0a0a0a; color:#eee; text-align:center; padding:40px;}
.box{max-width:700px; margin:auto; background:#151515; padding:30px; border-radius:12px; border:1px solid #222;}
input, select, button{padding:12px; margin:8px; border-radius:6px; border:1px solid #333; background:#111; color:#fff;}
button{background:#00ffa6; color:#000; font-weight:bold; border:none; cursor:pointer;}
table{width:100%; border-collapse:collapse; margin-top:20px;}
th, td{padding:12px; border-bottom:1px solid #222; text-align:left;}
a{color:#ff5555; text-decoration:none; font-size:14px;}
</style></head>
<body><div class="box">
    <h2>Access Management</h2>
    <form action="/add" method="POST"><input name="email" placeholder="Email" required><select name="days"><option value="1">1 Day</option><option value="30">30 Days</option><option value="36500">Life Time</option></select><button type="submit">Grant Access</button></form>
    <table><tr><th>User</th><th>Expiry</th><th>Action</th></tr>
    {% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}">Remove</a></td></tr>{% endfor %}
    </table>
</div></body></html>
"""

@app.route('/')
def index(): return render_template_string(HTML_ADMIN, users=list(users_col.find()))

@app.route('/add', methods=['POST'])
def add_user():
    exp = (datetime.now() + timedelta(days=int(request.form.get('days')))).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower().strip()}, {"$set": {"expiry": exp}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete_user(email): users_col.delete_one({"email": email}); return redirect('/')

@bot.message_handler(commands=['start'])
def cmd_start(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "📧 Enter registered Email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Authorized. Enter Deriv Token:")
        bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 No active subscription.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False}}, upsert=True)
    bot.send_message(m.chat.id, "Initial Stake (per trade):")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text)}})
    bot.send_message(m.chat.id, "Target Profit ($):")
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
        bot.send_message(m.chat.id, "🚀 *Bot Started (1 Tick / x5)*", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_bot(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    trading_lock[m.chat.id] = False
    bot.send_message(m.chat.id, "🛑 Stopped & Reset.", reply_markup=types.ReplyKeyboardRemove())
    time.sleep(1)
    cmd_start(m)

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
