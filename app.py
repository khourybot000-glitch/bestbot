import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIGURATION ---
TOKEN = "8433565422:AAFirUgJZ9gNNlfemCptH-PxbvupvE8--Hw"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V2']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

# --- المنطق المحدث لاستخراج الرقم الثاني بعد الفاصلة بدقة ---
def extract_second_digit_fixed(price):
    try:
        # تحويل السعر لنص بدقة عالية
        s_price = "{:.8f}".format(float(price)) 
        if "." in s_price:
            # تقسيم الرقم عند الفاصلة وأخذ الجزء العشري
            decimal_part = s_price.split(".")[1]
            # الرقم الثاني بعد الفاصلة هو العنصر ذو الكشاف 1
            digit = int(decimal_part[1])
            # السعر المختصر للعرض فقط
            display_price = s_price.split(".")[0] + "." + decimal_part[:2]
            return digit, display_price
    except Exception as e:
        print(f"Extraction Error: {e}")
    return None, None

def quick_ws_request(token, request_dict):
    ws = None
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=7)
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
        # محاولة التحليل في أول ثانيتين من الدقيقة
        if 0 <= now.second <= 2 and now.minute != last_processed_min:
            last_processed_min = now.minute
            
            # جلب تيك التحليل
            res = quick_ws_request(session['api_token'], {"ticks": "R_100", "count": 1})
            if res and "tick" in res:
                price_val = res["tick"]["quote"]
                target_digit, display_p = extract_second_digit_fixed(price_val)
                
                if target_digit is not None:
                    bot.send_message(chat_id, f"🕒 *دورة {now.strftime('%H:%M')}:*\n💰 السعر: `{display_p}`\n🎯 الرقم المستهدف: `{target_digit}`")
                    
                    try:
                        # فتح اتصال المطاردة
                        ws_hunt = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
                        ws_hunt.send(json.dumps({"authorize": session['api_token']}))
                        ws_hunt.recv()
                        ws_hunt.send(json.dumps({"ticks": "R_100", "subscribe": 1}))
                        
                        start_hunt = time.time()
                        trade_done = False
                        
                        while time.time() - start_hunt < 55:
                            tick_data = json.loads(ws_hunt.recv())
                            if "tick" in tick_data:
                                current_digit, _ = extract_second_digit_fixed(tick_data["tick"]["quote"])
                                
                                if current_digit == target_digit:
                                    stake = session["current_stake"]
                                    ws_hunt.send(json.dumps({
                                        "proposal": 1, "amount": stake, "basis": "stake",
                                        "contract_type": "DIGITDIFF", "duration": 1, "duration_unit": "t",
                                        "symbol": "R_100", "barrier": str(target_digit), "currency": "USD"
                                    }))
                                    p_res = json.loads(ws_hunt.recv()).get("proposal")
                                    if p_res:
                                        ws_hunt.send(json.dumps({"buy": p_res["id"], "price": stake}))
                                        buy_info = json.loads(ws_hunt.recv())
                                        if "buy" in buy_info:
                                            bot.send_message(chat_id, f"🎯 ظهر الرقم `{target_digit}`! تم الدخول.")
                                            ws_hunt.close()
                                            trade_done = True
                                            
                                            time.sleep(8) # انتظار النتيجة
                                            res_final = quick_ws_request(session['api_token'], {"proposal_open_contract": 1, "contract_id": buy_info["buy"]["contract_id"]})
                                            if res_final:
                                                profit = float(res_final["proposal_open_contract"].get("profit", 0))
                                                handle_result(chat_id, profit)
                                            break
                        if not trade_done: bot.send_message(chat_id, "⏳ لم يظهر الرقم.")
                        if ws_hunt: ws_hunt.close()
                    except: pass
        time.sleep(0.5)

