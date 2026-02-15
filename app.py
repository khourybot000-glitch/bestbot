import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
BOT_TOKEN = "8433565422:AAE9yQrlEujaQAtsNoIZJIRcgLV7aYKE6Iw"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V24_Final_Signal']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

trade_locks = {}

# --- KEYBOARDS ---
def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    markup.add(types.KeyboardButton('START 🚀'), types.KeyboardButton('STOP 🛑'))
    return markup

# --- UTILITY ---
def get_safe_connection(token):
    while True:
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
            ws.send(json.dumps({"authorize": token}))
            res = json.loads(ws.recv())
            if "authorize" in res: return ws, res["authorize"].get("currency", "USD")
            ws.close()
        except: time.sleep(2)

# --- CORE ENGINE ---
def user_trading_loop(chat_id, token):
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id, "is_running": True})
        if not session: break

        now = datetime.now()
        # التحليل الجديد فقط عند الثانية 0
        if now.second == 0 and not trade_locks.get(chat_id, False):
            acc_data = session["accounts_data"][token]
            # إذا لم نكن في وضع مضاعفة، ابدأ تحليل جديد
            if acc_data.get("streak", 0) == 0:
                threading.Thread(target=run_trade_logic, args=(chat_id, token)).start()
                time.sleep(50) 
        
        time.sleep(0.5)

def run_trade_logic(chat_id, token, force_target=None, force_stake=None):
    trade_locks[chat_id] = True
    session = active_sessions_col.find_one({"chat_id": chat_id, "is_running": True})
    if not session:
        trade_locks[chat_id] = False
        return

    ws = None
    try:
        ws, currency = get_safe_connection(token)
        target = force_target
        stake = force_stake if force_stake else session["accounts_data"][token]["current_stake"]

        # تحليل 30 تيك (6 شموع)
        if not target:
            ws.send(json.dumps({"ticks_history": "R_100", "count": 30, "end": "latest", "style": "ticks"}))
            res = json.loads(ws.recv())
            if "history" in res:
                p = res["history"]["prices"]
                c1 = "UP" if p[4] > p[0] else "DOWN"
                c2 = "UP" if p[9] > p[5] else "DOWN"
                c3 = "UP" if p[14] > p[10] else "DOWN"
                c4 = "UP" if p[19] > p[15] else "DOWN"
                c5 = "UP" if p[24] > p[20] else "DOWN"
                c6 = "UP" if p[29] > p[25] else "DOWN"
                
                pattern = [c1, c2, c3, c4, c5, c6]
                is_alternating = all(pattern[i] != pattern[i+1] for i in range(len(pattern)-1))
                
                if is_alternating:
                    target = "CALL" if c6 == "DOWN" else "PUT"

        if target:
            ws.send(json.dumps({
                "buy": "1", "price": stake,
                "parameters": {
                    "amount": stake, "basis": "stake", "contract_type": target,
                    "duration": 4, "duration_unit": "t", "symbol": "R_100", "currency": currency
                }
            }))
            buy_res = json.loads(ws.recv())
            if "buy" in buy_res:
                contract_id = buy_res["buy"]["contract_id"]
                bot.send_message(chat_id, "Trade Entered")
                time.sleep(8) # انتظار النتيجة
                monitor_result(chat_id, token, contract_id, target)
            else: trade_locks[chat_id] = False
        else:
            trade_locks[chat_id] = False 
        
        if ws: ws.close()
    except:
        trade_locks[chat_id] = False

def monitor_result(chat_id, token, contract_id, last_type):
    while True:
        try:
            ws, _ = get_safe_connection(token)
            ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
            res = json.loads(ws.recv())
            if "proposal_open_contract" in res:
                data = res["proposal_open_contract"]
                if data.get("is_expired") or data.get("status") in ["won", "lost"]:
                    profit = float(data.get("profit", 0))
                    ws.close()
                    handle_outcome(chat_id, token, profit, last_type)
                    break
            ws.close()
            time.sleep(0.5)
        except: time.sleep(1)

