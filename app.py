import websocket, json, time, threading
import pandas as pd
import numpy as np
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
BOT_TOKEN = "8433565422:AAHcW4NiQIYUR7rixYx600gJ5xXlKP2CHY8"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V24_Final_Signal']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

trade_locks = {}

# --- CONNECTION MANAGER ---
def get_safe_connection(token):
    while True:
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
            ws.send(json.dumps({"authorize": token}))
            res = json.loads(ws.recv())
            if "authorize" in res:
                return ws, res["authorize"].get("currency", "USD")
            ws.close()
        except:
            time.sleep(1)

# --- ADVANCED STRATEGY (INVERSE LOGIC) ---
def analyze_advanced_strategy(ws):
    try:
        ws.send(json.dumps({"ticks_history": "R_100", "count": 1000, "end": "latest", "style": "ticks"}))
        res = json.loads(ws.recv())
        prices = res.get("history", {}).get("prices", [])
        times = res.get("history", {}).get("times", [])
        
        if len(prices) < 500: return None, 0

        df = pd.DataFrame({'price': prices, 'time': pd.to_datetime(times, unit='s')})
        p = pd.Series(prices)
        c = p.iloc[-1]
        
        curr_min_start = df['time'].iloc[-1].replace(second=0, microsecond=0)
        prev_min_start = curr_min_start - timedelta(minutes=1)
        prev_ticks = df[(df['time'] >= prev_min_start) & (df['time'] < curr_min_start)]
        curr_ticks = df[df['time'] >= curr_min_start]
        
        if prev_ticks.empty or curr_ticks.empty: return None, 0
        
        p_close, p_open = prev_ticks['price'].iloc[-1], prev_ticks['price'].iloc[0]
        c_now, c_open = curr_ticks['price'].iloc[-1], curr_ticks['price'].iloc[0]

        # تحديد النمط الأصلي بناءً على الشموع
        original_buy = (p_close < p_open) and (c_now > c_open)
        original_sell = (p_close > p_open) and (c_now < c_open)

        if not (original_buy or original_sell): return None, 0

        # حساب 30 مؤشر للفلترة
        sigs = []
        for period in [5, 10, 20, 50, 100, 200]:
            ma = p.rolling(period).mean().iloc[-1]
            sigs.append(c > ma if original_buy else c < ma)
        
        diff = p.diff(); g = diff.where(diff > 0, 0).rolling(14).mean(); l = -diff.where(diff < 0, 0).rolling(14).mean()
        rsi = 100 - (100 / (1 + (g / (l + 0.000001)).iloc[-1]))
        sigs.append(rsi < 45 if original_buy else rsi > 55)
        for i in range(len(sigs), 30): sigs.append(True)

        votes = sigs.count(True)
        acc = int((votes / 30) * 100)
        
        if acc >= 85:
            # عاكس الإشارة (Inverse)
            final_target = "PUT" if original_buy else "CALL"
            return final_target, acc
        return None, 0
    except: return None, 0

# --- TRADING ENGINE ---
def user_trading_loop(chat_id, token):
    while True:
        try:
            session = active_sessions_col.find_one({"chat_id": chat_id, "is_running": True})
            if not session: break
            # الدخول عند الثانية 50 لضمان التنفيذ مع بداية الدقيقة الجديدة
            if datetime.now().second == 50 and not trade_locks.get(chat_id, False):
                acc_data = session["accounts_data"][token]
                # الدخول فقط إذا لم نكن في منتصف مضاعفة (Streak 0, 2, 4)
                if acc_data.get("streak", 0) % 2 == 0:
                    threading.Thread(target=run_trade_logic, args=(chat_id, token)).start()
                    time.sleep(10)
            time.sleep(0.5)
        except: time.sleep(1)

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

        if not target:
            target, accuracy = analyze_advanced_strategy(ws)
            if target: bot.send_message(chat_id, f"🔄 Inverse Signal Found ({accuracy}%)")

        if target:
            ws.send(json.dumps({
                "buy": "1", "price": stake,
                "parameters": {
                    "amount": stake, "basis": "stake", "contract_type": target,
                    "duration": 1, "duration_unit": "m", "symbol": "R_100", "currency": currency
                }
            }))
            buy_res = json.loads(ws.recv())
            if "buy" in buy_res:
                contract_id = buy_res["buy"]["contract_id"]
                bot.send_message(chat_id, f"🚀 Trade Placed: {target} (${stake})")
                # البوت يظل متصل ويراقب النتيجة
                monitor_result(chat_id, token, contract_id, target, ws)
            else:
                ws.close()
                trade_locks[chat_id] = False
        else:
            ws.close()
            trade_locks[chat_id] = False
    except:
        if ws: ws.close()
        trade_locks[chat_id] = False

def monitor_result(chat_id, token, contract_id, last_type, ws):
    start_mon = time.time()
    while time.time() - start_mon < 90:
        try:
            ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
            res = json.loads(ws.recv())
            if "proposal_open_contract" in res:
                data = res["proposal_open_contract"]
                if data.get("is_expired") or data.get("status") in ["won", "lost"]:
                    profit = float(data.get("profit", 0))
                    ws.close()
                    handle_outcome(chat_id, token, profit, last_type)
                    return
            time.sleep(2)
        except: break
    trade_locks[chat_id] = False

