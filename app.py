import websocket, json, time, os, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
TOKEN = "8433565422:AAFFPS270wli4dJzqb1VvM2O_JCZm-NaAOM"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

# --- ANALYSIS (4 TICKS) ---
def analyze_four_ticks(prices):
    if len(prices) < 4: return None
    if prices[3] > prices[0]: return "CALL"
    if prices[3] < prices[0]: return "PUT"
    return None

def execute_trade(ws, session, signal, amount):
    try:
        barrier = "-1.0" if signal == "CALL" else "+1.0"
        ws.send(json.dumps({
            "proposal": 1, "amount": amount, "basis": "stake",
            "contract_type": signal, "duration": 6, "duration_unit": "t",
            "symbol": "R_100", "barrier": barrier, "currency": session.get("currency", "USD")
        }))
        res = json.loads(ws.recv()).get("proposal")
        if res:
            ws.send(json.dumps({"buy": res["id"], "price": amount}))
            buy_data = json.loads(ws.recv())
            if "buy" in buy_data:
                return buy_data["buy"]["contract_id"]
    except: return None
    return None

# --- TRADING ENGINE ---
def trading_process(chat_id):
    last_processed_min = -1
    
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        now = datetime.now()
        if now.second == 6 and now.minute != last_processed_min:
            last_processed_min = now.minute
            
            # اتصال عند الحاجة فقط
            ws = None
            for _ in range(3):
                try:
                    ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
                    break
                except: time.sleep(1)
            
            if not ws: continue

            try:
                ws.send(json.dumps({"authorize": session['api_token']}))
                auth_res = json.loads(ws.recv())
                
                if "authorize" in auth_res:
                    currency = auth_res["authorize"].get("currency", "USD")
                    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"currency": currency}})
                    
                    ws.send(json.dumps({"ticks_history": "R_100", "count": 4, "end": "latest", "style": "ticks"}))
                    prices = json.loads(ws.recv()).get("history", {}).get("prices", [])
                    
                    signal = analyze_four_ticks(prices)
                    if signal:
                        initial_stake = session["current_stake"]
                        contract_id = execute_trade(ws, session, signal, initial_stake)
                        
                        if contract_id:
                            bot.send_message(chat_id, f"🚀 *Trade 1:* {signal} | {initial_stake}")
                            time.sleep(20)
                            
                            ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
                            result = json.loads(ws.recv()).get("proposal_open_contract", {})
                            
                            if result.get("is_expired"):
                                profit = float(result.get("profit", 0))
                                
                                if profit <= 0: # خسارة الصفقة الأولى
                                    opposite = "PUT" if signal == "CALL" else "CALL"
                                    m_stake = float("{:.2f}".format(initial_stake * 19))
                                    bot.send_message(chat_id, f"❌ Loss 1! Martingale x19: {opposite}")
                                    
                                    m_cid = execute_trade(ws, session, opposite, m_stake)
                                    if m_cid:
                                        time.sleep(20)
                                        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": m_cid}))
                                        m_res = json.loads(ws.recv()).get("proposal_open_contract", {})
                                        m_profit = float(m_res.get("profit", 0))
                                        
                                        # تحديث البيانات وإنهاء الجلسة إذا خسر الثانية أيضاً
                                        update_and_check_stop(chat_id, profit + m_profit, session["initial_stake"])
                                else: # ربح من الصفقة الأولى
                                    update_and_check_stop(chat_id, profit, session["initial_stake"])
                ws.close()
            except: pass
        time.sleep(0.5)

def update_and_check_stop(chat_id, net_change, reset_stake):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session: return
    
    new_total = session["total_profit"] + net_change
    is_win = net_change > 0 # إذا كان صافي الربح من العملية (أساسي + مضاعفة) موجب
    
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        "total_profit": new_total,
        "current_stake": reset_stake,
        "win_count": session["win_count"] + (1 if is_win else 0),
        "loss_count": session["loss_count"] + (0 if is_win else 1)
    }})
    
    status_icon = "✅ WIN" if is_win else "❌ LOSS (SL Hit)"
    bot.send_message(chat_id, f"📊 *Result:* {status_icon}\n💰 Net: {new_total:.2f}")

    # الشرط الصارم للتوقف: إذا كانت النتيجة النهائية خسارة (خسر الاثنين معاً) أو وصل للهدف
    if not is_win or new_total >= session.get("target_profit", 9999):
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
        bot.send_message(chat_id, "🛑 *Session Ended: Stopped after 2 losses or TP.*", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

# --- HTML ADMIN PANEL (المنسق) ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin Panel</title>
<style>
    body { font-family: 'Segoe UI', sans-serif; background: #f4f7f6; padding: 20px; }
    .container { max-width: 850px; margin: auto; background: white; padding: 30px; border-radius: 15px; box-shadow: 0 10px 25px rgba(0,0,0,0.1); }
    .form-group { display: flex; gap: 10px; justify-content: center; margin-bottom: 30px; background: #eee; padding: 20px; border-radius: 10px; }
    input, select { padding: 10px; border: 1px solid #ddd; border-radius: 5px; }
    button { padding: 10px 20px; background: #3498db; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; }
    table { width: 100%; border-collapse: collapse; margin-top: 20px; }
    th, td { padding: 12px; text-align: left; border-bottom: 1px solid #eee; }
    th { background: #2c3e50; color: white; }
    .btn-del { color: #e74c3c; text-decoration: none; font-weight: bold; }
</style></head>
<body><div class="container">
    <h2>👥 Trading Admin Panel</h2>
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
        <thead><tr><th>Email</th><th>Expiry Date</th><th>Action</th></tr></thead>
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
    bot.send_message(m.chat.id, "👋 Welcome! Enter your email:", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(m, auth)

def auth(m):
    user = users_col.find_one({"email": m.text.strip().lower()})
    if user and datetime.strptime(user['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Access Granted! Enter API Token:"); bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 No Access.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"api_token": m.text.strip()}}, upsert=True)
    bot.send_message(m.chat.id, "Enter Initial Stake:"); bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    try:
        s = float(m.text); active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": s, "current_stake": s}})
        bot.send_message(m.chat.id, "Enter TP:"); bot.register_next_step_handler(m, save_tp)
    except: bot.send_message(m.chat.id, "Error in number.")

def save_tp(m):
    try:
        tp = float(m.text)
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": tp, "total_profit": 0, "win_count": 0, "loss_count": 0, "is_running": True}})
        bot.send_message(m.chat.id, "🚀 Bot Active! Analysis at :06", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trading_process, args=(m.chat.id,), daemon=True).start()
    except: bot.send_message(m.chat.id, "Error in number.")

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_btn(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    bot.send_message(m.chat.id, "🛑 Stopped.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
