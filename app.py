import websocket, json, time, os, threading, queue
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
TOKEN = "8433565422:AAEBqTWdpwzOa4MBzF6gsQY-H28ibuQUmy0"
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
            buy_req['amount'] = float("{:.2f}".format(buy_req['amount']))
            buy_req['currency'] = currency
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

# --- ENGINE: 15 TICKS PATTERN ANALYSIS (3 CANDLES) ---
def trade_engine(chat_id):
    last_processed_minute = -1
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        try:
            now = datetime.now()
            
            # 1. فحص النتائج (Check for results)
            for token, acc in session.get("accounts_data", {}).items():
                if acc.get("active_contract") and acc.get("target_check_time"):
                    target_time = datetime.fromisoformat(acc["target_check_time"])
                    if now >= target_time:
                        res_res = quick_request(token, {"proposal_open_contract": 1, "contract_id": acc["active_contract"]})
                        if res_res and res_res.get("proposal_open_contract", {}).get("is_expired"):
                            process_result(chat_id, token, res_res)
                            continue

            # 2. التحليل عند الثانية 00
            if now.second == 0 and now.minute != last_processed_minute:
                last_processed_minute = now.minute 
                
                is_any_active = any(acc.get("active_contract") for acc in session.get("accounts_data", {}).values())
                if is_any_active: continue

                # طلب آخر 15 تيك لتقسيمها إلى 3 شموع
                res = quick_request(session['tokens'][0], {"ticks_history": "R_100", "count": 15, "end": "latest", "style": "ticks"})
                prices = res.get("history", {}).get("prices", []) if res else []

                if len(prices) >= 15:
                    # Cand 1: 0-4 | Cand 2: 5-9 | Cand 3: 10-14
                    c1 = "UP" if prices[4] > prices[0] else "DOWN"
                    c2 = "UP" if prices[9] > prices[5] else "DOWN"
                    c3 = "UP" if prices[14] > prices[10] else "DOWN"
                    
                    pattern = f"{c1}-{c2}-{c3}"
                    direction = None
                    barrier_v = "0"
                    
                    if pattern == "UP-DOWN-UP":
                        direction = "CALL"
                        barrier_v = "-0.8"
                    elif pattern == "DOWN-UP-DOWN":
                        direction = "PUT"
                        barrier_v = "+0.8"

                    if direction:
                        acc_example = list(session.get("accounts_data", {}).values())[0]
                        is_mg = acc_example.get("consecutive_losses", 0) > 0
                        open_trade(chat_id, session, direction, barrier_v, is_mg)
            
            time.sleep(0.5)
        except: time.sleep(1)

def open_trade(chat_id, session, direction, barrier_v, is_martingale):
    now = datetime.now()
    target_time = (now + timedelta(seconds=16)).isoformat()
    
    msg = f"🔄 *MG Trade:* {direction}" if is_martingale else f"🎯 *Pattern Match:* {direction}"
    safe_send(chat_id, msg)

    for t in session['tokens']:
        acc = session['accounts_data'].get(t)
        if acc:
            buy_res = execute_trade(t, {
                "amount": acc["current_stake"], "basis": "stake", "contract_type": direction,
                "duration": 5, "duration_unit": "t", "symbol": "R_100", "barrier": barrier_v
            }, acc["currency"])
            if buy_res and "buy" in buy_res:
                active_sessions_col.update_one({"chat_id": chat_id}, {
                    "$set": {
                        f"accounts_data.{t}.active_contract": buy_res["buy"]["contract_id"],
                        f"accounts_data.{t}.target_check_time": target_time
                    }
                })

def process_result(chat_id, token, res):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    acc = session['accounts_data'].get(token)
    contract = res.get("proposal_open_contract", {})
    profit = float(contract.get("profit", 0))
    status = "✅ *WIN*" if profit > 0 else "❌ *LOSS*"
    
    new_total_net = acc["total_profit"] + profit
    
    if profit > 0:
        new_stake = session["initial_stake"]
        new_mg = 0
    else:
        # المضاعفة المطلوبة x14
        new_stake = float("{:.2f}".format(acc["current_stake"] * 14))
        new_mg = acc["consecutive_losses"] + 1

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.win_count": acc["win_count"] + (1 if profit > 0 else 0),
        f"accounts_data.{token}.loss_count": acc["loss_count"] + (1 if profit <= 0 else 0),
        f"accounts_data.{token}.consecutive_losses": new_mg,
        f"accounts_data.{token}.total_profit": new_total_net,
        f"accounts_data.{token}.active_contract": None,
        f"accounts_data.{token}.target_check_time": None
    }})
    
    safe_send(chat_id, f"📊 *Result:* {status}\n💰 Net Profit: `{new_total_net:.2f}` {acc['currency']}\n🔄 Next Stake: `{new_stake:.2f}`")
    
    if new_total_net >= session.get("target_profit", 999999):
        safe_send(chat_id, f"🎯 *Target Reached!* Net: `{new_total_net:.2f}`. Stopping."); 
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
        return

    if new_mg >= 2:
        safe_send(chat_id, "🛑 *Limit Reached (2 Losses)!* Stopping."); 
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})

