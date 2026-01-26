import websocket, json, time, os, threading, queue, pandas as pd, pandas_ta as ta
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION (Updated Token) ---
TOKEN = "8433565422:AAEIvqa6_aCM3TyHTIv7Cp0Qz6zjueBfB2o"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN, threaded=True, num_threads=100)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

msg_queue = queue.Queue()

def message_worker():
    while True:
        try:
            chat_id, text = msg_queue.get()
            bot.send_message(chat_id, text, parse_mode="Markdown")
            msg_queue.task_done()
            time.sleep(0.04) 
        except: pass

threading.Thread(target=message_worker, daemon=True).start()

def safe_send(chat_id, text):
    msg_queue.put((chat_id, text))

def quick_request(api_token, request_data):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=12)
        ws.send(json.dumps({"authorize": api_token}))
        if "authorize" in json.loads(ws.recv()):
            ws.send(json.dumps(request_data))
            res = json.loads(ws.recv())
            ws.close()
            return res
        ws.close()
    except: pass
    return None

def execute_trade(api_token, buy_req):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=12)
        ws.send(json.dumps({"authorize": api_token}))
        if "authorize" in json.loads(ws.recv()):
            buy_req['amount'] = float("{:.2f}".format(buy_req['amount']))
            ws.send(json.dumps({"proposal": 1, **buy_req}))
            prop_res = json.loads(ws.recv())
            if "proposal" in prop_res:
                ws.send(json.dumps({"buy": prop_res["proposal"]["id"], "price": buy_req['amount']}))
                res = json.loads(ws.recv())
                ws.close()
                return res
        ws.close()
    except: pass
    return None

# --- ENGINE: BOLLINGER BREAKOUT WITH REDEPLOY RECOVERY ---
def trade_engine(chat_id):
    last_processed_minute = -1
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        try:
            now = datetime.now()
            
            # --- ميزة الاستعادة بعد Redeploy ---
            for token, acc in session.get("accounts_data", {}).items():
                if acc.get("active_contract") and acc.get("target_check_time"):
                    target_time = datetime.fromisoformat(acc["target_check_time"])
                    if now >= target_time:
                        # حان وقت التحقق من النتيجة (حتى لو بعد إعادة تشغيل السيرفر)
                        res_res = quick_request(token, {"proposal_open_contract": 1, "contract_id": acc["active_contract"]})
                        if res_res and res_res.get("proposal_open_contract", {}).get("is_expired"):
                            process_result(chat_id, token, res_res)
                            continue

            # --- منطق فتح الصفقات عند الثانية 0 ---
            if now.second == 0 and now.minute != last_processed_minute:
                last_processed_minute = now.minute 
                
                # التحقق إذا كان هناك صفقة جارية حالياً (لمنع التداخل)
                is_any_active = any(acc.get("active_contract") for acc in session.get("accounts_data", {}).values())
                if is_any_active: continue

                res = quick_request(session['tokens'][0], {"ticks_history": "R_100", "count": 1000, "end": "latest", "style": "ticks"})
                prices = res.get("history", {}).get("prices", []) if res else []

                if len(prices) >= 600:
                    df_ticks = pd.DataFrame(prices, columns=['close'])
                    candles = df_ticks.iloc[::30, :].copy().reset_index(drop=True)
                    bb = ta.bbands(candles['close'], length=20, std=2)
                    
                    c_close = candles['close'].iloc[-1]
                    lower_b = bb['BBL_20_2.0'].iloc[-1]
                    upper_b = bb['BBU_20_2.0'].iloc[-1]
                    
                    direction = None
                    if c_close <= lower_b: direction = "CALL"
                    elif c_close >= upper_b: direction = "PUT"

                    if direction:
                        target_time = (now + timedelta(seconds=186)).isoformat()
                        safe_send(chat_id, f"🚀 *Breakout!* {direction}\nDuration: 3m\nWait until: `{target_time[11:19]}`")
                        
                        for t in session['tokens']:
                            acc = session['accounts_data'].get(t)
                            if acc:
                                amt = float("{:.2f}".format(acc["current_stake"]))
                                buy_res = execute_trade(t, {"amount": amt, "basis": "stake", "contract_type": direction, "currency": "USD", "duration": 3, "duration_unit": "m", "symbol": "R_100"})
                                if buy_res and "buy" in buy_res:
                                    # تسجيل العقد ووقت الانتهاء في الداتابيز
                                    active_sessions_col.update_one({"chat_id": chat_id}, {
                                        "$set": {
                                            f"accounts_data.{t}.active_contract": buy_res["buy"]["contract_id"],
                                            f"accounts_data.{t}.target_check_time": target_time
                                        }
                                    })
            time.sleep(1)
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(1)

