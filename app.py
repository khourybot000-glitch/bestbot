import websocket, json, time, threading, queue
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
# التوكن الجديد المستبدل بناءً على طلبك
TOKEN = "8433565422:AAGdAjNAooUVYdqkdOZQbKQtn7HvgKaj0yI"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

msg_queue = queue.Queue()

def message_worker():
    while True:
        try:
            item = msg_queue.get()
            bot.send_message(item[0], item[1], parse_mode="Markdown", reply_markup=item[2] if len(item)>2 else None)
            msg_queue.task_done()
            time.sleep(0.1)
        except: pass

threading.Thread(target=message_worker, daemon=True).start()

def safe_send(chat_id, text, markup=None):
    msg_queue.put((chat_id, text, markup))

def execute_trade(api_token, request_data):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": api_token}))
        auth_res = json.loads(ws.recv())
        if "authorize" in auth_res:
            user_currency = auth_res["authorize"].get("currency", "USD")
            if "parameters" in request_data:
                request_data["parameters"]["currency"] = user_currency
            ws.send(json.dumps(request_data))
            res = json.loads(ws.recv())
            ws.close()
            return res, user_currency
        ws.close()
    except: pass
    return None, "USD"

# --- ENGINE: TIME-BASED ANALYSIS (0, 10, 20, 30, 40, 50s) ---
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
                
                now = datetime.now()
                if now.second in [0, 10, 20, 30, 40, 50]:
                    if len(tick_prices) == 5:
                        diff = max(tick_prices) - min(tick_prices)
                        if diff >= 0.8:
                            current_session = active_sessions_col.find_one({"chat_id": chat_id})
                            if not any(acc.get("active_contract") for acc in current_session.get("accounts_data", {}).values()):
                                open_acc_trade(chat_id, current_session, token)
                                time.sleep(1) # تأمين لمنع التكرار في نفس الثانية

            # فحص الإغلاق بعد 2 تيك
            for t, acc in session.get("accounts_data", {}).items():
                if acc.get("active_contract"):
                    c_info, _ = execute_trade(t, {"proposal_open_contract": 1, "contract_id": acc["active_contract"]})
                    if c_info and "proposal_open_contract" in c_info:
                        contract = c_info["proposal_open_contract"]
                        if contract.get("tick_count", 0) >= 2:
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
            "contract_type": "ACCU", "symbol": "R_10", "growth_rate": 0.04
        }
    }
    res, detected_currency = execute_trade(token, buy_req)
    if res and "buy" in res:
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
            f"accounts_data.{token}.active_contract": res["buy"]["contract_id"],
            f"accounts_data.{token}.currency": detected_currency
        }})
        safe_send(chat_id, f"🎯 *Triggered at {datetime.now().second}s*\nCurrency: `{detected_currency}` | Stake: `{acc_data['current_stake']}`")

def process_acc_result(chat_id, token, close_res, contract):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    acc = session['accounts_data'][token]
    profit = float(contract.get("profit", 0))
    new_total = acc["total_profit"] + profit
    
    if profit > 0:
        status, new_stake, new_losses = "✅ WIN", session["initial_stake"], 0
    else:
        status, new_stake, new_losses = "❌ LOSS", float("{:.2f}".format(acc["current_stake"] * 14)), acc.get("consecutive_losses", 0) + 1

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.active_contract": None,
        f"accounts_data.{token}.total_profit": new_total,
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.consecutive_losses": new_losses
    }})
    
    safe_send(chat_id, f"📊 *Result:* {status}\nProfit: `{profit:.2f}`\nNext Stake: `{new_stake}`")
    if new_losses >= 2:
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
        safe_send(chat_id, "🛑 *Stop Loss!* 2 Consecutive Losses.")

# --- FULL HTML WITH DURATION OPTIONS ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin Panel</title>
<style>
    body{font-family:sans-serif; background:#f4f7f6; padding:20px; text-align:center;}
    .card{max-width:700px; margin:auto; background:white; padding:30px; border-radius:12px; box-shadow:0 4px 10px rgba(0,0,0,0.1);}
    input, select{padding:10px; margin:5px; border-radius:5px; border:1px solid #ddd; width:200px;}
    button{padding:10px 20px; background:#3498db; color:white; border:none; border-radius:5px; cursor:pointer; font-weight:bold;}
    table{width:100%; border-collapse:collapse; margin-top:20px;}
    th, td{padding:12px; border-bottom:1px solid #eee; text-align:left;}
    th{background:#f8f9fa;}
    .btn-del{color:red; text-decoration:none; font-weight:bold;}
</style></head>
<body><div class="card">
    <h2>👥 User Access Control</h2>
    <form action="/add" method="POST">
        <input type="email" name="email" placeholder="Email Address" required>
        <select name="days">
            <option value="1">1 Day</option>
            <option value="30">30 Days</option>
            <option value="36500">Life Time (36500 Days)</option>
        </select>
        <button type="submit">Add User</button>
    </form>
    <table>
        <thead><tr><th>Email</th><th>Expiry Date</th><th>Action</th></tr></thead>
        <tbody>
            {% for u in users %}
            <tr>
                <td>{{u.email}}</td>
                <td>{{u.expiry}}</td>
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

# --- TELEGRAM ---
@bot.message_handler(commands=['start'])
def start(m):
    safe_send(m.chat.id, "🤖 *Accumulator Time-Trigger V2*\nAnalysis at: 0,10,20,30,40,50s\nGrowth: 4% | Martingale: x14\nEnter Email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.lower().strip()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        safe_send(m.chat.id, "✅ OK. Send Token:")
        bot.register_next_step_handler(m, save_token)
    else: safe_send(m.chat.id, "🚫 No Access or Expired.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False}}, upsert=True)
    safe_send(m.chat.id, "Initial Stake:")
    bot.register_next_step_handler(m, setup_stake)

def setup_stake(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text)}})
    safe_send(m.chat.id, "Ready!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def run_bot(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    if sess:
        accs = {t: {"current_stake": sess["initial_stake"], "total_profit": 0, "active_contract": None, "consecutive_losses": 0} for t in sess["tokens"]}
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
        safe_send(m.chat.id, "🚀 *Bot Online*\nMonitoring R_10 at specific intervals...", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    safe_send(m.chat.id, "🛑 Stopped.")

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
