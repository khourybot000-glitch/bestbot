import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
TOKEN = "8433565422:AAFGCKaNnJ-A4T5b2RXPDjjbzzL1dQJtRFk"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

def get_digit(price):
    try:
        val = int(round(float(price) * 100))
        return val % 10, "{:.2f}".format(float(price))
    except: return None, None

def quick_check(token, contract_id):
    ws = None
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=5)
        ws.send(json.dumps({"authorize": token}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
        res = json.loads(ws.recv())
        ws.close()
        return res
    except:
        if ws: ws.close()
        return None

# --- TRADING ENGINE (STREAM MODE) ---
def trading_process(chat_id):
    last_min = -1
    target_digit = None
    
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
            ws.send(json.dumps({"authorize": session['api_token']}))
            ws.recv()
            ws.send(json.dumps({"ticks": "R_100", "subscribe": 1}))
            
            bot.send_message(chat_id, "📡 *Connected!* Waiting for :00 second to analyze...")
            
            while True:
                session = active_sessions_col.find_one({"chat_id": chat_id})
                if not session or not session.get("is_running"): break
                
                raw = ws.recv()
                data = json.loads(raw)
                
                if "tick" in data:
                    tick_time = datetime.fromtimestamp(data["tick"]["epoch"])
                    price = data["tick"]["quote"]
                    
                    # 1. Analysis at :00
                    if tick_time.second == 0 and tick_time.minute != last_min:
                        last_min = tick_time.minute
                        target_digit, disp_p = get_digit(price)
                        bot.send_message(chat_id, f"🔍 *Analysis @ :00*\n🔢 Target Digit: `{target_digit}`\n💰 Price: `{disp_p}`\n🔭 Hunting for entry...")
                    
                    # 2. Hunting
                    if target_digit is not None and tick_time.minute == last_min:
                        curr_digit, _ = get_digit(price)
                        
                        if curr_digit == target_digit:
                            stake = session["current_stake"]
                            ws.send(json.dumps({
                                "buy": 1, "price": stake,
                                "parameters": {
                                    "amount": stake, "basis": "stake", "contract_type": "DIGITDIFF",
                                    "currency": "USD", "duration": 1, "duration_unit": "t",
                                    "symbol": "R_100", "barrier": str(target_digit)
                                }
                            }))
                            buy_res = json.loads(ws.recv())
                            
                            if "buy" in buy_res:
                                c_id = buy_res["buy"]["contract_id"]
                                bot.send_message(chat_id, f"🎯 *Digit {target_digit} Spotted!* Trade entered.")
                                target_digit = None
                                ws.close() 
                                
                                time.sleep(8) # Wait 8 seconds as requested
                                result = quick_check(session['api_token'], c_id)
                                if result:
                                    profit = float(result["proposal_open_contract"].get("profit", 0))
                                    handle_stats(chat_id, profit)
                                break 
                                
            if ws: ws.close()
        except:
            time.sleep(2)
            continue

def handle_stats(chat_id, profit):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    is_win = profit > 0
    new_total = session["total_profit"] + profit
    new_wins = session.get("win_count", 0) + (1 if is_win else 0)
    new_losses = session.get("loss_count", 0) + (0 if is_win else 1)
    
    stop = False
    if is_win:
        nxt = session["initial_stake"]
    else:
        if session["current_stake"] > session["initial_stake"]:
            nxt = session["initial_stake"]
            stop = True # Stop after 2 losses (Martingale failed)
        else:
            nxt = float("{:.2f}".format(session["initial_stake"] * 14)) # x14 Martingale

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        "total_profit": new_total, 
        "current_stake": nxt,
        "win_count": new_wins,
        "loss_count": new_losses
    }})

    status_icon = "✅ WIN" if is_win else "❌ LOSS"
    stats_msg = (
        f"📊 *Trade Result:* {status_icon}\n"
        f"💰 Profit: `{profit:.2f}`\n"
        f"💵 Total P/L: `{new_total:.2f}`\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🏆 Wins: `{new_wins}` | 💀 Losses: `{new_losses}`"
    )
    bot.send_message(chat_id, stats_msg)

    if stop or new_total >= session["target_profit"]:
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
        reason = "Target Reached! 🏆" if new_total >= session["target_profit"] else "2 Consecutive Losses. 🛑"
        bot.send_message(chat_id, f"🔚 *Bot Stopped*\nReason: {reason}", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

# --- ADMIN PANEL (HTML) ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin Panel</title>
<style>
    body { font-family: 'Segoe UI', sans-serif; background: #f4f7f6; padding: 20px; text-align: center; }
    .box { max-width: 850px; margin: auto; background: white; padding: 30px; border-radius: 15px; box-shadow: 0 5px 15px rgba(0,0,0,0.1); }
    .form { display: flex; gap: 10px; margin-bottom: 30px; background: #2c3e50; padding: 20px; border-radius: 10px; }
    input, select { padding: 10px; border-radius: 5px; border: 1px solid #ddd; flex: 1; }
    button { background: #2ecc71; color: white; border: none; padding: 10px 20px; border-radius: 5px; cursor: pointer; font-weight: bold; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 12px; border-bottom: 1px solid #eee; text-align: left; }
    th { background: #f8f9fa; }
</style></head>
<body><div class="box">
    <h2>💎 User Access Management</h2>
    <form class="form" action="/add" method="POST">
        <input type="email" name="email" placeholder="User Email" required>
        <select name="days"><option value="1">1 Day</option><option value="30">30 Days</option><option value="36500">Lifetime</option></select>
        <button type="submit">Add User</button>
    </form>
    <table><thead><tr><th>Email</th><th>Expiry</th><th>Action</th></tr></thead>
        <tbody>{% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" style="color:red; font-weight:bold;">Delete</a></td></tr>{% endfor %}</tbody>
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
@bot.message_handler(commands=['start', 'START 🚀'])
def start_bot(m):
    bot.send_message(m.chat.id, "👋 Welcome! Please enter your registered email:", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Access Granted! Enter your API Token:"); bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 Unauthorized or Expired.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"api_token": m.text.strip()}}, upsert=True)
    bot.send_message(m.chat.id, "Enter Initial Stake (e.g., 1):"); bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    try:
        s = float(m.text); active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": s, "current_stake": s}})
        bot.send_message(m.chat.id, "Enter Target Profit (TP):"); bot.register_next_step_handler(m, save_tp)
    except: bot.send_message(m.chat.id, "Invalid number.")

def save_tp(m):
    try:
        tp = float(m.text)
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {
            "target_profit": tp, "total_profit": 0, "win_count": 0, "loss_count": 0, "is_running": True
        }})
        bot.send_message(m.chat.id, "🚀 Bot is Running! Analyzing at :00 seconds...", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trading_process, args=(m.chat.id,), daemon=True).start()
    except: bot.send_message(m.chat.id, "Invalid number.")

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_btn(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    bot.send_message(m.chat.id, "🛑 Bot Stopped.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
