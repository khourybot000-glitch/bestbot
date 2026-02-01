import websocket, json, time, os, threading, queue
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
TOKEN = "8433565422:AAEnku035sQ_4v1MU06RlNtP4SqsjPdWEig"
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
            chat_id, text = msg_queue.get()
            bot.send_message(chat_id, text, parse_mode="Markdown")
            msg_queue.task_done()
            time.sleep(0.04) 
        except: pass

threading.Thread(target=message_worker, daemon=True).start()

def safe_send(chat_id, text):
    msg_queue.put((chat_id, text))

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
        if "authorize" in json.loads(ws.recv()):
            ws.send(json.dumps({"proposal": 1, **buy_req, "currency": currency}))
            prop_res = json.loads(ws.recv())
            if "proposal" in prop_res:
                ws.send(json.dumps({"buy": prop_res["proposal"]["id"], "price": buy_req['amount']}))
                res = json.loads(ws.recv())
                ws.close()
                return res
        ws.close()
    except: pass
    return None

# --- ENGINE: 60 TICKS STRATEGY ---
def trade_engine(chat_id):
    last_processed_minute = -1
    
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        try:
            now = datetime.now()
            
            # 1. Result Check (Wait 18s)
            for token, acc in session.get("accounts_data", {}).items():
                if acc.get("active_contract") and acc.get("target_check_time"):
                    if now >= datetime.fromisoformat(acc["target_check_time"]):
                        res_res = quick_request(token, {"proposal_open_contract": 1, "contract_id": acc["active_contract"]})
                        if res_res and res_res.get("proposal_open_contract", {}).get("is_expired"):
                            process_result(chat_id, token, res_res)

            # 2. Analysis ONLY AT SECOND 0
            if now.second == 0 and now.minute != last_processed_minute:
                last_processed_minute = now.minute
                
                if any(acc.get("active_contract") for acc in session.get("accounts_data", {}).values()): continue

                res = quick_request(session['tokens'][0], {"ticks_history": "R_100", "count": 60, "end": "latest", "style": "ticks"})
                if res and "history" in res:
                    prices = res["history"]["prices"]
                    c1_trend = "UP" if prices[29] > prices[0] else "DOWN"
                    c2_trend = "UP" if prices[59] > prices[30] else "DOWN"
                    last_5_trend = "UP" if prices[-1] > prices[-5] else "DOWN"
                    
                    if c1_trend == "UP" and c2_trend == "DOWN" and last_5_trend == "DOWN":
                        open_trade(chat_id, session, "PUT", "+0.8")
                    elif c1_trend == "DOWN" and c2_trend == "UP" and last_5_trend == "UP":
                        open_trade(chat_id, session, "CALL", "-0.8")

            time.sleep(0.5) 
        except: time.sleep(1)

def open_trade(chat_id, session, side, barrier):
    target_time = (datetime.now() + timedelta(seconds=18)).isoformat()
    for t in session['tokens']:
        acc = session['accounts_data'].get(t)
        if acc:
            buy_res = execute_trade(t, {
                "amount": acc["current_stake"], "basis": "stake", 
                "contract_type": side, "duration": 5, 
                "duration_unit": "t", "symbol": "R_100", 
                "barrier": barrier
            }, acc["currency"])
            if buy_res and "buy" in buy_res:
                active_sessions_col.update_one({"chat_id": chat_id}, {
                    "$set": {
                        f"accounts_data.{t}.active_contract": buy_res["buy"]["contract_id"],
                        f"accounts_data.{t}.target_check_time": target_time
                    }
                })
                safe_send(chat_id, f"🚀 *New {side} Opened*")

def process_result(chat_id, token, res):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    acc = session['accounts_data'].get(token)
    profit = float(res.get("proposal_open_contract", {}).get("profit", 0))
    
    new_total = acc["total_profit"] + profit
    new_wins = acc.get("win_count", 0) + (1 if profit > 0 else 0)
    new_losses = acc.get("loss_count", 0) + (1 if profit <= 0 else 0)
    
    if profit > 0:
        new_stake = session["initial_stake"]
        new_streak = 0
        status = "✅ *WIN*"
    else:
        new_stake = float("{:.2f}".format(acc["current_stake"] * 19))
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
    
    stats_msg = (
        f"📊 *Status:* {status}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"W: `{new_wins}` | L: `{new_losses}`\n"
        f"Net: `{new_total:.2f}`\n"
        f"Next: `{new_stake}`"
    )
    safe_send(chat_id, stats_msg)

    if new_streak >= 2:
        safe_send(chat_id, "🛑 *System Stopped (2 Losses).*")
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})

# --- UPDATED HTML ADMIN PANEL ---
@app.route('/')
def index():
    users = list(users_col.find())
    return render_template_string("""
    <!DOCTYPE html><html><head><title>Admin Panel</title>
    <style>
        body{font-family:'Segoe UI',sans-serif; background:#f0f2f5; text-align:center; padding:50px;}
        .card{max-width:850px; margin:auto; background:white; padding:40px; border-radius:15px; box-shadow:0 10px 30px rgba(0,0,0,0.1);}
        h2{color:#1a73e8; margin-bottom:30px;}
        input, select{padding:12px; margin:10px; border:1px solid #ddd; border-radius:8px; width:200px;}
        .btn{background:#28a745; color:white; border:none; padding:12px 25px; border-radius:8px; cursor:pointer; font-weight:bold;}
        table{width:100%; border-collapse:collapse; margin-top:30px;}
        th,td{padding:15px; border-bottom:1px solid #eee; text-align:left;}
        .del-btn{color:#dc3545; text-decoration:none; font-weight:bold;}
    </style></head>
    <body><div class="card">
        <h2>🚀 User Management</h2>
        <form action="/add" method="POST">
            <input type="email" name="email" placeholder="Email" required>
            <select name="days">
                <option value="1">1 Day</option>
                <option value="30">30 Days</option>
                <option value="36500">Lifetime (36500 Days)</option>
            </select>
            <button type="submit" class="btn">Add User</button>
        </form>
        <table><thead><tr><th>Email</th><th>Expiry</th><th>Action</th></tr></thead>
        <tbody>{% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" class="del-btn">Remove</a></td></tr>{% endfor %}</tbody>
        </table></div></body></html>""", users=users)

@app.route('/add', methods=['POST'])
def add_user():
    days = int(request.form.get('days'))
    exp = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower()}, {"$set": {"expiry": exp}}, upsert=True); return redirect('/')

@app.route('/delete/<email>')
def delete_user(email):
    users_col.delete_one({"email": email}); return redirect('/')

@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id, "🤖 *System Interface*\nPlease enter Email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Verified. Enter Token:")
        bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 Denied.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False}}, upsert=True)
    bot.send_message(m.chat.id, "Stake:")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text)}})
    bot.send_message(m.chat.id, "Ready.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def run_bot(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    if sess:
        accs = {t: {"current_stake": sess["initial_stake"], "total_profit": 0.0, "consecutive_losses": 0, "win_count": 0, "loss_count": 0, "active_contract": None, "target_check_time": None, "currency": "USD"} for t in sess["tokens"]}
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
        bot.send_message(m.chat.id, "🚀 *Bot Started*", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    bot.send_message(m.chat.id, "🛑 *Stopped*")

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
