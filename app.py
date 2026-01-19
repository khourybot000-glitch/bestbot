import websocket, json, time, multiprocessing, os, math
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
TOKEN = "8264292822:AAGyXo8S3wK66UF2WSD-ftbu_-zMdnsKLt8"
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
        "last_minute": -1
    }

state = manager.dict(get_initial_state())

# --- UTILS ---
def round_stake(value):
    return round(float(value), 2)

def get_ws_connection(api_token, retries=5):
    for i in range(retries):
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
            ws.send(json.dumps({"authorize": api_token}))
            res = json.loads(ws.recv())
            if "authorize" in res: return ws
            ws.close()
        except: time.sleep(1)
    return None

# --- BOLLINGER CALCULATION ---
def calculate_bollinger(all_ticks, candle_size=30, period=20, std_dev=2):
    if len(all_ticks) < (period * candle_size): return None, None
    candle_closes = []
    for i in range(len(all_ticks) - 1, (len(all_ticks) - (period + 1) * candle_size), -candle_size):
        if i >= 0: candle_closes.append(all_ticks[i])
    if len(candle_closes) < period: return None, None
    sma = sum(candle_closes[:period]) / period
    variance = sum((x - sma) ** 2 for x in candle_closes[:period]) / period
    stdev = math.sqrt(variance)
    return sma + (std_dev * stdev), sma - (std_dev * stdev)

# --- SYSTEM FUNCTIONS ---
def is_authorized(email):
    user_data = users_col.find_one({"email": email.strip().lower()})
    return user_data and (datetime.now() <= datetime.strptime(user_data["expiry_date"], "%Y-%m-%d %H:%M"))

def reset_and_stop(state_proxy, text):
    if state_proxy["chat_id"]:
        report = (f"🛑 **SESSION TERMINATED**\n━━━━━━━━━━━━━━\n"
                  f"✅ Wins: `{state_proxy['win_count']}` | ❌ Losses: `{state_proxy['loss_count']}`\n"
                  f"💰 Final Profit: **{state_proxy['total_profit']:.2f}**\n📝 Reason: {text}")
        bot.send_message(state_proxy["chat_id"], report, parse_mode="Markdown", reply_markup=types.ReplyKeyboardRemove())
    initial = get_initial_state()
    for k, v in initial.items(): state_proxy[k] = v

# --- RESULT CHECK (Statistics inside result message) ---
def check_result(state_proxy):
    if not state_proxy["active_contract"] or time.time() - state_proxy["start_time"] < 902:
        return
    ws = get_ws_connection(state_proxy["api_token"])
    if not ws: return
    try:
        while True:
            ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": state_proxy["active_contract"]}))
            res = json.loads(ws.recv())
            contract = res.get("proposal_open_contract", {})
            if contract.get("is_expired") == 1:
                profit = float(contract.get("profit", 0))
                if profit > 0:
                    state_proxy["win_count"] += 1
                    state_proxy["consecutive_losses"] = 0
                    state_proxy["current_stake"] = round_stake(state_proxy["initial_stake"])
                    icon = "✅ WIN"
                else:
                    state_proxy["loss_count"] += 1
                    state_proxy["consecutive_losses"] += 1
                    state_proxy["current_stake"] = round_stake(state_proxy["current_stake"] * 2.2)
                    icon = "❌ LOSS"
                
                state_proxy["total_profit"] += profit
                state_proxy["active_contract"] = None 
                state_proxy["is_trading"] = False

                # الإحصائيات تظهر مع النتيجة في نفس الرسالة
                stats_msg = (f"{icon} (**{profit:.2f}**)\n━━━━━━━━━━━━━━\n"
                             f"✅ Wins: `{state_proxy['win_count']}` | ❌ Losses: `{state_proxy['loss_count']}`\n"
                             f"🔄 Consecutive Losses: `{state_proxy['consecutive_losses']}/5`\n"
                             f"💰 Net Profit: **{state_proxy['total_profit']:.2f}**")
                bot.send_message(state_proxy["chat_id"], stats_msg, parse_mode="Markdown")

                if state_proxy["consecutive_losses"] >= 5:
                    reset_and_stop(state_proxy, "Reached 5 consecutive losses.")
                elif state_proxy["total_profit"] >= state_proxy["tp"]:
                    reset_and_stop(state_proxy, "Target Profit Reached!")
                break
            else:
                time.sleep(1)
        ws.close()
    except:
        if ws: ws.close()

