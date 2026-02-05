import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
TOKEN = "8433565422:AAFj-q-oYjd9dvNXb7zfRMnNbe1brT1Ynmk"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

def get_second_digit(price):
    try:
        s_price = "{:.2f}".format(price)
        return int(s_price.split('.')[1][1])
    except: return None

def quick_ws_request(token, request_dict):
    ws = None
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": token}))
        ws.recv() 
        ws.send(json.dumps(request_dict))
        response = json.loads(ws.recv())
        ws.close()
        return response
    except:
        if ws: ws.close()
        return None

# --- TRADING ENGINE ---
def trading_process(chat_id):
    last_processed_min = -1
    
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        now = datetime.now()
        # التحليل يبدأ عند الثانية 00
        if now.second == 0 and now.minute != last_processed_min:
            last_processed_min = now.minute
            
            # جلب تيك التحليل (اتصال سريع)
            res = quick_ws_request(session['api_token'], {"ticks": "R_100", "count": 1})
            if not res: continue
            
            target_digit = get_second_digit(res.get("tick", {}).get("quote"))
            
            if target_digit is not None:
                bot.send_message(chat_id, f"🔍 *Analysis :00* | Target: `{target_digit}`\nHunting for entry...")
                
                # فتح اتصال المطاردة
                try:
                    ws_hunt = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
                    ws_hunt.send(json.dumps({"authorize": session['api_token']}))
                    ws_hunt.recv()
                    ws_hunt.send(json.dumps({"ticks": "R_100", "subscribe": 1}))
                    
                    start_hunt = time.time()
                    while time.time() - start_hunt < 50: # البحث لمدة 50 ثانية
                        tick_data = json.loads(ws_hunt.recv())
                        if get_second_digit(tick_data.get("tick", {}).get("quote")) == target_digit:
                            # تنفيذ الشراء
                            current_stake = session["current_stake"]
                            ws_hunt.send(json.dumps({
                                "proposal": 1, "amount": current_stake, "basis": "stake",
                                "contract_type": "DIGITDIFF", "duration": 1, "duration_unit": "t",
                                "symbol": "R_100", "barrier": str(target_digit), "currency": "USD"
                            }))
                            prop = json.loads(ws_hunt.recv()).get("proposal")
                            if prop:
                                ws_hunt.send(json.dumps({"buy": prop["id"], "price": current_stake}))
                                buy_res = json.loads(ws_hunt.recv())
                                if "buy" in buy_res:
                                    contract_id = buy_res["buy"]["contract_id"]
                                    bot.send_message(chat_id, f"🎯 *Entered Digit {target_digit}*\nWaiting 8s for result...")
                                    ws_hunt.close() # قطع الاتصال فور الشراء
                                    
                                    # الانتظار 8 ثوانٍ للنتيجة
                                    time.sleep(8)
                                    check_res = quick_ws_request(session['api_token'], {"proposal_open_contract": 1, "contract_id": contract_id})
                                    
                                    if check_res:
                                        p_data = check_res.get("proposal_open_contract", {})
                                        profit = float(p_data.get("profit", 0))
                                        
                                        if profit <= 0:
                                            # إذا كانت هذه أول خسارة، نجهز للمضاعفة في الدورة القادمة
                                            if session["current_stake"] == session["initial_stake"]:
                                                new_stake = float("{:.2f}".format(session["initial_stake"] * 14))
                                                update_stats(chat_id, profit, new_stake, False)
                                                bot.send_message(chat_id, "❌ Loss! Martingale x14 queued for next signal.")
                                            else:
                                                # إذا كانت الخسارة الثانية (بعد المضاعفة) يتوقف البوت
                                                update_stats(chat_id, profit, session["initial_stake"], True)
                                        else:
                                            # في حال الربح نعود للمبلغ الأساسي
                                            update_stats(chat_id, profit, session["initial_stake"], False)
                            break
                    if ws_hunt: ws_hunt.close()
                except: pass
        time.sleep(0.5)

