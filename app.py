import websocket, json, time, threading, queue
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
TOKEN = "8433565422:AAFcTciY-QU1x3wo7ww0UJyPrtZzvziQQV8"
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
            ws.send(json.dumps(request_data))
            res = json.loads(ws.recv())
            ws.close()
            return res, user_currency
        ws.close()
    except: pass
    return None, "USD"

# --- ENGINE: DIGIT DIFF (INTEGER CHANGE) ---
def trade_engine(chat_id):
    last_integer = None
    
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        token = session['tokens'][0]
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
            ws.send(json.dumps({"authorize": token}))
            ws.recv()
            ws.send(json.dumps({"ticks": "R_10", "subscribe": 1}))
            
            while True:
                current_status = active_sessions_col.find_one({"chat_id": chat_id})
                if not current_status or not current_status.get("is_running"):
                    ws.close()
                    return

                ws.settimeout(1)
                try: data = json.loads(ws.recv())
                except: break 

                if "tick" in data:
                    price = float(data["tick"]["quote"])
                    current_integer = int(price)
                    
                    if last_integer is not None and current_integer != last_integer:
                        acc_data = current_status['accounts_data'][token]
                        if not acc_data.get("processing"):
                            open_digit_trade(chat_id, current_status, token)
                    
                    last_integer = current_integer
            ws.close()
        except: time.sleep(2)

def open_digit_trade(chat_id, session, token):
    acc_data = session['accounts_data'][token]
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {f"accounts_data.{token}.processing": True}})
    
    buy_req = {
        "buy": 1, "price": acc_data["current_stake"],
        "parameters": {
            "amount": acc_data["current_stake"], "basis": "stake",
            "contract_type": "DIGITDIFF", "symbol": "R_10", 
            "duration": 1, "duration_unit": "t", "barrier": "1"
        }
    }
    
    res, _ = execute_trade(token, buy_req)
    if res and "buy" in res:
        contract_id = res["buy"]["contract_id"]
        threading.Thread(target=monitor_digit_result, args=(chat_id, token, contract_id)).start()
    else:
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {f"accounts_data.{token}.processing": False}})

def monitor_digit_result(chat_id, token, contract_id):
    time.sleep(2)
    res, _ = execute_trade(token, {"proposal_open_contract": 1, "contract_id": contract_id})
    if res and "proposal_open_contract" in res:
        contract = res["proposal_open_contract"]
        if contract.get("is_expired"):
            process_final_result(chat_id, token, contract)
        else:
            time.sleep(1)
            monitor_digit_result(chat_id, token, contract_id)

def process_final_result(chat_id, token, contract):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    acc = session['accounts_data'][token]
    profit = float(contract.get("profit", 0))
    is_win = profit > 0
    
    new_total = acc["total_profit"] + profit
    win_count = acc.get("win_count", 0) + (1 if is_win else 0)
    loss_count = acc.get("loss_count", 0) + (0 if is_win else 1)
    
    if is_win:
        status, new_stake, new_losses = "✅ WIN", session["initial_stake"], 0
    else:
        # المضاعفة x14 حسب طلبك
        status, new_stake, new_losses = "❌ LOSS", float("{:.2f}".format(acc["current_stake"] * 14)), acc.get("consecutive_losses", 0) + 1

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.processing": False,
        f"accounts_data.{token}.total_profit": new_total,
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.consecutive_losses": new_losses,
        f"accounts_data.{token}.win_count": win_count,
        f"accounts_data.{token}.loss_count": loss_count
    }})
    
    msg = (f"📊 *Digit Diff Result:* {status}\n"
           f"💰 Profit: `{profit:.2f}`\n"
           f"📈 Net: `{new_total:.2f}`\n"
           f"--- Statistics ---\n"
           f"🏆 Wins: `{win_count}`\n"
           f"💀 Losses: `{loss_count}`")
    
    safe_send(chat_id, msg)

    # التحقق من شروط التوقف (خسارتين أو الوصول للهدف)
    if new_losses >= 2 or new_total >= session.get("target_profit", 999999):
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
        # تحويل الزر تلقائياً إلى START
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀')
        safe_send(chat_id, "🏁 *Session Finished (TP/SL reached).* \nBot is now Offline.", markup)

# --- HTML ADMIN PANEL ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin Panel</title>
<style>
    body{font-family:sans-serif; background:#f4f7f6; padding:20px; text-align:center;}
    .card{max-width:800px; margin:auto; background:white; padding:30px; border-radius:15px; box-shadow:0 4px 15px rgba(0,0,0,0.1);}
    input, select{padding:12px; margin:5px; border-radius:8px; border:1px solid #ddd; width:220px;}
    button{padding:12px 25px; background:#3498db; color:white; border:none; border-radius:8px; cursor:pointer;}
    table{width:100%; border-collapse:collapse; margin-top:25px;}
    th, td{padding:15px; border-bottom:1px solid #eee; text-align:left;}
</style></head>
<body><div class="card">
    <h2>👥 User Control Panel</h2>
    <form action="/add" method="POST">
        <input type="email" name="email" placeholder="Email" required>
        <select name="days">
            <option value="1">1 Day</option>
            <option value="30">30 Days</option>
            <option value="36500">Life Time</option>
        </select>
        <button type="submit">Add User</button>
    </form>
    <table>
        <thead><tr><th>Email</th><th>Expiry</th><th>Action</th></tr></thead>
        <tbody>
            {% for u in users %}
            <tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" style="color:red; font-weight:bold; text-decoration:none;">Remove</a></td></tr>
            {% endfor %}
        </tbody>
    </table>
</div></body></html>
"""

@app.route('/')
def index(): return render_template_string(HTML_ADMIN, users=list(users_col.find()))

@app.route('/add', methods=['POST'])
def add_user():
    days = int(request.form.get('days'))
    exp = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower().strip()}, {"$set": {"expiry": exp}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete_user(email):
    users_col.delete_one({"email": email}); return redirect('/')

@bot.message_handler(commands=['start'])
def start(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    safe_send(m.chat.id, "🤖 *Digit Diff V6.1 (Martingale x14)*\nEnter Email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.lower().strip()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        safe_send(m.chat.id, "✅ OK. Send Token:")
        bot.register_next_step_handler(m, save_token)
    else: safe_send(m.chat.id, "🚫 No Access.")

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
        safe_send(m.chat.id, "❌ Error. Enter Stake:"); bot.register_next_step_handler(m, save_stake)

def setup_tp(m):
    try:
        tp = float(m.text)
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": tp}})
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀')
        safe_send(m.chat.id, f"✅ Ready! TP: ${tp}. Mult: x14. Pred: 1. Click Start.", markup)
    except:
        safe_send(m.chat.id, "❌ Error. Enter TP:"); bot.register_next_step_handler(m, setup_tp)

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def run_bot(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    if sess:
        accs = {t: {"current_stake": sess["initial_stake"], "total_profit": 0, "active_contract": None, "consecutive_losses": 0, "win_count": 0, "loss_count": 0, "processing": False} for t in sess["tokens"]}
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
        safe_send(m.chat.id, "🚀 *Bot Online! Monitoring Integer Changes...*", types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀')
    safe_send(m.chat.id, "🛑 Bot Stopped manually.", markup)

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
