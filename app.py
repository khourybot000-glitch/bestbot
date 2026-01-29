import websocket, json, time, os, threading, queue
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
TOKEN = "8433565422:AAGc5pl9hMGwj9AC04Db0cv-CFWIlKLkvVI"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

# تخزين التيكات لكل مستخدم (Moving Window)
user_ticks = {} 

def calculate_sma(prices):
    return sum(prices) / 30

def process_trade_logic(chat_id, current_price):
    if chat_id not in user_ticks: user_ticks[chat_id] = []
    
    user_ticks[chat_id].append(current_price)
    
    # النافذة المتحركة: حذف الأقدم إذا تجاوزنا 30
    if len(user_ticks[chat_id]) > 30:
        user_ticks[chat_id].pop(0)
    
    # التحليل عند اكتمال 30 تيك
    if len(user_ticks[chat_id]) == 30:
        prices = user_ticks[chat_id]
        sma30 = calculate_sma(prices)
        p29 = prices[-2]
        p30 = prices[-1]
        
        # شرط CALL: من تحت الـ SMA إلى فوقه
        if p29 < sma30 and p30 > sma30:
            return "CALL", "-0.8"
        # شرط PUT: من فوق الـ SMA إلى تحته
        elif p29 > sma30 and p30 < sma30:
            return "PUT", "+0.8"
            
    return None, None

def trade_stream(chat_id, token):
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
            ws.send(json.dumps({"authorize": token}))
            auth_res = json.loads(ws.recv())
            
            if "authorize" in auth_res:
                currency = auth_res["authorize"].get("currency", "USD")
                ws.send(json.dumps({"ticks": "R_100"})) 
                
                while True:
                    session = active_sessions_col.find_one({"chat_id": chat_id})
                    if not session or not session.get("is_running"): 
                        ws.close(); return

                    try:
                        msg = json.loads(ws.recv())
                    except: break # إعادة اتصال تلقائي

                    if "tick" in msg:
                        price = msg["tick"]["quote"]
                        direction, barrier = process_trade_logic(chat_id, price)
                        
                        if direction:
                            acc_data = session["accounts_data"][token]
                            stake = acc_data["current_stake"]
                            
                            # إرسال طلب الصفقة
                            ws.send(json.dumps({
                                "proposal": 1, "amount": stake, "basis": "stake",
                                "contract_type": direction, "duration": 5, "duration_unit": "t",
                                "symbol": "R_100", "barrier": barrier, "currency": currency
                            }))
                            prop = json.loads(ws.recv())
                            
                            if "proposal" in prop:
                                ws.send(json.dumps({"buy": prop["proposal"]["id"], "price": stake}))
                                buy_res = json.loads(ws.recv())
                                
                                if "buy" in buy_res:
                                    contract_id = buy_res["buy"]["contract_id"]
                                    bot.send_message(chat_id, f"🎯 *Signal:* {direction}\n📊 SMA-30 Cross Confirmed!")
                                    
                                    # إدارة الانقطاع والانتظار المطلوبة
                                    ws.close()             
                                    user_ticks[chat_id] = [] # تصفير القائمة لبدء 30 جديدة
                                    time.sleep(18)         
                                    
                                    # جلب النتيجة وتحديث الإحصائيات
                                    res_msg = quick_check_result(token, contract_id)
                                    update_stats(chat_id, token, res_msg)
                                    break 
            ws.close()
        except: time.sleep(1)

def quick_check_result(token, contract_id):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": token}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
        res = json.loads(ws.recv())
        ws.close()
        return res
    except: return {}

def update_stats(chat_id, token, res):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    acc = session['accounts_data'][token]
    contract = res.get("proposal_open_contract", {})
    profit = float(contract.get("profit", 0))
    
    status = "✅ *WIN*" if profit > 0 else "❌ *LOSS*"
    new_total_net = acc["total_profit"] + profit
    
    if profit > 0:
        new_stake = session["initial_stake"]
        new_mg = 0
    else:
        new_stake = float("{:.2f}".format(acc["current_stake"] * 19)) # المضاعفة x19
        new_mg = acc["consecutive_losses"] + 1

    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.total_profit": new_total_net,
        f"accounts_data.{token}.consecutive_losses": new_mg,
        f"accounts_data.{token}.win_count": acc["win_count"] + (1 if profit > 0 else 0),
        f"accounts_data.{token}.loss_count": acc["loss_count"] + (1 if profit <= 0 else 0)
    }})
    
    bot.send_message(chat_id, f"📊 *Result:* {status}\n💰 Total Net: `{new_total_net:.2f}`\n🔄 Next Stake: `{new_stake:.2f}`\n\n⌛ *Restarting SMA Analysis...*")
    
    if new_mg >= 2:
        bot.send_message(chat_id, "🛑 *Limit Reached (2 Losses)!* Stopping.")
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})

