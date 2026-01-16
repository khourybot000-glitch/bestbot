import websocket, json, time, multiprocessing, os
from flask import Flask, render_template_string, request
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION WITH NEW TOKEN ---
TOKEN = "8433565422:AAE4gElgK7ERAr2hIJRCRNVozLi0-5z_2NM"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN)
db_client = MongoClient(MONGO_URI)
db = db_client['trading_bot']
sessions_col = db['active_sessions'] 

manager = multiprocessing.Manager()
users_states = manager.dict()

# --- UTILITIES ---
def clear_user_session(chat_id, email):
    email = email.lower()
    user_data = sessions_col.find_one({"email": email})
    expiry = user_data.get("expiry_date") if user_data else None
    if chat_id in users_states: del users_states[chat_id]
    sessions_col.delete_one({"chat_id": chat_id})
    if expiry: sessions_col.update_one({"email": email}, {"$set": {"expiry_date": expiry}}, upsert=True)

def is_authorized(email):
    email = email.strip().lower()
    if not os.path.exists("user_ids.txt"): return False
    with open("user_ids.txt", "r") as f:
        auth_emails = [line.strip().lower() for line in f.readlines()]
    if email not in auth_emails: return False
    user_data = sessions_col.find_one({"email": email})
    if user_data and "expiry_date" in user_data:
        try:
            expiry_time = datetime.strptime(user_data["expiry_date"], "%Y-%m-%d %H:%M")
            return datetime.now() <= expiry_time
        except: return False
    return False

def sync_to_cloud(chat_id):
    if chat_id in users_states:
        data = dict(users_states[chat_id])
        sessions_col.update_one({"chat_id": chat_id}, {"$set": data}, upsert=True)

