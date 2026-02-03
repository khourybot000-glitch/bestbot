import websocket, json, time, os, threading, queue, collections
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
# التوكن الجديد الذي أرسلته
TOKEN = "8433565422:AAFesIsT8U1Cqbw_z_eN4kw5gCfvQEdt8N8"
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
            chat_id, text = item[0], item[1]
            markup = item[2] if len(item) > 2 else None
            bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)
            msg_queue.task_done()
            time.sleep(0.05) 
        except: pass

threading.Thread(target=message_worker, daemon=True).start()

def safe_send(chat_id, text, markup=None):
    msg_queue.put((chat_id, text, markup))

def quick_request(api_token, request_data):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=12)
        ws.send(json.dumps({"authorize": api_token}))
        res_auth = json.loads(ws.recv())
        if "authorize" in res_auth:
            ws.send(json.dumps(request_data))
            res = json.loads(ws.recv())
            ws.close()
            return res
        ws.close()
    except: pass
    return None

def execute_trade(api_token, buy_req, currency):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=12)
        ws.send(json.dumps({"authorize": api_token}))
        auth_res = json.loads(ws.recv())
        if "authorize" in auth_res:
            curr = auth_res["authorize"].get("currency", currency)
            buy_req["currency"] = curr
            ws.send(json.dumps({"proposal": 1, **buy_req}))
            prop_res = json.loads(ws.recv())
            if "proposal" in prop_res:
                ws.send(json.dumps({"buy": prop_res["proposal"]["id"], "price": buy_req['amount']}))
                res = json.loads(ws.recv())
                ws.close()
                return res
        ws.close()
    except: pass
    return None

# --- ENGINE: FREQUENCY & INSTANT RECOVERY ---
def trade_engine(chat_id):
    last_processed_sec = -1
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        try:
            now = datetime.now()
            # فحص النتائج المستمر
            for token, acc in session.get("accounts_data", {}).items():
                if acc.get("active_contract") and acc.get("target_check_time"):
                    if now >= datetime.fromisoformat(acc["target_check_time"]):
                        res_res = quick_request(token, {"proposal_open_contract": 1, "contract_id": acc["active_contract"]})
                        if res_res and res_res.get("proposal_open_contract", {}).get("is_expired"):
                            process_result(chat_id, token, res_res)

            # التحليل عند الثواني 0, 10, 20, 30, 40, 50
            if now.second in [0, 10, 20, 30, 40, 50] and now.second != last_processed_sec:
                last_processed_sec = now.second
                if any(acc.get("active_contract") for acc in session.get("accounts_data", {}).values()): continue

                res = quick_request(session['tokens'][0], {"ticks_history": "R_100", "count": 5, "end": "latest", "style": "ticks"})
                if res and "history" in res:
                    prices = res["history"]["prices"]
                    digits = ["{:.2f}".format(p).split('.')[1][1] for p in prices]
                    counts = collections.Counter(digits)
                    most_common_digit, count = counts.most_common(1)[0]
                    
                    if count >= 4:
                        open_digit_trade(chat_id, session, most_common_digit)

            time.sleep(0.1)
        except: time.sleep(1)

def open_digit_trade(chat_id, session, target_digit):
    target_time = (datetime.now() + timedelta(seconds=8)).isoformat()
    for t in session['tokens']:
        acc = session['accounts_data'].get(t)
        if acc:
            buy_res = execute_trade(t, {
                "amount": acc["current_stake"],
                "basis": "stake",
                "contract_type": "DIGITDIFF",
                "duration": 1,
                "duration_unit": "t",
                "symbol": "R_100",
                "barrier": target_digit
            }, acc["currency"])
            
            if buy_res and "buy" in buy_res:
                active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
                    f"accounts_data.{t}.active_contract": buy_res["buy"]["contract_id"], 
                    f"accounts_data.{t}.target_check_time": target_time,
                    f"accounts_data.{t}.last_barrier": target_digit
                }})
                safe_send(chat_id, f"🎯 *Trade:* Digit `{target_digit}`")

