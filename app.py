import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
BOT_TOKEN = "8433565422:AAGQcmEJPEp_iM4ONFG6Fz1Tv2fPbkfkGes"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V27_Final']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

trade_locks = {}

# --- KEYBOARDS ---
def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('START 🚀', 'STOP 🛑')
    return markup

# --- UTILITY: WEBSOCKET ---
def get_safe_connection(token):
    while True:
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
            ws.send(json.dumps({"authorize": token}))
            res = json.loads(ws.recv())
            if "authorize" in res: return ws, res["authorize"].get("currency", "USD")
            ws.close()
        except: time.sleep(2)

# --- CORE ENGINE: SMART DELAY LOGIC ---
def user_trading_loop(chat_id, token):
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id, "is_running": True})
        if not session: break

        now = datetime.now()
        # Trigger ONLY at Second 0 if no trade is currently locked
        if now.second == 0 and not trade_locks.get(chat_id, False):
            # Whether it's a normal trade or a martingale, we always re-analyze at Second 0
            threading.Thread(target=run_analysis_and_trade, args=(chat_id, token)).start()
            time.sleep(50) 
        time.sleep(0.5)

def run_analysis_and_trade(chat_id, token):
    trade_locks[chat_id] = True
    session = active_sessions_col.find_one({"chat_id": chat_id, "is_running": True})
    if not session:
        trade_locks[chat_id] = False
        return

    ws = None
    try:
        ws, currency = get_safe_connection(token)
        acc_data = session["accounts_data"][token]
        stake = acc_data["current_stake"]

        # 1. Analyze 30 Ticks
        ws.send(json.dumps({"ticks_history": "R_100", "count": 30, "end": "latest", "style": "ticks"}))
        res = json.loads(ws.recv())
        
        target = None
        if "history" in res:
            p = res["history"]["prices"]
            # Signal: UP if current > start of 30 ticks, else DOWN
            target = "CALL" if p[-1] > p[0] else "PUT"

        # 2. Execute Trade (Normal or Martingale)
        if target:
            ws.send(json.dumps({
                "buy": "1", "price": stake,
                "parameters": {
                    "amount": stake, "basis": "stake", "contract_type": target,
                    "duration": 5, "duration_unit": "t", "symbol": "R_100", "currency": currency
                }
            }))
            buy_res = json.loads(ws.recv())
            if "buy" in buy_res:
                contract_id = buy_res["buy"]["contract_id"]
                is_m = " (Martingale)" if acc_data.get("streak", 0) > 0 else ""
                bot.send_message(chat_id, f"🛰️ **Entry Active{is_m}**\nSignal: `{target}` | Stake: `${stake}`")
                
                time.sleep(18) # Result waiting time
                monitor_result(chat_id, token, contract_id)
            else: trade_locks[chat_id] = False
        else: trade_locks[chat_id] = False
        
        if ws: ws.close()
    except:
        trade_locks[chat_id] = False

def monitor_result(chat_id, token, contract_id):
    while True:
        try:
            ws, _ = get_safe_connection(token)
            ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
            res = json.loads(ws.recv())
            if "proposal_open_contract" in res:
                data = res["proposal_open_contract"]
                if data.get("is_expired"):
                    profit = float(data.get("profit", 0))
                    ws.close()
                    handle_outcome(chat_id, token, profit)
                    break
            ws.close()
            time.sleep(1)
        except: time.sleep(1)

def handle_outcome(chat_id, token, profit):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session: return
    
    acc = session["accounts_data"][token]
    is_win = profit > 0
    
    # Logic: Martingale prepared for NEXT Second 0 analysis
    new_streak = 0 if is_win else acc.get("streak", 0) + 1
    new_stake = session["initial_stake"] if is_win else round(acc["current_stake"] * 2.2, 2)
    new_total_profit = round(acc["total_profit"] + profit, 2)
    
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.streak": new_streak,
        f"accounts_data.{token}.total_profit": new_total_profit,
        f"accounts_data.{token}.wins": acc.get("wins", 0) + (1 if is_win else 0),
        f"accounts_data.{token}.losses": acc.get("losses", 0) + (0 if is_win else 1)
    }})

    status = "✅ WIN" if is_win else "❌ LOSS"
    bot.send_message(chat_id, f"{status} (${profit})\nStreak: {new_streak}/5 | Total: {new_total_profit}")

    # Stop Conditions
    if new_streak >= 5:
        stop_session(chat_id, "STOP LOSS REACHED (5 Losses). Bot Terminated.")
    elif new_total_profit >= session["target_profit"]:
        stop_session(chat_id, "TARGET REACHED! Bot Terminated.")
    else:
        # Release lock: Loop will wait for the next Second 0 to re-analyze and enter
        trade_locks[chat_id] = False

