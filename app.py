import websocket, json, time, multiprocessing, os
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION (Updated Token) ---
TOKEN = "8264292822:AAFnWp_MVhGwRiprSsJFqg47_sOxYD90q3Q"
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

def analyze_hourly_trend(ws):
    """Analyze 1800 ticks (approx 30 mins) to determine trend at the top of the hour"""
    ws.send(json.dumps({"ticks_history": "R_100", "count": 1800, "end": "latest", "style": "ticks"}))
    res = json.loads(ws.recv())
    history = res.get("history", {}).get("prices", [])
    
    if len(history) < 1800: return None
    
    first_price = history[0]
    last_price = history[-1]
    
    # Counter-trend Logic:
    if last_price > first_price: 
        return "PUT" # Market is Up -> Enter PUT
    elif last_price < first_price: 
        return "CALL" # Market is Down -> Enter CALL
    return None

def reset_and_stop(state_proxy, reason):
    if state_proxy["chat_id"]:
        report = (f"🛑 **SESSION ENDED**\n━━━━━━━━━━━━━━\n"
                  f"✅ Total Wins: `{state_proxy['win_count']}`\n"
                  f"❌ Total Losses: `{state_proxy['loss_count']}`\n"
                  f"💰 Final Profit: **{state_proxy['total_profit']:.2f}**\n"
                  f"📝 Reason: {reason}")
        bot.send_message(state_proxy["chat_id"], report, parse_mode="Markdown")
    initial = get_initial_state()
    for k, v in initial.items(): state_proxy[k] = v

# --- TRADING LOGIC ---
def check_result(state_proxy):
    # Wait 62 seconds for 1-minute trade settlement
    if not state_proxy["active_contract"] or time.time() - state_proxy["start_time"] < 62:
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
                # Multiplier 2.2x as requested
                state_proxy["current_stake"] = round_stake(state_proxy["current_stake"] * 2.2)
                status = "❌ LOSS"
            
            state_proxy["total_profit"] += profit
            state_proxy["active_contract"] = None 
            state_proxy["is_trading"] = False

            stats_msg = (f"{status} (**{profit:.2f}**)\n━━━━━━━━━━━━━━\n"
                         f"✅ Wins: `{state_proxy['win_count']}`\n"
                         f"❌ Losses: `{state_proxy['loss_count']}`\n"
                         f"🔄 Attempt: `{state_proxy['consecutive_losses']}/4`\n"
                         f"💰 Total Profit: **{state_proxy['total_profit']:.2f}**")
            bot.send_message(state_proxy["chat_id"], stats_msg, parse_mode="Markdown")

            # Stop after 4 consecutive losses
            if state_proxy["consecutive_losses"] >= 4:
                reset_and_stop(state_proxy, "Stop Loss (4 Losses).")
            elif state_proxy["total_profit"] >= state_proxy["tp"]:
                reset_and_stop(state_proxy, "Target Profit Reached.")
        ws.close()
    except:
        if ws: ws.close()

def main_loop(state_proxy):
    while True:
        try:
            if state_proxy["is_running"] and not state_proxy["is_trading"]:
                now = datetime.now()
                # Starts only at Minute 00, Second 00
                if now.minute == 0 and now.second == 0:
                    ws = get_ws_connection(state_proxy["api_token"])
                    if ws:
                        sig = analyze_hourly_trend(ws)
                        if sig:
                            amount = round_stake(state_proxy["current_stake"])
                            req = {
                                "proposal": 1, "amount": amount, "basis": "stake", 
                                "contract_type": sig, "currency": "USD", 
                                "duration": 1, "duration_unit": "m", "symbol": "R_100"
                            }
                            ws.send(json.dumps(req))
                            prop = json.loads(ws.recv()).get("proposal")
                            if prop:
                                ws.send(json.dumps({"buy": prop["id"], "price": amount}))
                                buy_res = json.loads(ws.recv())
                                if "buy" in buy_res:
                                    state_proxy["active_contract"] = buy_res["buy"]["contract_id"]
                                    state_proxy["start_time"] = time.time()
                                    state_proxy["is_trading"] = True
                                    bot.send_message(state_proxy["chat_id"], f"🎯 **Trade Entered**\nType: {sig}\nStake: {amount}")
                        ws.close()
                    time.sleep(1) # Block double trigger within the same second
            
            elif state_proxy["is_trading"]:
                check_result(state_proxy)
            
            time.sleep(0.5)
        except: time.sleep(1)

