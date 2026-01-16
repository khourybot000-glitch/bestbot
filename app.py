import websocket, json, time, multiprocessing, os
from flask import Flask, render_template_string, request
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION WITH UPDATED TOKEN ---
TOKEN = "8433565422:AAGlL3-HlvwC2buSvOMNEplL2nHfq_-DsCY"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN)
db_client = MongoClient(MONGO_URI)
db = db_client['trading_bot']
sessions_col = db['active_sessions'] 

manager = multiprocessing.Manager()

def get_initial_state():
    return {
        "email": "", "api_token": "", "initial_stake": 0.0, "current_stake": 0.0, "tp": 0.0, 
        "currency": "USD", "is_running": False, "chat_id": None,
        "total_profit": 0.0, "win_count": 0, "loss_count": 0, "is_trading": False,
        "consecutive_losses": 0, "active_contract": None, "start_time": 0
    }

state = manager.dict(get_initial_state())

# --- AUTH LOGIC ---
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

# --- ADMIN PANEL HTML ---
@app.route('/')
def home():
    emails = []
    if os.path.exists("user_ids.txt"):
        with open("user_ids.txt", "r") as f:
            emails = [line.strip() for line in f.readlines() if line.strip()]
    
    html = """
    <body style="font-family:sans-serif; text-align:center; padding:50px;">
        <h2>User Management</h2>
        <table border="1" style="margin:auto; width:80%">
            <tr><th>Email</th><th>Action</th></tr>
            {% for email in emails %}
            <tr>
                <td>{{ email }}</td>
                <td>
                    <form method="POST" action="/update_expiry">
                        <input type="hidden" name="email" value="{{ email }}">
                        <select name="duration">
                            <option value="1_days">1 Day</option>
                            <option value="30_days">30 Days</option>
                            <option value="999_days">Lifetime</option>
                        </select>
                        <button type="submit">Activate</button>
                    </form>
                </td>
            </tr>
            {% endfor %}
        </table>
    </body>
    """
    return render_template_string(html, emails=emails)

@app.route('/update_expiry', methods=['POST'])
def update_expiry():
    email = request.form.get('email').lower()
    days = int(request.form.get('duration').split('_')[0])
    exp = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    sessions_col.update_one({"email": email}, {"$set": {"expiry_date": exp}}, upsert=True)
    return f"Activated {email} until {exp} <br> <a href='/'>Back</a>"

# --- CORE LOGIC ---
def get_second_decimal(price):
    try:
        s_price = f"{price:.2f}"
        return int(s_price.split('.')[1][1])
    except: return None

