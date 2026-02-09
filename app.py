import websocket, json, time, os, threading, queue
import numpy as np
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
BOT_TOKEN = "8433565422:AAH4JvxP1WGGVWBP9INsnnboKYOe6jaANE8"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=100)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

msg_queue = queue.Queue()

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

# --- STRATEGY LOGIC: 30 TICKS (15 + 15) ---
def analyze_rebound_strategy(ticks):
    if len(ticks) < 30: return {"signal": "NEUTRAL", "barrier": 0}
    segment1 = ticks[-30:-15]
    segment2 = ticks[-15:]
    c1_up = segment1[-1] > segment1[0]
    c1_down = segment1[-1] < segment1[0]
    c2_up = segment2[-1] > segment2[0]
    c2_down = segment2[-1] < segment2[0]
    
    if c1_down and c2_up: return {"signal": "PUT", "barrier": +1}
    if c1_up and c2_down: return {"signal": "CALL", "barrier": -1}
    return {"signal": "NEUTRAL", "barrier": 0}

# --- TRADING ENGINE (On-Demand Connection) ---
def trade_engine(chat_id):
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        if datetime.now().second == 56:
            token = session['tokens'][0]
            
            def on_message(ws, message):
                data = json.loads(message)
                if "authorize" in data:
                    ws.send(json.dumps({"ticks_history": "R_100", "end": "latest", "count": 30, "style": "ticks"}))
                
                if "history" in data:
                    ticks = data["history"].get("prices", [])
                    res = analyze_rebound_strategy(ticks)
                    if res["signal"] != "NEUTRAL":
                        acc = session["accounts_data"][token]
                        currency = data.get("authorize", {}).get("currency", "USD")
                        barrier = f"+{res['barrier']}" if res['barrier'] > 0 else f"{res['barrier']}"
                        
                        ws.send(json.dumps({
                            "buy": "1", "price": acc["current_stake"],
                            "parameters": {
                                "amount": acc["current_stake"], "basis": "stake",
                                "contract_type": res["signal"], "duration": 5,
                                "duration_unit": "t", "symbol": "R_100",
                                "barrier": barrier, "currency": currency
                            }
                        }))
                    else: ws.close()

                if "buy" in data:
                    cid = data["buy"]["contract_id"]
                    safe_send(chat_id, f"🚀 *Order Placed!* \nID: `{cid}`")
                    ws.close()
                    threading.Timer(18, lambda: check_result_connection(chat_id, token, cid)).start()

            def on_open(ws):
                ws.send(json.dumps({"authorize": token}))

            ws = websocket.WebSocketApp("wss://blue.derivws.com/websockets/v3?app_id=16929", 
                                        on_open=on_open, on_message=on_message)
            ws.run_forever()
        time.sleep(0.5)

def check_result_connection(chat_id, token, contract_id):
    def on_message(ws, message):
        data = json.loads(message)
        if "authorize" in data:
            ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
        if "proposal_open_contract" in data:
            poc = data["proposal_open_contract"]
            if poc.get("is_sold"):
                process_result(chat_id, token, data)
                ws.close()
    def on_open(ws): ws.send(json.dumps({"authorize": token}))
    ws = websocket.WebSocketApp("wss://blue.derivws.com/websockets/v3?app_id=16929", on_open=on_open, on_message=on_message)
    ws.run_forever()

def process_result(chat_id, token, res):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    acc = session['accounts_data'].get(token)
    poc = res.get("proposal_open_contract", {})
    profit = float(poc.get("profit", 0))
    
    is_win = profit > 0
    new_wins = acc.get("win_count", 0) + (1 if is_win else 0)
    new_losses = acc.get("loss_count", 0) + (1 if not is_win else 0)
    new_total = acc.get("total_profit", 0) + profit
    
    if is_win:
        new_stake = session["initial_stake"]
        new_streak = 0
        status = "✅ *WIN*"
    else:
        new_stake = float("{:.2f}".format(acc["current_stake"] * 24))
        new_streak = acc.get("consecutive_losses", 0) + 1
        status = "❌ *LOSS*"
        
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake, 
        f"accounts_data.{token}.consecutive_losses": new_streak, 
        f"accounts_data.{token}.total_profit": new_total, 
        f"accounts_data.{token}.win_count": new_wins, 
        f"accounts_data.{token}.loss_count": new_losses
    }})
    
    msg = (f"{status}\n💰 Profit: `{profit:.2f}$` | Net: `{new_total:.2f}$`\n"
           f"🟢 Wins: `{new_wins}` | 🔴 Losses: `{new_losses}`\n"
           f"⚠️ Streak: `{new_streak}/2` | Next Stake: `{new_stake}`")
    safe_send(chat_id, msg)
    
    # Updated to stop after 2 consecutive losses
    if new_total >= session.get("target_profit", 10) or new_streak >= 2:
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
        reason = "Target reached! 🎯" if new_total >= session.get("target_profit", 10) else "Stop Loss: 2 Consecutive Losses ⚠️"
        safe_send(chat_id, f"🛑 *Session Terminated*\nReason: {reason}")

# --- ADMIN PANEL & FLASK ---
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
    <form action="/add" method="POST"><input name="email" placeholder="Email Address" required><select name="days"><option value="1">1 Day</option><option value="30">30 Days</option><option value="36500">Life Time</option></select><button type="submit">Grant Access</button></form>
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
    bot.send_message(m.chat.id, "📧 Enter your registered Email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Authorized. Enter your Deriv Token:")
        bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 No active subscription found.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False}}, upsert=True)
    bot.send_message(m.chat.id, "Initial Stake (Lot):")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text)}})
    bot.send_message(m.chat.id, "Target Profit ($):")
    bot.register_next_step_handler(m, save_tp)

def save_tp(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": float(m.text)}})
    bot.send_message(m.chat.id, "Configuration Ready!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def run_bot(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    if sess:
        accs = {sess["tokens"][0]: {"current_stake": sess["initial_stake"], "total_profit": 0.0, "consecutive_losses": 0, "win_count": 0, "loss_count": 0}}
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
        bot.send_message(m.chat.id, "🚀 *Bot is now running (Strategy: 30 Ticks)*", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_bot(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    bot.send_message(m.chat.id, "🛑 Session Stopped.", reply_markup=types.ReplyKeyboardRemove())
    cmd_start(m)

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
