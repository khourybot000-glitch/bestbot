import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
TOKEN = "8433565422:AAH4x4l6E5-Y_DORDOst3o5d-k5hxafzL4Y"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
MARKET_SYMBOL = "R_100"

bot = telebot.TeleBot(TOKEN)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

def get_account_info(token):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": token}))
        res = json.loads(ws.recv())
        ws.close()
        if "authorize" in res:
            return res["authorize"].get("currency", "USD")
    except: pass
    return "USD"

def quick_execute(token, request_data):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=5)
        ws.send(json.dumps({"authorize": token}))
        ws.recv()
        ws.send(json.dumps(request_data))
        res = json.loads(ws.recv())
        ws.close()
        return res
    except: return None

# --- CORE ENGINE: 3 CONSECUTIVE TICKS DIFF >= 0.1 ---
def trade_engine(chat_id):
    tick_history = [] # لتخزين آخر تيكات
    session = active_sessions_col.find_one({"chat_id": chat_id})
    if not session: return
    
    token = session['tokens'][0]
    user_currency = get_account_info(token)
    current_stake = session['initial_stake']
    total_profit, win_count, loss_count, consecutive_losses = 0, 0, 0, 0

    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": token}))
        ws.recv()
        ws.send(json.dumps({"ticks": MARKET_SYMBOL, "subscribe": 1}))
        
        bot.send_message(chat_id, "🔍 Strategy: 3 Consecutive Ticks with Diff >= 0.1")

        while True:
            status = active_sessions_col.find_one({"chat_id": chat_id})
            if not status or not status.get("is_running"): break

            try:
                ws.settimeout(1)
                data = json.loads(ws.recv())
            except: break

            if "tick" in data:
                current_price = float(data["tick"]["quote"])
                tick_history.append(current_price)
                
                # الاحتفاظ بآخر 3 تيكات فقط
                if len(tick_history) > 3:
                    tick_history.pop(0)

                # التحقق من وجود 3 تيكات لتحليلها
                if len(tick_history) == 3:
                    diff1 = abs(tick_history[2] - tick_history[1]) # الفرق بين الأخير وقبله
                    diff2 = abs(tick_history[1] - tick_history[0]) # الفرق بين قبل الأخير واللي قبله
                    
                    if diff1 >= 0.1 and diff2 >= 0.1:
                        # استخراج الـ Barrier من التيك الحالي (الرقم الثاني بعد الفاصلة)
                        price_str = "{:.2f}".format(current_price)
                        target_barrier = price_str[-1]
                        
                        buy_req = {
                            "buy": 1, "price": current_stake,
                            "parameters": {
                                "amount": current_stake, "basis": "stake",
                                "contract_type": "DIGITDIFF", "symbol": MARKET_SYMBOL, 
                                "duration": 1, "duration_unit": "t",
                                "barrier": target_barrier, "currency": user_currency
                            }
                        }
                        
                        res = quick_execute(token, buy_req)
                        if res and "buy" in res:
                            bot.send_message(chat_id, f"⚡ Sequence Found!\nDiffs: {diff2:.2f} & {diff1:.2f}\nBarrier: {target_barrier}. Waiting 8s...")
                            tick_history = [] # تصفير القائمة لبدء سلسلة جديدة
                            time.sleep(8)
                            
                            res_contract = quick_execute(token, {"proposal_open_contract": 1, "contract_id": res["buy"]["contract_id"]})
                            if res_contract and "proposal_open_contract" in res_contract:
                                contract = res_contract["proposal_open_contract"]
                                profit = float(contract.get("profit", 0))
                                is_win = profit > 0
                                
                                total_profit += profit
                                if is_win:
                                    win_count += 1
                                    current_stake = session['initial_stake']
                                    consecutive_losses = 0
                                    status_text = "✅ WIN"
                                else:
                                    loss_count += 1
                                    consecutive_losses += 1
                                    current_stake = float("{:.2f}".format(current_stake * 14))
                                    status_text = "❌ LOSS"
                                
                                bot.send_message(chat_id, f"📊 *{status_text}*\nNet: `{total_profit:.2f}`\nW: {win_count} | L: {loss_count}", parse_mode="Markdown")

                                if consecutive_losses >= 4 or total_profit >= session.get("target_profit", 9999):
                                    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
                                    bot.send_message(chat_id, "🏁 Session Stopped.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))
                                    break
                
        ws.close()
    except: time.sleep(2)

# --- HTML ADMIN PANEL ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin Panel</title>
<style>
    body{font-family:sans-serif; background:#f4f7f6; padding:20px; text-align:center;}
    .card{max-width:800px; margin:auto; background:white; padding:30px; border-radius:15px; box-shadow:0 4px 15px rgba(0,0,0,0.1);}
    input, select{padding:12px; margin:5px; border-radius:8px; border:1px solid #ddd; width:220px;}
    button{padding:12px 25px; background:#3498db; color:white; border:none; border-radius:8px; cursor:pointer;}
    table{width:100%; border-collapse:collapse; margin-top:25px;}
    th, td{padding:15px; border-bottom:1px solid #eee; text-align:left;}
</style></head>
<body><div class="card">
    <h2>👥 Access Control Panel</h2>
    <form action="/add" method="POST">
        <input type="email" name="email" placeholder="Email Address" required>
        <select name="days"><option value="1">1 Day</option><option value="30">30 Days</option><option value="36500">Life Time</option></select>
        <button type="submit">Add User</button>
    </form>
    <table>
        <thead><tr><th>Email</th><th>Expiry Date</th><th>Action</th></tr></thead>
        <tbody>{% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" style="color:red; font-weight:bold; text-decoration:none;">Remove</a></td></tr>{% endfor %}</tbody>
    </table>
</div></body></html>
"""

@app.route('/')
def index(): return render_template_string(HTML_ADMIN, users=list(users_col.find()))

@app.route('/add', methods=['POST'])
def add_user():
    days = int(request.form.get('days'))
    exp = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower().strip()}, {"$set": {"expiry": exp}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete_user(email): users_col.delete_one({"email": email}); return redirect('/')

@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id, "🤖 *Digit Bot V7.7*\nEnter Email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.lower().strip()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Authorized. Enter Token:"); bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 No access.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False}}, upsert=True)
    bot.send_message(m.chat.id, "Initial Stake ($):"); bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text)}})
    bot.send_message(m.chat.id, "Target Profit ($):"); bot.register_next_step_handler(m, setup_tp)

def setup_tp(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": float(m.text)}})
    bot.send_message(m.chat.id, "✅ Ready!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def run(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True}})
    bot.send_message(m.chat.id, f"🚀 Analyzing {MARKET_SYMBOL}...", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
    threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    bot.send_message(m.chat.id, "🛑 Stopped.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
