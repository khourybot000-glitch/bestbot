import websocket, json, time, multiprocessing, os
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
# Updated Token as requested
TOKEN = "8433565422:AAFvyupJ6fPjW9tFNpv66ZKZKeoaDBar58k"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']

manager = multiprocessing.Manager()

def get_initial_state():
    return {
        "email": "", "api_token": "", "initial_stake": 0.0, "current_stake": 0.0, "tp": 0.0, 
        "currency": "USD", "is_running": False, "chat_id": None,
        "total_profit": 0.0, "win_count": 0, "loss_count": 0, "is_trading": False,
        "consecutive_losses": 0, "active_contract": None, "start_time": 0
    }

state = manager.dict(get_initial_state())

# --- UTILS ---
def round_stake(value):
    return round(float(value), 2)

def get_ws_connection(api_token):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": api_token}))
        res = json.loads(ws.recv())
        if "authorize" in res: return ws
        ws.close()
    except: return None
    return None

# --- TRADING LOGIC ---
def analyze_price_difference(ticks):
    if len(ticks) < 15: return None
    diff = ticks[-1] - ticks[-15]
    
    # REVERSE LOGIC:
    # If Diff >= 1 (Rising) -> Entry PUT
    if diff >= 1.5: 
        return "PUT"
    # If Diff <= -1 (Falling) -> Entry CALL
    elif diff <= -1.5: 
        return "CALL"
    return None

def execute_trade(state_proxy, ws, direction):
    amount = round_stake(state_proxy["current_stake"])
    # Barrier offset 1.4
    bar = "-1.5" if direction == "CALL" else "+1.5"
    
    req = {
        "proposal": 1, "amount": amount, "basis": "stake", 
        "contract_type": direction, "currency": state_proxy["currency"], 
        "duration": 30, "duration_unit": "s", 
        "symbol": "R_100", "barrier": bar
    }
    ws.send(json.dumps(req))
    res = json.loads(ws.recv())
    prop = res.get("proposal")
    
    if prop:
        ws.send(json.dumps({"buy": prop["id"], "price": amount}))
        buy_data = json.loads(ws.recv())
        if "buy" in buy_data:
            state_proxy["active_contract"] = buy_data["buy"]["contract_id"]
            state_proxy["start_time"] = time.time()
            state_proxy["is_trading"] = True
            bot.send_message(state_proxy["chat_id"], f"🎯 **Trade Entered: {direction}**\nStake: {amount} | Barrier: {bar}")
            return True
    return False

def check_result(state_proxy):
    # Wait time for result check (40 seconds)
    if not state_proxy["active_contract"] or time.time() - state_proxy["start_time"] < 40:
        return

    ws = get_ws_connection(state_proxy["api_token"])
    if not ws: return
    try:
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": state_proxy["active_contract"]}))
        res = json.loads(ws.recv())
        contract = res.get("proposal_open_contract", {})
        
        if contract.get("is_expired") == 1:
            profit = float(contract.get("profit", 0))
            if profit > 0:
                state_proxy["win_count"] += 1
                state_proxy["consecutive_losses"] = 0
                state_proxy["current_stake"] = round_stake(state_proxy["initial_stake"])
                status = "✅ WIN"
            else:
                state_proxy["loss_count"] += 1
                state_proxy["consecutive_losses"] += 1
                # Martingale x19
                state_proxy["current_stake"] = round_stake(state_proxy["current_stake"] * 19)
                status = "❌ LOSS"
            
            state_proxy["total_profit"] += profit
            state_proxy["active_contract"] = None 
            state_proxy["is_trading"] = False

            stats_msg = (f"{status} ({profit:.2f})\n━━━━━━━━━━━━━━\n"
                         f"✅ Wins: {state_proxy['win_count']}\n"
                         f"❌ Losses: {state_proxy['loss_count']}\n"
                         f"🔄 MG: {state_proxy['consecutive_losses']}/2\n"
                         f"💰 Total Profit: {state_proxy['total_profit']:.2f}")
            bot.send_message(state_proxy["chat_id"], stats_msg)

            # Stop after 2 consecutive losses
            if state_proxy["consecutive_losses"] >= 2:
                reset_and_stop(state_proxy, "Stop Loss: 2 Losses reached.")
            elif state_proxy["total_profit"] >= state_proxy["tp"]:
                reset_and_stop(state_proxy, "Target Profit Reached.")
        ws.close()
    except:
        if ws: ws.close()

