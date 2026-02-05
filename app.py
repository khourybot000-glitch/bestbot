import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
TOKEN = "8433565422:AAHXZr0c1Ay7CkiP6Z4UGCZy-5H81RNvrJM"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

# --- التحسين الجوهري في استخراج الرقم ---
def get_second_digit(price):
    try:
        # تحويل السعر لرقم عشري ثم نص بخانتين فقط
        # مثال: 1234.567 -> "1234.57"
        formatted = "{:.2f}".format(float(price))
        # استخراج الرقم الأخير في النص (الذي هو الرقم الثاني بعد الفاصلة)
        digit = int(formatted[-1])
        return digit, formatted
    except:
        return None, None

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
        if now.second == 0 and now.minute != last_processed_min:
            last_processed_min = now.minute
            
            res = quick_ws_request(session['api_token'], {"ticks": "R_100", "count": 1})
            if not res or "tick" not in res: continue
            
            target_digit, price_at_zero = get_second_digit(res["tick"]["quote"])
            
            if target_digit is not None:
                bot.send_message(chat_id, f"🕒 *دورة جديدة (00:00)*\n💰 السعر: `{price_at_zero}`\n🎯 الرقم المطلوب: `{target_digit}`\n🔍 جاري الانتظار...")
                
                try:
                    ws_hunt = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
                    ws_hunt.send(json.dumps({"authorize": session['api_token']}))
                    ws_hunt.recv()
                    ws_hunt.send(json.dumps({"ticks": "R_100", "subscribe": 1}))
                    
                    start_hunt = time.time()
                    trade_done = False
                    
                    while time.time() - start_hunt < 55:
                        tick_data = json.loads(ws_hunt.recv())
                        if "tick" in tick_data:
                            current_digit, _ = get_second_digit(tick_data["tick"]["quote"])
                            
                            if current_digit == target_digit:
                                stake = session["current_stake"]
                                ws_hunt.send(json.dumps({
                                    "proposal": 1, "amount": stake, "basis": "stake",
                                    "contract_type": "DIGITDIFF", "duration": 1, "duration_unit": "t",
                                    "symbol": "R_100", "barrier": str(target_digit), "currency": "USD"
                                }))
                                prop = json.loads(ws_hunt.recv()).get("proposal")
                                if prop:
                                    ws_hunt.send(json.dumps({"buy": prop["id"], "price": stake}))
                                    buy_info = json.loads(ws_hunt.recv())
                                    if "buy" in buy_info:
                                        c_id = buy_info["buy"]["contract_id"]
                                        bot.send_message(chat_id, f"🎯 تم رصد الرقم {target_digit} ودخول الصفقة!")
                                        ws_hunt.close()
                                        trade_done = True
                                        
                                        time.sleep(8) # مدة الانتظار التي طلبتها
                                        res_final = quick_ws_request(session['api_token'], {"proposal_open_contract": 1, "contract_id": c_id})
                                        if res_final:
                                            profit = float(res_final["proposal_open_contract"].get("profit", 0))
                                            handle_logic(chat_id, profit)
                                        break
                    if not trade_done:
                        bot.send_message(chat_id, "⏳ انتهت الدقيقة ولم يظهر الرقم.")
                    if ws_hunt: ws_hunt.close()
                except: pass
        time.sleep(0.5)

