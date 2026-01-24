import websocket, json, time, os, threading, queue
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
# Updated with your latest token
TOKEN = "8433565422:AAEliw8XjhOtew10SYRCrERjmeRK1hAbK1w"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN, threaded=True, num_threads=50)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

msg_queue = queue.Queue()

# --- MESSAGE WORKER ---
def message_worker():
    while True:
        try:
            chat_id, text = msg_queue.get()
            bot.send_message(chat_id, text, parse_mode="Markdown")
            msg_queue.task_done()
            time.sleep(0.05) 
        except: pass

threading.Thread(target=message_worker, daemon=True).start()

def safe_send(chat_id, text):
    msg_queue.put((chat_id, text))

# --- CORE TRADING FUNCTION (FIXED: Single connection for Proposal & Buy) ---
def execute_trade(api_token, buy_req):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
        ws.send(json.dumps({"authorize": api_token}))
        auth_res = json.loads(ws.recv())
        
        if "authorize" in auth_res:
            # Step 1: Request Proposal
            ws.send(json.dumps(buy_req))
            prop_res = json.loads(ws.recv())
            
            if "proposal" in prop_res:
                # Step 2: Execute Buy immediately on the same connection
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
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
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
    res = quick_request(api_token, {"get_settings": 1})
    if res and "authorize" in res:
        return res["authorize"].get("currency", "USD")
    # Fallback to manual check if quick_request fails to nest authorize
    auth_data = quick_request(api_token, {"balance": 1})
    return auth_data.get("authorize", {}).get("currency", "USD") if auth_data else "USD"

# --- TRADING ENGINE ---
def trade_engine(chat_id):
    last_processed_minute = -1
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        try:
            now = datetime.now()
            if now.second == 30 and now.minute != last_processed_minute:
                # Check Global TP
                total_profit = sum(acc['total_profit'] for acc in session['accounts_data'].values())
                if total_profit >= session.get('tp_goal', 999999):
                    safe_send(chat_id, f"🎉 *Target Profit Reached:* `{total_profit:.2f}`. Session Cleared.")
                    active_sessions_col.delete_one({"chat_id": chat_id})
                    break

                # Analysis
                res = quick_request(session['tokens'][0], {"ticks_history": "R_100", "count": 15, "end": "latest", "style": "ticks"})
                prices = res.get("history", {}).get("prices", []) if res else []

                if len(prices) >= 15:
                    diff = prices[-1] - prices[-15]
                    direction = "CALL" if diff >= 1.5 else "PUT" if diff <= -1.5 else None
                    if direction:
                        barrier = "-1.5" if direction == "CALL" else "+1.5"
                        safe_send(chat_id, f"🎯 *Signal:* {direction} | Barrier: {barrier}")
                        
                        for token in session['tokens']:
                            acc = session['accounts_data'].get(token)
                            if acc and not acc.get("stopped"):
                                amount = round(float(acc["current_stake"]), 2)
                                buy_req = {
                                    "proposal": 1, "amount": amount, "basis": "stake", 
                                    "contract_type": direction, "currency": acc.get("currency", "USD"), 
                                    "duration": 30, "duration_unit": "s", "symbol": "R_100", "barrier": barrier
                                }
                                
                                final_res = execute_trade(token, buy_req)
                                if final_res and "buy" in final_res:
                                    c_id = final_res["buy"]["contract_id"]
                                    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {f"accounts_data.{token}.active_contract": c_id}})
                                    safe_send(chat_id, f"✅ *Confirmed Trade:* {token[:5]}... | ID: `{c_id}`")
                                else:
                                    err = final_res.get("error", {}).get("message", "Execution Error") if final_res else "Network Error"
                                    safe_send(chat_id, f"⚠️ *Failed:* {token[:5]}... | `{err}`")

                        last_processed_minute = now.minute
                        time.sleep(40)
                        
                        # Result Processing
                        current_session = active_sessions_col.find_one({"chat_id": chat_id})
                        for token in current_session['tokens']:
                            acc = current_session['accounts_data'].get(token)
                            if acc.get("active_contract"):
                                result_res = quick_request(token, {"proposal_open_contract": 1, "contract_id": acc["active_contract"]})
                                if result_res: process_result(chat_id, token, result_res)
                    else:
                        last_processed_minute = now.minute
            time.sleep(0.5)
        except: time.sleep(1)