def stop_session(chat_id, reason):
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
    trade_locks[chat_id] = False
    bot.send_message(chat_id, f"🛑 **Bot Stopped**\nReason: {reason}", reply_markup=main_keyboard())

# --- ADMIN PANEL & TELEGRAM HANDLERS ---
@app.route('/')
def admin():
    users = list(users_col.find())
    return render_template_string("""
    <body style="background:#0f172a; color:#f8fafc; font-family:sans-serif; text-align:center; padding:50px;">
        <div style="background:#1e293b; padding:20px; border-radius:12px; display:inline-block; width:85%;">
            <h2>Sniper Admin v27</h2>
            <form action="/add" method="POST">
                <input name="email" placeholder="Email" required style="padding:10px;">
                <select name="days" style="padding:10px;">
                    <option value="1">1 Day</option>
                    <option value="30">30 Days</option>
                    <option value="36500">LifeTime</option>
                </select>
                <button type="submit" style="padding:10px; background:#38bdf8; border:none; border-radius:5px;">Activate</button>
            </form>
            <table style="width:100%; margin-top:20px; text-align:left;">
                <tr style="border-bottom:2px solid #334155;"><th>Email</th><th>Expiry</th><th>Action</th></tr>
                {% for u in users %}
                <tr>
                    <td style="padding:10px; border-bottom:1px solid #334155;">{{u.email}}</td>
                    <td style="padding:10px; border-bottom:1px solid #334155;">{{u.expiry}}</td>
                    <td style="padding:10px; border-bottom:1px solid #334155;"><a href="/delete/{{u.email}}" style="color:#f43f5e;">Delete</a></td>
                </tr>
                {% endfor %}
            </table>
        </div>
    </body>""", users=users)

@app.route('/add', methods=['POST'])
def add():
    exp = (datetime.now() + timedelta(days=int(request.form.get('days')))).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower().strip()}, {"$set": {"expiry": exp}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete(email):
    users_col.delete_one({"email": email})
    return redirect('/')

@bot.message_handler(commands=['start'])
def welcome(m):
    trade_locks[m.chat.id] = False
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "Enter your Email:", reply_markup=main_keyboard())
    bot.register_next_step_handler(m, auth)

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def b_start(m): welcome(m)

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def b_stop(m): stop_session(m.chat.id, "Manual Stop.")

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "Enter API Token:")
        bot.register_next_step_handler(m, lambda msg: setup_stake(msg, msg.text.strip()))
    else: bot.send_message(m.chat.id, "🚫 Denied.")

def setup_stake(m, token):
    bot.send_message(m.chat.id, "Initial Stake:")
    bot.register_next_step_handler(m, lambda msg: setup_target(msg, token, float(msg.text)))

def setup_target(m, token, stake):
    bot.send_message(m.chat.id, "Target Profit:")
    bot.register_next_step_handler(m, lambda msg: start_engine(msg, token, stake, float(msg.text)))

def start_engine(m, token, stake, target):
    acc_data = {token: {"current_stake": stake, "total_profit": 0, "streak": 0, "wins": 0, "losses": 0}}
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {
        "is_running": True, "tokens": [token], "initial_stake": stake, "target_profit": target, "accounts_data": acc_data
    }}, upsert=True)
    bot.send_message(m.chat.id, "🛰️ Sniper V27 Active. Analyzing at Sec 0 (5-Loss SL).")
    threading.Thread(target=user_trading_loop, args=(m.chat.id, token), daemon=True).start()

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
