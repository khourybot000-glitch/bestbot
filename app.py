import websocket, json, time, os, threading, queue
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
# تم تحديث التوكن الجديد بناءً على طلبك
TOKEN = "8433565422:AAE-r4dg0C2hM_SzTWatNwExp8d9QGA55Bk"
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

# دالة حساب المتوسط الأسي EMA
def calculate_ema(prices, period):
    if len(prices) < period: return None
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price * k) + (ema * (1 - k))
    return ema

# --- STREAM ENGINE: REAL-TIME TICKS ---
def trade_engine(chat_id):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session: return
    
    token = session['tokens'][0]
    prices_list = []
    prev_ema10 = None
    prev_ema30 = None

    def on_message(ws, message):
        nonlocal prices_list, prev_ema10, prev_ema30
        data = json.loads(message)
        
        curr_session = active_sessions_col.find_one({"chat_id": chat_id})
        if not curr_session or not curr_session.get("is_running"):
            ws.close()
            return

        # استقبال التيكات الحية
        if "tick" in data:
            price = float(data["tick"]["quote"])
            prices_list.append(price)
            if len(prices_list) > 60: prices_list.pop(0)

            if len(prices_list) >= 30:
                current_ema10 = calculate_ema(prices_list, 10)
                current_ema30 = calculate_ema(prices_list, 30)

                if prev_ema10 is not None and prev_ema30 is not None:
                    # فحص الصفقات المفتوحة
                    for t, acc in curr_session.get("accounts_data", {}).items():
                        if acc.get("active_contract") and acc.get("target_check_time"):
                            if datetime.now() >= datetime.fromisoformat(acc["target_check_time"]):
                                ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": acc["active_contract"]}))

                    # تنفيذ الصفقات بناءً على التقاطع
                    if not any(acc.get("active_contract") for acc in curr_session.get("accounts_data", {}).values()):
                        if prev_ema10 <= prev_ema30 and current_ema10 > current_ema30:
                            open_trade(chat_id, curr_session, "CALL", "-0.8")
                        elif prev_ema10 >= prev_ema30 and current_ema10 < current_ema30:
                            open_trade(chat_id, curr_session, "PUT", "+0.8")

                prev_ema10 = current_ema10
                prev_ema30 = current_ema30

        if "proposal_open_contract" in data:
            contract = data["proposal_open_contract"]
            if contract.get("is_expired"):
                process_result(chat_id, token, data)

    def on_open(ws):
        ws.send(json.dumps({"authorize": token}))
        ws.send(json.dumps({"ticks_history": "R_100", "count": 50, "end": "latest", "style": "ticks"}))
        ws.send(json.dumps({"ticks": "R_100", "subscribe": 1}))

    ws = websocket.WebSocketApp("wss://blue.derivws.com/websockets/v3?app_id=16929", on_open=on_open, on_message=on_message)
    ws.run_forever()

