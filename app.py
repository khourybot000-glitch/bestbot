import websocket, json, time, threading, queue
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
TOKEN = "8433565422:AAHXN080Latzw4Em4vE77mKoJUENHGboYwA"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

msg_queue = queue.Queue()

# --- MESSAGE WORKER (FIXED) ---
def message_worker():
    while True:
        try:
            item = msg_queue.get()
            chat_id = item[0]
            text = item[1]
            markup = item[2] if len(item) > 2 else None
            bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)
            msg_queue.task_done()
            time.sleep(0.1)
        except: pass

threading.Thread(target=message_worker, daemon=True).start()

def safe_send(chat_id, text, markup=None):
    msg_queue.put((chat_id, text, markup))

# --- API EXECUTION WITH AUTO-CURRENCY ---
def execute_trade(api_token, request_data):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": api_token}))
        auth_res = json.loads(ws.recv())
        if "authorize" in auth_res:
            user_currency = auth_res["authorize"].get("currency", "USD")
            # حقن العملة تلقائياً في الطلب
            if "parameters" in request_data:
                request_data["parameters"]["currency"] = user_currency
            
            ws.send(json.dumps(request_data))
            res = json.loads(ws.recv())
            ws.close()
            return res, user_currency
        ws.close()
    except: pass
    return None, "USD"

# --- ENGINE: TIME ANALYSIS & 8% PROFIT EXIT ---
def trade_engine(chat_id):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session: return
    token = session['tokens'][0]
    tick_prices = []

    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
        ws.send(json.dumps({"authorize": token}))
        ws.recv()
        ws.send(json.dumps({"ticks": "R_10", "subscribe": 1}))
        
        while True:
            session = active_sessions_col.find_one({"chat_id": chat_id})
            if not session or not session.get("is_running"): break
            
            data = json.loads(ws.recv())
            if "tick" in data:
                price = data["tick"]["quote"]
                tick_prices.append(price)
                if len(tick_prices) > 5: tick_prices.pop(0)
                
                # التحليل الزمني (0, 10, 20, 30, 40, 50 ثانية)
                now = datetime.now()
                if now.second in [0, 10, 20, 30, 40, 50]:
                    if len(tick_prices) == 5:
                        diff = max(tick_prices) - min(tick_prices)
                        if diff >= 0.8:
                            current_session = active_sessions_col.find_one({"chat_id": chat_id})
                            if not any(acc.get("active_contract") for acc in current_session.get("accounts_data", {}).values()):
                                open_acc_trade(chat_id, current_session, token)
                                time.sleep(1.2)

            # مراقبة العقد المفتوح للإغلاق عند ربح 8%
            for t, acc in session.get("accounts_data", {}).items():
                if acc.get("active_contract"):
                    c_info, _ = execute_trade(t, {"proposal_open_contract": 1, "contract_id": acc["active_contract"]})
                    if c_info and "proposal_open_contract" in c_info:
                        contract = c_info["proposal_open_contract"]
                        current_profit = float(contract.get("profit", 0))
                        stake = float(acc["current_stake"])
                        
                        # حساب النسبة المئوية للربح
                        if stake > 0 and (current_profit / stake) >= 0.08:
                            close_res, _ = execute_trade(t, {"sell": acc["active_contract"], "price": 0})
                            process_acc_result(chat_id, t, close_res, contract)
                        elif contract.get("status") == "lost":
                            process_acc_result(chat_id, t, None, contract)
    except:
        time.sleep(3)
        if active_sessions_col.find_one({"chat_id": chat_id, "is_running": True}):
            threading.Thread(target=trade_engine, args=(chat_id,), daemon=True).start()

def open_acc_trade(chat_id, session, token):
    acc_data = session['accounts_data'][token]
    buy_req = {
        "buy": 1, "price": acc_data["current_stake"],
        "parameters": {
            "amount": acc_data["current_stake"], "basis": "stake",
            "contract_type": "ACCU", "symbol": "R_10", "growth_rate": 0.04,
            "currency": "" # سيتم تعبئتها تلقائياً
        }
    }
    res, det_curr = execute_trade(token, buy_req)
    if res and "buy" in res:
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
            f"accounts_data.{token}.active_contract": res["buy"]["contract_id"],
            f"accounts_data.{token}.currency": det_curr
        }})
        safe_send(chat_id, f"🚀 *Trade Open* | Currency: `{det_curr}`\nExit Strategy: *8% Profit*")

