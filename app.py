import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
# التوكن الجديد الذي أرفقته
BOT_TOKEN = "8433565422:AAGuhwV-zvwBzNL7VKgIlxfQ5J3u8ncmlZc"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V5_LifeTime']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

# لوحة الأزرار الدائمة
def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('START 🚀', 'STOP 🛑')
    return markup

# --- TRADING ENGINE ---

def execute_trade(chat_id, token, contract_type, stake):
    ws = None
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": token}))
        auth_res = json.loads(ws.recv())
        currency = auth_res.get("authorize", {}).get("currency", "USD")

        ws.send(json.dumps({
            "buy": "1", "price": stake,
            "parameters": {
                "amount": stake, "basis": "stake", "contract_type": contract_type,
                "duration": 5, "duration_unit": "t",
                "symbol": "R_100", "currency": currency
            }
        }))
        
        buy_res = json.loads(ws.recv())
        if "buy" in buy_res:
            contract_id = buy_res["buy"]["contract_id"]
            bot.send_message(chat_id, f"🚀 **Trade Active**\nType: `{contract_type}` | Stake: `{stake}$`", reply_markup=main_keyboard())
            
            time.sleep(10)
            
            while True:
                session = active_sessions_col.find_one({"chat_id": chat_id})
                if not session or not session.get("is_running"):
                    ws.close()
                    return

                ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
                res = json.loads(ws.recv())
                if "proposal_open_contract" in res:
                    data = res["proposal_open_contract"]
                    if data.get("is_expired"):
                        profit = float(data.get("profit", 0))
                        ws.close()
                        process_result(chat_id, token, contract_type, profit)
                        break
                time.sleep(1)
        else:
            ws.close()
    except Exception:
        if ws: ws.close()

def process_result(chat_id, token, last_type, profit):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session or not session.get("is_running"): return
    
    acc = session["accounts_data"][token]
    is_win = profit > 0
    new_total = round(acc["total_profit"] + profit, 2)
    wins = acc.get("wins_count", 0) + (1 if is_win else 0)
    losses = acc.get("losses_count", 0) + (0 if is_win else 1)
    
    if is_win:
        new_stake = session["initial_stake"]
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
            f"accounts_data.{token}.current_stake": new_stake,
            f"accounts_data.{token}.streak": 0,
            f"accounts_data.{token}.total_profit": new_total,
            f"accounts_data.{token}.wins_count": wins,
            f"accounts_data.{token}.losses_count": losses
        }})
        bot.send_message(chat_id, f"✅ **WIN**\nProfit: `{profit}$` | Total: `{new_total}$` \n📊 W:{wins} L:{losses}")
        
        if new_total >= session["target_profit"]:
            reset_and_stop(chat_id, f"🎯 Target Profit Reached (+{new_total}$)! Session Wiped.")
            return
    else:
        new_streak = acc.get("streak", 0) + 1
        if new_streak >= 4:
            reset_and_stop(chat_id, "❌ Stop Loss: 4 Consecutive Losses. Session Wiped.")
            return

        new_stake = round(acc["current_stake"] * 2.2, 2)
        next_type = "CALL" if last_type == "PUT" else "PUT"
        
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
            f"accounts_data.{token}.current_stake": new_stake,
            f"accounts_data.{token}.streak": new_streak,
            f"accounts_data.{token}.total_profit": new_total,
            f"accounts_data.{token}.wins_count": wins,
            f"accounts_data.{token}.losses_count": losses
        }})
        
        bot.send_message(chat_id, f"❌ **LOSS**\nRevenge Martingale x2.2 | Next Immediate: {next_type}")
        threading.Thread(target=execute_trade, args=(chat_id, token, next_type, new_stake)).start()

