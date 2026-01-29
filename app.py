import websocket, json, time, os, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- الإعدادات (التوكن الجديد) ---
TOKEN = "8433565422:AAFOvdP2gpuR7E6wVaNyw3gtY9Zwp_hLa0E"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN, threaded=True)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

def get_deriv_data(token, request_data):
    """جلب البيانات مع محاولة إعادة الاتصال عند الفشل"""
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=8)
        ws.send(json.dumps({"authorize": token}))
        auth_res = json.loads(ws.recv())
        if "authorize" in auth_res:
            ws.send(json.dumps(request_data))
            response = json.loads(ws.recv())
            ws.close()
            return response, auth_res["authorize"].get("currency", "USD")
        ws.close()
    except:
        pass
    return None, None

def trade_engine(chat_id):
    last_trigger_time = "" 
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break
        
        now = datetime.now()
        # فحص الثواني المطلوبة
        if now.second in [0, 20, 40]:
            current_mark = f"{now.minute}:{now.second}"
            if current_mark != last_trigger_time:
                last_trigger_time = current_mark
                token = session['tokens'][0]
                
                # جلب آخر 10 تيكات
                tick_req = {"ticks_history": "R_100", "count": 10, "end": "latest", "style": "ticks"}
                res, currency = get_deriv_data(token, tick_req)
                
                if res and "history" in res:
                    prices = res["history"]["prices"]
                    if len(prices) >= 10:
                        t1 = prices[0]  # الأقدم
                        t5 = prices[4]  # المنتصف
                        t10 = prices[9] # الأحدث
                        
                        direction, barrier = None, "0"
                        
                        # منطق الاستراتيجية المطلوب
                        # CALL: T1 > T5 AND T10 > T1
                        if t1 > t5 and t10 > t1:
                            direction, barrier = "CALL", "-0.8"
                        # PUT: T1 < T5 AND T10 < T1
                        elif t1 < t5 and t10 < t1:
                            direction, barrier = "PUT", "+0.8"
                        
                        if direction:
                            acc = session["accounts_data"][token]
                            buy_req = {
                                "proposal": 1, "amount": acc["current_stake"], "basis": "stake",
                                "contract_type": direction, "duration": 5, "duration_unit": "t",
                                "symbol": "R_100", "barrier": barrier, "currency": currency
                            }
                            prop_res, _ = get_deriv_data(token, buy_req)
                            
                            if prop_res and "proposal" in prop_res:
                                buy_id = prop_res["proposal"]["id"]
                                exec_res, _ = get_deriv_data(token, {"buy": buy_id, "price": acc["current_stake"]})
                                
                                if exec_res and "buy" in exec_res:
                                    contract_id = exec_res["buy"]["contract_id"]
                                    bot.send_message(chat_id, f"🚀 *تم دخول صفقة {direction}*\nT1: {t1} | T5: {t5} | T10: {t10}")
                                    
                                    # انتظار النتيجة 18 ثانية
                                    time.sleep(18)
                                    final_res, _ = get_deriv_data(token, {"proposal_open_contract": 1, "contract_id": contract_id})
                                    update_stats(chat_id, token, final_res)
                
        time.sleep(0.1) # سرعة فحص الثواني

def update_stats(chat_id, token, res):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    acc = session['accounts_data'][token]
    contract = res.get("proposal_open_contract", {})
    profit = float(contract.get("profit", 0))
    
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
    
    bot.send_message(chat_id, 
        f"📊 *النتيجة:* {status}\n"
        f"💰 صافي الربح: `{new_total_net:.2f}`\n"
        f"🏆 ربح: `{win_count}` | خسارة: `{loss_count}`\n"
        f"🔄 الرهان القادم: `{new_stake:.2f}`")

    if new_total_net >= session.get("target_profit", 99999) or new_mg >= 2:
        bot.send_message(chat_id, "🛑 توقف البوت تلقائياً."); 
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})

# --- واجهة الـ HTML (Admin Panel) ---
@app.route('/')
def index():
    users = list(users_col.find())
    return render_template_string("""
    <!DOCTYPE html><html><head><title>Admin Panel</title>
    <style>
        body { font-family: 'Segoe UI', sans-serif; background: #f0f2f5; padding: 20px; }
        .container { max-width: 900px; margin: auto; background: white; padding: 40px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); }
        h2 { color: #1a73e8; text-align: center; }
        .form-box { background: #f8f9fa; padding: 25px; border-radius: 10px; border: 1px solid #dee2e6; margin-bottom: 30px; }
        input, select { padding: 12px; margin: 5px; border-radius: 6px; border: 1px solid #ced4da; width: 200px; }
        button { background: #28a745; color: white; border: none; padding: 12px 25px; border-radius: 6px; cursor: pointer; font-weight: bold; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { padding: 15px; border-bottom: 1px solid #eee; text-align: center; }
    </style></head>
    <body><div class="container">
        <h2>🚀 Candle-10 Triple Pulse Admin</h2>
        <div class="form-box">
            <form action="/add" method="POST">
                <input type="email" name="email" placeholder="User Email" required>
                <select name="days">
                    <option value="1">1 Day</option>
                    <option value="30">30 Days</option>
                    <option value="36500">36500 Days (Lifetime)</option>
                </select>
                <button type="submit">Authorize User</button>
            </form>
        </div>
        <table><thead><tr><th>Email</th><th>Expiry Date</th><th>Action</th></tr></thead>
        <tbody>{% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" style="color:red; font-weight:bold; text-decoration:none;">Remove</a></td></tr>{% endfor %}</tbody>
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

# --- أوامر التلغرام ---
@bot.message_handler(commands=['start'])
def start_cmd(m):
    bot.send_message(m.chat.id, "🤖 *Pulse Bot v2.0*\nتحليل 10 تيكات عند ثانية 0، 20، 40\nأدخل الإيميل:")
    bot.register_next_step_handler(m, verify_auth)

def verify_auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ تم التحقق. أرسل API Token الخاص بك:"); bot.register_next_step_handler(m, setup_bot)
    else: bot.send_message(m.chat.id, "🚫 إيميل غير مسجل أو منتهي الصلاحية.")

def setup_bot(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"tokens": [m.text.strip()], "is_running": False}}, upsert=True)
    bot.send_message(m.chat.id, "أدخل مبلغ الرهان الأساسي (Stake):"); bot.register_next_step_handler(m, set_stake)

def set_stake(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text)}})
    bot.send_message(m.chat.id, "أدخل الهدف (Target Profit):"); bot.register_next_step_handler(m, set_tp)

def set_tp(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": float(m.text)}})
    bot.send_message(m.chat.id, "تم الإعداد بنجاح!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

@bot.message_handler(func=lambda m: m.text == 'START 🚀')
def run_trading(m):
    sess = active_sessions_col.find_one({"chat_id": m.chat.id})
    accs = {sess["tokens"][0]: {"current_stake": sess["initial_stake"], "total_profit": 0.0, "consecutive_losses": 0, "win_count": 0, "loss_count": 0}}
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": True, "accounts_data": accs}})
    threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()
    bot.send_message(m.chat.id, "🚀 البوت يعمل الآن ويراقب السوق كل 20 ثانية.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_bot(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}}); bot.send_message(m.chat.id, "🛑 تم إيقاف البوت.")

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
