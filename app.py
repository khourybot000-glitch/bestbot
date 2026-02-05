import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
TOKEN = "8433565422:AAEGCEOjQytWTigOhjZpeDcPrHYos2zf7Gs"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

def get_digit(price):
    try:
        val = int(round(float(price) * 100))
        return val % 10
    except: return None

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

# --- TRADING ENGINE (TARGET 4 - ONE TRADE PER MINUTE) ---
def trading_process(chat_id):
    last_processed_min = -1
    TARGET_DIGIT = 4
    
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        now = datetime.now()
        
        # Connect exactly at second 00
        if now.second == 0 and now.minute != last_processed_min:
            last_processed_min = now.minute
            
            try:
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
                ws.send(json.dumps({"authorize": session['api_token']}))
                ws.recv()
                ws.send(json.dumps({"ticks": "R_100", "subscribe": 1}))
                
                bot.send_message(chat_id, f"🕒 *Minute {now.minute} Cycle Started*\n🔭 Searching for digit `{TARGET_DIGIT}`...")
                
                start_search = time.time()
                trade_done = False
                
                while time.time() - start_search < 58:
                    raw_data = ws.recv()
                    data = json.loads(raw_data)
                    
                    if "tick" in data:
                        digit = get_digit(data["tick"]["quote"])
                        
                        if digit == TARGET_DIGIT:
                            stake = session["current_stake"]
                            ws.send(json.dumps({
                                "buy": 1, "price": stake,
                                "parameters": {
                                    "amount": stake, "basis": "stake", "contract_type": "DIGITDIFF",
                                    "currency": "USD", "duration": 1, "duration_unit": "t",
                                    "symbol": "R_100", "barrier": str(TARGET_DIGIT)
                                }
                            }))
                            resp = json.loads(ws.recv())
                            
                            if "buy" in resp:
                                c_id = resp["buy"]["contract_id"]
                                bot.send_message(chat_id, f"🎯 *Digit {TARGET_DIGIT} spotted!* Entry executed.")
                                trade_done = True
                                ws.close() # Close after entry
                                
                                # Wait 18 seconds for result verification
                                time.sleep(18)
                                result = quick_check(session['api_token'], c_id)
                                if result:
                                    profit = float(result["proposal_open_contract"].get("profit", 0))
                                    handle_stats(chat_id, profit)
                                break
                
                if not trade_done:
                    bot.send_message(chat_id, f"⏳ Digit {TARGET_DIGIT} not found in this minute.")
                    ws.close()
                    
            except Exception as e:
                time.sleep(2)
        
        time.sleep(0.5)

def handle_stats(chat_id, profit):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    is_win = profit > 0
    
    # Logic: Martingale x14 | Stop after 2 losses
    stop_session = False
    if is_win:
        next_stake = session["initial_stake"]
    else:
        # Check if current stake was already a Martingale stake
        if session["current_stake"] > session["initial_stake"]:
            next_stake = session["initial_stake"]
            stop_session = True # 2nd Loss detected
        else:
            next_stake = float("{:.2f}".format(session["initial_stake"] * 14))

    new_total = session.get("total_profit", 0) + profit
    new_wins = session.get("win_count", 0) + (1 if is_win else 0)
    new_losses = session.get("loss_count", 0) + (0 if is_win else 1)

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        "total_profit": new_total, "current_stake": next_stake,
        "win_count": new_wins, "loss_count": new_losses
    }})

    status_txt = "✅ WIN" if is_win else "❌ LOSS"
    stats_msg = (
        f"📊 *RESULT:* {status_txt}\n"
        f"💰 Profit: `{profit:.2f}` | Total: `{new_total:.2f}`\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🏆 Wins: `{new_wins}` | 💀 Losses: `{new_losses}`"
    )
    bot.send_message(chat_id, stats_msg)

    if stop_session or new_total >= session["target_profit"]:
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
        reason = "Target Profit Reached! 💰" if not stop_session else "2 Consecutive Losses. 🛑"
        bot.send_message(chat_id, f"🔚 *Session Stopped*\nReason: {reason}", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

# --- HTML ADMIN PANEL ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Bot Admin Panel</title>
<style>
    body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f7f6; margin: 0; padding: 20px; }
    .container { max-width: 900px; margin: auto; background: white; padding: 40px; border-radius: 15px; box-shadow: 0 10px 25px rgba(0,0,0,0.05); }
    h2 { color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }
    .add-user { background: #ecf0f1; padding: 25px; border-radius: 10px; margin-bottom: 30px; display: flex; gap: 10px; flex-wrap: wrap; }
    input, select { padding: 12px; border: 1px solid #ddd; border-radius: 8px; flex: 1; min-width: 200px; }
    button { background: #3498db; color: white; border: none; padding: 12px 25px; border-radius: 8px; cursor: pointer; font-weight: bold; transition: 0.3s; }
    button:hover { background: #2980b9; }
    table { width: 100%; border-collapse: collapse; margin-top: 20px; }
    th { background: #2c3e50; color: white; text-align: left; padding: 15px; }
    td { padding: 15px; border-bottom: 1px solid #eee; }
    .btn-del { color: #e74c3c; text-decoration: none; font-weight: bold; }
</style></head>
<body><div class="container">
    <h2>💎 Trading Bot Admin Panel</h2>
    <form class="add-user" action="/add" method="POST">
        <input type="email" name="email" placeholder="User Email Address" required>
        <select name="days">
            <option value="1">1 Day Access</option>
            <option value="30">30 Days Access</option>
            <option value="36500">36500 Days (Lifetime)</option>
        </select>
        <button type="submit">Add Authorized User</button>
    </form>
    <table><thead><tr><th>Email</th><th>Expiry Date</th><th>Action</th></tr></thead>
        <tbody>{% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" class="btn-del">Remove User</a></td></tr>{% endfor %}</tbody>
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

# --- TELEGRAM HANDLERS ---
@bot.message_handler(commands=['start', 'START 🚀'])
def start_bot(m):
    bot.send_message(m.chat.id, "👋 Hello! Please enter your authorized email:"); bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Access Verified! Enter your API Token:"); bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 No active subscription found.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"api_token": m.text.strip()}}, upsert=True)
    bot.send_message(m.chat.id, "Enter Initial Stake:"); bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text), "current_stake": float(m.text)}})
    bot.send_message(m.chat.id, "Enter Target Profit:"); bot.register_next_step_handler(m, save_tp)

def save_tp(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": float(m.text), "total_profit": 0, "win_count": 0, "loss_count": 0, "is_running": True}})
    bot.send_message(m.chat.id, "🚀 Bot Active! Monitoring for Digit 4.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
    threading.Thread(target=trading_process, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_btn(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    bot.send_message(m.chat.id, "🛑 Bot has been stopped.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