# --- HTML ADMIN PANEL ---
@app.route('/')
def index():
    users = list(users_col.find())
    return render_template_string("""
    <!DOCTYPE html><html><head><title>Admin Control</title>
    <style>
        body{font-family:'Segoe UI', sans-serif; background:#f0f2f5; text-align:center; padding:50px;}
        .card{max-width:900px; margin:auto; background:white; padding:40px; border-radius:15px; box-shadow:0 10px 30px rgba(0,0,0,0.1);}
        h2{color:#1a73e8; margin-bottom:30px;}
        .form-box{background:#f8f9fa; padding:20px; border-radius:10px; margin-bottom:30px; border:1px solid #e1e4e8;}
        input, select{padding:12px; margin:10px; border:1px solid #ddd; border-radius:8px; width:220px;}
        .btn{background:#28a745; color:white; border:none; padding:12px 25px; border-radius:8px; cursor:pointer; font-weight:bold; transition:0.3s;}
        .btn:hover{background:#218838;}
        table{width:100%; border-collapse:collapse; margin-top:20px;}
        th,td{padding:15px; border-bottom:1px solid #eee; text-align:center;}
        th{background:#f1f3f4; color:#555;}
        .del-btn{color:#dc3545; text-decoration:none; font-weight:bold;}
    </style></head>
    <body><div class="card">
        <h2>🚀 SMA-30 Hybrid Admin Panel</h2>
        <div class="form-box">
            <form action="/add" method="POST">
                <input type="email" name="email" placeholder="User Email" required>
                <select name="days"><option value="30">30 Days</option><option value="36500">Lifetime</option></select>
                <button type="submit" class="btn">Add Authorized User</button>
            </form>
        </div>
        <table><thead><tr><th>Email</th><th>Expiry Date</th><th>Action</th></tr></thead>
        <tbody>{% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" class="del-btn">Remove</a></td></tr>{% endfor %}</tbody>
        </table></div></body></html>""", users=users)

@app.route('/add', methods=['POST'])
def add_user():
    exp = (datetime.now() + timedelta(days=int(request.form.get('days')))).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower()}, {"$set": {"expiry": exp}}, upsert=True); return redirect('/')

@app.route('/delete/<email>')
def delete_user(email):
    users_col.delete_one({"email": email}); return redirect('/')

# --- TELEGRAM ---
@bot.message_handler(commands=['start'])
def start_cmd(m):
    bot.send_message(m.chat.id, "🤖 *SMA-30 Hybrid Bot*\nAnalysis: Moving Window (30 Ticks)\nCooldown: 18s (Total Disconnect)\nEnter Email:")
    bot.register_next_step_handler(m, verify_auth)

def verify_auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Verified. Send API Token:"); bot.register_next_step_handler(m, set_token)
    else: bot.send_message(m.chat.id, "🚫 Access Denied.")

def set_token(m):
    token = m.text.strip()
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [token], "initial_stake": 0.35, "is_running": False}}, upsert=True)
    bot.send_message(m.chat.id, "Stake Amount:"); bot.register_next_step_handler(m, set_stake)

def set_stake(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text)}})
    bot.send_message(m.chat.id, "Ready!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def run_trading(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    accs = {sess["tokens"][0]: {"current_stake": sess["initial_stake"], "total_profit": 0, "consecutive_losses": 0, "win_count": 0, "loss_count": 0}}
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
    user_ticks[m.chat.id] = [] # تصفير القائمة عند البداية
    threading.Thread(target=trade_stream, args=(m.chat.id, sess["tokens"][0]), daemon=True).start()
    bot.send_message(m.chat.id, "🚀 Running! Collecting 30 Ticks...", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_bot(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    bot.send_message(m.chat.id, "🛑 Stopped.")

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000))), daemon=True).start()
    bot.infinity_polling()
