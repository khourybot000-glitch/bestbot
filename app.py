import websocket, json, time, multiprocessing, os
from flask import Flask, render_template_string, request
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION WITH NEW TOKEN ---
TOKEN = "8433565422:AAG-FNtP2oUcp1SaYPzmgl3WHEFkN3TUkxk"
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

# --- AUTHORIZATION LOGIC ---
def is_authorized(email):
    email = email.strip().lower()
    if not os.path.exists("user_ids.txt"): 
        with open("user_ids.txt", "w") as f: f.write("")
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

# --- WIPE SESSION DATA ---
def reset_and_stop(state_proxy, text):
    if state_proxy["chat_id"]:
        try:
            report = (f"🛑 **SESSION ENDED & DATA WIPED**\n"
                      f"━━━━━━━━━━━━━━\n"
                      f"📊 Wins: `{state_proxy['win_count']}` | Losses: `{state_proxy['loss_count']}`\n"
                      f"💰 Net Profit: **{state_proxy['total_profit']:.2f}**\n"
                      f"📝 Reason: {text}\n"
                      f"━━━━━━━━━━━━━━\n"
                      f"⚠️ *Note: Tokens and settings have been cleared for safety.*")
            bot.send_message(state_proxy["chat_id"], report, parse_mode="Markdown", reply_markup=types.ReplyKeyboardRemove())
            bot.send_message(state_proxy["chat_id"], "To restart, send /start and log in again.")
        except: pass
    
    initial = get_initial_state()
    for k, v in initial.items():
        state_proxy[k] = v

def get_second_decimal(price):
    try:
        return int(f"{price:.2f}".split('.')[1][1])
    except: return None

# --- TRADE RESULT CHECKER ---
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
            profit = float(contract.get("profit", 0))
            if profit > 0:
                state_proxy["win_count"] += 1
                state_proxy["consecutive_losses"] = 0
                state_proxy["current_stake"] = state_proxy["initial_stake"]
                icon = "✅ WIN"
            else:
                state_proxy["loss_count"] += 1
                state_proxy["consecutive_losses"] += 1
                state_proxy["current_stake"] *= 9
                icon = "❌ LOSS"
            
            state_proxy["total_profit"] += profit
            state_proxy["active_contract"] = None 
            state_proxy["is_trading"] = False

            stats_msg = (f"{icon} (**{profit:.2f}**)\n"
                         f"━━━━━━━━━━━━━━\n"
                         f"📊 Wins: `{state_proxy['win_count']}` | Losses: `{state_proxy['loss_count']}`\n"
                         f"🔄 Consecutive: `{state_proxy['consecutive_losses']}/2`\n"
                         f"💰 Total Profit: **{state_proxy['total_profit']:.2f}**")
            bot.send_message(state_proxy["chat_id"], stats_msg, parse_mode="Markdown")

            if state_proxy["consecutive_losses"] >= 2:
                reset_and_stop(state_proxy, "Stop Loss: 2 Consecutive Losses.")
            elif state_proxy["total_profit"] >= state_proxy["tp"]:
                reset_and_stop(state_proxy, "Target Profit Reached! 🏆")
    except: pass

# --- MAIN ENGINE ---
def main_loop(state_proxy):
    ws_persistent = None
    while True:
        try:
            if state_proxy["is_running"] and not state_proxy["is_trading"]:
                if ws_persistent is None:
                    ws_persistent = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
                    ws_persistent.send(json.dumps({"authorize": state_proxy["api_token"]}))
                    ws_persistent.recv()
                
                ws_persistent.send(json.dumps({"ticks_history": "R_100", "count": 3, "end": "latest", "style": "ticks"}))
                prices = json.loads(ws_persistent.recv()).get("history", {}).get("prices", [])
                
                if len(prices) >= 3:
                    d1, d2, d3 = [get_second_decimal(p) for p in prices]
                    
                    # Logic: T1(0,1) AND T2(0,1) AND T3(8,9)
                    if d1 in [0, 1] and d2 in [0, 1] and d3 in [8, 9]:
                        if not is_authorized(state_proxy["email"]):
                            reset_and_stop(state_proxy, "Subscription Expired.")
                            continue
                        
                        req = {"proposal": 1, "amount": state_proxy["current_stake"], "basis": "stake", 
                               "contract_type": "DIGITOVER", "barrier": "1", "currency": state_proxy["currency"], 
                               "duration": 1, "duration_unit": "t", "symbol": "R_100"}
                        ws_persistent.send(json.dumps(req))
                        res_p = json.loads(ws_persistent.recv()).get("proposal")
                        if res_p:
                            ws_persistent.send(json.dumps({"buy": res_p["id"], "price": state_proxy["current_stake"]}))
                            res_b = json.loads(ws_persistent.recv())
                            if "buy" in res_b:
                                state_proxy["active_contract"] = res_b["buy"]["contract_id"]
                                state_proxy["start_time"] = time.time()
                                state_proxy["is_trading"] = True
                                bot.send_message(state_proxy["chat_id"], f"🎯 Pattern: {d1}-{d2}-{d3} | Entry: Over 1")
                                ws_persistent.close(); ws_persistent = None
            elif state_proxy["is_trading"]:
                check_result(state_proxy)
            time.sleep(0.5)
        except:
            if ws_persistent: ws_persistent.close()
            ws_persistent = None; time.sleep(1)

