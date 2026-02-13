import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
BOT_TOKEN = "8433565422:AAGiaH5ZZwkvrJl1ycIqJI2epgm0iE9IXQk"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V12_English_Final']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

user_locks = {}

# --- KEYBOARDS ---
def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('START 🚀', 'STOP 🛑')
    return markup

# --- UTILITY: SAFE CONNECTION & DYNAMIC CURRENCY ---
def get_safe_connection(token):
    """Establishes connection and retrieves account currency dynamically."""
    while True:
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
            ws.send(json.dumps({"authorize": token}))
            res = json.loads(ws.recv())
            if "authorize" in res:
                currency = res["authorize"].get("currency", "USD")
                return ws, currency
            ws.close()
        except:
            time.sleep(1)

# --- TRADING ENGINE ---
def execute_trade(chat_id, token, contract_type, stake):
    contract_id = None
    while not contract_id:
        if not active_sessions_col.find_one({"chat_id": chat_id, "is_running": True}): return
        ws = None
        try:
            ws, currency = get_safe_connection(token)
            ws.send(json.dumps({
                "buy": "1", "price": stake,
                "parameters": {
                    "amount": stake, "basis": "stake", "contract_type": contract_type,
                    "duration": 10, "duration_unit": "t",
                    "symbol": "R_100", "currency": currency
                }
            }))
            buy_res = json.loads(ws.recv())
            if "buy" in buy_res:
                contract_id = buy_res["buy"]["contract_id"]
                bot.send_message(chat_id, f"🚀 **Trade Executed!**\nType: `{contract_type}` | Stake: `{stake} {currency}` | Duration: `10 Ticks`")
            ws.close()
        except:
            if ws: ws.close()
            time.sleep(1)

    # Wait 20 seconds as requested before checking results
    time.sleep(20)

    # Persistent Result Hunting
    while True:
        if not active_sessions_col.find_one({"chat_id": chat_id, "is_running": True}): return
        ws_res = None
        try:
            ws_res, _ = get_safe_connection(token)
            ws_res.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
            res = json.loads(ws_res.recv())
            if "proposal_open_contract" in res:
                data = res["proposal_open_contract"]
                if data.get("is_expired"):
                    profit = float(data.get("profit", 0))
                    ws_res.close()
                    process_result(chat_id, token, contract_type, profit)
                    break 
            ws_res.close()
            time.sleep(1)
        except:
            if ws_res: ws_res.close()
            time.sleep(1)

def process_result(chat_id, token, last_type, profit):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session or not session.get("is_running"): return
    acc = session["accounts_data"][token]
    is_win = profit > 0
    new_total = round(acc["total_profit"] + profit, 2)
    
    if is_win:
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
            f"accounts_data.{token}.current_stake": session["initial_stake"],
            f"accounts_data.{token}.streak": 0,
            f"accounts_data.{token}.total_profit": new_total
        }})
        bot.send_message(chat_id, f"✅ **WIN (+{profit})**\nTotal Profit: `{new_total}`")
        if new_total >= session["target_profit"]: 
            reset_and_stop(chat_id, "🎯 Target Profit Reached! Session Cleared.")
    else:
        new_streak = acc.get("streak", 0) + 1
        if new_streak >= 4:
            reset_and_stop(chat_id, "❌ Stop Loss: 4 Consecutive Losses. Session Reset.")
            return
        new_stake = round(acc["current_stake"] * 2.2, 2)
        next_type = "CALL" if last_type == "PUT" else "PUT"
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
            f"accounts_data.{token}.current_stake": new_stake, 
            f"accounts_data.{token}.streak": new_streak, 
            f"accounts_data.{token}.total_profit": new_total
        }})
        bot.send_message(chat_id, f"❌ **LOSS**\nImmediate Martingale (2.2x): `{next_type}` with `{new_stake}`")
        threading.Thread(target=execute_trade, args=(chat_id, token, next_type, new_stake)).start()

# --- ANALYSIS: HISTORY REQUEST AT SECOND 0 ---
def run_history_analysis(chat_id, token):
    if user_locks.get(chat_id): return
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session or not session.get("is_running") or session["accounts_data"][token].get("streak", 0) > 0: return
    
    user_locks[chat_id] = True
    ws = None
    try:
        ws, _ = get_safe_connection(token)
        ws.send(json.dumps({
            "ticks_history": "R_100",
            "adjust_start_time": 1,
            "count": 30,
            "end": "latest",
            "style": "ticks"
        }))
        
        response = json.loads(ws.recv())
        if "history" in response:
            ticks = response["history"]["prices"]
            if len(ticks) >= 30:
                # 3 Candles logic: 10 ticks per candle
                c1 = "UP" if ticks[9] > ticks[0] else "DOWN"
                c2 = "UP" if ticks[19] > ticks[10] else "DOWN"
                c3 = "UP" if ticks[29] > ticks[20] else "DOWN"
                
                candles = [c1, c2, c3]
                target = "CALL" if candles == ["DOWN", "UP", "DOWN"] else ("PUT" if candles == ["UP", "DOWN", "UP"] else None)
                
                if target:
                    execute_trade(chat_id, token, target, session["accounts_data"][token]["current_stake"])
        ws.close()
    except:
        if ws: ws.close()
    user_locks[chat_id] = False