def open_trade(chat_id, session, side, barrier):
    target_time = (datetime.now() + timedelta(seconds=18)).isoformat()
    for t in session['tokens']:
        acc = session['accounts_data'].get(t)
        if acc:
            ws_temp = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
            ws_temp.send(json.dumps({"authorize": t}))
            json.loads(ws_temp.recv())
            payload = {"proposal": 1, "amount": acc["current_stake"], "basis": "stake", "contract_type": side, "duration": 5, "duration_unit": "t", "symbol": "R_100", "barrier": barrier, "currency": acc["currency"]}
            ws_temp.send(json.dumps(payload))
            prop = json.loads(ws_temp.recv())
            if "proposal" in prop:
                ws_temp.send(json.dumps({"buy": prop["proposal"]["id"], "price": acc["current_stake"]}))
                buy_res = json.loads(ws_temp.recv())
                if "buy" in buy_res:
                    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {f"accounts_data.{t}.active_contract": buy_res["buy"]["contract_id"], f"accounts_data.{t}.target_check_time": target_time}})
                    safe_send(chat_id, f"⚡ *EMA Entry:* {side} (B: {barrier})")
            ws_temp.close()

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
        new_stake = float("{:.2f}".format(acc["current_stake"] * 19)) 
        new_streak = acc.get("consecutive_losses", 0) + 1
        status = "❌ *LOSS*"

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {f"accounts_data.{token}.current_stake": new_stake, f"accounts_data.{token}.consecutive_losses": new_streak, f"accounts_data.{token}.total_profit": new_total, f"accounts_data.{token}.win_count": new_wins, f"accounts_data.{token}.loss_count": new_losses, f"accounts_data.{token}.active_contract": None, f"accounts_data.{token}.target_check_time": None}})
    
    stats_msg = f"📊 *Result:* {status}\nW: `{new_wins}` | L: `{new_losses}`\nNet: `{new_total:.2f}`\nNext: `{new_stake}`"

    if new_total >= session.get("target_profit", 999999) or new_streak >= 2:
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}, "$unset": {"accounts_data": ""}})
        msg = "🎯 *Target Reached!*" if new_total >= session.get("target_profit", 999999) else "🛑 *Stop Loss (2 Losses).*"
        safe_send(chat_id, stats_msg + f"\n\n{msg}\n*Data Reset.*", types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))
    else:
        safe_send(chat_id, stats_msg)

# --- HTML ADMIN PANEL ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin Control</title><style>
body{font-family:sans-serif; background:#f4f7f6; padding:30px; text-align:center;}
.card{max-width:800px; margin:auto; background:white; padding:30px; border-radius:12px; box-shadow:0 4px 10px rgba(0,0,0,0.1);}
input, select{padding:12px; margin:5px; border-radius:6px; border:1px solid #ddd;}
button{padding:12px 20px; background:#3498db; color:white; border:none; border-radius:6px; cursor:pointer; font-weight:bold;}
table{width:100%; border-collapse:collapse; margin-top:20px;}
th, td{padding:15px; border-bottom:1px solid #eee; text-align:left;}
.del{color:#e74c3c; font-weight:bold; text-decoration:none;}</style></head>
<body><div class="card"><h2>👥 User Access Management</h2>
<form action="/add" method="POST">
<input type="email" name="email" placeholder="User Email" required>
<select name="days">
<option value="1">1 Day</option><option value="30">30 Days</option><option value="36500">36500 Days</option>
</select><button type="submit">Grant Access</button></form>
<table><thead><tr><th>Email</th><th>Expiry</th><th>Action</th></tr></thead>
<tbody>{% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" class="del">Remove</a></td></tr>{% endfor %}</tbody>
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

# --- TELEGRAM HANDLERS ---
@bot.message_handler(commands=['start'])
def cmd_start(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "🤖 *System Interface*\nPlease enter Email:", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Access Granted. Enter Token:")
        bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 Denied.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False}}, upsert=True)
    bot.send_message(m.chat.id, "Stake Amount:")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    try:
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text)}})
        bot.send_message(m.chat.id, "Target Profit (TP):")
        bot.register_next_step_handler(m, save_tp)
    except: bot.register_next_step_handler(m, save_stake)

def save_tp(m):
    try:
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": float(m.text)}})
        bot.send_message(m.chat.id, "Setup Ready.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))
    except: bot.register_next_step_handler(m, save_tp)

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def run_bot(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    if sess and not sess.get("is_running"):
        accs = {}
        for t in sess["tokens"]:
            accs[t] = {"current_stake": sess["initial_stake"], "total_profit": 0.0, "consecutive_losses": 0, "win_count": 0, "loss_count": 0, "active_contract": None, "target_check_time": None, "currency": "USD"}
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
        bot.send_message(m.chat.id, "🚀 *Bot Working (Real-time EMA)...*", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_bot(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}, "$unset": {"accounts_data": ""}})
    bot.send_message(m.chat.id, "🛑 *Stopped & Data Cleared.*", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