def reset_and_stop(state_proxy, text):
    if state_proxy["chat_id"]:
        try:
            report = (f"🛑 **SESSION ENDED**\n━━━━━━━━━━━━━━\n"
                      f"✅ Wins: {state_proxy['win_count']} | ❌ Losses: {state_proxy['loss_count']}\n"
                      f"💰 Final Profit: {state_proxy['total_profit']:.2f}\n📝 Reason: {text}")
            bot.send_message(state_proxy["chat_id"], report, reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰'))
        except: pass
    initial = get_initial_state()
    for k, v in initial.items(): state_proxy[k] = v

def open_trade(state_proxy, ws_persistent):
    if not is_authorized(state_proxy["email"]):
        reset_and_stop(state_proxy, "Subscription Expired.")
        return False
    try:
        req = {
            "proposal": 1, "amount": state_proxy["current_stake"], "basis": "stake", 
            "contract_type": "DIGITOVER", "barrier": "1", "currency": state_proxy["currency"], 
            "duration": 1, "duration_unit": "t", "symbol": "R_100"
        }
        ws_persistent.send(json.dumps(req))
        res = json.loads(ws_persistent.recv())
        prop = res.get("proposal")
        if prop:
            ws_persistent.send(json.dumps({"buy": prop["id"], "price": state_proxy["current_stake"]}))
            buy_res = json.loads(ws_persistent.recv())
            if "buy" in buy_res:
                state_proxy["active_contract"] = buy_res["buy"]["contract_id"]
                state_proxy["start_time"] = time.time()
                state_proxy["is_trading"] = True
                bot.send_message(state_proxy["chat_id"], "🎯 Pattern Found! Entry: Over 1")
                return True
    except: pass
    return False

def check_result(state_proxy):
    if not state_proxy["active_contract"] or time.time() - state_proxy["start_time"] < 8:
        return
    try:
        ws_temp = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws_temp.send(json.dumps({"authorize": state_proxy["api_token"]}))
        ws_temp.recv()
        ws_temp.send(json.dumps({"proposal_open_contract": 1, "contract_id": state_proxy["active_contract"]}))
        res = json.loads(ws_temp.recv())
        ws_temp.close()
        contract = res.get("proposal_open_contract", {})
        if contract.get("is_expired") == 1:
            state_proxy["active_contract"] = None 
            profit = float(contract.get("profit", 0))
            new_state_local = dict(state_proxy)
            if profit > 0:
                new_state_local["total_profit"] += profit
                new_state_local["win_count"] += 1
                new_state_local["consecutive_losses"] = 0
                new_state_local["current_stake"] = new_state_local["initial_stake"]
                icon = "✅ WIN"
            else:
                new_state_local["total_profit"] += profit
                new_state_local["loss_count"] += 1
                new_state_local["consecutive_losses"] += 1
                new_state_local["current_stake"] *= 9
                icon = "❌ LOSS"

            for k,v in new_state_local.items(): state_proxy[k] = v
            bot.send_message(state_proxy["chat_id"], f"{icon} ({profit:.2f})\n💰 Net: {state_proxy['total_profit']:.2f}")
            state_proxy["is_trading"] = False
            if state_proxy["total_profit"] >= state_proxy["tp"]:
                reset_and_stop(state_proxy, "TP Reached.")
    except: pass

def main_loop(state_proxy):
    ws_persistent = None
    while True:
        try:
            if state_proxy["is_running"] and not state_proxy["is_trading"]:
                if ws_persistent is None:
                    ws_persistent = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
                    ws_persistent.send(json.dumps({"authorize": state_proxy["api_token"]}))
                    ws_persistent.recv()
                
                ws_persistent.send(json.dumps({"ticks_history": "R_100", "count": 2, "end": "latest", "style": "ticks"}))
                prices = json.loads(ws_persistent.recv()).get("history", {}).get("prices", [])
                
                if len(prices) >= 2:
                    d1 = get_second_decimal(prices[0])
                    d2 = get_second_decimal(prices[1])
                    if (d1 == 8 and d2 == 9) or (d1 == 9 and d2 == 8):
                        open_trade(state_proxy, ws_persistent)
            elif state_proxy["is_trading"]:
                if ws_persistent: ws_persistent.close(); ws_persistent = None
                check_result(state_proxy)
            time.sleep(0.5)
        except:
            if ws_persistent: ws_persistent.close()
            ws_persistent = None
            time.sleep(1)

# --- BOT HANDLERS ---
@bot.message_handler(commands=['start'])
def welcome(m):
    bot.send_message(m.chat.id, "👋 Welcome! Enter your authorized email:")
    bot.register_next_step_handler(m, login)

def login(m):
    email = m.text.strip().lower()
    if is_authorized(email):
        state["email"] = email
        state["chat_id"] = m.chat.id
        bot.send_message(m.chat.id, "✅ Success!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰'))
    else:
        bot.send_message(m.chat.id, "🚫 Unauthorized.")

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def ask_token(m):
    state["currency"] = "USD" if "Demo" in m.text else "tUSDT"
    bot.send_message(m.chat.id, "API Token:")
    bot.register_next_step_handler(m, save_token)

def save_token(m):
    state["api_token"] = m.text.strip()
    bot.send_message(m.chat.id, "Stake:")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    try:
        val = float(m.text)
        state["initial_stake"] = val
        state["current_stake"] = val
        bot.send_message(m.chat.id, "TP:")
        bot.register_next_step_handler(m, save_tp)
    except: pass

def save_tp(m):
    try:
        state["tp"] = float(m.text)
        state["is_running"] = True
        bot.send_message(m.chat.id, "🚀 Running!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
    except: pass

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_all(m): reset_and_stop(state, "Manual Stop.")

if __name__ == '__main__':
    multiprocessing.Process(target=main_loop, args=(state,), daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    multiprocessing.Process(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    bot.infinity_polling()