def handle_result(chat_id, profit):
    session = active_sessions_col.find_one({"chat_id": chat_id})
    is_win = profit > 0
    new_total = session["total_profit"] + profit
    
    stop_session = False
    if is_win:
        nxt = session["initial_stake"]
    else:
        if session["current_stake"] > session["initial_stake"]:
            nxt = session["initial_stake"]
            stop_session = True # خسارة ثانية
        else:
            nxt = float("{:.2f}".format(session["initial_stake"] * 14))
    
    active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"total_profit": new_total, "current_stake": nxt}})
    bot.send_message(chat_id, f"📊 النتيجة: {'✅ ربح' if is_win else '❌ خسارة'}\n💰 الإجمالي: `{new_total:.2f}`")

    if stop_session or new_total >= session["target_profit"]:
        active_sessions_col.update_one({"chat_id": chat_id}, {"$set": {"is_running": False}})
        bot.send_message(chat_id, "🛑 توقف البوت.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

# --- HTML ADMIN PANEL ---
HTML_ADMIN = """
<!DOCTYPE html><html><head><title>Admin Panel</title>
<style>
    body { font-family: 'Segoe UI', sans-serif; background: #f4f7f6; margin: 0; padding: 20px; text-align: center; }
    .container { max-width: 800px; margin: auto; background: white; padding: 40px; border-radius: 20px; box-shadow: 0 10px 30px rgba(0,0,0,0.1); }
    h2 { color: #2c3e50; margin-bottom: 30px; }
    form { background: #ecf0f1; padding: 20px; border-radius: 15px; display: flex; gap: 10px; justify-content: center; margin-bottom: 30px; }
    input, select { padding: 12px; border: 1px solid #ddd; border-radius: 8px; }
    button { background: #3498db; color: white; border: none; padding: 12px 25px; border-radius: 8px; cursor: pointer; font-weight: bold; }
    table { width: 100%; border-collapse: collapse; background: white; }
    th { background: #2c3e50; color: white; padding: 15px; }
    td { padding: 15px; border-bottom: 1px solid #eee; text-align: left; }
    .del { color: #e74c3c; font-weight: bold; text-decoration: none; }
</style></head>
<body><div class="container">
    <h2>💎 Digit Trader Control</h2>
    <form action="/add" method="POST">
        <input type="email" name="email" placeholder="User Email" required>
        <select name="days"><option value="1">1 Day</option><option value="30">30 Days</option><option value="36500">Lifetime</option></select>
        <button type="submit">Grant Access</button>
    </form>
    <table><thead><tr><th>Email</th><th>Expiry</th><th>Action</th></tr></thead>
        <tbody>{% for u in users %}<tr><td>{{u.email}}</td><td>{{u.expiry}}</td><td><a href="/delete/{{u.email}}" class="del">Delete</a></td></tr>{% endfor %}</tbody>
    </table>
</div></body></html>
"""

@app.route('/')
def index(): return render_template_string(HTML_ADMIN, users=list(users_col.find()))

@app.route('/add', methods=['POST'])
def add_user():
    e, d = request.form.get('email').lower().strip(), int(request.form.get('days'))
    exp = (datetime.now() + timedelta(days=d)).strftime("%Y-%m-%d")
    users_col.update_one({"email": e}, {"$set": {"expiry": exp}}, upsert=True); return redirect('/')

@app.route('/delete/<email>')
def delete_user(email): users_col.delete_one({"email": email}); return redirect('/')

@bot.message_handler(commands=['start', 'START 🚀'])
def start_bot(m):
    bot.send_message(m.chat.id, "👋 أدخل إيميلك المسجل:"); bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ تم التحقق! أدخل API Token:"); bot.register_next_step_handler(m, save_token)
    else: bot.send_message(m.chat.id, "🚫 لا تملك صلاحية.")

def save_token(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"api_token": m.text.strip()}}, upsert=True)
    bot.send_message(m.chat.id, "مبلغ الرهان:"); bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    try:
        s = float(m.text); active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": s, "current_stake": s}})
        bot.send_message(m.chat.id, "الهدف (TP):"); bot.register_next_step_handler(m, save_tp)
    except: bot.send_message(m.chat.id, "خطأ.")

def save_tp(m):
    try:
        tp = float(m.text)
        active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": tp, "total_profit": 0, "is_running": True}})
        bot.send_message(m.chat.id, "🚀 انطلق! سأحلل عند :00", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=trading_process, args=(m.chat.id,), daemon=True).start()
    except: bot.send_message(m.chat.id, "خطأ.")

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_btn(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    bot.send_message(m.chat.id, "🛑 توقف.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('START 🚀'))

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
