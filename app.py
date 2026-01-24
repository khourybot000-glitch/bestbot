import websocket, json, time, multiprocessing, os
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)

# --- CONFIGURATION ---
# التوكن الجديد المستبدل
TOKEN = "8433565422:AAE6ah0Mpq3meeWST75a4rSeTUYSFDbcU3o"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']

manager = multiprocessing.Manager()

def get_initial_state():
    return {
        "email": "", 
        "tokens": [], 
        "accounts_data": {}, 
        "initial_stake": 0.0, 
        "tp": 0.0, 
        "currency": "USD", 
        "is_running": False, 
        "chat_id": None,
        "is_trading": False,
        "start_time": 0
    }

state = manager.dict(get_initial_state())

# --- UTILS (Infinite Retry Connection) ---
def get_ws_connection(api_token):
    """يحاول الاتصال بشكل دائم حتى ينجح"""
    while True:
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
            ws.send(json.dumps({"authorize": api_token}))
            res = json.loads(ws.recv())
            if "authorize" in res: 
                return ws
            ws.close()
        except:
            pass
        time.sleep(1) 

# --- TRADING LOGIC ---
def analyze_price_difference(ticks):
    if len(ticks) < 15: return None
    diff = ticks[-1] - ticks[-15]
    if diff >= 1.5: return "CALL"
    elif diff <= -1.5: return "PUT"
    return None

def execute_multi_trade(direction, state_proxy):
    with ThreadPoolExecutor() as executor:
        for token in state_proxy["tokens"]:
            executor.submit(single_trade_worker, token, direction, state_proxy)

def single_trade_worker(token, direction, state_proxy):
    acc_info = state_proxy["accounts_data"].get(token)
    if not acc_info or acc_info.get("stopped", False): return
    
    ws = get_ws_connection(token)
    amount = round(float(acc_info["current_stake"]), 2)
    bar = "-1.5" if direction == "CALL" else "+1.5"
    
    req = {
        "proposal": 1, "amount": amount, "basis": "stake", 
        "contract_type": direction, "currency": state_proxy["currency"], 
        "duration": 30, "duration_unit": "s", 
        "symbol": "R_100", "barrier": bar
    }
    
    try:
        ws.send(json.dumps(req))
        res = json.loads(ws.recv())
        prop = res.get("proposal")
        if prop:
            ws.send(json.dumps({"buy": prop["id"], "price": amount}))
            buy_data = json.loads(ws.recv())
            if "buy" in buy_data:
                acc_info["active_contract"] = buy_data["buy"]["contract_id"]
                temp_data = state_proxy["accounts_data"]
                temp_data[token] = acc_info
                state_proxy["accounts_data"] = temp_data
        ws.close()
    except: pass

def check_all_results(state_proxy):
    with ThreadPoolExecutor() as executor:
        for token in state_proxy["tokens"]:
            executor.submit(single_result_worker, token, state_proxy)

def single_result_worker(token, state_proxy):
    acc_info = state_proxy["accounts_data"].get(token)
    if not acc_info or not acc_info.get("active_contract"): return

    ws = get_ws_connection(token)
    try:
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": acc_info["active_contract"]}))
        res = json.loads(ws.recv())
        contract = res.get("proposal_open_contract", {})
        
        if contract.get("is_expired") == 1:
            profit = float(contract.get("profit", 0))
            if profit > 0:
                acc_info["win_count"] += 1
                acc_info["consecutive_losses"] = 0
                acc_info["current_stake"] = state_proxy["initial_stake"]
                status = "✅ WIN"
            else:
                acc_info["loss_count"] += 1
                acc_info["consecutive_losses"] += 1
                acc_info["current_stake"] = acc_info["current_stake"] * 19 
                status = "❌ LOSS"
            
            acc_info["total_profit"] += profit
            acc_info["active_contract"] = None
            
            stats_msg = (f"👤 Acc: {token[:5]}... | {status} ({profit:.2f})\n"
                         f"━━━━━━━━━━━━━━\n"
                         f"✅ Wins: {acc_info['win_count']}\n"
                         f"❌ Losses: {acc_info['loss_count']}\n"
                         f"🔄 MG: {acc_info['consecutive_losses']}/2\n"
                         f"💰 Total Profit: {acc_info['total_profit']:.2f}")
            bot.send_message(state_proxy["chat_id"], stats_msg)

            if acc_info["consecutive_losses"] >= 2:
                acc_info["stopped"] = True
                bot.send_message(state_proxy["chat_id"], f"🛑 Account {token[:5]}... Stopped (2 Losses)")

            temp_data = state_proxy["accounts_data"]
            temp_data[token] = acc_info
            state_proxy["accounts_data"] = temp_data
        ws.close()
    except: pass

def main_loop(state_proxy):
    last_processed_minute = -1
    while True:
        try:
            if state_proxy["is_running"]:
                now = datetime.now()
                if now.second == 30 and now.minute != last_processed_minute:
                    test_ws = get_ws_connection(state_proxy["tokens"][0])
                    test_ws.send(json.dumps({"ticks_history": "R_100", "count": 15, "end": "latest", "style": "ticks"}))
                    history = json.loads(test_ws.recv()).get("history", {}).get("prices", [])
                    sig = analyze_price_difference(history)
                    if sig:
                        state_proxy["is_trading"] = True
                        state_proxy["start_time"] = time.time()
                        execute_multi_trade(sig, state_proxy)
                        last_processed_minute = now.minute
                    test_ws.close()

                if state_proxy["is_trading"] and (time.time() - state_proxy["start_time"] >= 40):
                    check_all_results(state_proxy)
                    state_proxy["is_trading"] = False
            time.sleep(0.1)
        except: time.sleep(1)

# --- FLASK ADMIN PANEL ---
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

# --- TELEGRAM HANDLERS ---
@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id, "Welcome! Please enter your registered email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    user = users_col.find_one({"email": m.text.strip().lower()})
    if user:
        state["email"] = m.text.strip().lower(); state["chat_id"] = m.chat.id
        bot.send_message(m.chat.id, "✅ Access Granted! Send all API Tokens separated by a comma (,):")
        bot.register_next_step_handler(m, tokens_step)
    else: bot.send_message(m.chat.id, "🚫 Email not registered.")

def tokens_step(m):
    token_list = [t.strip() for t in m.text.split(",")]
    state["tokens"] = token_list
    bot.send_message(m.chat.id, f"✅ {len(token_list)} Tokens added. Enter Initial Stake:")
    bot.register_next_step_handler(m, stake_step)

def stake_step(m):
    try:
        stake = float(m.text)
        state["initial_stake"] = stake
        accs = {}
        for t in state["tokens"]:
            accs[t] = {"current_stake": stake, "win_count": 0, "loss_count": 0, "total_profit": 0.0, "consecutive_losses": 0, "active_contract": None, "stopped": False}
        state["accounts_data"] = accs
        bot.send_message(m.chat.id, "Target Profit:")
        bot.register_next_step_handler(m, run_bot)
    except: bot.send_message(m.chat.id, "Invalid. Start over.")

def run_bot(m):
    state["tp"] = float(m.text); state["is_running"] = True
    bot.send_message(m.chat.id, "🚀 Multi-Bot Started! Waiting for Signal...", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def manual_stop(m):
    state["is_running"] = False
    bot.send_message(m.chat.id, "🛑 Bot Stopped.")

if __name__ == '__main__':
    multiprocessing.Process(target=main_loop, args=(state,), daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    multiprocessing.Process(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    bot.infinity_polling()
