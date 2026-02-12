import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
BOT_TOKEN = "8433565422:AAEYJSwj1jIEeX8XQEPzyplTpyAonVazWU0"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V3']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

is_analyzing = {}

# --- TRADING ENGINE ---

def run_analysis_cycle(chat_id, token):
    """Phase 1: Connect at :50, Collect 25 ticks, Analyze 10-10-5, Buy."""
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
        
        # Collect exactly 25 ticks
        while len(ticks) < 25:
            msg = json.loads(ws.recv())
            if "tick" in msg:
                ticks.append(msg["tick"]["quote"])
        
        # Candle Logic (10 - 10 - 5)
        c1 = ticks[0:10]
        c2 = ticks[10:20]
        c3 = ticks[20:25]
        
        up1, down1 = c1[-1] > c1[0], c1[-1] < c1[0]
        up2, down2 = c2[-1] > c2[0], c2[-1] < c2[0]
        up3, down3 = c3[-1] > c3[0], c3[-1] < c3[0]

        contract_type = None
        barrier = ""
        
        # Pattern: Up -> Down -> Up => CALL
        if up1 and down2 and up3:
            contract_type, barrier = "PUT", "+0.9"
        # Pattern: Down -> Up -> Down => PUT
        elif down1 and up2 and down3:
            contract_type, barrier = "CALL", "-0.9"

        if contract_type:
            session = active_sessions_col.find_one({"chat_id": chat_id})
            stake = session["accounts_data"][token]["current_stake"]

            ws.send(json.dumps({
                "buy": "1", "price": stake,
                "parameters": {
                    "amount": stake, "basis": "stake", "contract_type": contract_type,
                    "barrier": barrier, "duration": 6, "duration_unit": "t",
                    "symbol": "R_100", "currency": currency
                }
            }))
            
            buy_res = json.loads(ws.recv())
            if "buy" in buy_res:
                contract_id = buy_res["buy"]["contract_id"]
                bot.send_message(chat_id, f"🚀 **Trade Placed**\nType: `{contract_type}` | Barrier: `{barrier}`\nID: `{contract_id}`\nResult in 18s...")
                
                ws.close()
                time.sleep(18)
                fetch_contract_result(chat_id, token, contract_id)
            else:
                ws.close()
        else:
            ws.close()

    except Exception as e:
        if ws: ws.close()
        print(f"Cycle Error: {e}")
    
    is_analyzing[chat_id] = False

def fetch_contract_result(chat_id, token, contract_id):
    """Phase 2: Fetch outcome and update stats."""
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
    
    # Update Stats
    wins = acc.get("wins_count", 0) + (1 if is_win else 0)
    losses = acc.get("losses_count", 0) + (0 if is_win else 1)
    
    new_stake = session["initial_stake"] if is_win else round(acc["current_stake"] * 19, 2)
    new_streak = 0 if is_win else acc.get("streak", 0) + 1
    new_total = round(acc["total_profit"] + profit, 2)

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.streak": new_streak,
        f"accounts_data.{token}.total_profit": new_total,
        f"accounts_data.{token}.wins_count": wins,
        f"accounts_data.{token}.losses_count": losses
    }})

    bot.send_message(chat_id, 
        f"{'✅ WIN' if is_win else '❌ LOSS'}\n"
        f"Profit: `{profit}$` | Total: `{new_total}$` \n"
        f"📊 Stats: Wins: `{wins}` | Losses: `{losses}`"
    )
    
    if new_total >= session["target_profit"]: stop_bot(chat_id, "Target Reached!")
    elif new_streak >= 2: stop_bot(chat_id, "Stop Loss Triggered!")

def stop_bot(chat_id, reason):
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
    bot.send_message(chat_id, f"🛑 **BOT STOPPED**\nReason: {reason}\n\n/start", reply_markup=types.ReplyKeyboardRemove())

# --- SCHEDULER ---
def main_scheduler(chat_id):
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        if datetime.now().second == 50:
            threading.Thread(target=run_analysis_cycle, args=(chat_id, session['tokens'][0])).start()
            time.sleep(20) 
        time.sleep(0.5)

# --- BOT HANDLERS ---
@bot.message_handler(commands=['start'])
def start(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "Welcome! Enter Email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "Enter API Token:")
        bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 No Access.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False}}, upsert=True)
    bot.send_message(m.chat.id, "Initial Stake:")
    bot.register_next_step_handler(m, lambda msg: save_config(msg, "initial_stake"))

def save_config(m, key):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {key: float(m.text)}})
    if key == "initial_stake":
        bot.send_message(m.chat.id, "Target Profit:")
        bot.register_next_step_handler(m, lambda msg: save_config(msg, "target_profit"))
    else: bot.send_message(m.chat.id, "Setup Done!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def run_start(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    if sess:
        accs = {sess["tokens"][0]: {
            "current_stake": sess["initial_stake"], 
            "total_profit": 0.0, 
            "streak": 0,
            "wins_count": 0,
            "losses_count": 0
        }}
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
        bot.send_message(m.chat.id, "🛰️ Analysis running at :50s mark...", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=main_scheduler, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def run_stop(m): stop_bot(m.chat.id, "Manual Stop.")

# --- ADMIN PANEL ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin</title><style>
body{background:#0a0a0a; color:white; font-family:sans-serif; text-align:center; padding:50px;}
.box{background:#151515; padding:30px; border-radius:15px; border:1px solid #333; display:inline-block;}
button{background:#00ff88; color:black; font-weight:bold; cursor:pointer; padding:10px; border:none; border-radius:5px;}
table{width:100%; margin-top:20px; border-collapse:collapse;}
th, td{padding:10px; border-bottom:1px solid #333;}
</style></head>
<body><div class="box">
    <h2>Sniper Admin</h2>
    <form action="/add" method="POST">
        <input name="email" placeholder="Email" required style="padding:10px;">
        <select name="days" style="padding:10px;"><option value="30">30 Days</option><option value="36500">Lifetime</option></select>
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