def handle_logic(chat_id, profit):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    is_win = profit > 0
    new_total = session["total_profit"] + profit
    
    stop = False
    if is_win:
        nxt = session["initial_stake"]
    else:
        if session["current_stake"] > session["initial_stake"]:
            nxt = session["initial_stake"]
            stop = True # خسارة ثانية بعد المضاعفة
        else:
            nxt = float("{:.2f}".format(session["initial_stake"] * 14)) # مضاعفة x14
    
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"total_profit": new_total, "current_stake": nxt}})
    bot.send_message(chat_id, f"📊 النتيجة: {'✅ ربح' if is_win else '❌ خسارة'}\n💰 الإجمالي: `{new_total:.2f}`")

    if stop or new_total >= session["target_profit"]:
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
        bot.send_message(chat_id, "🛑 تم التوقف النهائي.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

# --- HTML ADMIN PANEL (The Professional Design) ---
HTML_ADMIN = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Digit Admin Panel</title>
    <style>
        :root { --p: #3498db; --s: #2ecc71; --d: #2c3e50; --dg: #e74c3c; --l: #ecf0f1; }
        body { font-family: 'Segoe UI', Tahoma; background: #f4f7f6; margin: 0; padding: 20px; }
        .container { max-width: 900px; margin: 30px auto; background: white; padding: 40px; border-radius: 20px; box-shadow: 0 15px 35px rgba(0,0,0,0.1); }
        h2 { text-align: center; color: var(--d); border-bottom: 2px solid var(--l); padding-bottom: 10px; }
        .form-group { display: flex; gap: 15px; flex-wrap: wrap; justify-content: center; background: var(--l); padding: 25px; border-radius: 15px; margin-bottom: 40px; }
        input, select { padding: 12px; border: 1px solid #ddd; border-radius: 10px; outline: none; }
        button { padding: 12px 30px; background: var(--p); color: white; border: none; border-radius: 10px; font-weight: bold; cursor: pointer; transition: 0.3s; }
        button:hover { background: #2980b9; transform: translateY(-2px); }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; border-radius: 10px; overflow: hidden; }
        th { background: var(--d); color: white; padding: 18px; text-align: left; }
        td { padding: 16px; border-bottom: 1px solid #eee; }
        .btn-delete { color: var(--dg); text-decoration: none; font-weight: bold; padding: 8px 15px; border: 1px solid var(--dg); border-radius: 8px; }
        .btn-delete:hover { background: var(--dg); color: white; }
    </style>
</head>
<body>
    <div class="container">
        <h2>👥 User Management System</h2>
        <form class="form-group" action="/add" method="POST">
            <input type="email" name="email" placeholder="Email Address" required>
            <select name="days">
                <option value="1">1 Day</option>
                <option value="30">30 Days</option>
                <option value="36500">Lifetime</option>
            </select>
            <button type="submit">Add Authorized User</button>
        </form>
        <table>
            <thead><tr><th>Email</th><th>Expiry Date</th><th>Action</th></tr></thead>
            <tbody>
                {% for u in users %}
                <tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" class="btn-delete">Remove</a></td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</body>
</html>
"""

@app.route('/')
def index(): return render_template_string(HTML_ADMIN, users=list(users_col.find()))

@app.route('/add', methods=['POST'])
def add_user():
    e, d = request.form.get('email').lower().strip(), int(request.form.get('days'))
    exp = (datetime.now() + timedelta(days=d)).strftime("%Y-%m-%d")
    users_col.update_one({"email": e}, {"$set": {"expiry": exp}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete_user(email): users_col.delete_one({"email": email}); return redirect('/')

# --- TELEGRAM HANDLERS ---
@bot.message_handler(commands=['start', 'START 🚀'])
def start_bot(m):
    bot.send_message(m.chat.id, "👋 أدخل بريدك المسجل:", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ تم التحقق! أدخل الـ API Token الخاص بك:"); bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 لا تملك صلاحية.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"api_token": m.text.strip()}}, upsert=True)
    bot.send_message(m.chat.id, "أدخل مبلغ الرهان الأساسي:"); bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    try:
        s = float(m.text); active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": s, "current_stake": s}})
        bot.send_message(m.chat.id, "أدخل هدف الربح (TP):"); bot.register_next_step_handler(m, save_tp)
    except: bot.send_message(m.chat.id, "رقم خاطئ.")

def save_tp(m):
    try:
        tp = float(m.text)
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": tp, "total_profit": 0, "is_running": True}})
        bot.send_message(m.chat.id, "🚀 بدأ العمل! التحليل عند الثانية 00.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trading_process, args=(m.chat.id,), daemon=True).start()
    except: bot.send_message(m.chat.id, "رقم خاطئ.")

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_btn(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    bot.send_message(m.chat.id, "🛑 توقف.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