def process_acc_result(chat_id, token, close_res, contract):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    acc = session['accounts_data'][token]
    
    is_win = contract.get("status") == "won" or float(contract.get("profit", 0)) > 0
    profit = float(contract.get("profit", 0))
    new_total = acc["total_profit"] + profit
    
    if is_win:
        status, new_stake, new_losses = "✅ WIN", session["initial_stake"], 0
    else:
        # مضاعفة x14 عند الخسارة
        status, new_stake, new_losses = "❌ LOSS", float("{:.2f}".format(acc["current_stake"] * 14)), acc.get("consecutive_losses", 0) + 1

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.active_contract": None,
        f"accounts_data.{token}.total_profit": new_total,
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.consecutive_losses": new_losses
    }})
    
    safe_send(chat_id, f"📊 *Result:* {status}\nProfit: `{profit:.2f}`\nNet Profit: `{new_total:.2f}`")

    if new_total >= session.get("target_profit", 999999):
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
        safe_send(chat_id, "🎯 *Target Reached!* Bot Stopped.")
    elif new_losses >= 2:
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
        safe_send(chat_id, "🛑 *Stop Loss (2 Losses)!* Bot Stopped.")

# --- FULL HTML ADMIN PANEL ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin Panel</title>
<style>
    body{font-family:'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background:#f4f7f6; padding:20px; text-align:center;}
    .card{max-width:800px; margin:auto; background:white; padding:30px; border-radius:15px; box-shadow:0 4px 15px rgba(0,0,0,0.1);}
    h2{color:#2c3e50;}
    input, select{padding:12px; margin:5px; border-radius:8px; border:1px solid #ddd; width:220px; font-size:14px;}
    button{padding:12px 25px; background:#3498db; color:white; border:none; border-radius:8px; cursor:pointer; font-weight:bold; transition:0.3s;}
    button:hover{background:#2980b9;}
    table{width:100%; border-collapse:collapse; margin-top:25px;}
    th, td{padding:15px; border-bottom:1px solid #eee; text-align:left;}
    th{background:#f8f9fa; color:#7f8c8d; text-transform:uppercase; font-size:12px;}
    .status-active{color:#27ae60; font-weight:bold;}
    .btn-del{color:#e74c3c; text-decoration:none; font-weight:bold;}
</style></head>
<body><div class="card">
    <h2>👥 User Management Control</h2>
    <form action="/add" method="POST">
        <input type="email" name="email" placeholder="User Email" required>
        <select name="days">
            <option value="1">1 Day Access</option>
            <option value="30">30 Days Access</option>
            <option value="36500">Life Time Access (36500d)</option>
        </select>
        <button type="submit">Add User</button>
    </form>
    <table>
        <thead><tr><th>User Email</th><th>Expiry Date</th><th>Status</th><th>Action</th></tr></thead>
        <tbody>
            {% for u in users %}
            <tr>
                <td>{{u.email}}</td>
                <td>{{u.expiry}}</td>
                <td class="status-active">Active</td>
                <td><a href="/delete/{{u.email}}" class="btn-del">Remove</a></td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
</div></body></html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_ADMIN, users=list(users_col.find()))

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

# --- TELEGRAM BOT LOGIC ---
@bot.message_handler(commands=['start'])
def start(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    safe_send(m.chat.id, "🤖 *Accumulator Pro V2.5*\nEnter Authorized Email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.lower().strip()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        safe_send(m.chat.id, "✅ Access Confirmed. Send Token:")
        bot.register_next_step_handler(m, save_token)
    else: safe_send(m.chat.id, "🚫 No Access or Expired.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False}}, upsert=True)
    safe_send(m.chat.id, "Initial Stake ($):")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    try:
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text)}})
        safe_send(m.chat.id, "Target Profit ($):")
        bot.register_next_step_handler(m, setup_tp)
    except:
        safe_send(m.chat.id, "❌ Error. Send Stake again:")
        bot.register_next_step_handler(m, save_stake)

def setup_tp(m):
    try:
        tp = float(m.text)
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": tp}})
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀')
        safe_send(m.chat.id, f"✅ Setup Complete!\nTP: `${tp}`\nClick Start to begin.", markup)
    except:
        safe_send(m.chat.id, "❌ Invalid TP. Send Target Profit again:")
        bot.register_next_step_handler(m, setup_tp)

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def run_bot(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    if sess:
        accs = {t: {"current_stake": sess["initial_stake"], "total_profit": 0, "active_contract": None, "consecutive_losses": 0} for t in sess["tokens"]}
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
        safe_send(m.chat.id, "🚀 *Bot Online & Analyzing...*", types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    safe_send(m.chat.id, "🛑 Bot Stopped.")

if __name__ == '__main__':
    # تشغيل خادم الويب واللبوت في وقت واحد
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
