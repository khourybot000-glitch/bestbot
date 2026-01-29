import websocket, json, time, os, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION (Updated Token) ---
TOKEN = "8433565422:AAGKf2Yxlk1gnm0RmSnrSm0IhmKHnl70IZU"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

def get_deriv_data(token, request_data):
    """Fast execution for Buy/Data requests to ensure speed"""
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": token}))
        auth_res = json.loads(ws.recv())
        if "authorize" in auth_res:
            currency = auth_res["authorize"].get("currency", "USD")
            ws.send(json.dumps(request_data))
            response = json.loads(ws.recv())
            ws.close()
            return response, currency
        ws.close()
        return None, None
    except:
        return None, None

def trade_engine(chat_id):
    last_trigger_time = ""
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        now = datetime.now()
        # Triggering at 0, 20, 40 seconds
        if now.second in [0, 20, 40]:
            current_mark = f"{now.minute}:{now.second}"
            if current_mark != last_trigger_time:
                last_trigger_time = current_mark
                token = session['tokens'][0]
                
                # Fetching 10 ticks for analysis
                res, currency = get_deriv_data(token, {"ticks_history": "R_100", "count": 10, "end": "latest", "style": "ticks"})
                
                if res and "history" in res:
                    prices = res["history"]["prices"]
                    t1, t5, t10 = float(prices[-1]), float(prices[-5]), float(prices[-10])
                    
                    direction = None
                    if t1 > t5 and t10 > t1: direction = "CALL"
                    elif t1 < t5 and t10 < t1: direction = "PUT"
                    
                    if direction:
                        acc = session["accounts_data"][token]
                        barrier = "-0.8" if direction == "CALL" else "+0.8"
                        
                        # Direct Execution Logic
                        buy_payload = {
                            "proposal": 1, "amount": acc["current_stake"], "basis": "stake",
                            "contract_type": direction, "duration": 5, "duration_unit": "t",
                            "symbol": "R_100", "barrier": barrier, "currency": currency
                        }
                        
                        prop_res, _ = get_deriv_data(token, buy_payload)
                        
                        if prop_res and "proposal" in prop_res:
                            p_id = prop_res["proposal"]["id"]
                            exec_res, _ = get_deriv_data(token, {"buy": p_id, "price": acc["current_stake"]})
                            
                            if exec_res and "buy" in exec_res:
                                bot.send_message(chat_id, f"🎯 *Signal Found: {direction}*\nExecuting trade... ⏳")
                                time.sleep(18) # Transaction wait time
                                final_res, _ = get_deriv_data(token, {"proposal_open_contract": 1, "contract_id": exec_res["buy"]["contract_id"]})
                                update_stats(chat_id, token, final_res)
                
        time.sleep(0.1)

def update_stats(chat_id, token, res):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    acc = session['accounts_data'][token]
    contract = res.get("proposal_open_contract", {})
    profit = float(contract.get("profit", 0))
    
    status = "✅ *WIN*" if profit > 0 else "❌ *LOSS*"
    new_total_net = acc["total_profit"] + profit
    win_count = acc["win_count"] + (1 if profit > 0 else 0)
    loss_count = acc["loss_count"] + (1 if profit <= 0 else 0)
    
    # 19x Martingale & Stop after 2 losses
    if profit > 0:
        new_stake, new_mg = session["initial_stake"], 0
    else:
        new_stake = round(acc["current_stake"] * 19, 2)
        new_mg = acc["consecutive_losses"] + 1

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.total_profit": new_total_net,
        f"accounts_data.{token}.consecutive_losses": new_mg,
        f"accounts_data.{token}.win_count": win_count,
        f"accounts_data.{token}.loss_count": loss_count
    }})
    
    bot.send_message(chat_id, 
        f"📊 *Trade Result:* {status}\n"
        f"💰 Net: `{new_total_net:.2f}`\n"
        f"🏆 W: `{win_count}` | L: `{loss_count}`\n"
        f"🔄 Next Stake: `{new_stake:.2f}`")

    if new_total_net >= session.get("target_profit", 99999) or new_mg >= 2:
        bot.send_message(chat_id, "🛑 *Limit Reached!* Stopping.")
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})

