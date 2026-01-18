import websocket, json, time, multiprocessing, os
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
TOKEN = "8264292822:AAEoA3f_G_NsRdhJhoskXx0lFfBUOj4LrQs"
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
        "consecutive_losses": 0, "active_contract": None, "start_time": 0,
        "last_execution_time": ""
    }

state = manager.dict(get_initial_state())

# --- DATABASE AUTHORIZATION SYSTEM (No more .txt) ---
def is_authorized(email):
    email = email.strip().lower()
    user_data = users_col.find_one({"email": email})
    
    if not user_data:
        return False
    
    if "expiry_date" in user_data:
        try:
            expiry_time = datetime.strptime(user_data["expiry_date"], "%Y-%m-%d %H:%M")
            return datetime.now() <= expiry_time
        except:
            return False
    return False

def reset_and_stop(state_proxy, text):
    if state_proxy["chat_id"]:
        try:
            report = (f"🛑 **SESSION TERMINATED**\n"
                      f"━━━━━━━━━━━━━━\n"
                      f"✅ Wins: `{state_proxy['win_count']}` | ❌ Losses: `{state_proxy['loss_count']}`\n"
                      f"💰 Final Profit: **{state_proxy['total_profit']:.2f}**\n"
                      f"📝 Reason: {text}")
            bot.send_message(state_proxy["chat_id"], report, parse_mode="Markdown", reply_markup=types.ReplyKeyboardRemove())
        except: pass
    initial = get_initial_state()
    for k, v in initial.items(): state_proxy[k] = v

# --- RESULT CHECK ---
def check_result(state_proxy):
    if not state_proxy["active_contract"] or time.time() - state_proxy["start_time"] < 18:
        return
    try:
        ws_temp = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
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
                state_proxy["current_stake"] *= 19 
                icon = "❌ LOSS"
            
            state_proxy["total_profit"] += profit
            state_proxy["active_contract"] = None 
            state_proxy["is_trading"] = False

            stats_msg = (f"{icon} (**{profit:.2f}**)\n"
                         f"━━━━━━━━━━━━━━\n"
                         f"✅ Wins: `{state_proxy['win_count']}` | ❌ Losses: `{state_proxy['loss_count']}`\n"
                         f"🔄 Consecutive: `{state_proxy['consecutive_losses']}/2`\n"
                         f"💰 Net: **{state_proxy['total_profit']:.2f}**")
            bot.send_message(state_proxy["chat_id"], stats_msg, parse_mode="Markdown")

            if state_proxy["consecutive_losses"] >= 2:
                reset_and_stop(state_proxy, "Stopped: 2 Consecutive Losses.")
            elif state_proxy["total_profit"] >= state_proxy["tp"]:
                reset_and_stop(state_proxy, "Target Profit Reached!")
    except: pass

# --- MAIN TRADING ENGINE ---
def main_loop(state_proxy):
    ws_persistent = None
    while True:
        try:
            now = datetime.now()
            if state_proxy["is_running"] and not state_proxy["is_trading"] and (now.second == 0 or now.second == 30):
                current_timestamp = f"{now.minute}:{now.second}"
                if state_proxy["last_execution_time"] != current_timestamp:
                    if ws_persistent is None:
                        ws_persistent = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
                        ws_persistent.send(json.dumps({"authorize": state_proxy["api_token"]}))
                        ws_persistent.recv()

                    ws_persistent.send(json.dumps({"ticks_history": "R_100", "count": 45, "end": "latest", "style": "ticks"}))
                    ticks = json.loads(ws_persistent.recv()).get("history", {}).get("prices", [])
                    
                    if len(ticks) == 45:
                        c1, c2, c3 = ticks[14]-ticks[0], ticks[29]-ticks[15], ticks[44]-ticks[30]
                        sig = None
                        if c1 >= 0.6 and c2 <= -0.6 and c3 >= 0.6: sig = "CALL"
                        elif c1 <= -0.6 and c2 >= 0.6 and c3 <= -0.6: sig = "PUT"

                        if sig:
                            barrier = "-0.8" if sig == "CALL" else "+0.8"
                            req = {"proposal": 1, "amount": state_proxy["current_stake"], "basis": "stake", 
                                   "contract_type": sig, "barrier": barrier, "currency": state_proxy["currency"], 
                                   "duration": 5, "duration_unit": "t", "symbol": "R_100"}
                            ws_persistent.send(json.dumps(req))
                            res_p = json.loads(ws_persistent.recv()).get("proposal")
                            if res_p:
                                ws_persistent.send(json.dumps({"buy": res_p["id"], "price": state_proxy["current_stake"]}))
                                buy_data = json.loads(ws_persistent.recv())
                                if "buy" in buy_data:
                                    bot.send_message(state_proxy["chat_id"], f"🚀 **Trade at :{now.second:02d}**\nSignal: {sig}")
                                    state_proxy["active_contract"] = buy_data["buy"]["contract_id"]
                                    state_proxy["start_time"] = time.time()
                                    state_proxy["is_trading"] = True
                    state_proxy["last_execution_time"] = current_timestamp
            elif state_proxy["is_trading"]:
                check_result(state_proxy)
            time.sleep(0.5)
        except:
            if ws_persistent: ws_persistent.close()
            ws_persistent = None; time.sleep(1)