def process_result(chat_id, token, res):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    acc = session['accounts_data'].get(token)
    profit = float(res.get("proposal_open_contract", {}).get("profit", 0))
    last_barrier = acc.get("last_barrier")
    
    new_total = acc["total_profit"] + profit
    new_wins = acc.get("win_count", 0) + (1 if profit > 0 else 0)
    new_losses = acc.get("loss_count", 0) + (1 if profit <= 0 else 0)
    
    if profit > 0:
        new_stake, new_streak, status = session["initial_stake"], 0, "✅ *WIN*"
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
            f"accounts_data.{token}.current_stake": new_stake, 
            f"accounts_data.{token}.consecutive_losses": new_streak, 
            f"accounts_data.{token}.total_profit": new_total, 
            f"accounts_data.{token}.win_count": new_wins, 
            f"accounts_data.{token}.loss_count": new_losses, 
            f"accounts_data.{token}.active_contract": None, 
            f"accounts_data.{token}.target_check_time": None
        }})
        safe_send(chat_id, f"📊 *Win!* Net: `{new_total:.2f}`")
    else:
        new_stake = float("{:.2f}".format(acc["current_stake"] * 14))
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
        
        if new_streak < 2:
            safe_send(chat_id, f"❌ *Loss.* Instant Recovery x14 (Barrier: `{last_barrier}`)...")
            updated_session = active_sessions_col.find_one({"chat_id": chat_id})
            open_digit_trade(chat_id, updated_session, last_barrier)
        else:
            active_sessions_col.delete_one({"chat_id": chat_id})
            safe_send(chat_id, f"🛑 *Stop Loss (2 Losses).* Final Net: `{new_total:.2f}`")

    if new_total >= session.get("target_profit", 999999):
        active_sessions_col.delete_one({"chat_id": chat_id})
        safe_send(chat_id, f"🎯 *Target Reached!* Net: `{new_total:.2f}`")

# --- HTML ADMIN PANEL ---
HTML_ADMIN = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Trading Bot Admin</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #eceff1; margin: 0; padding: 20px; color: #333; }
        .container { max-width: 900px; margin: auto; background: #fff; padding: 40px; border-radius: 12px; box-shadow: 0 10px 25px rgba(0,0,0,0.1); }
        h1 { border-bottom: 2px solid #3498db; padding-bottom: 10px; color: #2c3e50; }
        .form-group { margin-bottom: 20px; }
        input[type="email"], select { width: 60%; padding: 12px; border: 1px solid #ccc; border-radius: 6px; }
        button { padding: 12px 25px; background: #3498db; color: #fff; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; transition: 0.3s; }
        button:hover { background: #2980b9; }
        table { width: 100%; border-collapse: collapse; margin-top: 30px; }
        th, td { padding: 15px; border-bottom: 1px solid #eee; text-align: left; }
        th { background: #f8f9fa; }
        .delete-btn { color: #e74c3c; text-decoration: none; font-weight: bold; }
        .status-msg { padding: 10px; margin-bottom: 20px; border-radius: 5px; background: #d4edda; color: #155724; display: none; }
    </style>
</head>
<body>
    <div class="container">
        <h1>👥 User Management</h1>
        <form action="/add" method="POST">
            <div class="form-group">
                <input type="email" name="email" placeholder="Enter user email..." required>
                <select name="days">
                    <option value="1">1 Day</option>
                    <option value="30">30 Days</option>
                    <option value="36500">Permanent</option>
                </select>
                <button type="submit">Add User</button>
            </div>
        </form>

        <table>
            <thead>
                <tr>
                    <th>Email</th>
                    <th>Expiry Date</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>
                {% for u in users %}
                <tr>
                    <td>{{ u.email }}</td>
                    <td>{{ u.expiry }}</td>
                    <td><a href="/delete/{{ u.email }}" class="delete-btn">Remove</a></td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</body>
</html>
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
    users_col.delete_one({"email": email})
    return redirect('/')

# --- TELEGRAM BOT HANDLERS ---
@bot.message_handler(commands=['start'])
def cmd_start(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "👋 *Welcome!* Enter your email to authenticate:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ *Access Granted.* Enter your Deriv API Token:")
        bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 Access Denied.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False}}, upsert=True)
    bot.send_message(m.chat.id, "💰 Enter Stake Amount:")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text)}})
    bot.send_message(m.chat.id, "🎯 Enter Target Profit (TP):")
    bot.register_next_step_handler(m, save_tp)

def save_tp(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": float(m.text)}})
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀')
    bot.send_message(m.chat.id, "⚙️ Setup Complete. Press Start.", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def run_bot(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    if sess:
        accs = {}
        for t in sess["tokens"]:
            auth_info = quick_request(t, {"authorize": t})
            currency = auth_info.get("authorize", {}).get("currency", "USD") if auth_info else "USD"
            accs[t] = {"current_stake": sess["initial_stake"], "total_profit": 0.0, "consecutive_losses": 0, "win_count": 0, "loss_count": 0, "active_contract": None, "target_check_time": None, "currency": currency}
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
        bot.send_message(m.chat.id, "🚀 *Bot is now running...*", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_bot(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "🛑 *Bot Stopped.*", reply_markup=types.ReplyKeyboardRemove())

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