# --- HTML ADMIN PANEL ---
@app.route('/')
def index():
    users = list(users_col.find())
    return render_template_string("""
    <!DOCTYPE html><html><head><title>Admin Control</title>
    <style>
        body{font-family:'Segoe UI', sans-serif; background:#f0f2f5; text-align:center; padding:50px;}
        .card{max-width:900px; margin:auto; background:white; padding:40px; border-radius:15px; box-shadow:0 10px 30px rgba(0,0,0,0.1);}
        h2{color:#1a73e8; margin-bottom:30px;}
        .form-box{background:#f8f9fa; padding:20px; border-radius:10px; margin-bottom:30px;}
        input, select{padding:12px; margin:10px; border:1px solid #ddd; border-radius:8px; width:220px;}
        .btn{background:#28a745; color:white; border:none; padding:12px 25px; border-radius:8px; cursor:pointer; font-weight:bold; transition:0.3s;}
        .btn:hover{background:#218838;}
        table{width:100%; border-collapse:collapse; margin-top:20px;}
        th,td{padding:15px; border-bottom:1px solid #eee; text-align:center;}
        th{background:#f1f3f4; color:#555;}
        .del-btn{color:#dc3545; text-decoration:none; font-weight:bold;}
    </style></head>
    <body><div class="card">
        <h2>🚀 Trading Bot Admin Panel</h2>
        <div class="form-box">
            <form action="/add" method="POST">
                <input type="email" name="email" placeholder="User Email" required>
                <select name="days"><option value="30">30 Days</option><option value="36500">Lifetime</option></select>
                <button type="submit" class="btn">Add Authorized User</button>
            </form>
        </div>
        <table><thead><tr><th>Email</th><th>Expiry Date</th><th>Action</th></tr></thead>
        <tbody>{% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" class="del-btn">Remove</a></td></tr>{% endfor %}</tbody>
        </table></div></body></html>""", users=users)

@app.route('/add', methods=['POST'])
def add_user():
    exp = (datetime.now() + timedelta(days=int(request.form.get('days')))).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower()}, {"$set": {"expiry": exp}}, upsert=True); return redirect('/')

@app.route('/delete/<email>')
def delete_user(email):
    users_col.delete_one({"email": email}); return redirect('/')

# --- TELEGRAM HANDLERS ---
@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id, "🤖 *Tick Bot V2*\nPattern: (UP-DOWN-UP) or (DOWN-UP-DOWN)\nAnalysis at :00\nEnter Email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}}, upsert=True)
        bot.send_message(m.chat.id, "✅ Verified. Enter API Token(s):"); bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 Access Denied.")

def save_token(m):
    tokens = [t.strip() for t in m.text.split(",")]
    accounts_info = {}
    for t in tokens:
        res = quick_request(t, {"get_settings": 1})
        curr = "USD"
        if res and "authorize" in res:
            curr = res["authorize"].get("currency", "USD")
        accounts_info[t] = curr
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": tokens, "account_currencies": accounts_info}})
    bot.send_message(m.chat.id, "Initial Stake:"); bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text)}})
    bot.send_message(m.chat.id, "Target Profit (TP):"); bot.register_next_step_handler(m, save_tp)

def save_tp(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": float(m.text)}})
    bot.send_message(m.chat.id, "Ready!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def run_bot(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    if sess:
        accs = {}
        for t in sess["tokens"]:
            curr = sess.get("account_currencies", {}).get(t, "USD")
            accs[t] = {"current_stake": sess["initial_stake"], "win_count": 0, "loss_count": 0, "total_profit": 0.0, "consecutive_losses": 0, "active_contract": None, "target_check_time": None, "currency": curr}
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
        bot.send_message(m.chat.id, "🚀 Running! Waiting for pattern...", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}}); bot.send_message(m.chat.id, "🛑 Stopped.")

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000))), daemon=True).start()
    bot.infinity_polling()