# --- MAIN LOOP ---
def main_loop(state_proxy):
    while True:
        try:
            now = datetime.now()
            if state_proxy["is_running"] and not state_proxy["is_trading"]:
                if now.second == 0 and now.minute != state_proxy["last_minute"]:
                    state_proxy["last_minute"] = now.minute
                    ws = get_ws_connection(state_proxy["api_token"])
                    if ws:
                        ws.send(json.dumps({"ticks_history": "R_100", "count": 635, "end": "latest", "style": "ticks"}))
                        ticks = json.loads(ws.recv()).get("history", {}).get("prices", [])
                        if len(ticks) >= 60:
                            c_open, c_close = ticks[-30], ticks[-1]
                            upper, lower = calculate_bollinger(ticks[:-30], candle_size=30)
                            sig = None
                            if upper and lower:
                                if c_open > upper and c_close > upper: sig = "CALL"
                                elif c_open < lower and c_close < lower: sig = "PUT"
                            if sig:
                                amount = round_stake(state_proxy["current_stake"])
                                req = {"proposal": 1, "amount": amount, "basis": "stake", "contract_type": sig, 
                                       "currency": state_proxy["currency"], "duration": 15, "duration_unit": "m", "symbol": "R_100"}
                                ws.send(json.dumps(req))
                                prop = json.loads(ws.recv()).get("proposal")
                                if prop:
                                    ws.send(json.dumps({"buy": prop["id"], "price": amount}))
                                    buy_data = json.loads(ws.recv())
                                    if "buy" in buy_data:
                                        bot.send_message(state_proxy["chat_id"], f"🚀 **Trade Sent!**\nSignal: {sig}\nStake: {amount}")
                                        state_proxy["active_contract"] = buy_data["buy"]["contract_id"]
                                        state_proxy["start_time"] = time.time()
                                        state_proxy["is_trading"] = True
                        ws.close()
            elif state_proxy["is_trading"]:
                check_result(state_proxy)
            time.sleep(0.5)
        except: time.sleep(1)

# --- HTML ADMIN PANEL ---
@app.route('/')
def home():
    users = list(users_col.find())
    html = """
    <!DOCTYPE html><html><head><title>Admin Panel</title>
    <style>body{font-family:sans-serif;text-align:center;background:#f4f7f6;padding:20px;}
    .card{background:white;width:95%;max-width:900px;margin:auto;padding:25px;border-radius:12px;box-shadow:0 4px 10px rgba(0,0,0,0.1);}
    table{width:100%;border-collapse:collapse;margin-top:20px;}th,td{padding:12px;border:1px solid #ddd;}
    th{background:#333;color:white;}.btn{padding:8px 15px;border:none;border-radius:5px;color:white;cursor:pointer;}
    .btn-add{background:#28a745;}.btn-del{background:#dc3545;}</style></head>
    <body><div class="card"><h2>User Management</h2>
    <form method="POST" action="/add_user"><input type="email" name="email" placeholder="Email" required>
    <select name="duration"><option value="1">1 Day</option><option value="30">30 Days</option><option value="36500">Lifetime</option></select>
    <button type="submit" class="btn btn-add">Add User</button></form>
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
        bot.send_message(m.chat.id, "✅ Authorized!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰'))
    else: bot.send_message(m.chat.id, "🚫 Access Denied.")

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def ask_token(m):
    bot.send_message(m.chat.id, "Enter API Token:")
    bot.register_next_step_handler(m, save_token)

def save_token(m):
    ws = get_ws_connection(m.text.strip())
    if ws:
        state["api_token"] = m.text.strip()
        ws.close()
        bot.send_message(m.chat.id, "✅ Verified! Enter Stake:")
        bot.register_next_step_handler(m, save_stake)
    else: bot.send_message(m.chat.id, "❌ Invalid Token.")

def save_stake(m):
    try:
        v = round_stake(m.text)
        state["initial_stake"] = v; state["current_stake"] = v
        bot.send_message(m.chat.id, "Enter Target Profit:")
        bot.register_next_step_handler(m, save_tp)
    except: bot.send_message(m.chat.id, "Invalid number.")

def save_tp(m):
    try:
        state["tp"] = float(m.text); state["is_running"] = True
        bot.send_message(m.chat.id, "🚀 Bot Active.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
    except: bot.send_message(m.chat.id, "Invalid number.")

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_all(m): reset_and_stop(state, "Stopped by user.")

if __name__ == '__main__':
    multiprocessing.Process(target=main_loop, args=(state,), daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    multiprocessing.Process(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    bot.infinity_polling()
