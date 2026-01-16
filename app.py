import websocket, json, time, multiprocessing, os
from flask import Flask, render_template_string, request
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION WITH UPDATED TOKEN ---
TOKEN = "8433565422:AAEBZnQIDBkXcpU8xR8tC04ssiEylUdYAy8"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN)
db_client = MongoClient(MONGO_URI)
db = db_client['trading_bot']
sessions_col = db['active_sessions'] 

manager = multiprocessing.Manager()
users_states = manager.dict()

# --- THE CORE LOGIC FROM YOUR OLD CODE ---
def get_second_decimal(price):
    try:
        # تحويل السعر لنص بخانتين عشريتين
        s_price = "{:.2f}".format(price)
        # التقسيم عند النقطة وأخذ الخانة الثانية من الجزء العشري
        return int(s_price.split('.')[1][1])
    except:
        return None

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

# --- ADMIN PANEL HTML ---
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Admin Panel</title>
<style>
    body { font-family: 'Segoe UI', sans-serif; background: #f0f2f5; padding: 20px; text-align: center; }
    .container { background: white; padding: 20px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); max-width: 700px; margin: auto; }
    table { width: 100%; border-collapse: collapse; margin-top: 20px; }
    th, td { padding: 12px; border-bottom: 1px solid #eee; }
    th { background: #007bff; color: white; }
    .btn-upd { background: #28a745; color: white; border: none; padding: 8px 12px; border-radius: 4px; cursor: pointer; }
    .btn-stop { background: #dc3545; color: white; border: none; padding: 8px 12px; border-radius: 4px; cursor: pointer; }
</style>
</head>
<body>
    <div class="container">
        <h2>👥 User Management</h2>
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
                            <option value="30_days">30 Days</option>
                            <option value="lifetime">Lifetime ∞</option>
                        </select>
                    </td>
                    <td>
                        <button type="submit" name="action" value="update" class="btn-upd">Set</button>
                        <button type="submit" name="action" value="cancel" class="btn-stop">Off</button>
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
        expiry_str = "2000-01-01 00:00"
    else:
        now = datetime.now()
        durations = {"1_hours": 1/24, "1_days": 1, "30_days": 30, "lifetime": 36500}
        exp = now + timedelta(days=durations.get(choice, 1))
        expiry_str = exp.strftime("%Y-%m-%d %H:%M")
    sessions_col.update_one({"email": email}, {"$set": {"expiry_date": expiry_str}}, upsert=True)
    return f"<h3>Updated!</h3><br><a href='/'>Back</a>"

# --- ANALYSIS & TRADING ENGINE ---
def global_analysis():
    ws = None
    last_d2 = None
    while True:
        try:
            if ws is None:
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
                ws.send(json.dumps({"ticks": "R_100"}))
            
            msg = json.loads(ws.recv())
            if "tick" in msg:
                price = msg["tick"]["quote"]
                current_d2 = get_second_decimal(price)
                
                if last_d2 is not None and current_d2 is not None:
                    # Pattern check: 8-9 or 9-8
                    if (last_d2 == 8 and current_d2 == 9) or (last_d2 == 9 and current_d2 == 8):
                        for cid in list(users_states.keys()):
                            u = users_states[cid]
                            if u.get("is_running") and not u.get("is_trading"):
                                if is_authorized(u.get("email")):
                                    multiprocessing.Process(target=execute_trade, args=(cid,)).start()
                last_d2 = current_d2
        except:
            if ws: ws.close()
            ws = None; last_d2 = None; time.sleep(1)

def execute_trade(chat_id):
    if chat_id not in users_states: return
    state = users_states[chat_id]
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": state["api_token"]}))
        auth_res = json.loads(ws.recv())
        if "error" in auth_res:
            bot.send_message(chat_id, f"❌ API Error: {auth_res['error']['message']}")
            ws.close(); return

        req = {
            "proposal": 1, "amount": state["current_stake"], "basis": "stake", 
            "contract_type": "DIGITOVER", "barrier": "1", "currency": state["currency"], 
            "duration": 1, "duration_unit": "t", "symbol": "R_100"
        }
        ws.send(json.dumps(req))
        prop = json.loads(ws.recv()).get("proposal")
        if prop:
            ws.send(json.dumps({"buy": prop["id"], "price": state["current_stake"]}))
            buy_res = json.loads(ws.recv())
            if "buy" in buy_res:
                new_state = users_states[chat_id].copy()
                new_state["is_trading"] = True
                new_state["active_contract"] = buy_res["buy"]["contract_id"]
                users_states[chat_id] = new_state
                bot.send_message(chat_id, "🎯 Pattern Spotted! Order Sent.")
                time.sleep(8)
                check_result(chat_id)
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
            new_state = users_states[chat_id].copy()
            new_state["is_trading"] = False
            if profit > 0:
                new_state["win_count"] += 1; new_state["current_stake"] = new_state["initial_stake"]; icon = "✅ WIN"
            else:
                new_state["loss_count"] += 1; new_state["current_stake"] *= 9; icon = "❌ LOSS"
            
            new_state["total_profit"] += profit; users_states[chat_id] = new_state
            stats = (f"{icon} ({profit:.2f})\n💰 Net: {new_state['total_profit']:.2f}\n"
                     f"W: {new_state['win_count']} | L: {new_state['loss_count']}")
            
            if new_state["total_profit"] >= new_state["tp"]:
                bot.send_message(chat_id, "🏆 Target Profit Reached!")
                clear_user_session(chat_id, new_state["email"]); return
            sync_to_cloud(chat_id); bot.send_message(chat_id, stats)
    except: pass

# --- TELEGRAM COMMANDS ---
@bot.message_handler(commands=['start'])
def start(m):
    user_data = sessions_col.find_one({"chat_id": m.chat.id})
    if user_data and is_authorized(user_data['email']):
        users_states[m.chat.id] = user_data
        bot.send_message(m.chat.id, "Select mode:", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰'))
    else:
        bot.send_message(m.chat.id, "👋 Welcome! Enter your authorized email:")
        bot.register_next_step_handler(m, login_process)

def login_process(m):
    email = m.text.strip().lower()
    if is_authorized(email):
        config = {"chat_id": m.chat.id, "email": email, "api_token": "", "initial_stake": 0.0, "current_stake": 0.0, "tp": 0.0, "currency": "USD", "is_running": False, "is_trading": False, "total_profit": 0.0, "win_count": 0, "loss_count": 0}
        users_states[m.chat.id] = config; sync_to_cloud(m.chat.id)
        bot.send_message(m.chat.id, "✅ Logged In! Choose mode:", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰'))
    else: bot.send_message(m.chat.id, "🚫 Unauthorized email.")

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def mode(m):
    if m.chat.id not in users_states: return start(m)
    new_state = users_states[m.chat.id].copy(); new_state["currency"] = "USD" if "Demo" in m.text else "tUSDT"
    users_states[m.chat.id] = new_state
    bot.send_message(m.chat.id, "Enter API Token:"); bot.register_next_step_handler(m, save_token)

def save_token(m):
    new_state = users_states[m.chat.id].copy(); new_state["api_token"] = m.text.strip(); users_states[m.chat.id] = new_state
    bot.send_message(m.chat.id, "Stake:"); bot.register_next_step_handler(m, save_stake)

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
        bot.send_message(m.chat.id, "🚀 Running! Monitoring patterns...", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
    except: pass

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_call(m):
    email = users_states[m.chat.id].get("email") if m.chat.id in users_states else ""
    clear_user_session(m.chat.id, email)
    bot.send_message(m.chat.id, "🛑 Stopped.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰'))

if __name__ == '__main__':
    for doc in sessions_col.find(): 
        if "chat_id" in doc: users_states[doc['chat_id']] = doc
    port = int(os.environ.get("PORT", 10000))
    multiprocessing.Process(target=global_analysis, daemon=True).start()
    multiprocessing.Process(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    bot.infinity_polling()
