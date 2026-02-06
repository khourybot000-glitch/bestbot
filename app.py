import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION (UPDATED TOKEN) ---
TOKEN = "8433565422:AAEbKctJA6kUo7sdvgYBpFrqP8APIOoET2E"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V3']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

# --- UTILS ---
def check_double_zero_logic(price):
    try:
        # يضمن وجود رقمين بعد الفاصلة حتى لو كانا أصفاراً
        formatted_price = "{:.2f}".format(float(price))
        decimal_part = formatted_price.split(".")[1]
        return decimal_part == "00"
    except: return False

def quick_check(token, contract_id):
    ws = None
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": token}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
        res = json.loads(ws.recv())
        ws.close()
        return res
    except:
        if ws: ws.close()
        return None

# --- TRADING ENGINE ---
def trading_process(chat_id):
    history = [] 
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
            ws.send(json.dumps({"authorize": session['api_token']}))
            ws.recv()
            ws.send(json.dumps({"ticks": "R_100", "subscribe": 1}))
            
            bot.send_message(chat_id, "📡 *Bot Active*\nStrategy: .00 Counter-Trend\nDuration: 5 Ticks")
            
            while True:
                session = active_sessions_col.find_one({"chat_id": chat_id})
                if not session or not session.get("is_running"): break
                
                raw = ws.recv()
                data = json.loads(raw)
                if "tick" in data:
                    curr_price = float(data["tick"]["quote"])
                    history.append(curr_price)
                    if len(history) > 6: history.pop(0) 
                    
                    # الفحص عند توفر 6 تيكات (الحالي + 5 سابقين) وظهور .00
                    if len(history) >= 6 and check_double_zero_logic(curr_price):
                        old_price = history[0] # السعر قبل 5 تيكات
                        ctype = None
                        barrier = ""
                        
                        # الدخول عكس الاتجاه
                        if curr_price > old_price: # صعود -> PUT
                            ctype, barrier = "PUT", "+0.5"
                        elif curr_price < old_price: # هبوط -> CALL
                            ctype, barrier = "CALL", "-0.5"
                        
                        if ctype:
                            stake = session["current_stake"]
                            ws.send(json.dumps({
                                "buy": 1, "price": stake,
                                "parameters": {
                                    "amount": stake, "basis": "stake", "contract_type": ctype,
                                    "currency": "USD", "duration": 5, "duration_unit": "t",
                                    "symbol": "R_100", "barrier": barrier
                                }
                            }))
                            resp = json.loads(ws.recv())
                            if "buy" in resp:
                                c_id = resp["buy"]["contract_id"]
                                bot.send_message(chat_id, f"🎯 *Found {curr_price:.2f}*\nEntering {ctype} (Counter-Trend)")
                                ws.close()
                                
                                # انتظار انتهاء الـ 5 تيكات + وقت معالجة
                                time.sleep(25)
                                res = quick_check(session['api_token'], c_id)
                                if res:
                                    handle_stats(chat_id, float(res["proposal_open_contract"].get("profit", 0)))
                                break 
            if ws: ws.close()
        except Exception:
            time.sleep(2); continue

def handle_stats(chat_id, profit):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    is_win = profit > 0
    losses = session.get("consecutive_losses", 0)
    
    stop_signal = False
    if is_win:
        next_stake, losses = session["initial_stake"], 0
    else:
        losses += 1
        if losses >= 3:
            next_stake, stop_signal = session["initial_stake"], True
        else:
            next_stake = float("{:.2f}".format(session["current_stake"] * 6))

    new_total = session.get("total_profit", 0) + profit
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        "total_profit": new_total, "current_stake": next_stake,
        "win_count": session.get("win_count", 0) + (1 if is_win else 0),
        "loss_count": session.get("loss_count", 0) + (0 if is_win else 1),
        "consecutive_losses": losses
    }})

    status = "✅ WIN" if is_win else "❌ LOSS"
    msg = (f"📊 *Trade Result:* {status}\n"
           f"💰 Profit: `{profit:.2f}` | Total: `{new_total:.2f}`\n"
           f"🏆 Wins: `{session.get('win_count', 0) + (1 if is_win else 0)}` | 💀 Losses: `{session.get('loss_count', 0) + (0 if is_win else 1)}`")
    bot.send_message(chat_id, msg)

    if stop_signal or new_total >= session["target_profit"]:
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
        reason = "Target Reached! 💰" if not stop_signal else "3 Losses Reached! 🛑"
        bot.send_message(chat_id, f"🛑 *Session Ended: {reason}*", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('/start'))

