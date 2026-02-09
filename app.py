import websocket, json, time, os, threading, queue
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
# Updated with your new token
BOT_TOKEN = "8433565422:AAEgKMgk5H2cVzwKlsLU44GgtHaobogwHfo"
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

# --- TRADING ENGINE (DUAL DIGIT STRATEGY) ---
def trade_engine(chat_id):
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        token = session['tokens'][0]

        def on_message(ws, message):
            data = json.loads(message)
            
            if "tick" in data:
                # Extract Last Digit
                last_digit = int(str(data["tick"]["quote"])[-1])
                
                # Condition: Open trades when Last Digit is 4
                if last_digit == 4:
                    acc = session["accounts_data"][token]
                    currency = data.get("authorize", {}).get("currency", "USD")
                    
                    # Trade 1: DIGITOVER 5
                    ws.send(json.dumps({
                        "buy": "1", "price": acc["current_stake"],
                        "parameters": {
                            "amount": acc["current_stake"], "basis": "stake",
                            "contract_type": "DIGITOVER", "barrier": "5",
                            "duration": 5, "duration_unit": "t", "symbol": "R_100", "currency": currency
                        }
                    }))
                    
                    # Trade 2: DIGITUNDER 4
                    ws.send(json.dumps({
                        "buy": "1", "price": acc["current_stake"],
                        "parameters": {
                            "amount": acc["current_stake"], "basis": "stake",
                            "contract_type": "DIGITUNDER", "barrier": "4",
                            "duration": 1, "duration_unit": "t", "symbol": "R_100", "currency": currency
                        }
                    }))

            if "buy" in data:
                cid = data["buy"]["contract_id"]
                safe_send(chat_id, f"🚀 *Dual Trade Executed!* \nID: `{cid}`")
                # Wait 18s to request results
                threading.Timer(8, lambda: check_result_connection(chat_id, token, cid)).start()

        def on_open(ws):
            ws.send(json.dumps({"authorize": token}))
            ws.send(json.dumps({"ticks": "R_100", "subscribe": 1}))

        ws = websocket.WebSocketApp("wss://blue.derivws.com/websockets/v3?app_id=16929", 
                                    on_open=on_open, on_message=on_message)
        ws.run_forever()
        time.sleep(1)

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
        # Multiplier x5
        new_stake = float("{:.2f}".format(acc["current_stake"] * 5))
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
           f"⚠️ Streak: `{new_streak}/3` | Next: `{new_stake}`")
    safe_send(chat_id, msg)
    
    # Stop after 3 losses or hitting target
    if new_total >= session.get("target_profit", 10) or new_streak >= 3:
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
        reason = "Target reached! 🎯" if new_total >= session.get("target_profit", 10) else "Stop Loss (3 Consecutive Losses) ⚠️"
        safe_send(chat_id, f"🛑 *Session Terminated*\nReason: {reason}")

# --- ADMIN PANEL HTML ---
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
    <form action="/add" method="POST"><input name="email" placeholder="User Email" required><select name="days"><option value="1">1 Day</option><option value="30">30 Days</option><option value="36500">Life Time</option></select><button type="submit">Grant Access</button></form>
    <table><tr><th>User</th><th>Expiry</th><th>Action</th></tr>
    {% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}">Remove</a></td></tr>{% endfor %}
    </table>
</div></body></html>
"""

# --- FLASK & TELEGRAM HANDLERS ---
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
    bot.send_message(m.chat.id, "Initial Stake (Lot):")
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
        bot.send_message(m.chat.id, "🚀 *Bot Started (Multiplier x5)*", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_bot(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    bot.send_message(m.chat.id, "🛑 Stopped.", reply_markup=types.ReplyKeyboardRemove())
    cmd_start(m)

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
