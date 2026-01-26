import websocket, json, time, os, threading, queue, pandas as pd, pandas_ta as ta
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
TOKEN = "8433565422:AAHY-VWf__T4DkDFxMyz7NAzl2uHXYr3d5Y"
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

def execute_trade(api_token, buy_req):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
        ws.send(json.dumps({"authorize": api_token}))
        auth_res = json.loads(ws.recv())
        if "authorize" in auth_res:
            # تقريب الـ Stake لرقمين عشريين
            buy_req['amount'] = float("{:.2f}".format(buy_req['amount']))
            ws.send(json.dumps({"proposal": 1, **buy_req}))
            prop_res = json.loads(ws.recv())
            if "proposal" in prop_res:
                buy_id = prop_res["proposal"]["id"]
                ws.send(json.dumps({"buy": buy_id, "price": buy_req['amount']}))
                buy_res = json.loads(ws.recv())
                ws.close() 
                return buy_res
        ws.close()
    except: pass
    return None

def quick_request(api_token, request_data):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
        ws.send(json.dumps({"authorize": api_token}))
        if "authorize" in json.loads(ws.recv()):
            ws.send(json.dumps(request_data))
            res = json.loads(ws.recv())
            ws.close()
            return res
        ws.close()
    except: pass
    return None

def get_account_currency(api_token):
    res = quick_request(api_token, {"balance": 1})
    return res.get("authorize", {}).get("currency", "USD") if res else "USD"

# --- TRADING ENGINE ---
def trade_engine(chat_id):
    last_processed_minute = -1
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        try:
            now = datetime.now()
            if now.second == 0 and now.minute != last_processed_minute:
                # التحليل الفني (اتصال سريع ثم فصل)
                res = quick_request(session['tokens'][0], {"ticks_history": "R_100", "count": 1500, "end": "latest", "style": "ticks"})
                prices = res.get("history", {}).get("prices", []) if res else []

                if len(prices) >= 1500:
                    df = pd.DataFrame(prices, columns=['close'])
                    ema50 = ta.ema(df['close'], length=50)
                    rsi = ta.rsi(df['close'], length=14)
                    
                    curr_p, curr_ema = prices[-1], ema50.iloc[-1]
                    rsi_open, rsi_close = rsi.iloc[-31], rsi.iloc[-1]
                    
                    direction = "CALL" if curr_p > curr_ema and rsi_open < 70 and rsi_close >= 70 else \
                                "PUT" if curr_p < curr_ema and rsi_open > 30 and rsi_close <= 30 else None

                    if direction:
                        safe_send(chat_id, f"🎯 *Signal Found:* {direction}\nEMA 50: `{curr_ema:.2f}`\nRSI Cross: `{rsi_open:.1f} ➔ {rsi_close:.1f}`")
                        
                        for token in session['tokens']:
                            acc = session['accounts_data'].get(token)
                            if acc:
                                amount = float("{:.2f}".format(acc["current_stake"]))
                                buy_params = {
                                    "amount": amount, "basis": "stake", "contract_type": direction,
                                    "currency": acc.get("currency", "USD"), "duration": 1, "duration_unit": "m",
                                    "symbol": "R_100"
                                }
                                final_res = execute_trade(token, buy_params)
                                if final_res and "buy" in final_res:
                                    c_id = final_res["buy"]["contract_id"]
                                    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {f"accounts_data.{token}.active_contract": c_id}})
                                    safe_send(chat_id, f"✅ *Confirmed:* {token[:5]}... | ID: `{c_id}`")

                        last_processed_minute = now.minute
                        time.sleep(66) # مدة الانتظار المطلوبة لضمان استقرار النتيجة
                        
                        current_session = active_sessions_col.find_one({"chat_id": chat_id})
                        for token in current_session['tokens']:
                            acc = current_session['accounts_data'].get(token)
                            if acc.get("active_contract"):
                                for _ in range(25): # التحقق كل ثانية لمنع التعليق
                                    result_res = quick_request(token, {"proposal_open_contract": 1, "contract_id": acc["active_contract"]})
                                    if result_res and result_res.get("proposal_open_contract", {}).get("is_expired"):
                                        process_result(chat_id, token, result_res)
                                        break
                                    time.sleep(1)
                    else: last_processed_minute = now.minute 
            time.sleep(0.5)
        except: time.sleep(1)