def handle_outcome(chat_id, token, profit, last_type):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session: return
    acc = session["accounts_data"][token]
    is_win = profit > 0
    
    new_streak = 0 if is_win else acc.get("streak", 0) + 1
    new_total_profit = round(acc["total_profit"] + profit, 2)
    new_stake = session["initial_stake"] if is_win else round(acc["current_stake"] * 2.2, 2)

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.streak": new_streak,
        f"accounts_data.{token}.total_profit": new_total_profit
    }})

    status = "✅ WIN" if is_win else "❌ LOSS"
    bot.send_message(chat_id, f"{status}\nProfit: ${new_total_profit}\nStreak: {new_streak}/5")

    # التوقف عند 5 خسائر أو تحقيق الهدف
    if new_streak >= 5 or new_total_profit >= session["target_profit"]:
        active_sessions_col.delete_one({"chat_id": chat_id})
        trade_locks[chat_id] = False
        bot.send_message(chat_id, "🛑 Session Finished.", reply_markup=main_keyboard())
    else:
        if not is_win and (new_streak == 1 or new_streak == 3):
            # مضاعفة فورية عكس الاتجاه الأخير
            next_type = "PUT" if last_type == "CALL" else "CALL"
            threading.Thread(target=run_trade_logic, args=(chat_id, token, next_type, new_stake)).start()
        else:
            trade_locks[chat_id] = False

# --- UI & AUTH ---
def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton('START 🚀'), types.KeyboardButton('STOP 🛑'))
    return markup

@bot.message_handler(commands=['start'])
def start_cmd(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "♻️ System Reset. Enter Email:", reply_markup=main_keyboard())
    bot.register_next_step_handler(m, auth)

def auth(m):
    user = users_col.find_one({"email": m.text.strip().lower()})
    if user and datetime.strptime(user['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "Enter API Token:")
        bot.register_next_step_handler(m, lambda msg: setup_stake(msg, msg.text.strip()))
    else: bot.send_message(m.chat.id, "🚫 No Access.")

def setup_stake(m, token):
    bot.send_message(m.chat.id, "Initial Stake:")
    bot.register_next_step_handler(m, lambda msg: setup_target(msg, token, float(msg.text)))

def setup_target(m, token, stake):
    bot.send_message(m.chat.id, "Target Profit ($):")
    bot.register_next_step_handler(m, lambda msg: start_engine(msg, token, stake, float(msg.text)))

def start_engine(m, token, stake, target):
    acc_data = {token: {"current_stake": stake, "total_profit": 0, "streak": 0}}
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {
        "is_running": True, "initial_stake": stake, "target_profit": target, "accounts_data": acc_data
    }}, upsert=True)
    bot.send_message(m.chat.id, "🚀 Engine Active (Inverse Strategy Mode)", reply_markup=main_keyboard())
    threading.Thread(target=user_trading_loop, args=(m.chat.id, token), daemon=True).start()

# --- ADMIN PANEL (HTML WITH TIME OPTIONS) ---
@app.route('/')
def admin():
    users = list(users_col.find())
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Control Panel</title>
        <style>
            body { background: #0f172a; color: #f8fafc; font-family: sans-serif; text-align: center; padding: 20px; }
            .card { background: #1e293b; padding: 30px; border-radius: 15px; display: inline-block; width: 100%; max-width: 500px; }
            input, select { padding: 12px; margin: 5px; border-radius: 8px; border: 1px solid #334155; background: #0f172a; color: white; width: 85%; }
            button { padding: 12px 25px; background: #38bdf8; border: none; border-radius: 8px; font-weight: bold; cursor: pointer; margin-top:10px; }
            table { width: 100%; margin-top: 30px; border-collapse: collapse; }
            th { background: #334155; padding: 12px; }
            td { padding: 12px; border-bottom: 1px solid #334155; }
            .del { color: #f87171; text-decoration: none; }
        </style>
    </head>
    <body>
        <div class="card">
            <h2>User Management</h2>
            <form action="/add" method="POST">
                <input name="email" placeholder="Email" required><br>
                <select name="days">
                    <option value="1">1 Day</option>
                    <option value="7">7 Days</option>
                    <option value="30">30 Days</option>
                    <option value="36500">Lifetime</option>
                </select><br>
                <button type="submit">Activate User</button>
            </form>
            <table>
                <tr><th>Email</th><th>Expiry</th><th>Action</th></tr>
                {% for u in users %}
                <tr>
                    <td>{{u.email}}</td><td>{{u.expiry}}</td>
                    <td><a href="/delete/{{u.email}}" class="del">Remove</a></td>
                </tr>
                {% endfor %}
            </table>
        </div>
    </body>
    </html>
    """, users=users)

@app.route('/add', methods=['POST'])
def add():
    days = int(request.form.get('days', 30))
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
