import websocket, json, time, os, threading, queue, pandas as pd
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
TOKEN = "8433565422:AAETReVEiqQQtlLf4kVaNANCfseGAEGAL8w"
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
        if "authorize" in json.loads(ws.recv()):
            ws.send(json.dumps(request_data))
            res = json.loads(ws.recv())
            ws.close()
            return res
        ws.close()
    except: pass
    return None

def execute_trade(api_token, buy_req):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=12)
        ws.send(json.dumps({"authorize": api_token}))
        if "authorize" in json.loads(ws.recv()):
            buy_req['amount'] = float("{:.2f}".format(buy_req['amount']))
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

# --- ENGINE: LOGIC UPDATED ---
def trade_engine(chat_id):
    last_processed_minute = -1
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        try:
            now = datetime.now()
            
            # Check results
            for token, acc in session.get("accounts_data", {}).items():
                if acc.get("active_contract") and acc.get("target_check_time"):
                    target_time = datetime.fromisoformat(acc["target_check_time"])
                    if now >= target_time:
                        res_res = quick_request(token, {"proposal_open_contract": 1, "contract_id": acc["active_contract"]})
                        if res_res and res_res.get("proposal_open_contract", {}).get("is_expired"):
                            process_result(chat_id, token, res_res)
                            continue

            # التحليل عند الثانية 0
            if now.second == 0 and now.minute != last_processed_minute:
                last_processed_minute = now.minute 
                is_any_active = any(acc.get("active_contract") for acc in session.get("accounts_data", {}).values())
                if is_any_active: continue

                res = quick_request(session['tokens'][0], {"ticks_history": "R_100", "count": 30, "end": "latest", "style": "ticks"})
                prices = res.get("history", {}).get("prices", []) if res else []

                if len(prices) >= 30:
                    diff = prices[-1] - prices[0]
                    direction = None
                    
                    # التحليل المطلوب: 1 للـ CALL و -1 للـ PUT
                    if diff >= 1: direction = "CALL"
                    elif diff <= -1: direction = "PUT"

                    if direction:
                        acc_example = list(session.get("accounts_data", {}).values())[0]
                        is_mg = acc_example.get("consecutive_losses", 0) > 0
                        open_trade(chat_id, session, direction, is_mg)

            time.sleep(0.5)
        except: time.sleep(1)

def open_trade(chat_id, session, direction, is_martingale):
    now = datetime.now()
    # وقت الانتظار المطلوب 16 ثانية
    target_time = (now + timedelta(seconds=16)).isoformat()
    
    # الـ Barrier: CALL -> -1 | PUT -> +1
    barrier_value = "-1" if direction == "CALL" else "+1"
    
    msg = f"🔄 *MG Trade:* {direction}" if is_martingale else f"🎯 *Initial Signal:* {direction}"
    safe_send(chat_id, msg)

    for t in session['tokens']:
        acc = session['accounts_data'].get(t)
        if acc:
            buy_res = execute_trade(t, {
                "amount": acc["current_stake"], 
                "basis": "stake", 
                "contract_type": direction,
                "currency": "USD", 
                "duration": 5,        # 5 Ticks
                "duration_unit": "t", 
                "symbol": "R_100",
                "barrier": barrier_value
            })
            if buy_res and "buy" in buy_res:
                active_sessions_col.update_one({"chat_id": chat_id}, {
                    "$set": {
                        f"accounts_data.{t}.active_contract": buy_res["buy"]["contract_id"],
                        f"accounts_data.{t}.target_check_time": target_time,
                        f"accounts_data.{t}.last_direction": direction
                    }
                })

