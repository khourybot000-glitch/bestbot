import websocket, json, time, os, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION (NEW TELEGRAM TOKEN) ---
TOKEN = "8433565422:AAF5EHDbM-ITKJmOhHRdyF_0y1OmWl6_AME"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

# --- ANALYZE LOGIC (REVERSED) ---
def analyze_history(prices):
    if len(prices) < 45: return None
    big_open, big_close = prices[0], prices[29]
    small_open, small_close = prices[30], prices[44]
    
    # REVERSED: Big Down + Small Up = CALL | Big Up + Small Down = PUT
    if big_close < big_open and small_close > small_open: return "CALL"
    if big_close > big_open and small_close < small_open: return "PUT"
    return None

# --- TRADING ENGINE (SYNCHRONOUS HISTORY MODEL) ---
def trading_process(chat_id):
    last_processed_min = -1
    
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        now = datetime.now()
        # Trigger exactly at :30
        if now.second == 30 and now.minute != last_processed_min:
            last_processed_min = now.minute
            
            try:
                # Sync connection for stability
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
                ws.send(json.dumps({"authorize": session['api_token']}))
                auth_res = json.loads(ws.recv())
                
                if "authorize" in auth_res:
                    currency = auth_res["authorize"].get("currency", "USD")
                    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"currency": currency}})
                    
                    # 1. Fetch History
                    ws.send(json.dumps({"ticks_history": "R_100", "count": 45, "end": "latest", "style": "ticks"}))
                    hist_data = json.loads(ws.recv())
                    prices = hist_data.get("history", {}).get("prices", [])
                    
                    signal = analyze_history(prices)
                    
                    if signal:
                        barrier = "-0.7" if signal == "CALL" else "+0.7"
                        # 2. Proposal
                        ws.send(json.dumps({
                            "proposal": 1, "amount": session["current_stake"], "basis": "stake",
                            "contract_type": signal, "duration": 5, "duration_unit": "t",
                            "symbol": "R_100", "barrier": barrier, "currency": currency
                        }))
                        prop_data = json.loads(ws.recv()).get("proposal")
                        
                        if prop_data:
                            # 3. Buy
                            ws.send(json.dumps({"buy": prop_data["id"], "price": session["current_stake"]}))
                            buy_res = json.loads(ws.recv())
                            
                            if "buy" in buy_res:
                                contract_id = buy_res["buy"]["contract_id"]
                                bot.send_message(chat_id, f"🚀 *Trade Sent:* {signal}\nStake: {session['current_stake']} {currency}")
                                
                                # 4. Wait 18 seconds (Transaction wait time)
                                time.sleep(18)
                                ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
                                result_data = json.loads(ws.recv()).get("proposal_open_contract", {})
                                
                                if result_data.get("is_expired"):
                                    process_result(chat_id, result_data, currency)
                ws.close()
            except Exception as e:
                print(f"Error: {e}")
        
        time.sleep(0.5)

def process_result(chat_id, res, currency):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session: return
    
    profit = float(res.get("profit", 0))
    total_p = session.get("total_profit", 0) + profit
    wins = session.get("win_count", 0)
    losses_total = session.get("loss_count", 0)
    c_losses = session.get("consecutive_losses", 0)

    if profit > 0:
        new_stake, c_losses, wins, status = session["initial_stake"], 0, wins + 1, "WIN ✅"
    else:
        new_stake = float("{:.2f}".format(session["current_stake"] * 14)) # Multiplier x14
        c_losses, losses_total, status = c_losses + 1, losses_total + 1, "LOSS ❌"

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        "current_stake": new_stake, "consecutive_losses": c_losses,
        "win_count": wins, "loss_count": losses_total, "total_profit": total_p
    }})
    
    bot.send_message(chat_id, (
        f"📊 *Result:* {status}\n"
        f"💰 Profit: {profit:.2f} | Net: {total_p:.2f} {currency}\n"
        f"🏆 Wins: {wins} | 📉 Losses: {losses_total}\n"
        f"🔄 Next Stake: {new_stake}"
    ))

    # Stop after TWO consecutive losses
    if total_p >= session.get("target_profit", 9999) or c_losses >= 2:
        reason = "Target Profit Reached" if total_p >= session.get("target_profit", 9999) else "STOPPED: 2 Consecutive Losses"
        active_sessions_col.delete_one({"chat_id": chat_id})
        bot.send_message(chat_id, f"🛑 *SESSION OVER*\nReason: {reason}", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

# --- HTML ADMIN PANEL ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin Panel</title>
<style>
    body{font-family:sans-serif; background:#f4f7f6; padding:40px; text-align:center;}
    .container{max-width:850px; margin:auto; background:white; padding:30px; border-radius:15px; box-shadow:0 10px 25px rgba(0,0,0,0.1);}
    h2{color:#2c3e50;}
    form{display:flex; gap:10px; justify-content:center; margin-bottom:30px;}
    input, select{padding:12px; border:1px solid #ddd; border-radius:8px;}
    button{padding:12px 25px; background:#3498db; color:white; border:none; border-radius:8px; cursor:pointer; font-weight:bold;}
    table{width:100%; border-collapse:collapse; margin-top:20px;}
    th, td{padding:15px; text-align:left; border-bottom:1px solid #eee;}
    th{background:#f8f9fa;}
</style></head>
<body><div class="container">
    <h2>👥 User Management</h2>
    <form action="/add" method="POST">
        <input type="email" name="email" placeholder="User Email" required>
        <select name="days">
            <option value="1">1 Day</option>
            <option value="30">30 Days</option>
            <option value="36500">Lifetime (36500 Days)</option>
        </select>
        <button type="submit">Add User</button>
    </form>
    <table>
        <thead><tr><th>Email</th><th>Expiry Date</th><th>Action</th></tr></thead>
        <tbody>
            {% for u in users %}
            <tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" style="color:red; font-weight:bold; text-decoration:none;">Delete</a></td></tr>
            {% endfor %}
        </tbody>
    </table>
</div></body></html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_ADMIN, users=list(users_col.find()))

@app.route('/add', methods=['POST'])
def add_user():
    email = request.form.get('email').lower().strip()
    days = int(request.form.get('days'))
    expiry_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    users_col.update_one({"email": email}, {"$set": {"expiry": expiry_date}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete_user(email):
    users_col.delete_one({"email": email})
    return redirect('/')

# --- TELEGRAM HANDLERS ---
@bot.message_handler(commands=['start', 'START 🚀'])
def cmd_start(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "👋 Hello! Enter your registered email:", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(m, auth)

def auth(m):
    user = users_col.find_one({"email": m.text.strip().lower()})
    if user and datetime.strptime(user['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Access Granted! Enter Deriv API Token:")
        bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 No access found for this email.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"api_token": m.text.strip()}}, upsert=True)
    bot.send_message(m.chat.id, "Enter Initial Stake:")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    try:
        stake = float(m.text)
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": stake, "current_stake": stake}})
        bot.send_message(m.chat.id, "Enter Target Profit (TP):")
        bot.register_next_step_handler(m, save_tp)
    except: bot.send_message(m.chat.id, "Invalid number.")

def save_tp(m):
    try:
        tp = float(m.text)
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {
            "target_profit": tp, "total_profit": 0, "consecutive_losses": 0,
            "win_count": 0, "loss_count": 0, "is_running": True
        }})
        bot.send_message(m.chat.id, "🚀 *Bot Online!* Analyzing history at :30.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trading_process, args=(m.chat.id,), daemon=True).start()
    except: bot.send_message(m.chat.id, "Invalid number.")

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_btn(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "🛑 Stopped.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