# --- ADMIN PANEL HTML ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin Panel</title>
<style>
    body { font-family: 'Segoe UI', Tahoma, sans-serif; background-color: #f0f2f5; margin: 0; padding: 20px; }
    .container { max-width: 900px; margin: auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); }
    h2 { color: #1a73e8; border-bottom: 2px solid #e8eaed; padding-bottom: 10px; }
    .form-box { background: #f8f9fa; padding: 20px; border-radius: 8px; margin-bottom: 25px; display: flex; gap: 10px; flex-wrap: wrap; }
    input, select { padding: 12px; border: 1px solid #dadce0; border-radius: 6px; flex: 1; min-width: 150px; }
    button { background: #1a73e8; color: white; border: none; padding: 12px 24px; border-radius: 6px; cursor: pointer; font-weight: bold; }
    button:hover { background: #1557b0; }
    table { width: 100%; border-collapse: collapse; margin-top: 15px; }
    th { background: #f1f3f4; color: #5f6368; text-align: left; padding: 12px; }
    td { padding: 12px; border-bottom: 1px solid #f1f3f4; }
    .del-link { color: #d93025; text-decoration: none; font-weight: bold; }
</style></head>
<body><div class="container">
    <h2>💎 Bot Access Manager</h2>
    <form class="form-box" action="/add" method="POST">
        <input type="email" name="email" placeholder="User Email" required>
        <select name="days">
            <option value="1">1 Day Access</option>
            <option value="30">30 Days Access</option>
            <option value="36500">36500 Days (Lifetime)</option>
        </select>
        <button type="submit">Authorize User</button>
    </form>
    <table><thead><tr><th>Email</th><th>Expiry Date</th><th>Action</th></tr></thead>
        <tbody>{% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" class="del-link">Remove</a></td></tr>{% endfor %}</tbody>
    </table>
</div></body></html>
"""

@app.route('/')
def index(): return render_template_string(HTML_ADMIN, users=list(users_col.find()))

@app.route('/add', methods=['POST'])
def add_user():
    e, d = request.form.get('email').lower().strip(), int(request.form.get('days'))
    exp = (datetime.now() + timedelta(days=d)).strftime("%Y-%m-%d")
    users_col.update_one({"email": e}, {"$set": {"expiry": exp}}, upsert=True); return redirect('/')

@app.route('/delete/<email>')
def delete_user(email): users_col.delete_one({"email": email}); return redirect('/')

# --- TELEGRAM COMMANDS ---
@bot.message_handler(commands=['start'])
def start_cmd(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    bot.send_message(m.chat.id, "👋 Welcome! Enter your email:", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(m, auth_step)

def auth_step(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Approved! Enter API Token:"); bot.register_next_step_handler(m, token_step)
    else: bot.send_message(m.chat.id, "🚫 No active subscription found.")

def token_step(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"api_token": m.text.strip()}}, upsert=True)
    bot.send_message(m.chat.id, "Enter Initial Stake:"); bot.register_next_step_handler(m, stake_step)

def stake_step(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text), "current_stake": float(m.text), "consecutive_losses": 0}})
    bot.send_message(m.chat.id, "Enter Target Profit:"); bot.register_next_step_handler(m, tp_step)

def tp_step(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": float(m.text), "total_profit": 0, "win_count": 0, "loss_count": 0, "is_running": True}})
    bot.send_message(m.chat.id, "🚀 Bot Active! Searching for .00 Reversals...", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
    threading.Thread(target=trading_process, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_btn(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    bot.send_message(m.chat.id, "🛑 Bot Stopped.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('/start'))

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
