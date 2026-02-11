import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
BOT_TOKEN = "8433565422:AAFlU_nZ0qwIJS8-Lrd66pPSxkczEmVAwEQ"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

# Thread Safety
is_analyzing = {}

# --- TRADING ENGINE ---

def run_analysis_cycle(chat_id, token):
    """Phase 1: Connect, Analyze (10v10), Buy, Store ID, Disconnect."""
    if is_analyzing.get(chat_id): return
    is_analyzing[chat_id] = True
    
    ticks = []
    ws = None
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": token}))
        auth_res = json.loads(ws.recv())
        currency = auth_res.get("authorize", {}).get("currency", "USD")
        
        ws.send(json.dumps({"ticks": "R_100", "subscribe": 1}))
        
        # Collect exactly 20 ticks
        while len(ticks) < 20:
            msg = json.loads(ws.recv())
            if "tick" in msg:
                ticks.append(msg["tick"]["quote"])
        
        # Logic: First 10 vs Second 10
        f10, s10 = ticks[:10], ticks[10:]
        up1, down1 = f10[-1] > f10[0], f10[-1] < f10[0]
        up2, down2 = s10[-1] > s10[0], s10[-1] < s10[0]

        contract_type = None
        barrier = ""
        if up1 and down2: contract_type, barrier = "PUT", "+1"
        elif down1 and up2: contract_type, barrier = "CALL", "-1"

        if contract_type:
            session = active_sessions_col.find_one({"chat_id": chat_id})
            stake = session["accounts_data"][token]["current_stake"]

            # Execute Trade
            ws.send(json.dumps({
                "buy": "1", "price": stake,
                "parameters": {
                    "amount": stake, "basis": "stake", "contract_type": contract_type,
                    "barrier": barrier, "duration": 10, "duration_unit": "t",
                    "symbol": "R_100", "currency": currency
                }
            }))
            
            buy_res = json.loads(ws.recv())
            if "buy" in buy_res:
                contract_id = buy_res["buy"]["contract_id"]
                bot.send_message(chat_id, f"🚀 **Trade Entered**\nType: `{contract_type}` | ID: `{contract_id}`\nFetching results in 35s...")
                
                # Disconnect immediately after buying
                ws.close()
                
                # Move to Phase 2 after trade duration
                time.sleep(35)
                fetch_contract_result(chat_id, token, contract_id)
            else:
                ws.close()
        else:
            ws.close() # Silent exit if no pattern

    except Exception as e:
        if ws: ws.close()
        print(f"Cycle Error: {e}")
    
    is_analyzing[chat_id] = False

def fetch_contract_result(chat_id, token, contract_id):
    """Phase 2: Short connection to fetch outcome by ID."""
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": token}))
        ws.recv() 
        
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
        res = json.loads(ws.recv())
        ws.close()
        
        if "proposal_open_contract" in res:
            data = res["proposal_open_contract"]
            profit = float(data.get("profit", 0))
            update_account_stats(chat_id, token, profit)
    except Exception as e:
        print(f"Result Fetch Error: {e}")

def update_account_stats(chat_id, token, profit):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session: return
    
    acc = session["accounts_data"][token]
    is_win = profit > 0
    
    # Martingale x11 on loss
    new_stake = session["initial_stake"] if is_win else round(acc["current_stake"] * 11, 2)
    new_streak = 0 if is_win else acc.get("streak", 0) + 1
    new_total = round(acc["total_profit"] + profit, 2)

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.streak": new_streak,
        f"accounts_data.{token}.total_profit": new_total
    }})

    bot.send_message(chat_id, f"{'✅ WIN' if is_win else '❌ LOSS'}\nProfit: `{profit}$` | Total: `{new_total}$` | Streak: `{new_streak}/2`")
    
    if new_total >= session["target_profit"]: stop_bot(chat_id, "Target Profit Reached!")
    elif new_streak >= 2: stop_bot(chat_id, "Stop Loss (2 Consecutive Losses)!")

def stop_bot(chat_id, reason):
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
    bot.send_message(chat_id, f"🛑 **BOT STOPPED**\nReason: {reason}\n\nType /start to restart.", reply_markup=types.ReplyKeyboardRemove())

# --- SCHEDULER ---
def main_scheduler(chat_id):
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        if datetime.now().second == 40:
            threading.Thread(target=run_analysis_cycle, args=(chat_id, session['tokens'][0])).start()
            time.sleep(25) # Prevent re-triggering within the same minute
        time.sleep(0.5)

# --- BOT HANDLERS ---
@bot.message_handler(commands=['start'])
def start(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "Welcome! Please enter your registered email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "Access Granted. Please enter your Deriv API Token:")
        bot.register_next_step_handler(m, save_token)
    else: 
        bot.send_message(m.chat.id, "🚫 Access Denied or Expired.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False}}, upsert=True)
    bot.send_message(m.chat.id, "Enter Initial Stake (e.g., 0.35):")
    bot.register_next_step_handler(m, lambda msg: save_config(msg, "initial_stake"))

def save_config(m, key):
    try:
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {key: float(m.text)}})
        if key == "initial_stake":
            bot.send_message(m.chat.id, "Enter Target Profit ($):")
            bot.register_next_step_handler(m, lambda msg: save_config(msg, "target_profit"))
        else:
            bot.send_message(m.chat.id, "Setup Complete!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))
    except:
        bot.send_message(m.chat.id, "Invalid number. Please try again.")

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def run_start(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    if sess:
        accs = {sess["tokens"][0]: {"current_stake": sess["initial_stake"], "total_profit": 0.0, "streak": 0}}
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
        bot.send_message(m.chat.id, "🛰️ Sniper Mode Active. Waiting for :40s mark...", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=main_scheduler, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def run_stop(m): stop_bot(m.chat.id, "Manual Stop.")

# --- ADMIN PANEL HTML ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin Panel</title><style>
body{background:#0a0a0a; color:white; font-family:sans-serif; text-align:center; padding:50px;}
.box{background:#151515; padding:30px; border-radius:15px; border:1px solid #333; display:inline-block;}
input, select, button{padding:10px; margin:5px; border-radius:5px;}
button{background:#00ff88; color:black; font-weight:bold; cursor:pointer; border:none;}
table{width:100%; margin-top:20px; border-collapse:collapse;}
th, td{padding:10px; border-bottom:1px solid #333;}
</style></head>
<body><div class="box">
    <h2>Multi-User Management</h2>
    <form action="/add" method="POST">
        <input name="email" placeholder="Email" required>
        <select name="days"><option value="1">1 Day</option><option value="30">30 Days</option><option value="36500">Lifetime</option></select>
        <button type="submit">Add User</button>
    </form>
    <table><tr><th>User</th><th>Expiry</th><th>Action</th></tr>
    {% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" style="color:red">Delete</a></td></tr>{% endfor %}
    </table>
</div></body></html>
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
