import websocket, json, time, os, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
TOKEN = "8433565422:AAG4t7SGIO6q0zMO6SghNKAMDztqZYOsca4"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

def get_deriv_data(token, request_data):
    while True:
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
            ws.send(json.dumps({"authorize": token}))
            auth_res = json.loads(ws.recv())
            if "authorize" in auth_res:
                ws.send(json.dumps(request_data))
                response = json.loads(ws.recv())
                ws.close()
                return response, auth_res["authorize"].get("currency", "USD")
            ws.close()
        except:
            time.sleep(1)

def trade_engine(chat_id):
    last_processed_time = ""
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        if session.get("is_waiting"):
            time.sleep(1)
            continue

        now = datetime.now()
        if now.second in [0, 10, 20, 30, 40, 50]:
            current_mark = f"{now.minute}:{now.second}"
            
            if current_mark != last_processed_time:
                last_processed_time = current_mark
                token = session['tokens'][0]
                
                tick_req = {"ticks_history": "R_100", "count": 10, "end": "latest", "style": "ticks"}
                res, currency = get_deriv_data(token, tick_req)
                prices = res.get("history", {}).get("prices", [])
                
                if len(prices) >= 10:
                    t1, t5, t10 = prices[-1], prices[-5], prices[0]
                    direction, barrier = None, "0"
                    
                    if t1 > t5 and t10 > t1: direction, barrier = "CALL", "-0.8"
                    elif t1 < t5 and t10 < t1: direction, barrier = "PUT", "+0.8"
                    
                    if direction:
                        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_waiting": True}})
                        acc = session["accounts_data"][token]
                        buy_req = {
                            "proposal": 1, "amount": acc["current_stake"], "basis": "stake",
                            "contract_type": direction, "duration": 5, "duration_unit": "t",
                            "symbol": "R_100", "barrier": barrier, "currency": currency
                        }
                        prop_res, _ = get_deriv_data(token, buy_req)
                        if "proposal" in prop_res:
                            exec_res, _ = get_deriv_data(token, {"buy": prop_res["proposal"]["id"], "price": acc["current_stake"]})
                            if "buy" in exec_res:
                                contract_id = exec_res["buy"]["contract_id"]
                                bot.send_message(chat_id, f"🎯 *Signal at :{now.second}s*\nDir: {direction}")
                                threading.Thread(target=process_result, args=(chat_id, token, contract_id), daemon=True).start()
                        else:
                            active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_waiting": False}})
        time.sleep(0.5)

def process_result(chat_id, token, contract_id):
    try:
        time.sleep(18) 
        final_res, _ = get_deriv_data(token, {"proposal_open_contract": 1, "contract_id": contract_id})
        update_stats(chat_id, token, final_res)
    finally:
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_waiting": False}})

def update_stats(chat_id, token, res):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session: return
    
    acc = session['accounts_data'].get(token)
    profit = float(res.get("proposal_open_contract", {}).get("profit", 0))
    status = "✅ *WIN*" if profit > 0 else "❌ *LOSS*"
    
    new_total_net = acc["total_profit"] + profit
    win_count = acc["win_count"] + (1 if profit > 0 else 0)
    loss_count = acc["loss_count"] + (1 if profit <= 0 else 0)
    
    if profit > 0:
        new_stake, new_mg = session["initial_stake"], 0
    else:
        new_stake = float("{:.2f}".format(acc["current_stake"] * 19))
        new_mg = acc["consecutive_losses"] + 1

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.total_profit": new_total_net,
        f"accounts_data.{token}.consecutive_losses": new_mg,
        f"accounts_data.{token}.win_count": win_count,
        f"accounts_data.{token}.loss_count": loss_count
    }})
    
    # تم تعديل هذه الرسالة لترسل عدد الربح والخسارة للمستخدم
    bot.send_message(chat_id, 
                     f"📊 *Result:* {status}\n"
                     f"💰 Net Profit: `{new_total_net:.2f}`\n"
                     f"🏆 Wins: `{win_count}` | ❌ Losses: `{loss_count}`\n"
                     f"🔄 Next Stake: `{new_stake:.2f}`")

    if new_total_net >= session.get("target_profit", 99999) or new_mg >= 2:
        bot.send_message(chat_id, "🛑 *Limit Reached!* Stopping bot."); 
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})

# --- FLASK & TELEGRAM (نفس الهيكلية الأصلية) ---
@app.route('/')
def index():
    users = list(users_col.find())
    return render_template_string("""
    <!DOCTYPE html><html><head><title>T1-T5-T10 Admin Panel</title>
    <style>
        body { font-family: 'Segoe UI', sans-serif; background: #f0f2f5; padding: 20px; }
        .container { max-width: 900px; margin: auto; background: white; padding: 40px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); }
        h2 { color: #1a73e8; text-align: center; }
        .form-box { background: #f8f9fa; padding: 25px; border-radius: 10px; border: 1px solid #dee2e6; margin-bottom: 30px; }
        input, select { padding: 12px; margin: 5px; border: 1px solid #ced4da; border-radius: 6px; }
        .btn-add { background: #28a745; color: white; border: none; padding: 12px 25px; border-radius: 6px; cursor: pointer; font-weight: bold; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { padding: 15px; text-align: center; border-bottom: 1px solid #eee; }
        .btn-delete { color: #dc3545; text-decoration: none; font-weight: bold; }
    </style></head>
    <body><div class="container">
        <h2>🚀 Multi-User Strategy Admin Panel</h2>
        <div class="form-box">
            <form action="/add" method="POST">
                <input type="email" name="email" placeholder="User Email" required>
                <select name="days">
                    <option value="1">1 Day</option>
                    <option value="30">30 Days</option>
                    <option value="36500">Lifetime</option>
                </select>
                <button type="submit" class="btn-add">Add Authorized User</button>
            </form>
        </div>
        <table><thead><tr><th>Email</th><th>Expiry Date</th><th>Action</th></tr></thead>
        <tbody>{% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" class="btn-delete">Remove</a></td></tr>{% endfor %}</tbody>
        </table></div></body></html>""", users=users)

@app.route('/add', methods=['POST'])
def add_user():
    days = int(request.form.get('days'))
    expiry_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower()}, {"$set": {"expiry": expiry_date}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete_user(email):
    users_col.delete_one({"email": email}); return redirect('/')

@bot.message_handler(commands=['start'])
def start_cmd(m):
    bot.send_message(m.chat.id, "🤖 *T1-T5-T10 Bot Ready*\nInterval: 10s\nEnter Email:")
    bot.register_next_step_handler(m, verify_auth)

def verify_auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Access Granted. Send API Token:")
        bot.register_next_step_handler(m, setup_bot)
    else: bot.send_message(m.chat.id, "🚫 No Access.")

def setup_bot(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False, "is_waiting": False}}, upsert=True)
    bot.send_message(m.chat.id, "Initial Stake:"); bot.register_next_step_handler(m, set_stake)

def set_stake(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text)}})
    bot.send_message(m.chat.id, "Target Profit:"); bot.register_next_step_handler(m, set_tp)

def set_tp(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": float(m.text)}})
    bot.send_message(m.chat.id, "Ready!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def start_trading(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    accs = {sess["tokens"][0]: {"current_stake": sess["initial_stake"], "total_profit": 0.0, "consecutive_losses": 0, "win_count": 0, "loss_count": 0}}
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "is_waiting": False, "accounts_data": accs}})
    threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()
    bot.send_message(m.chat.id, "🚀 Running!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_bot(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}}); bot.send_message(m.chat.id, "🛑 Stopped.")

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