# --- ADMIN PANEL ---
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
    body { font-family: sans-serif; background: #f4f7f9; padding: 20px; text-align: center; }
    .container { background: white; padding: 30px; border-radius: 15px; box-shadow: 0 5px 20px rgba(0,0,0,0.05); max-width: 800px; margin: auto; }
    table { width: 100%; border-collapse: collapse; margin-top: 20px; }
    th, td { padding: 12px; border-bottom: 1px solid #eee; }
    th { background: #4a5568; color: white; }
    .btn-upd { background: #38a169; color: white; border: none; padding: 8px 15px; border-radius: 5px; cursor: pointer; }
    .btn-stop { background: #e53e3e; color: white; border: none; padding: 8px 15px; border-radius: 5px; cursor: pointer; }
</style>
</head>
<body>
    <div class="container">
        <h2>🛠 Admin Management Panel</h2>
        <table>
            <tr><th>Email</th><th>Plan</th><th>Action</th></tr>
            {% for email in emails %}
            <tr>
                <form method="POST" action="/update_expiry">
                    <td>{{ email }}<input type="hidden" name="email" value="{{ email }}"></td>
                    <td>
                        <select name="duration_choice">
                            <option value="1_hours">1 Hour</option>
                            <option value="1_days">1 Day</option>
                            <option value="2_days">2 Days</option>
                            <option value="5_days">5 Days</option>
                            <option value="30_days">30 Days</option>
                            <option value="lifetime">Lifetime ∞</option>
                        </select>
                    </td>
                    <td>
                        <button type="submit" name="action" value="update" class="btn-upd">SET</button>
                        <button type="submit" name="action" value="cancel" class="btn-stop">OFF</button>
                    </td>
                </form>
            </tr>
            {% endfor %}
        </table>
    </div>
</body></html>
"""

@app.route('/')
def admin_panel():
    emails = []
    if os.path.exists("user_ids.txt"):
        with open("user_ids.txt", "r") as f:
            emails = [line.strip() for line in f.readlines() if line.strip()]
    return render_template_string(ADMIN_HTML, emails=emails)

@app.route('/update_expiry', methods=['POST'])
def update_expiry():
    email = request.form.get('email').lower()
    action = request.form.get('action')
    choice = request.form.get('duration_choice')
    if action == "cancel":
        expiry_str = "2000-01-01 00:00"; msg = "Subscription Stopped."
    else:
        now = datetime.now()
        durations = {"1_hours": 1/24, "1_days": 1, "2_days": 2, "5_days": 5, "30_days": 30, "lifetime": 36500}
        exp = now + timedelta(days=durations.get(choice, 1))
        expiry_str = exp.strftime("%Y-%m-%d %H:%M")
        msg = f"Active until {expiry_str}"
    sessions_col.update_one({"email": email}, {"$set": {"expiry_date": expiry_str}}, upsert=True)
    return f"<h3>{msg}</h3><br><a href='/'>Back</a>"

# --- CORE ANALYSIS ENGINE (D2 LOGIC) ---
def global_analysis():
    ws = None
    while True:
        try:
            if ws is None: ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
            ws.send(json.dumps({"ticks_history": "R_100", "count": 2, "end": "latest", "style": "ticks"}))
            res = json.loads(ws.recv()).get("history", {}).get("prices", [])
            
            if len(res) >= 2:
                # D2 Logic: Extract the 2nd digit after decimal
                def get_d2(price):
                    return int("{:.2f}".format(price)[-1])
                
                t1d2 = get_d2(res[0]) 
                t2d2 = get_d2(res[1]) 
                
                # Condition: (8-9) or (9-8)
                if (t1d2 == 8 and t2d2 == 9) or (t1d2 == 9 and t2d2 == 8):
                    for cid in list(users_states.keys()):
                        u = users_states[cid]
                        if u.get("is_running") and not u.get("is_trading") and is_authorized(u.get("email")):
                            multiprocessing.Process(target=execute_trade, args=(cid,)).start()
            time.sleep(0.3)
        except:
            if ws: ws.close()
            ws = None; time.sleep(2)

def execute_trade(chat_id):
    if chat_id not in users_states: return
    state = users_states[chat_id]
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": state["api_token"]}))
        auth_res = json.loads(ws.recv())
        if "error" in auth_res:
             bot.send_message(chat_id, "❌ API Token Error! Please check your token settings.")
             ws.close(); return

        req = {"proposal": 1, "amount": state["current_stake"], "basis": "stake", 
               "contract_type": "DIGITOVER", "barrier": "1", "currency": state["currency"], 
               "duration": 1, "duration_unit": "t", "symbol": "R_100"}
        ws.send(json.dumps(req))
        prop = json.loads(ws.recv()).get("proposal")
        if prop:
            ws.send(json.dumps({"buy": prop["id"], "price": state["current_stake"]}))
            buy_res = json.loads(ws.recv())
            if "buy" in buy_res:
                new_state = users_states[chat_id].copy(); new_state["is_trading"] = True
                new_state["active_contract"] = buy_res["buy"]["contract_id"]; users_states[chat_id] = new_state
                bot.send_message(chat_id, "🎯 Pattern Found (D2)! Trade Executed.")
                time.sleep(8); check_result(chat_id)
        ws.close()
    except: pass

def check_result(chat_id):
    if chat_id not in users_states: return
    state = users_states[chat_id]
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": state["api_token"]})); ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": state["active_contract"]}))
        res = json.loads(ws.recv()).get("proposal_open_contract", {})
        ws.close()
        if res.get("is_expired") == 1:
            profit = float(res.get("profit", 0))
            new_state = users_states[chat_id].copy(); new_state["is_trading"] = False
            if profit > 0:
                new_state["win_count"] += 1; new_state["current_stake"] = new_state["initial_stake"]; icon = "✅ WIN"
            else:
                new_state["loss_count"] += 1; new_state["current_stake"] *= 9; icon = "❌ LOSS"
            
            new_state["total_profit"] += profit; users_states[chat_id] = new_state
            stats = (f"{icon} ({profit:.2f})\n💰 Total: {new_state['total_profit']:.2f}\n"
                     f"W: {new_state['win_count']} | L: {new_state['loss_count']}")
            
            if new_state["total_profit"] >= new_state["tp"]:
                bot.send_message(chat_id, f"🏆 Target Reached! Status Cleared.")
                clear_user_session(chat_id, new_state["email"]); return
            
            sync_to_cloud(chat_id); bot.send_message(chat_id, stats)
    except: pass

# --- TELEGRAM HANDLERS ---
@bot.message_handler(commands=['start'])
def start(m):
    user_data = sessions_col.find_one({"chat_id": m.chat.id})
    if user_data and is_authorized(user_data['email']):
        users_states[m.chat.id] = user_data
        bot.send_message(m.chat.id, "Welcome back! Choose account:", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰'))
    else:
        bot.send_message(m.chat.id, "👋 Hello! Enter your authorized email:")
        bot.register_next_step_handler(m, login_process)

def login_process(m):
    email = m.text.strip().lower()
    if is_authorized(email):
        config = {"chat_id": m.chat.id, "email": email, "api_token": "", "initial_stake": 0.0, "current_stake": 0.0, "tp": 0.0, "currency": "USD", "is_running": False, "is_trading": False, "total_profit": 0.0, "win_count": 0, "loss_count": 0}
        users_states[m.chat.id] = config; sync_to_cloud(m.chat.id)
        bot.send_message(m.chat.id, "✅ Login Success! Choose mode:", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰'))
    else: bot.send_message(m.chat.id, "🚫 Email not authorized.")

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def mode(m):
    if m.chat.id not in users_states: return start(m)
    new_state = users_states[m.chat.id].copy(); new_state["currency"] = "USD" if "Demo" in m.text else "tUSDT"
    users_states[m.chat.id] = new_state
    bot.send_message(m.chat.id, "API Token:"); bot.register_next_step_handler(m, save_token)

def save_token(m):
    new_state = users_states[m.chat.id].copy(); new_state["api_token"] = m.text.strip(); users_states[m.chat.id] = new_state
    bot.send_message(m.chat.id, "Initial Stake:"); bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    try:
        new_state = users_states[m.chat.id].copy(); val = float(m.text)
        new_state["initial_stake"] = val; new_state["current_stake"] = val; users_states[m.chat.id] = new_state
        bot.send_message(m.chat.id, "Target Profit:"); bot.register_next_step_handler(m, save_tp)
    except: pass

def save_tp(m):
    try:
        new_state = users_states[m.chat.id].copy(); new_state["tp"] = float(m.text); new_state["is_running"] = True
        users_states[m.chat.id] = new_state; sync_to_cloud(m.chat.id)
        bot.send_message(m.chat.id, "🚀 Running! Monitoring D2 patterns...", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
    except: pass

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_call(m):
    email = users_states[m.chat.id].get("email") if m.chat.id in users_states else ""
    clear_user_session(m.chat.id, email)
    bot.send_message(m.chat.id, "🛑 Bot Stopped. Statistics cleared.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰'))

if __name__ == '__main__':
    for doc in sessions_col.find(): 
        if "chat_id" in doc: users_states[doc['chat_id']] = doc
    port = int(os.environ.get("PORT", 10000))
    multiprocessing.Process(target=global_analysis, daemon=True).start()
    multiprocessing.Process(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    bot.infinity_polling()
