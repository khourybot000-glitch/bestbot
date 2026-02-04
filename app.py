import websocket, json, time, os, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION (NEW TELEGRAM TOKEN) ---
TOKEN = "8433565422:AAHHwOaclpHDomy57OPq9dWxVsfR-n3raLo"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

# --- ANALYSIS LOGIC (3 TICKS AT SECOND :04) ---
def analyze_three_ticks(prices):
    if len(prices) < 3: return None
    # مقارنة التيك الأخير (prices[2]) بالتيك الأول (prices[0]) لمعرفة الاتجاه العام
    if prices[2] > prices[0]: return "CALL"
    if prices[2] < prices[0]: return "PUT"
    return None

# --- TRADING ENGINE ---
def trading_process(chat_id):
    last_processed_min = -1
    
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        now = datetime.now()
        # التحليل عند الثانية 4 بالضبط من كل دقيقة
        if now.second == 4 and now.minute != last_processed_min:
            last_processed_min = now.minute
            
            try:
                # اتصال متزامن لضمان السرعة والدقة
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
                ws.send(json.dumps({"authorize": session['api_token']}))
                auth_res = json.loads(ws.recv())
                
                if "authorize" in auth_res:
                    currency = auth_res["authorize"].get("currency", "USD")
                    
                    # 1. جلب آخر 3 تيكات للتحليل
                    ws.send(json.dumps({"ticks_history": "R_100", "count": 3, "end": "latest", "style": "ticks"}))
                    hist_data = json.loads(ws.recv())
                    prices = hist_data.get("history", {}).get("prices", [])
                    
                    signal = analyze_three_ticks(prices)
                    
                    if signal:
                        # حاجز 1.4 (CALL -1.4 / PUT +1.4) كما طلبت
                        barrier = "-1" if signal == "CALL" else "+1"
                        
                        # 2. طلب عرض السعر (مدة 6 تيكات)
                        ws.send(json.dumps({
                            "proposal": 1, "amount": session["current_stake"], "basis": "stake",
                            "contract_type": signal, "duration": 6, "duration_unit": "t",
                            "symbol": "R_100", "barrier": barrier, "currency": currency
                        }))
                        prop_data = json.loads(ws.recv()).get("proposal")
                        
                        if prop_data:
                            # 3. تنفيذ الشراء
                            ws.send(json.dumps({"buy": prop_data["id"], "price": session["current_stake"]}))
                            buy_res = json.loads(ws.recv())
                            
                            if "buy" in buy_res:
                                contract_id = buy_res["buy"]["contract_id"]
                                bot.send_message(chat_id, f"🎯 *Signal at :04 (3 Ticks)*\nDirection: {signal}\nBarrier: {barrier}\nStake: {session['current_stake']} {currency}")
                                
                                # 4. انتظار النتيجة لمدة 20 ثانية
                                time.sleep(20)
                                ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
                                result_data = json.loads(ws.recv()).get("proposal_open_contract", {})
                                
                                if result_data.get("is_expired"):
                                    process_result(chat_id, result_data, currency)
                ws.close()
            except Exception as e:
                print(f"Error in trading process: {e}")
        
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
        # مضاعفة 14x
        new_stake = float("{:.2f}".format(session["current_stake"] * 19))
        c_losses, losses_total, status = c_losses + 1, losses_total + 1, "LOSS ❌"

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        "current_stake": new_stake, "consecutive_losses": c_losses,
        "win_count": wins, "loss_count": losses_total, "total_profit": total_p
    }})
    
    bot.send_message(chat_id, (
        f"📊 *Result:* {status}\n"
        f"💰 Profit: {profit:.2f} | Net Total: {total_p:.2f} {currency}\n"
        f"🏆 Wins: {wins} | 📉 Losses: {losses_total}\n"
        f"🔄 Next Stake: {new_stake}"
    ))

    # التوقف فوراً بعد خسارة واحدة (c_losses >= 1)
    if total_p >= session.get("target_profit", 9999) or c_losses >= 2:
        reason = "Target Profit Reached" if total_p >= session.get("target_profit", 9999) else "STOPPED: 2 Loss Hit"
        active_sessions_col.delete_one({"chat_id": chat_id})
        bot.send_message(chat_id, f"🛑 *SESSION FINISHED*\nReason: {reason}", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

# --- HTML ADMIN PANEL ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin Panel</title>
<style>
    body{font-family:'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background:#f4f7f6; padding:40px; text-align:center;}
    .container{max-width:850px; margin:auto; background:white; padding:30px; border-radius:15px; box-shadow:0 10px 25px rgba(0,0,0,0.1);}
    h2{color:#2c3e50; margin-bottom:20px;}
    form{display:flex; gap:10px; justify-content:center; margin-bottom:30px;}
    input, select{padding:12px; border:1px solid #ddd; border-radius:8px; outline:none;}
    button{padding:12px 25px; background:#3498db; color:white; border:none; border-radius:8px; cursor:pointer; font-weight:bold; transition:0.3s;}
    button:hover{background:#2980b9;}
    table{width:100%; border-collapse:collapse; margin-top:20px;}
    th, td{padding:15px; text-align:left; border-bottom:1px solid #eee;}
    th{background:#f8f9fa; color:#333;}
    .del-link{color:#e74c3c; text-decoration:none; font-weight:bold;}
</style></head>
<body><div class="container">
    <h2>👥 User Access Management</h2>
    <form action="/add" method="POST">
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
            <tr>
                <td>{{u.email}}</td>
                <td>{{u.expiry}}</td>
                <td><a href="/delete/{{u.email}}" class="del-link">Delete</a></td>
            </tr>
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

# --- TELEGRAM BOT HANDLERS ---
@bot.message_handler(commands=['start', 'START 🚀'])
def cmd_start(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "👋 Welcome! Please enter your registered email:", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(m, auth)

def auth(m):
    user = users_col.find_one({"email": m.text.strip().lower()})
    if user and datetime.strptime(user['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Authorized! Please enter your Deriv API Token:")
        bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 Access Denied or Expired.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"api_token": m.text.strip()}}, upsert=True)
    bot.send_message(m.chat.id, "Enter Initial Stake ($):")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    try:
        stake = float(m.text)
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": stake, "current_stake": stake}})
        bot.send_message(m.chat.id, "Enter Target Profit (TP):")
        bot.register_next_step_handler(m, save_tp)
    except: bot.send_message(m.chat.id, "Invalid number. Try again:")

def save_tp(m):
    try:
        tp = float(m.text)
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {
            "target_profit": tp, "total_profit": 0, "consecutive_losses": 0,
            "win_count": 0, "loss_count": 0, "is_running": True
        }})
        bot.send_message(m.chat.id, "🚀 *Bot Online!* Listening at second :04", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trading_process, args=(m.chat.id,), daemon=True).start()
    except: bot.send_message(m.chat.id, "Invalid number. Try again:")

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_btn(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "🛑 Bot Stopped and data cleared.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
