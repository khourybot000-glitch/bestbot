import websocket, json, time, os, threading, queue
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
TOKEN = "8433565422:AAFkpdRh0DUoqTAxlK_G63NAbRPTPYoFM38"
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
            item = msg_queue.get()
            chat_id, text = item[0], item[1]
            markup = item[2] if len(item) > 2 else None
            bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)
            msg_queue.task_done()
            time.sleep(0.05) 
        except: pass

threading.Thread(target=message_worker, daemon=True).start()

def safe_send(chat_id, text, markup=None):
    msg_queue.put((chat_id, text, markup))

def quick_request(api_token, request_data):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=12)
        ws.send(json.dumps({"authorize": api_token}))
        res_auth = json.loads(ws.recv())
        if "authorize" in res_auth:
            ws.send(json.dumps(request_data))
            res = json.loads(ws.recv())
            ws.close()
            return res
        ws.close()
    except: pass
    return None

def execute_trade(api_token, buy_req, currency):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=12)
        ws.send(json.dumps({"authorize": api_token}))
        auth_res = json.loads(ws.recv())
        if "authorize" in auth_res:
            curr = auth_res["authorize"].get("currency", currency)
            buy_req["currency"] = curr
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

# --- ENGINE ---
def trade_engine(chat_id):
    last_processed_second = -1
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): 
            break # الخروج من المحرك عند التوقف
        
        try:
            now = datetime.now()
            # فحص النتائج
            for token, acc in session.get("accounts_data", {}).items():
                if acc.get("active_contract") and acc.get("target_check_time"):
                    if now >= datetime.fromisoformat(acc["target_check_time"]):
                        res_res = quick_request(token, {"proposal_open_contract": 1, "contract_id": acc["active_contract"]})
                        if res_res and res_res.get("proposal_open_contract", {}).get("is_expired"):
                            process_result(chat_id, token, res_res)

            # التحليل السداسي
            if now.second in [0, 10, 20, 30, 40, 50] and now.second != last_processed_second:
                last_processed_second = now.second
                # التأكد من عدم وجود صفقة مفتوحة
                if any(acc.get("active_contract") for acc in session.get("accounts_data", {}).values()): continue

                res = quick_request(session['tokens'][0], {"ticks_history": "R_100", "count": 20, "end": "latest", "style": "ticks"})
                if res and "history" in res:
                    p = res["history"]["prices"]
                    c1 = "UP" if p[4] > p[0] else "DOWN"
                    c2 = "UP" if p[9] > p[5] else "DOWN"
                    c3 = "UP" if p[14] > p[10] else "DOWN"
                    c4 = "UP" if p[19] > p[15] else "DOWN"
                    
                    if c1 == "UP" and c2 == "DOWN" and c3 == "UP" and c4 == "DOWN":
                        open_trade(chat_id, session, "PUT", "+0.6")
                    elif c1 == "DOWN" and c2 == "UP" and c3 == "DOWN" and c4 == "UP":
                        open_trade(chat_id, session, "CALL", "-0.6")
            time.sleep(0.1)
        except: time.sleep(1)

def open_trade(chat_id, session, side, barrier):
    target_time = (datetime.now() + timedelta(seconds=18)).isoformat()
    for t in session['tokens']:
        acc = session['accounts_data'].get(t)
        if acc:
            buy_res = execute_trade(t, {"amount": acc["current_stake"], "basis": "stake", "contract_type": side, "duration": 5, "duration_unit": "t", "symbol": "R_100", "barrier": barrier}, acc["currency"])
            if buy_res and "buy" in buy_res:
                active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {f"accounts_data.{t}.active_contract": buy_res["buy"]["contract_id"], f"accounts_data.{t}.target_check_time": target_time}})
                safe_send(chat_id, f"🚀 *New {side} Opened*")

def process_result(chat_id, token, res):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    acc = session['accounts_data'].get(token)
    profit = float(res.get("proposal_open_contract", {}).get("profit", 0))
    
    new_total = acc["total_profit"] + profit
    new_wins = acc.get("win_count", 0) + (1 if profit > 0 else 0)
    new_losses = acc.get("loss_count", 0) + (1 if profit <= 0 else 0)
    
    if profit > 0:
        new_stake, new_streak, status = session["initial_stake"], 0, "✅ *WIN*"
    else:
        new_stake, new_streak, status = float("{:.2f}".format(acc["current_stake"] * 9)), acc.get("consecutive_losses", 0) + 1, "❌ *LOSS*"

    # تحديث مؤقت للبيانات
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {f"accounts_data.{token}.current_stake": new_stake, f"accounts_data.{token}.consecutive_losses": new_streak, f"accounts_data.{token}.total_profit": new_total, f"accounts_data.{token}.win_count": new_wins, f"accounts_data.{token}.loss_count": new_losses, f"accounts_data.{token}.active_contract": None, f"accounts_data.{token}.target_check_time": None}})
    
    stats_msg = f"📊 *Status:* {status}\n━━━━━━━━━━━━━━━\nW: `{new_wins}` | L: `{new_losses}`\nNet: `{new_total:.2f}`\nNext: `{new_stake}`"
    start_markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀')

    # فحص شروط التوقف ومسح البيانات
    if new_total >= session.get("target_profit", 999999):
        reset_session_data(chat_id)
        safe_send(chat_id, stats_msg + "\n\n🎯 *Target Profit Reached! Data Cleared.*", markup=start_markup)
    elif new_streak >= 2:
        reset_session_data(chat_id)
        safe_send(chat_id, stats_msg + "\n\n🛑 *Stopped (2 Losses). Data Cleared.*", markup=start_markup)
    else:
        safe_send(chat_id, stats_msg)