def run_zigzag_analysis(chat_id, token):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session or not session.get("is_running") or session["accounts_data"][token].get("streak", 0) > 0: return

    ticks = []
    ws = None
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": token}))
        ws.recv()
        ws.send(json.dumps({"ticks": "R_100", "subscribe": 1}))
        
        while len(ticks) < 30:
            if not active_sessions_col.find_one({"chat_id": chat_id, "is_running": True}):
                ws.close(); return
            msg = json.loads(ws.recv())
            if "tick" in msg: ticks.append(msg["tick"]["quote"])
        
        candles = []
        for i in range(0, 30, 5):
            seg = ticks[i:i+5]
            candles.append("UP" if seg[-1] > seg[0] else "DOWN")

        put_p = ["UP", "DOWN", "UP", "DOWN", "UP", "DOWN"]
        call_p = ["DOWN", "UP", "DOWN", "UP", "DOWN", "UP"]

        target = None
        if candles == put_p: target = "PUT"
        elif candles == call_p: target = "CALL"

        if target:
            stake = session["accounts_data"][token]["current_stake"]
            ws.close()
            execute_trade(chat_id, token, target, stake)
        else:
            ws.close()
    except:
        if ws: ws.close()

def reset_and_stop(chat_id, reason):
    active_sessions_col.delete_one({"chat_id": chat_id})
    bot.send_message(chat_id, f"🛑 **SYSTEM RESET**\n{reason}\n\nClick **START 🚀** to setup a new session.", reply_markup=main_keyboard())

def main_scheduler(chat_id):
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        if datetime.now().second % 10 == 0:
            threading.Thread(target=run_zigzag_analysis, args=(chat_id, session['tokens'][0])).start()
            time.sleep(2)
        time.sleep(0.5)

# --- TELEGRAM HANDLERS ---

@bot.message_handler(commands=['start'])
def cmd_start(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "Welcome. New Session Setup.\n\nStep 1: Enter Email:", reply_markup=main_keyboard())
    bot.register_next_step_handler(m, auth)

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def btn_start(m):
    cmd_start(m)

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def btn_stop(m):
    reset_and_stop(m.chat.id, "Manual termination. Session wiped.")

def auth(m):
    if m.text == 'STOP 🛑': return btn_stop(m)
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "Step 2: Enter Token:")
        bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 No Access.")

def save_token(m):
    if m.text == 'STOP 🛑': return btn_stop(m)
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False}}, upsert=True)
    bot.send_message(m.chat.id, "Step 3: Initial Stake:")
    bot.register_next_step_handler(m, lambda msg: save_config(msg, "initial_stake"))

def save_config(m, key):
    if m.text == 'STOP 🛑': return btn_stop(m)
    try:
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {key: float(m.text)}})
        if key == "initial_stake":
            bot.send_message(m.chat.id, "Step 4: Target Profit ($):")
            bot.register_next_step_handler(m, lambda msg: save_config(msg, "target_profit"))
        else:
            sess = active_sessions_col.find_one({"chat_id": m.chat.id})
            accs = {sess["tokens"][0]: {"current_stake": sess["initial_stake"], "total_profit": 0.0, "streak": 0, "wins_count": 0, "losses_count": 0}}
            active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
            bot.send_message(m.chat.id, "✅ Ready! Bot is running.", reply_markup=main_keyboard())
            threading.Thread(target=main_scheduler, args=(m.chat.id,), daemon=True).start()
    except:
        bot.send_message(m.chat.id, "Error. Start again /start")

# --- ADMIN PANEL ---
HTML_ADMIN = """
<body style="background:#0a0a0a; color:#fff; font-family:sans-serif; text-align:center; padding:50px;">
    <h2>LifeTime Admin Panel</h2>
    <form action="/add" method="POST">
        <input name="email" placeholder="User Email" required style="padding:10px;">
        <select name="days" style="padding:10px;">
            <option value="1">1 Day</option>
            <option value="30">30 Days</option>
            <option value="36500">LIFE TIME (36500 Days)</option>
        </select>
        <button type="submit" style="padding:10px; background:cyan;">Activate</button>
    </form>
    <hr>
    <table style="width:100%; color:white;">
        <tr><th>Email</th><th>Expiry</th><th>Action</th></tr>
        {% for u in users %}
        <tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" style="color:red">Delete</a></td></tr>
        {% endfor %}
    </table>
</body>
"""

@app.route('/')
def admin(): return render_template_string(HTML_ADMIN, users=list(users_col.find()))

@app.route('/add', methods=['POST'])
def add():
    exp = (datetime.now() + timedelta(days=int(request.form.get('days')))).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower().strip()}, {"$set": {"expiry": exp}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete(email): users_col.delete_one({"email": email}); return redirect('/')

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