# --- UPDATED HTML PANEL WITH EXPIRY OPTIONS ---
@app.route('/')
def index():
    users = list(users_col.find())
    return render_template_string("""
    <!DOCTYPE html><html><head><title>Admin Panel</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: #fff; min-height: 100vh; display: flex; align-items: center; justify-content: center; margin: 0; }
        .container { background: rgba(255, 255, 255, 0.1); backdrop-filter: blur(10px); padding: 40px; border-radius: 20px; box-shadow: 0 15px 35px rgba(0,0,0,0.2); width: 100%; max-width: 750px; border: 1px solid rgba(255,255,255,0.1); text-align: center; }
        h2 { margin-bottom: 25px; font-weight: 300; }
        .form-box { display: flex; gap: 10px; justify-content: center; margin-bottom: 30px; }
        input, select { padding: 12px; border-radius: 30px; border: none; outline: none; background: rgba(255,255,255,0.2); color: #fff; }
        select { color: #333; }
        button { padding: 12px 25px; border-radius: 30px; border: none; background: #00d2ff; color: #fff; font-weight: bold; cursor: pointer; transition: 0.3s; }
        button:hover { background: #00a8cc; transform: translateY(-2px); }
        table { width: 100%; border-collapse: collapse; background: rgba(0,0,0,0.2); border-radius: 10px; overflow: hidden; }
        th, td { padding: 15px; border-bottom: 1px solid rgba(255,255,255,0.05); }
        th { background: rgba(0,0,0,0.3); font-size: 13px; text-transform: uppercase; }
        .delete-btn { color: #ff4b2b; text-decoration: none; font-weight: bold; }
    </style></head>
    <body><div class="container">
        <h2>🚀 Bot Access Management</h2>
        <form action="/add" method="POST" class="form-box">
            <input type="email" name="email" placeholder="Email Address" required>
            <select name="days">
                <option value="1">1 Day</option>
                <option value="30">30 Days</option>
                <option value="36500">36500 Days (Lifetime)</option>
            </select>
            <button type="submit">Add User</button>
        </form>
        <table><thead><tr><th>User Email</th><th>Expiry Date</th><th>Action</th></tr></thead>
        <tbody>{% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" class="delete-btn">Remove</a></td></tr>{% endfor %}</tbody>
        </table></div></body></html>""", users=users)

@app.route('/add', methods=['POST'])
def add_user():
    email = request.form.get('email').lower()
    days = int(request.form.get('days'))
    expiry_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    users_col.update_one({"email": email}, {"$set": {"expiry": expiry_date}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete_user(email):
    users_col.delete_one({"email": email}); return redirect('/')

# --- TELEGRAM BOT HANDLERS ---
@bot.message_handler(commands=['start'])
def start_cmd(m):
    bot.send_message(m.chat.id, "🤖 *Pulse Master v2.2*\nPlease enter your Email:")
    bot.register_next_step_handler(m, verify_auth)

def verify_auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Verified! Enter Deriv API Token:")
        bot.register_next_step_handler(m, setup_bot)
    else:
        bot.send_message(m.chat.id, "🚫 Access Denied or Expired.")

def setup_bot(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False}}, upsert=True)
    bot.send_message(m.chat.id, "Initial Stake:")
    bot.register_next_step_handler(m, set_stake)

def set_stake(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text)}})
    bot.send_message(m.chat.id, "Target Profit:")
    bot.register_next_step_handler(m, set_tp)

def set_tp(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": float(m.text)}})
    bot.send_message(m.chat.id, "Configuration Done!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def run_trading(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    accs = {sess["tokens"][0]: {"current_stake": sess["initial_stake"], "total_profit": 0.0, "consecutive_losses": 0, "win_count": 0, "loss_count": 0}}
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
    threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()
    bot.send_message(m.chat.id, "🚀 Bot Active! Monitoring 0, 20, 40s.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_bot(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    bot.send_message(m.chat.id, "🛑 Bot Stopped.")

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