def main_scheduler(chat_id):
    bot.send_message(chat_id, "⏱️ Waiting for Second 0 to fetch 30 ticks...")
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        if datetime.now().second == 0:
            threading.Thread(target=run_history_analysis, args=(chat_id, session['tokens'][0])).start()
            time.sleep(50)
        time.sleep(0.5)

def reset_and_stop(chat_id, reason):
    active_sessions_col.delete_one({"chat_id": chat_id})
    user_locks[chat_id] = False
    bot.send_message(chat_id, f"🛑 **Bot Stopped**\n{reason}", reply_markup=main_keyboard())

# --- TELEGRAM HANDLERS ---
@bot.message_handler(commands=['start'])
def cmd_start(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "Step 1: Enter Registered Email:", reply_markup=main_keyboard())
    bot.register_next_step_handler(m, auth)

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def btn_start(m): cmd_start(m)

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def btn_stop(m): reset_and_stop(m.chat.id, "Manual Stop. Session Deleted.")

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "Step 2: Enter API Token:")
        bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 Access Denied / Expired.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False}}, upsert=True)
    bot.send_message(m.chat.id, "Step 3: Initial Stake:")
    bot.register_next_step_handler(m, lambda msg: save_config(msg, "initial_stake"))

def save_config(m, key):
    try:
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {key: float(m.text)}})
        if key == "initial_stake":
            bot.send_message(m.chat.id, "Step 4: Target Profit:")
            bot.register_next_step_handler(m, lambda msg: save_config(msg, "target_profit"))
        else:
            sess = active_sessions_col.find_one({"chat_id": m.chat.id})
            accs = {sess["tokens"][0]: {"current_stake": sess["initial_stake"], "total_profit": 0.0, "streak": 0}}
            active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
            bot.send_message(m.chat.id, "🛰️ Sniper Active!\n- Analysis: History (Sec 0)\n- Duration: 10 Ticks\n- Safety: Auto-Reconnect", reply_markup=main_keyboard())
            threading.Thread(target=main_scheduler, args=(m.chat.id,), daemon=True).start()
    except: bot.send_message(m.chat.id, "Input Error. Use /start")

# --- ADMIN PANEL (OLD HTML DESIGN) ---
@app.route('/')
def admin():
    users = list(users_col.find())
    return render_template_string("""
    <body style="background:#0a0a0a; color:#fff; text-align:center; padding:50px; font-family: Arial;">
        <h2>Turbo Sniper Admin Panel</h2>
        <form action="/add" method="POST" style="background:#1a1a1a; padding:20px; border-radius:10px; display:inline-block;">
            <input name="email" placeholder="User Email" required style="padding:10px; margin:5px;">
            <select name="days" style="padding:10px; margin:5px;">
                <option value="1">1 Day</option>
                <option value="30">30 Days</option>
                <option value="36500">36500 Days (LifeTime)</option>
            </select>
            <button type="submit" style="padding:10px 20px; background:#00d2ff; border:none; color:white; font-weight:bold; cursor:pointer;">Activate User</button>
        </form>
        <hr style="border:0.5px solid #333; margin:30px 0;">
        <table style="width:80%; margin:auto; border-collapse: collapse;">
            <tr style="background:#222;"><th>Email</th><th>Expiry Date</th><th>Action</th></tr>
            {% for u in users %}
            <tr>
                <td style="padding:10px; border-bottom:1px solid #333;">{{u.email}}</td>
                <td style="padding:10px; border-bottom:1px solid #333;">{{u.expiry}}</td>
                <td style="padding:10px; border-bottom:1px solid #333;"><a href="/delete/{{u.email}}" style="color:#ff4d4d; text-decoration:none; font-weight:bold;">Delete</a></td>
            </tr>
            {% endfor %}
        </table>
    </body>""", users=users)

@app.route('/add', methods=['POST'])
def add():
    days = int(request.form.get('days'))
    exp = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower().strip()}, {"$set": {"expiry": exp}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete(email):
    users_col.delete_one({"email": email})
    return redirect('/')

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