# --- ADMIN PANEL (FLASK) ---
@app.route('/')
def home():
    emails = []
    if os.path.exists("user_ids.txt"):
        with open("user_ids.txt", "r") as f:
            emails = [line.strip() for line in f.readlines() if line.strip()]
    html = """
    <body style="font-family:sans-serif; text-align:center; padding:50px; background:#f4f7f6;">
        <div style="background:white; display:inline-block; padding:30px; border-radius:15px; box-shadow:0 4px 15px rgba(0,0,0,0.1)">
            <h2>👥 User Management Panel</h2>
            <table border="1" style="margin:auto; width:100%; border-collapse:collapse;">
                <tr style="background:#007bff; color:white;"><th>Email</th><th>Subscription</th></tr>
                {% for email in emails %}
                <tr>
                    <td style="padding:10px;">{{ email }}</td>
                    <td style="padding:10px;">
                        <form method="POST" action="/update_expiry">
                            <input type="hidden" name="email" value="{{ email }}">
                            <select name="duration">
                                <option value="1">1 Day</option>
                                <option value="30">30 Days</option>
                                <option value="36500">Lifetime (100y)</option>
                            </select>
                            <button type="submit">Activate</button>
                        </form>
                    </td>
                </tr>
                {% endfor %}
            </table>
        </div>
    </body>
    """
    return render_template_string(html, emails=emails)

@app.route('/update_expiry', methods=['POST'])
def update_expiry():
    email, days = request.form.get('email').lower(), int(request.form.get('duration'))
    exp = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    sessions_col.update_one({"email": email}, {"$set": {"expiry_date": exp}}, upsert=True)
    return f"Success: {email} activated until {exp} <br><a href='/'>Go Back</a>"

# --- TELEGRAM BOT HANDLERS ---
@bot.message_handler(commands=['start'])
def welcome(m):
    bot.send_message(m.chat.id, "👋 Welcome! Please enter your authorized email:")
    bot.register_next_step_handler(m, login)

def login(m):
    email = m.text.strip().lower()
    if is_authorized(email):
        state["email"] = email; state["chat_id"] = m.chat.id
        bot.send_message(m.chat.id, "✅ Access Granted!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰'))
    else: bot.send_message(m.chat.id, "🚫 Unauthorized email.")

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def ask_token(m):
    state["currency"] = "USD" if "Demo" in m.text else "tUSDT"
    bot.send_message(m.chat.id, "Enter API Token:")
    bot.register_next_step_handler(m, save_token)

def save_token(m):
    state["api_token"] = m.text.strip()
    bot.send_message(m.chat.id, "Initial Stake Amount:")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    try:
        val = float(m.text)
        state["initial_stake"] = val; state["current_stake"] = val
        bot.send_message(m.chat.id, "Target Profit (TP):")
        bot.register_next_step_handler(m, save_tp)
    except: bot.send_message(m.chat.id, "Invalid number.")

def save_tp(m):
    try:
        state["tp"] = float(m.text); state["is_running"] = True
        bot.send_message(m.chat.id, "🚀 Running! Monitoring 3-tick patterns...", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
    except: bot.send_message(m.chat.id, "Invalid number.")

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_all(m): reset_and_stop(state, "Manual Stop Triggered.")

if __name__ == '__main__':
    multiprocessing.Process(target=main_loop, args=(state,), daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    multiprocessing.Process(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    bot.infinity_polling()