def handle_outcome(chat_id, token, profit, last_type):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session: return
    
    acc = session["accounts_data"][token]
    is_win = profit > 0
    new_streak = 0 if is_win else acc.get("streak", 0) + 1
    new_stake = session["initial_stake"] if is_win else round(acc["current_stake"] * 2.2, 2)
    new_total_profit = round(acc["total_profit"] + profit, 2)
    wins = acc.get("wins", 0) + (1 if is_win else 0)
    losses = acc.get("losses", 0) + (0 if is_win else 1)
    
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.streak": new_streak,
        f"accounts_data.{token}.last_type": last_type,
        f"accounts_data.{token}.total_profit": new_total_profit,
        f"accounts_data.{token}.wins": wins,
        f"accounts_data.{token}.losses": losses
    }})

    bot.send_message(chat_id, f"{'✅ WIN' if is_win else '❌ LOSS'}\nProfit: {new_total_profit}\nWins: {wins} | Losses: {losses}")

    # التوقف النهائي
    if new_streak >= 5 or new_total_profit >= session["target_profit"]:
        active_sessions_col.delete_one({"chat_id": chat_id})
        trade_locks[chat_id] = False
        bot.send_message(chat_id, "🛑 Session Finished. Data Deleted.", reply_markup=main_keyboard())
    else:
        # إذا كانت خسارة، ادخل المضاعفة "فوراً" بعكس الاتجاه
        if not is_win:
            next_type = "PUT" if last_type == "CALL" else "CALL"
            threading.Thread(target=run_trade_logic, args=(chat_id, token, next_type, new_stake)).start()
        else:
            trade_locks[chat_id] = False

# --- UI HANDLERS ---
@bot.message_handler(commands=['start'])
def welcome_msg(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    trade_locks[m.chat.id] = False
    bot.send_message(m.chat.id, "♻️ System Reset. Enter Email:", reply_markup=main_keyboard())
    bot.register_next_step_handler(m, auth)

@bot.message_handler(func=lambda m: m.text in ['STOP 🛑', 'START 🚀'])
def ui_buttons(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    trade_locks[m.chat.id] = False
    if m.text == 'STOP 🛑':
        bot.send_message(m.chat.id, "🛑 Data Deleted.", reply_markup=main_keyboard())
    else:
        bot.send_message(m.chat.id, "♻️ Reset. Enter Email:", reply_markup=main_keyboard())
        bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "API Token:")
        bot.register_next_step_handler(m, lambda msg: setup_stake(msg, msg.text.strip()))
    else: bot.send_message(m.chat.id, "🚫 Denied.", reply_markup=main_keyboard())

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
    bot.send_message(m.chat.id, "🛰️ Running...", reply_markup=main_keyboard())
    threading.Thread(target=user_trading_loop, args=(m.chat.id, token), daemon=True).start()

# --- ADMIN PANEL ---
@app.route('/')
def admin():
    users = list(users_col.find())
    return render_template_string("""
    <body style="background:#0f172a; color:#f8fafc; font-family:sans-serif; text-align:center; padding:50px;">
        <div style="background:#1e293b; padding:20px; border-radius:12px; display:inline-block; width:90%; max-width:500px;">
            <h3>Admin Panel</h3>
            <form action="/add" method="POST">
                <input name="email" placeholder="Email" required style="padding:10px;">
                <select name="days" style="padding:10px;">
                    <option value="1">1 Day</option><option value="7">7 Days</option>
                    <option value="30">30 Days</option><option value="36500">LifeTime</option>
                </select>
                <button type="submit" style="padding:10px; background:#38bdf8; border:none; cursor:pointer;">Activate</button>
            </form>
            <table style="width:100%; margin-top:20px; text-align:left;">
                {% for u in users %}
                <tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" style="color:red;">Delete</a></td></tr>
                {% endfor %}
            </table>
        </div>
    </body>""", users=users)

@app.route('/add', methods=['POST'])
def add():
    exp = (datetime.now() + timedelta(days=int(request.form.get('days', 1)))).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower().strip()}, {"$set": {"expiry": exp}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete(email):
    users_col.delete_one({"email": email})
    return redirect('/')

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