def update_stats(chat_id, profit_change, next_stake, force_stop):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session: return
    
    new_total = session["total_profit"] + profit_change
    is_win = profit_change > 0
    
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        "total_profit": new_total,
        "current_stake": next_stake,
        "win_count": session["win_count"] + (1 if is_win else 0),
        "loss_count": session["loss_count"] + (0 if is_win else 1)
    }})
    
    bot.send_message(chat_id, f"📊 *Result:* {'✅ WIN' if is_win else '❌ LOSS'}\n💰 Net Profit: {new_total:.2f}")

    # التوقف إذا تحقق شرط الخسارة الثانية أو الوصول للهدف
    if force_stop or new_total >= session.get("target_profit", 9999):
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
        msg = "🛑 *STOPPED: Target Reached!*" if is_win else "🛑 *STOPPED: 2 Consecutive Losses Hit!*"
        bot.send_message(chat_id, msg, reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

# --- HTML ADMIN PANEL ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin Panel</title>
<style>
    body { font-family: 'Segoe UI', sans-serif; background: #f4f7f6; padding: 20px; text-align: center; }
    .container { max-width: 850px; margin: auto; background: white; padding: 30px; border-radius: 15px; box-shadow: 0 10px 25px rgba(0,0,0,0.1); }
    .form-group { display: flex; gap: 10px; justify-content: center; margin-bottom: 30px; background: #2c3e50; padding: 20px; border-radius: 10px; }
    input, select { padding: 10px; border-radius: 5px; border: 1px solid #ddd; }
    button { padding: 10px 20px; background: #2ecc71; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; }
    table { width: 100%; border-collapse: collapse; margin-top: 20px; }
    th, td { padding: 12px; border-bottom: 1px solid #eee; text-align: left; }
    th { background: #34495e; color: white; }
    .btn-del { color: #e74c3c; text-decoration: none; font-weight: bold; }
</style></head>
<body><div class="container">
    <h2>💎 Digit Differ Admin Panel</h2>
    <form class="form-group" action="/add" method="POST">
        <input type="email" name="email" placeholder="User Email" required>
        <select name="days">
            <option value="1">1 Day</option>
            <option value="30">30 Days</option>
            <option value="36500">36500 Days (Lifetime)</option>
        </select>
        <button type="submit">Grant Access</button>
    </form>
    <table>
        <thead><tr><th>Email Address</th><th>Expiry Date</th><th>Action</th></tr></thead>
        <tbody>
            {% for u in users %}
            <tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" class="btn-del">Delete</a></td></tr>
            {% endfor %}
        </tbody>
    </table>
</div></body></html>
"""

@app.route('/')
def index(): return render_template_string(HTML_ADMIN, users=list(users_col.find()))

@app.route('/add', methods=['POST'])
def add_user():
    email, days = request.form.get('email').lower().strip(), int(request.form.get('days'))
    exp = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    users_col.update_one({"email": email}, {"$set": {"expiry": exp}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete_user(email): users_col.delete_one({"email": email}); return redirect('/')

@bot.message_handler(commands=['start', 'START 🚀'])
def start_bot(m):
    bot.send_message(m.chat.id, "👋 Welcome! Enter your registered email:", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(m, auth)

def auth(m):
    user = users_col.find_one({"email": m.text.strip().lower()})
    if user and datetime.strptime(user['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ OK! Enter API Token:"); bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 No Access.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"api_token": m.text.strip()}}, upsert=True)
    bot.send_message(m.chat.id, "Enter Initial Stake:"); bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    try:
        s = float(m.text); active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": s, "current_stake": s}})
        bot.send_message(m.chat.id, "Enter TP:"); bot.register_next_step_handler(m, save_tp)
    except: bot.send_message(m.chat.id, "Invalid number.")

def save_tp(m):
    try:
        tp = float(m.text)
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": tp, "total_profit": 0, "win_count": 0, "loss_count": 0, "is_running": True}})
        bot.send_message(m.chat.id, "🚀 Bot Running! (Analysis @ :00)", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trading_process, args=(m.chat.id,), daemon=True).start()
    except: bot.send_message(m.chat.id, "Invalid number.")

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_btn(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    bot.send_message(m.chat.id, "🛑 Stopped.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