# --- ADMIN PANEL (HTML) ---
@app.route('/')
def home():
    users = list(users_col.find())
    html = """
    <!DOCTYPE html><html><head><title>Admin Panel</title>
    <style>body{font-family:sans-serif;text-align:center;background:#f0f2f5;padding:30px;}
    .container{background:white;width:90%;max-width:800px;margin:auto;padding:20px;border-radius:10px;box-shadow:0 4px 15px rgba(0,0,0,0.1);}
    table{width:100%;border-collapse:collapse;margin-top:20px;}th,td{padding:12px;border:1px solid #ddd;}
    th{background:#2c3e50;color:white;}.btn{padding:8px 15px;border:none;border-radius:5px;cursor:pointer;color:white;}
    .btn-add{background:#27ae60;}.btn-del{background:#e74c3c;}input,select{padding:8px;margin:5px;}</style></head>
    <body><div class="container"><h2>User Management Panel</h2>
    <form method="POST" action="/add_user"><input type="email" name="email" placeholder="User Email" required>
    <select name="duration"><option value="1">1 Day</option><option value="30">30 Days</option><option value="36500">Lifetime</option></select>
    <button type="submit" class="btn btn-add">Authorize User</button></form>
    <table><tr><th>Email</th><th>Expiry Date</th><th>Action</th></tr>
    {% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry_date}}</td>
    <td><form method="POST" action="/delete_user" style="display:inline;"><input type="hidden" name="email" value="{{u.email}}">
    <button type="submit" class="btn btn-del">Delete</button></form></td></tr>{% endfor %}
    </table></div></body></html>"""
    return render_template_string(html, users=users)

@app.route('/add_user', methods=['POST'])
def add_user():
    e, d = request.form.get('email').strip().lower(), int(request.form.get('duration'))
    exp = (datetime.now() + timedelta(days=d)).strftime("%Y-%m-%d %H:%M")
    users_col.update_one({"email": e}, {"$set": {"expiry_date": exp}}, upsert=True)
    return redirect('/')

@app.route('/delete_user', methods=['POST'])
def delete_user():
    users_col.delete_one({"email": request.form.get('email').lower()})
    return redirect('/')

# --- TELEGRAM BOT HANDLERS ---
@bot.message_handler(commands=['start'])
def welcome(m):
    bot.send_message(m.chat.id, "👋 Welcome! Enter your registered email:")
    bot.register_next_step_handler(m, login)

def login(m):
    e = m.text.strip().lower()
    user_data = users_col.find_one({"email": e})
    if user_data and (datetime.now() <= datetime.strptime(user_data["expiry_date"], "%Y-%m-%d %H:%M")):
        state["email"] = e; state["chat_id"] = m.chat.id
        bot.send_message(m.chat.id, "✅ Authorized!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰'))
    else: bot.send_message(m.chat.id, "🚫 Access Denied.")

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def ask_token(m):
    bot.send_message(m.chat.id, "Enter API Token:")
    bot.register_next_step_handler(m, save_token)

def save_token(m):
    ws = get_ws_connection(m.text.strip())
    if ws:
        state["api_token"] = m.text.strip(); ws.close()
        bot.send_message(m.chat.id, "✅ Token Verified! Enter Initial Stake:")
        bot.register_next_step_handler(m, save_stake)
    else: bot.send_message(m.chat.id, "❌ Invalid Token.")

def save_stake(m):
    try:
        v = round_stake(m.text)
        state["initial_stake"] = v; state["current_stake"] = v
        bot.send_message(m.chat.id, "Enter Target Profit:")
        bot.register_next_step_handler(m, save_tp)
    except: bot.send_message(m.chat.id, "Error. Enter a number.")

def save_tp(m):
    try:
        state["tp"] = float(m.text); state["is_running"] = True
        bot.send_message(m.chat.id, "🚀 Bot active. Waiting for Top of the Hour (00:00)...", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
    except: bot.send_message(m.chat.id, "Error. Enter a number.")

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_all(m): reset_and_stop(state, "Stopped by user.")

# --- RUNNER ---
if __name__ == '__main__':
    # Start main trading process
    multiprocessing.Process(target=main_loop, args=(state,), daemon=True).start()
    # Start Web Admin Panel
    port = int(os.environ.get("PORT", 10000))
    multiprocessing.Process(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    # Start Bot Polling
    bot.infinity_polling()