def process_result(chat_id, token, res):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    acc = session['accounts_data'].get(token)
    contract = res.get("proposal_open_contract", {})
    
    profit = float(contract.get("profit", 0))
    status = "✅ *WIN*" if profit > 0 else "❌ *LOSS*"
    
    if profit > 0:
        new_stake = session["initial_stake"]
        new_mg = 0
    else:
        # المضاعفة x24
        new_stake = float("{:.2f}".format(acc["current_stake"] * 24))
        new_mg = acc["consecutive_losses"] + 1

    new_total = acc["total_profit"] + profit
    
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.win_count": acc["win_count"] + (1 if profit > 0 else 0),
        f"accounts_data.{token}.loss_count": acc["loss_count"] + (1 if profit <= 0 else 0),
        f"accounts_data.{token}.consecutive_losses": new_mg,
        f"accounts_data.{token}.total_profit": new_total,
        f"accounts_data.{token}.active_contract": None,
        f"accounts_data.{token}.target_check_time": None
    }})
    
    safe_send(chat_id, f"📊 *Result:* {status}\n💰 Profit: `{profit:.2f}`\n📈 Wins: `{acc['win_count'] + (1 if profit > 0 else 0)}` | Losses: `{acc['loss_count'] + (1 if profit <= 0 else 0)}` \n🔄 Next Stake: `{new_stake:.2f}` (Waiting Signal)")
    
    if new_mg >= 2:
        safe_send(chat_id, "🛑 *Limit Reached (2 Losses)!* Stopping Bot."); 
        active_sessions_col.delete_one({"chat_id": chat_id})

# --- HTML ADMIN PANEL ---
@app.route('/')
def index():
    users = list(users_col.find())
    return render_template_string("""
    <!DOCTYPE html><html><head><title>Admin Control</title>
    <style>
        body{font-family:'Segoe UI', sans-serif; background:#f0f2f5; text-align:center; padding:50px;}
        .card{max-width:850px; margin:auto; background:white; padding:40px; border-radius:15px; box-shadow:0 10px 30px rgba(0,0,0,0.1);}
        h2{color:#1a73e8; margin-bottom:30px;}
        input, select{padding:12px; margin:10px; border:1px solid #ddd; border-radius:8px; width:200px;}
        .btn{background:#28a745; color:white; border:none; padding:12px 25px; border-radius:8px; cursor:pointer; font-weight:bold;}
        table{width:100%; border-collapse:collapse; margin-top:30px;}
        th,td{padding:15px; border-bottom:1px solid #eee; text-align:left;}
        .del-btn{color:#dc3545; text-decoration:none; font-weight:bold;}
    </style></head>
    <body><div class="card">
        <h2>🚀 Trading Bot Admin Panel</h2>
        <form action="/add" method="POST">
            <input type="email" name="email" placeholder="User Email" required>
            <select name="days"><option value="30">30 Days</option><option value="36500">Lifetime</option></select>
            <button type="submit" class="btn">Add User</button>
        </form>
        <table><thead><tr><th>Email</th><th>Expiry</th><th>Action</th></tr></thead>
        <tbody>{% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" class="del-btn">Remove</a></td></tr>{% endfor %}</tbody>
        </table></div></body></html>""", users=users)

@app.route('/add', methods=['POST'])
def add_user():
    exp = (datetime.now() + timedelta(days=int(request.form.get('days')))).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower()}, {"$set": {"expiry": exp}}, upsert=True); return redirect('/')

@app.route('/delete/<email>')
def delete_user(email):
    users_col.delete_one({"email": email}); return redirect('/')

# --- TELEGRAM ---
@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id, "🤖 *Tick Bot V2*\n1/-1 Signal | x24 MG | 2-Loss Stop\nEnter Email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}}, upsert=True)
        bot.send_message(m.chat.id, "✅ Verified. Token:"); bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 Denied.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [t.strip() for t in m.text.split(",")]}})
    bot.send_message(m.chat.id, "Stake Amount:"); bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text)}})
    bot.send_message(m.chat.id, "Bot is Ready! Click below to start.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def run_bot(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    if sess:
        accs = {t: {"current_stake": sess["initial_stake"], "win_count": 0, "loss_count": 0, "total_profit": 0.0, "consecutive_losses": 0, "active_contract": None, "target_check_time": None, "last_direction": None} for t in sess["tokens"]}
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
        bot.send_message(m.chat.id, "🚀 Running! (Waiting for signal...)", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}}); bot.send_message(m.chat.id, "🛑 Stopped.")

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000))), daemon=True).start()
    bot.infinity_polling()
