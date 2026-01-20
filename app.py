import websocket, json, time, multiprocessing, os
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION (New Token Applied) ---
TOKEN = "8264292822:AAEQWc_f08RKiI5okSybzqKudS5AJn_SAMk"
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

def analyze_digits_and_trend(ticks):
    if len(ticks) < 2: return None
    t1, t2 = ticks[-2], ticks[-1]
    s_t2 = "{:.3f}".format(t2)
    d1 = int(s_t2.split('.')[1][0])
    
    # REVERSED SIGNALS LOGIC (As requested)
    if t2 > t1 and d1 == 9: return "PUT"   # Price Up + D1=9 -> PUT
    if t2 < t1 and d1 == 9: return "CALL"  # Price Down + D1=9 -> CALL
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

def check_result(state_proxy):
    # 18-second delay for settlement
    if not state_proxy["active_contract"] or time.time() - state_proxy["start_time"] < 18:
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
                state_proxy["current_stake"] = round_stake(state_proxy["current_stake"] * 14)
                status = "❌ LOSS"
            
            state_proxy["total_profit"] += profit
            state_proxy["active_contract"] = None 
            state_proxy["is_trading"] = False

            # Statistics included in result message
            stats_msg = (f"{status} (**{profit:.2f}**)\n━━━━━━━━━━━━━━\n"
                         f"✅ Wins: `{state_proxy['win_count']}`\n"
                         f"❌ Losses: `{state_proxy['loss_count']}`\n"
                         f"🔄 MG Level: `{state_proxy['consecutive_losses']}/2`\n"
                         f"💰 Current Profit: **{state_proxy['total_profit']:.2f}**")
            bot.send_message(state_proxy["chat_id"], stats_msg, parse_mode="Markdown")

            if state_proxy["consecutive_losses"] >= 2:
                reset_and_stop(state_proxy, "Reached 2 Consecutive Losses.")
            elif state_proxy["total_profit"] >= state_proxy["tp"]:
                reset_and_stop(state_proxy, "Target Profit Reached.")
        ws.close()
    except:
        if ws: ws.close()

def main_loop(state_proxy):
    while True:
        try:
            if state_proxy["is_running"] and not state_proxy["is_trading"]:
                ws = get_ws_connection(state_proxy["api_token"])
                if ws:
                    ws.send(json.dumps({"ticks": "R_100"}))
                    while state_proxy["is_running"] and not state_proxy["is_trading"]:
                        res = json.loads(ws.recv())
                        if "tick" in res:
                            ws.send(json.dumps({"ticks_history": "R_100", "count": 2, "end": "latest", "style": "ticks"}))
                            history = json.loads(ws.recv()).get("history", {}).get("prices", [])
                            sig = analyze_digits_and_trend(history)
                            if sig:
                                amount = round_stake(state_proxy["current_stake"])
                                bar = "-0.8" if sig == "CALL" else "+0.8"
                                req = {"proposal": 1, "amount": amount, "basis": "stake", "contract_type": sig, 
                                       "currency": state_proxy["currency"], "duration": 5, "duration_unit": "t", 
                                       "symbol": "R_100", "barrier": bar}
                                ws.send(json.dumps(req))
                                prop = json.loads(ws.recv()).get("proposal")
                                if prop:
                                    ws.send(json.dumps({"buy": prop["id"], "price": amount}))
                                    buy_data = json.loads(ws.recv())
                                    if "buy" in buy_data:
                                        state_proxy["active_contract"] = buy_data["buy"]["contract_id"]
                                        state_proxy["start_time"] = time.time()
                                        state_proxy["is_trading"] = True
                                        bot.send_message(state_proxy["chat_id"], f"🎯 **Reversed Signal Entry**\nType: {sig} | Barrier: {bar}\nStake: {amount}")
                                        break 
                    ws.close() # Immediate disconnect for resource efficiency
            elif state_proxy["is_trading"]:
                check_result(state_proxy)
            time.sleep(0.5)
        except: time.sleep(1)

# --- BOT INTERFACE ---
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
    else: bot.send_message(m.chat.id, "🚫 Denied.")

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def ask_token(m):
    bot.send_message(m.chat.id, "Enter API Token:")
    bot.register_next_step_handler(m, save_token)

def save_token(m):
    ws = get_ws_connection(m.text.strip())
    if ws:
        state["api_token"] = m.text.strip(); ws.close()
        bot.send_message(m.chat.id, "✅ Verified! Initial Stake:")
        bot.register_next_step_handler(m, save_stake)
    else: bot.send_message(m.chat.id, "❌ Invalid.")

def save_stake(m):
    try:
        v = round_stake(m.text)
        state["initial_stake"] = v; state["current_stake"] = v
        bot.send_message(m.chat.id, "Target Profit:")
        bot.register_next_step_handler(m, save_tp)
    except: bot.send_message(m.chat.id, "Error.")

def save_tp(m):
    try:
        state["tp"] = float(m.text); state["is_running"] = True
        bot.send_message(m.chat.id, "🚀 Running with Reversed Logic...", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
    except: bot.send_message(m.chat.id, "Error.")

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_all(m): reset_and_stop(state, "Manual Stop.")

# --- ADMIN PANEL ---
@app.route('/')
def home():
    users = list(users_col.find())
    html = """
    <!DOCTYPE html><html><head><title>Admin Panel</title></head>
    <body style="font-family:sans-serif;text-align:center;">
    <h2>Authorized Users</h2>
    <form method="POST" action="/add_user"><input type="email" name="email" placeholder="Email" required>
    <select name="duration"><option value="1">1 Day</option><option value="30">30 Days</option></select>
    <button type="submit">Add</button></form>
    <table border="1" style="margin:auto;margin-top:20px;">
    <tr><th>Email</th><th>Expiry</th><th>Action</th></tr>
    {% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry_date}}</td>
    <td><form method="POST" action="/delete_user"><input type="hidden" name="email" value="{{u.email}}"><button type="submit">Delete</button></form></td></tr>{% endfor %}
    </table></body></html>"""
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

if __name__ == '__main__':
    multiprocessing.Process(target=main_loop, args=(state,), daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    multiprocessing.Process(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    bot.infinity_polling()