# --- WEB ADMIN PANEL (MONGODB ONLY) ---
@app.route('/')
def home():
    users = list(users_col.find()) # جلب المستخدمين من Mongo مباشرة
    html = """
    <!DOCTYPE html><html><head><title>Cloud Admin Panel</title>
    <style>body{font-family:sans-serif;text-align:center;background:#f4f7f6;padding:20px;}
    .card{background:white;width:95%;max-width:900px;margin:auto;padding:25px;border-radius:12px;box-shadow:0 4px 10px rgba(0,0,0,0.1);}
    table{width:100%;border-collapse:collapse;margin-top:20px;}th,td{padding:12px;border:1px solid #ddd;}
    th{background:#333;color:white;}.btn{padding:8px 15px;border:none;border-radius:5px;color:white;cursor:pointer;}
    .btn-add{background:#28a745;}.btn-del{background:#dc3545;}</style></head>
    <body><div class="card"><h2>Database User Management</h2>
    <form method="POST" action="/add_user"><input type="email" name="email" placeholder="New User Email" required>
    <select name="duration"><option value="1">1 Day</option><option value="30">30 Days</option><option value="36500">Lifetime</option></select>
    <button type="submit" class="btn btn-add">Add/Update User</button></form>
    <table><tr><th>Email</th><th>Expiry Date</th><th>Action</th></tr>
    {% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry_date}}</td>
    <td><form method="POST" action="/delete_user" style="display:inline;"><input type="hidden" name="email" value="{{u.email}}">
    <button type="submit" class="btn btn-del">Delete</button></form></td></tr>{% endfor %}
    </table></div></body></html>"""
    return render_template_string(html, users=users)

@app.route('/add_user', methods=['POST'])
def add_user():
    e = request.form.get('email').strip().lower()
    d = int(request.form.get('duration'))
    exp = (datetime.now() + timedelta(days=d)).strftime("%Y-%m-%d %H:%M")
    users_col.update_one({"email": e}, {"$set": {"expiry_date": exp}}, upsert=True)
    return redirect('/')

@app.route('/delete_user', methods=['POST'])
def delete_user():
    e = request.form.get('email').lower()
    users_col.delete_one({"email": e})
    return redirect('/')

# --- BOT HANDLERS ---
@bot.message_handler(commands=['start'])
def welcome(m):
    bot.send_message(m.chat.id, "👋 Hello! Please enter your registered email:")
    bot.register_next_step_handler(m, login)

def login(m):
    e = m.text.strip().lower()
    if is_authorized(e):
        state["email"] = e; state["chat_id"] = m.chat.id
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰')
        bot.send_message(m.chat.id, "✅ Authorized!", reply_markup=markup)
    else: bot.send_message(m.chat.id, "🚫 Access Denied. Email not found or expired in Database.")

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def ask_token(m):
    bot.send_message(m.chat.id, "Enter API Token:")
    bot.register_next_step_handler(m, save_token)

def save_token(m):
    token = m.text.strip()
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": token}))
        res = json.loads(ws.recv())
        if "authorize" in res:
            state["currency"] = res["authorize"]["currency"]
            state["api_token"] = token
            bot.send_message(m.chat.id, f"✅ Verified! Currency: {state['currency']}\nEnter Stake:")
            bot.register_next_step_handler(m, save_stake)
        else: bot.send_message(m.chat.id, "❌ Invalid Token.")
        ws.close()
    except: bot.send_message(m.chat.id, "❌ Connection Error.")

def save_stake(m):
    try:
        v = float(m.text); state["initial_stake"] = v; state["current_stake"] = v
        bot.send_message(m.chat.id, "Enter Target Profit:")
        bot.register_next_step_handler(m, save_tp)
    except: bot.send_message(m.chat.id, "Invalid number.")

def save_tp(m):
    try:
        state["tp"] = float(m.text); state["is_running"] = True
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑')
        bot.send_message(m.chat.id, "🚀 Strategy Running... (Analyzing at :00 and :30)", reply_markup=markup)
    except: bot.send_message(m.chat.id, "Invalid number.")

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_all(m): reset_and_stop(state, "Stopped by user.")

if __name__ == '__main__':
    multiprocessing.Process(target=main_loop, args=(state,), daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    multiprocessing.Process(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    bot.infinity_polling()