def main_loop(state_proxy):
    last_processed_minute = -1
    while True:
        try:
            if state_proxy["is_running"] and not state_proxy["is_trading"]:
                now = datetime.now()
                # Analyze at exactly second 30
                if now.second == 30 and now.minute != last_processed_minute:
                    ws = get_ws_connection(state_proxy["api_token"])
                    if ws:
                        ws.send(json.dumps({"ticks_history": "R_100", "count": 15, "end": "latest", "style": "ticks"}))
                        history = json.loads(ws.recv()).get("history", {}).get("prices", [])
                        sig = analyze_price_difference(history)
                        if sig:
                            if execute_trade(state_proxy, ws, sig):
                                last_processed_minute = now.minute
                        ws.close()
            elif state_proxy["is_trading"]:
                check_result(state_proxy)
            time.sleep(0.1)
        except: time.sleep(1)

def reset_and_stop(state_proxy, reason):
    if state_proxy["chat_id"]:
        bot.send_message(state_proxy["chat_id"], f"🛑 **Bot Stopped**\nReason: {reason}")
    for k, v in get_initial_state().items(): state_proxy[k] = v

# --- FLASK ADMIN PANEL (Web UI) ---
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
        <select name="days">
            <option value="1">1 Day</option>
            <option value="30">30 Days</option>
            <option value="36500">Life Time (36500 Days)</option>
        </select>
        <button type="submit">Add User</button>
    </form><br>
    <table><tr><th>User Email</th><th>Expiry Date</th><th>Action</th></tr>
    {% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td>
    <td><a href="/delete/{{u.email}}" style="color:red; text-decoration:none;">Remove</a></td></tr>{% endfor %}
    </table></body></html>
    """
    return render_template_string(html, users=users)

@app.route('/add', methods=['POST'])
def add_user():
    email = request.form.get('email').lower()
    days = int(request.form.get('days'))
    expiry = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    users_col.update_one({"email": email}, {"$set": {"expiry": expiry}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete_user(email):
    users_col.delete_one({"email": email})
    return redirect('/')

# --- TELEGRAM COMMAND HANDLERS ---
@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id, "Welcome! Please enter your registered email to continue:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    user = users_col.find_one({"email": m.text.strip().lower()})
    if user:
        state["email"] = m.text.strip().lower(); state["chat_id"] = m.chat.id
        bot.send_message(m.chat.id, "✅ Access Granted!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰'))
    else: bot.send_message(m.chat.id, "🚫 Email not registered or expired.")

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def token_step(m):
    bot.send_message(m.chat.id, "Enter your Deriv API Token:")
    bot.register_next_step_handler(m, stake_step)

def stake_step(m):
    state["api_token"] = m.text.strip()
    bot.send_message(m.chat.id, "Initial Stake Amount:")
    bot.register_next_step_handler(m, tp_step)

def tp_step(m):
    try:
        state["initial_stake"] = float(m.text); state["current_stake"] = float(m.text)
        bot.send_message(m.chat.id, "Target Profit Amount:")
        bot.register_next_step_handler(m, run_bot)
    except: bot.send_message(m.chat.id, "Invalid number. Start again.")

def run_bot(m):
    try:
        state["tp"] = float(m.text); state["is_running"] = True
        bot.send_message(m.chat.id, "🚀 Bot Running! (Analyzing at :30, Reverse Strategy)", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
    except: bot.send_message(m.chat.id, "Invalid number. Start again.")

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def manual_stop(m): reset_and_stop(state, "Manual Stop Triggered.")

if __name__ == '__main__':
    # Start Trading Logic in Background
    multiprocessing.Process(target=main_loop, args=(state,), daemon=True).start()
    # Start Web Dashboard
    port = int(os.environ.get("PORT", 10000))
    multiprocessing.Process(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    # Start Telegram Listener
    bot.infinity_polling()