def process_result(chat_id, token, res):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    acc = session['accounts_data'].get(token)
    contract = res.get("proposal_open_contract", {})
    
    profit = float(contract.get("profit", 0))
    new_wins = acc["win_count"] + (1 if profit > 0 else 0)
    new_losses = acc["loss_count"] + (1 if profit <= 0 else 0)
    
    if profit > 0:
        new_stake = session["initial_stake"]; new_mg = 0; status = "✅ *WIN*"
    else:
        new_stake = float("{:.2f}".format(acc["current_stake"] * 2.2))
        new_mg = acc["consecutive_losses"] + 1; status = "❌ *LOSS*"
    
    new_total = acc["total_profit"] + profit
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {
        f"accounts_data.{token}.current_stake": new_stake,
        f"accounts_data.{token}.win_count": new_wins,
        f"accounts_data.{token}.loss_count": new_losses,
        f"accounts_data.{token}.consecutive_losses": new_mg,
        f"accounts_data.{token}.total_profit": new_total,
        f"accounts_data.{token}.active_contract": None,
        f"accounts_data.{token}.target_check_time": None
    }})
    
    report = (f"🔍 *Result Update:*\nStatus: {status}\nProfit: `{profit:.2f}`\nBalance: `{new_total:.2f}`\n🔄 MG: {new_mg}/4")
    safe_send(chat_id, report)
    
    if new_mg >= 4:
        safe_send(chat_id, "🛑 *Stop Loss:* 4 losses. Session closed."); active_sessions_col.delete_one({"chat_id": chat_id})

# --- UI & AUTH (نفس الكود السابق مع التوكن الجديد في البداية) ---
@app.route('/')
def index():
    users = list(users_col.find())
    return render_template_string("""
    <!DOCTYPE html><html><head><title>Persistence Admin</title>
    <style>
        body{font-family:Arial; background:#f4f7f6; text-align:center; padding:50px;}
        .card{max-width:800px; margin:auto; background:white; padding:30px; border-radius:12px; box-shadow:0 4px 15px rgba(0,0,0,0.1);}
        table{width:100%; border-collapse:collapse; margin-top:20px;}
        th,td{padding:12px; border:1px solid #ddd;} th{background:#007bff; color:white;}
        .btn{background:#28a745; color:white; border:none; padding:10px 20px; border-radius:5px; cursor:pointer;}
    </style></head>
    <body><div class="card">
        <h2>💾 Persistent Sniper Admin</h2>
        <form action="/add" method="POST">
            <input type="email" name="email" placeholder="Email" required style="padding:10px;">
            <select name="days" style="padding:10px;"><option value="1">1 Day</option><option value="30">30 Days</option><option value="36500">Life</option></select>
            <button type="submit" class="btn">Add User</button>
        </form>
        <table><tr><th>Email</th><th>Expiry</th><th>Action</th></tr>
        {% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" style="color:red;">Delete</a></td></tr>{% endfor %}
        </table></div></body></html>""", users=users)

@app.route('/add', methods=['POST'])
def add_user():
    exp = (datetime.now() + timedelta(days=int(request.form.get('days')))).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower()}, {"$set": {"expiry": exp}}, upsert=True); return redirect('/')

@app.route('/delete/<email>')
def delete_user(email):
    users_col.delete_one({"email": email}); return redirect('/')

@bot.message_handler(commands=['start'])
def start(m):
    # لا نحذف الجلسة القديمة هنا للسماح بالاستمرار بعد الـ Redeploy
    bot.send_message(m.chat.id, "🎯 *Persistent Bot Ready*\nRedeploy protection active.\nEnter Email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        # إذا كانت هناك جلسة قديمة، لا ننشئ واحدة جديدة بل نحدثها فقط
        existing = active_sessions_col.find_one({"chat_id": m.chat.id})
        if not existing:
            active_sessions_col.insert_one({"chat_id": m.chat.id, "email": m.text.strip().lower(), "is_running": False})
        bot.send_message(m.chat.id, "✅ Verified. Enter Token(s):"); bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 Denied.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [t.strip() for t in m.text.split(",")]}})
    bot.send_message(m.chat.id, "Enter Stake:"); bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text)}})
    bot.send_message(m.chat.id, "Enter Target Profit:"); bot.register_next_step_handler(m, save_tp)

def save_tp(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    if not sess.get("accounts_data"):
        accs = {t: {"current_stake": sess["initial_stake"], "win_count": 0, "loss_count": 0, "total_profit": 0.0, "consecutive_losses": 0, "active_contract": None, "target_check_time": None} for t in sess["tokens"]}
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"accounts_data": accs}})
    
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tp_goal": float(m.text), "is_running": True}})
    bot.send_message(m.chat.id, "🚀 Running! Data is now safe from Redeploy.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
    threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}}); bot.send_message(m.chat.id, "🛑 Stopped.")

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)), use_reloader=False), daemon=True).start()
    bot.infinity_polling()