def process_result(chat_id, token, res):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    acc = session['accounts_data'].get(token)
    contract = res.get("proposal_open_contract", {})
    if contract.get("is_expired") == 1:
        profit = float(contract.get("profit", 0))
        new_wins = acc["win_count"] + (1 if profit > 0 else 0)
        new_losses = acc["loss_count"] + (1 if profit <= 0 else 0)
        
        if profit > 0:
            new_stake = session["initial_stake"]; new_mg = 0; status = "✅ *WIN*"
        else:
            new_stake = float("{:.2f}".format(acc["current_stake"] * 2.2))
            new_mg = acc["consecutive_losses"] + 1; status = "❌ *LOSS*"
        
        new_total = acc["total_profit"] + profit
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
            f"accounts_data.{token}.current_stake": new_stake,
            f"accounts_data.{token}.win_count": new_wins,
            f"accounts_data.{token}.loss_count": new_losses,
            f"accounts_data.{token}.consecutive_losses": new_mg,
            f"accounts_data.{token}.total_profit": new_total,
            f"accounts_data.{token}.active_contract": None
        }})
        
        report = (f"🔍 *Result:* {status}\n💰 Profit: `{profit:.2f}`\n📊 Total: `{new_total:.2f}`\n📈 W: `{new_wins}` | L: `{new_losses}`\n🔄 MG: {new_mg}/4")
        safe_send(chat_id, report)
        
        if new_mg >= 4:
            safe_send(chat_id, f"🛑 *Stop Loss Reached (4 Steps).* Session Closed."); active_sessions_col.delete_one({"chat_id": chat_id})

# --- HTML ADMIN PANEL ---
@app.route('/')
def index():
    users = list(users_col.find())
    return render_template_string("""
    <!DOCTYPE html><html><head><title>Admin Dashboard</title>
    <style>
        body{font-family:Arial; text-align:center; background:#f0f2f5; padding:50px 20px;}
        .container{max-width:800px; margin:auto; background:white; padding:30px; border-radius:15px; box-shadow:0 5px 20px rgba(0,0,0,0.1);}
        table{width:100%; border-collapse:collapse; margin-top:20px;}
        th,td{padding:12px; border:1px solid #ddd;} th{background:#007bff; color:white;}
        .btn-add{background:#28a745; color:white; border:none; padding:10px 20px; border-radius:5px; cursor:pointer;}
        .btn-del{color:red; text-decoration:none; font-weight:bold;}
    </style></head>
    <body><div class="container">
        <h2>💎 Admin Access Panel</h2>
        <form action="/add" method="POST">
            <input type="email" name="email" placeholder="Email" required style="padding:10px; width:200px;">
            <select name="days" style="padding:10px;"><option value="1">1 Day</option><option value="30">30 Days</option><option value="36500">Life Time</option></select>
            <button type="submit" class="btn-add">Add User</button>
        </form>
        <table><tr><th>Email</th><th>Expiry</th><th>Action</th></tr>
        {% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" class="btn-del">Remove</a></td></tr>{% endfor %}
        </table></div></body></html>""", users=users)

@app.route('/add', methods=['POST'])
def add_user():
    expiry = (datetime.now() + timedelta(days=int(request.form.get('days')))).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower()}, {"$set": {"expiry": expiry}}, upsert=True); return redirect('/')

@app.route('/delete/<email>')
def delete_user(email):
    users_col.delete_one({"email": email}); return redirect('/')

@bot.message_handler(commands=['start'])
def start(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "🎯 *EMA 50 + RSI Strategy Loaded*\n1m Duration | 66s Sleep\nPlease enter your email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    user = users_col.find_one({"email": m.text.strip().lower()})
    if user and datetime.strptime(user['expiry'], "%Y-%m-%d") > datetime.now():
        active_sessions_col.insert_one({"chat_id": m.chat.id, "email": m.text.strip().lower(), "is_running": False})
        bot.send_message(m.chat.id, "✅ OK! Enter API Token(s):"); bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 No Access.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [t.strip() for t in m.text.split(",")]}})
    bot.send_message(m.chat.id, "Enter Initial Stake:"); bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text)}})
    bot.send_message(m.chat.id, "Enter Target Profit (TP):"); bot.register_next_step_handler(m, save_tp)

def save_tp(m):
    session = active_sessions_col.find_one({"chat_id": m.chat.id})
    accs_data = {t: {"currency": get_account_currency(t), "current_stake": session["initial_stake"], "win_count": 0, "loss_count": 0, "total_profit": 0.0, "consecutive_losses": 0, "active_contract": None} for t in session["tokens"]}
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tp_goal": float(m.text), "is_running": True, "accounts_data": accs_data}})
    bot.send_message(m.chat.id, "🚀 Bot Started! (Connected via Threading Pool)", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
    threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id}); bot.send_message(m.chat.id, "🛑 Stopped.")

def restore_sessions():
    for sess in active_sessions_col.find({"is_running": True}):
        threading.Thread(target=trade_engine, args=(sess['chat_id'],), daemon=True).start()

if __name__ == '__main__':
    restore_sessions()
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)), use_reloader=False), daemon=True).start()
    bot.infinity_polling()