def reset_session_data(chat_id):
    """وظيفة لمسح كافة الإحصائيات عند التوقف"""
    sess = active_sessions_col.find_one({"chat_id": chat_id})
    if sess:
        accs = sess.get("accounts_data", {})
        for t in accs:
            accs[t].update({
                "current_stake": sess["initial_stake"],
                "total_profit": 0.0,
                "consecutive_losses": 0,
                "win_count": 0,
                "loss_count": 0,
                "active_contract": None,
                "target_check_time": None
            })
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False, "accounts_data": accs}})

# --- HTML ADMIN PANEL (كما كان سابقا) ---
@app.route('/')
def index():
    users = list(users_col.find())
    return render_template_string("""
    <!DOCTYPE html><html><head><title>Admin Control</title><style>
    body{font-family:sans-serif; background:#f0f2f5; padding:40px; text-align:center;}
    .card{max-width:800px; margin:auto; background:white; padding:30px; border-radius:15px; box-shadow:0 5px 20px rgba(0,0,0,0.1);}
    input, select{padding:10px; margin:5px; border-radius:5px; border:1px solid #ccc;}
    button{padding:10px 20px; background:#28a745; color:white; border:none; border-radius:5px; cursor:pointer;}
    table{width:100%; border-collapse:collapse; margin-top:20px;}
    th, td{padding:12px; border-bottom:1px solid #eee; text-align:left;}
    .del{color:#dc3545; font-weight:bold; text-decoration:none;}
    </style></head><body><div class="card"><h2>🚀 User Management</h2>
    <form action="/add" method="POST">
    <input type="email" name="email" placeholder="Email" required>
    <select name="days">
    <option value="1">1 Day</option>
    <option value="30">30 Days</option>
    <option value="36500">36500 Days</option>
    </select><button type="submit">Add User</button></form>
    <table><thead><tr><th>Email</th><th>Expiry</th><th>Action</th></tr></thead>
    <tbody>{% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" class="del">Remove</a></td></tr>{% endfor %}</tbody>
    </table></div></body></html>""", users=users)

@app.route('/add', methods=['POST'])
def add_user():
    days = int(request.form.get('days'))
    exp = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    users_col.update_one({"email": request.form.get('email').lower().strip()}, {"$set": {"expiry": exp}}, upsert=True)
    return redirect('/')

@app.route('/delete/<email>')
def delete_user(email):
    users_col.delete_one({"email": email}); return redirect('/')

# --- TELEGRAM ---
@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id, "🤖 *System Interface*\nPlease enter Email:")
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Verified. Enter Token:")
        bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 Denied.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False}}, upsert=True)
    bot.send_message(m.chat.id, "Stake Amount:")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text)}})
    bot.send_message(m.chat.id, "Target Profit (TP):")
    bot.register_next_step_handler(m, save_tp)

def save_tp(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": float(m.text)}})
    bot.send_message(m.chat.id, "Ready.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def run_bot(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    if sess:
        # منع التشغيل المزدوج
        if sess.get("is_running"):
            return bot.send_message(m.chat.id, "⚠️ Bot is already running!")
            
        accs = {}
        for t in sess["tokens"]:
            auth_info = quick_request(t, {"authorize": t})
            currency = auth_info.get("authorize", {}).get("currency", "USD") if auth_info else "USD"
            accs[t] = {"current_stake": sess["initial_stake"], "total_profit": 0.0, "consecutive_losses": 0, "win_count": 0, "loss_count": 0, "active_contract": None, "target_check_time": None, "currency": currency}
        
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
        bot.send_message(m.chat.id, "🚀 *Bot Active*", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        
        # تشغيل المحرك في خيط جديد
        t = threading.Thread(target=trade_engine, args=(m.chat.id,))
        t.daemon = True
        t.start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_bot(m):
    reset_session_data(m.chat.id)
    bot.send_message(m.chat.id, "🛑 *Bot Stopped & Data Reset*", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