def process_result(chat_id, token, res):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    acc = session['accounts_data'].get(token)
    contract = res.get("proposal_open_contract", {})
    if contract.get("is_expired") == 1:
        profit = float(contract.get("profit", 0))
        if profit > 0:
            new_stake = session["initial_stake"]; new_wins = acc["win_count"] + 1; new_mg = 0; status = "✅ *WIN*"
        else:
            new_stake = acc["current_stake"] * 19; new_wins = acc["win_count"]; new_mg = acc["consecutive_losses"] + 1; status = "❌ *LOSS*"
        
        new_total = acc["total_profit"] + profit
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
            f"accounts_data.{token}.current_stake": new_stake,
            f"accounts_data.{token}.win_count": new_wins,
            f"accounts_data.{token}.consecutive_losses": new_mg,
            f"accounts_data.{token}.total_profit": new_total,
            f"accounts_data.{token}.active_contract": None
        }})
        
        safe_send(chat_id, f"🔍 *Result:* {status}\nProfit: `{profit:.2f}`\nTotal: `{new_total:.2f}`\nMG: {new_mg}/2")
        if new_mg >= 2:
            safe_send(chat_id, f"🛑 *Stop Loss Reached* (2 losses). Clearing session."); active_sessions_col.delete_one({"chat_id": chat_id})

def restore_sessions():
    for sess in active_sessions_col.find({"is_running": True}):
        threading.Thread(target=trade_engine, args=(sess['chat_id'],), daemon=True).start()

# --- ORIGINAL HTML ADMIN PANEL ---
@app.route('/')
def index():
    users = list(users_col.find())
    html = """
    <!DOCTYPE html><html><head><title>Admin Dashboard</title>
    <style>
        body{font-family:Arial; text-align:center; background:#f0f2f5; padding-top: 50px;} 
        table{margin:auto; border-collapse:collapse; width:80%; background:#fff; border-radius:8px; overflow:hidden; box-shadow:0 0 10px rgba(0,0,0,0.1);} 
        th,td{padding:15px; border:1px solid #ddd;} th{background:#333; color:white;}
        form{background:white; padding:20px; display:inline-block; border-radius:8px; margin-bottom:20px; box-shadow:0 0 10px rgba(0,0,0,0.1);}
        input, select, button{padding:10px; margin:5px;} button{background:#007bff; color:white; border:none; cursor:pointer;}
    </style></head>
    <body><h2>User Access Management</h2>
    <form action="/add" method="POST">
        <input type="email" name="email" placeholder="User Email" required>
        <select name="days"><option value="1">1 Day</option><option value="30">30 Days</option><option value="36500">Life Time</option></select>
        <button type="submit">Add User</button>
    </form><br>
    <table><tr><th>User Email</th><th>Expiry Date</th><th>Action</th></tr>
    {% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" style="color:red; text-decoration:none;">Remove</a></td></tr>{% endfor %}
    </table></body></html>
    """
    return render_template_string(html, users=users)

@app.route('/add', methods=['POST'])
def add_user():
    expiry = (datetime.now() + timedelta(days=int(request.form.get('days')))).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower()}, {"$set": {"expiry": expiry}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete_user(email):
    users_col.delete_one({"email": email}); return redirect('/')

# --- TELEGRAM HANDLERS (ENGLISH) ---
@bot.message_handler(commands=['start'])
def start(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "Welcome! Enter Email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    email = m.text.strip().lower()
    user = users_col.find_one({"email": email})
    if user and datetime.strptime(user['expiry'], "%Y-%m-%d") > datetime.now():
        active_sessions_col.insert_one({"chat_id": m.chat.id, "email": email, "is_running": False})
        bot.send_message(m.chat.id, "✅ OK! Enter API Token(s) (comma separated):")
        bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 Access Denied.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [t.strip() for t in m.text.split(",")]}})
    bot.send_message(m.chat.id, "Enter Initial Stake:")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    try:
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text)}})
        bot.send_message(m.chat.id, "Enter Target Profit (TP):")
        bot.register_next_step_handler(m, save_tp)
    except: bot.send_message(m.chat.id, "Invalid number.")

def save_tp(m):
    try:
        tp = float(m.text)
        session = active_sessions_col.find_one({"chat_id": m.chat.id})
        accs_data = {}
        for t in session["tokens"]:
            curr = get_account_currency(t)
            accs_data[t] = {"currency": curr, "current_stake": session["initial_stake"], "win_count": 0, "total_profit": 0.0, "consecutive_losses": 0, "active_contract": None}
            safe_send(m.chat.id, f"💳 Account {t[:5]} detected: **{curr}**")

        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tp_goal": tp, "is_running": True, "accounts_data": accs_data}})
        bot.send_message(m.chat.id, f"🚀 Bot Started! Persistence Active.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()
    except: bot.send_message(m.chat.id, "Invalid TP value.")

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "🛑 Session cleared and stopped.")

if __name__ == '__main__':
    restore_sessions()
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port, use_reloader=False), daemon=True).start()
    bot.infinity_polling()
