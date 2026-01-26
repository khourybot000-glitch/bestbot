import websocket, json, time, os, threading, queue, pandas as pd, pandas_ta as ta
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
TOKEN = "8433565422:AAHZizvDYI2Gml0tit0AQH92NmqGBtleigM"
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

# --- STRATEGY: 30-TICK CANDLE RE-ENTRY ---
def trade_engine(chat_id):
    last_processed_minute = -1
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        try:
            now = datetime.now()
            # التحليل عند بداية كل دقيقة (يتوافق مع دورات الشموع)
            if now.second == 0 and now.minute != last_processed_minute:
                res = quick_request(session['tokens'][0], {"ticks_history": "R_100", "count": 1500, "end": "latest", "style": "ticks"})
                prices = res.get("history", {}).get("prices", []) if res else []

                if len(prices) >= 1500:
                    df_ticks = pd.DataFrame(prices, columns=['close'])
                    # تحويل لشموع 30 تيك
                    candles = df_ticks.iloc[::30, :].copy().reset_index(drop=True)
                    
                    ema200 = ta.ema(candles['close'], length=20)
                    bb = ta.bbands(candles['close'], length=20, std=2)
                    rsi = ta.rsi(candles['close'], length=14)
                    
                    c_close = candles['close'].iloc[-1]   # الحالية
                    p_close = candles['close'].iloc[-2]   # السابقة
                    curr_ema = ema200.iloc[-1]
                    lower_b, upper_b = bb['BBL_20_2.0'].iloc[-1], bb['BBU_20_2.0'].iloc[-1]
                    curr_rsi = rsi.iloc[-1]
                    
                    direction = None
                    # منطق التلاقي الذهبي (EMA + BB Re-entry + RSI)
                    if c_close > curr_ema: # اتجاه صاعد
                        if p_close < lower_b and c_close > lower_b and curr_rsi > 30:
                            direction = "CALL"
                    elif c_close < curr_ema: # اتجاه هابط
                        if p_close > upper_b and c_close < upper_b and curr_rsi < 70:
                            direction = "PUT"

                    if direction:
                        safe_send(chat_id, f"🚀 *Strong Signal:* {direction}\nRSI: `{curr_rsi:.1f}` | BB Re-entry Detected")
                        for t in session['tokens']:
                            acc = session['accounts_data'].get(t)
                            if acc:
                                amt = float("{:.2f}".format(acc["current_stake"]))
                                buy_res = execute_trade(t, {"amount": amt, "basis": "stake", "contract_type": direction, "currency": "USD", "duration": 1, "duration_unit": "m", "symbol": "R_100"})
                                if buy_res and "buy" in buy_res:
                                    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {f"accounts_data.{t}.active_contract": buy_res["buy"]["contract_id"]}})

                        last_processed_minute = now.minute
                        time.sleep(66) # مدة الانتظار (66 ثانية)
                        
                        curr_sess = active_sessions_col.find_one({"chat_id": chat_id})
                        for t in curr_sess['tokens']:
                            acc = curr_sess['accounts_data'].get(t)
                            if acc.get("active_contract"):
                                for _ in range(15):
                                    res_res = quick_request(t, {"proposal_open_contract": 1, "contract_id": acc["active_contract"]})
                                    if res_res and res_res.get("proposal_open_contract", {}).get("is_expired"):
                                        process_result(chat_id, t, res_res)
                                        break
                                    time.sleep(1)
                    else: last_processed_minute = now.minute
            time.sleep(0.5)
        except: time.sleep(1)

def process_result(chat_id, token, res):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    acc = session['accounts_data'].get(token)
    contract = res.get("proposal_open_contract", {})
    if contract.get("is_expired") == 1:
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
            f"accounts_data.{token}.active_contract": None
        }})
        
        report = (f"🔍 *Result:* {status}\n💰 Profit: `{profit:.2f}`\n📊 Total: `{new_total:.2f}`\n📈 W: `{new_wins}` | L: `{new_losses}`\n🔄 MG: {new_mg}/2")
        safe_send(chat_id, report)
        
        # التوقف بعد خسارتين بناءً على تعليماتك
        if new_mg >= 4:
            safe_send(chat_id, "🛑 *Stop Loss:* 4 consecutive losses. Session Terminated."); active_sessions_col.delete_one({"chat_id": chat_id})

# --- ADMIN PANEL HTML ---
@app.route('/')
def index():
    users = list(users_col.find())
    return render_template_string("""
    <!DOCTYPE html><html><head><title>Bot Admin</title>
    <style>
        body{font-family:Arial; background:#f0f2f5; text-align:center; padding:50px;}
        .main{max-width:800px; margin:auto; background:white; padding:30px; border-radius:15px; shadow:0 5px 15px rgba(0,0,0,0.1);}
        table{width:100%; border-collapse:collapse; margin-top:20px;}
        th,td{padding:12px; border:1px solid #ddd;} th{background:#007bff; color:white;}
        .btn-add{background:#28a745; color:white; border:none; padding:10px 20px; border-radius:5px; cursor:pointer;}
    </style></head>
    <body><div class="main">
        <h2>🚀 Trading Bot Management</h2>
        <form action="/add" method="POST">
            <input type="email" name="email" placeholder="Email" required style="padding:10px;">
            <select name="days" style="padding:10px;"><option value="1">1 Day</option><option value="30">30 Days</option><option value="36500">Life</option></select>
            <button type="submit" class="btn-add">Grant Access</button>
        </form>
        <table><tr><th>User Email</th><th>Expiry</th><th>Action</th></tr>
        {% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" style="color:red;">Remove</a></td></tr>{% endfor %}
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
    active_sessions_col.delete_one({"chat_id": m.chat.id})
    bot.send_message(m.chat.id, "🎯 *Master AI V3 Ready*\nEMA 200 + BB Candle Logic\nPlease enter your email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        active_sessions_col.insert_one({"chat_id": m.chat.id, "email": m.text.strip().lower(), "is_running": False})
        bot.send_message(m.chat.id, "✅ Access Verified. Enter Token(s):"); bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 No active subscription.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [t.strip() for t in m.text.split(",")]}})
    bot.send_message(m.chat.id, "Enter Initial Stake:"); bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text)}})
    bot.send_message(m.chat.id, "Enter Target Profit (TP):"); bot.register_next_step_handler(m, save_tp)

def save_tp(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    accs = {t: {"current_stake": sess["initial_stake"], "win_count": 0, "loss_count": 0, "total_profit": 0.0, "consecutive_losses": 0, "active_contract": None} for t in sess["tokens"]}
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tp_goal": float(m.text), "is_running": True, "accounts_data": accs}})
    bot.send_message(m.chat.id, "🚀 Bot Started! Analyzing 30-Tick Candles.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
    threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop(m):
    active_sessions_col.delete_one({"chat_id": m.chat.id}); bot.send_message(m.chat.id, "🛑 Stopped.")

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)), use_reloader=False), daemon=True).start()
    bot.infinity_polling()
