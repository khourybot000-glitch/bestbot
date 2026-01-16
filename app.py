import websocket, json, time, multiprocessing, os
from flask import Flask, render_template_string, request
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- الإعدادات النهائية ---
TOKEN = "8433565422:AAGtEB14VNHt2n3wTkXkJ-Rt-9RBTJ4xdXo"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN)
db_client = MongoClient(MONGO_URI)
db = db_client['trading_bot']
sessions_col = db['active_sessions'] 

manager = multiprocessing.Manager()
users_states = manager.dict()

# --- دالة مسح بيانات التداول (TP/STOP) مع الحفاظ على الصلاحية فقط ---
def clear_user_session(chat_id, email):
    email = email.lower()
    # جلب الصلاحية قبل الحذف
    user_data = sessions_col.find_one({"email": email})
    expiry = user_data.get("expiry_date") if user_data else None
    
    # حذف الجلسة بالكامل
    if chat_id in users_states:
        del users_states[chat_id]
    sessions_col.delete_one({"chat_id": chat_id})
    
    # إعادة حفظ الصلاحية فقط تحت معرف الإيميل
    if expiry:
        sessions_col.update_one(
            {"email": email},
            {"$set": {"expiry_date": expiry}},
            upsert=True
        )

# --- فحص الصلاحية ---
def is_authorized(email):
    email = email.strip().lower()
    if not os.path.exists("user_ids.txt"): return False
    with open("user_ids.txt", "r") as f:
        auth_emails = [line.strip().lower() for line in f.readlines()]
    if email not in auth_emails: return False

    user_data = sessions_col.find_one({"email": email})
    if user_data and "expiry_date" in user_data:
        try:
            expiry_time = datetime.strptime(user_data["expiry_date"], "%Y-%m-%d %H:%M")
            return datetime.now() <= expiry_time
        except: return False
    return False

def sync_to_cloud(chat_id):
    if chat_id in users_states:
        data = dict(users_states[chat_id])
        sessions_col.update_one({"chat_id": chat_id}, {"$set": data}, upsert=True)

# --- محرك التحليل والصفقات ---
def global_analysis():
    ws = None
    while True:
        try:
            if ws is None: ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
            ws.send(json.dumps({"ticks_history": "R_100", "count": 3, "end": "latest", "style": "ticks"}))
            res = json.loads(ws.recv()).get("history", {}).get("prices", [])
            if len(res) >= 3:
                def d(p): return int("{:.2f}".format(p).split('.')[1][1])
                t1, t2, t3 = d(res[0]), d(res[1]), d(res[2])
                # النمط المطلوب 9-8-9 أو 8-9-8
                if (t1 == 9 and t2 == 8 and t3 == 9) or (t1 == 8 and t2 == 9 and t3 == 8):
                    for cid in list(users_states.keys()):
                        u = users_states[cid]
                        if u.get("is_running") and not u.get("is_trading") and is_authorized(u.get("email")):
                            multiprocessing.Process(target=execute_trade, args=(cid,)).start()
            time.sleep(0.5)
        except:
            if ws: ws.close()
            ws = None; time.sleep(2)

def execute_trade(chat_id):
    if chat_id not in users_states: return
    state = users_states[chat_id]
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": state["api_token"]}))
        ws.recv()
        req = {"proposal": 1, "amount": state["current_stake"], "basis": "stake", 
               "contract_type": "DIGITOVER", "barrier": "1", "currency": state["currency"], 
               "duration": 1, "duration_unit": "t", "symbol": "R_100"}
        ws.send(json.dumps(req))
        prop = json.loads(ws.recv()).get("proposal")
        if prop:
            ws.send(json.dumps({"buy": prop["id"], "price": state["current_stake"]}))
            buy_res = json.loads(ws.recv())
            if "buy" in buy_res:
                new_state = users_states[chat_id].copy()
                new_state["is_trading"] = True
                new_state["active_contract"] = buy_res["buy"]["contract_id"]
                users_states[chat_id] = new_state
                bot.send_message(chat_id, "🚀 **نمط مكتشف!** تم دخول صفقة OVER 1")
                time.sleep(8)
                check_result(chat_id)
        ws.close()
    except: pass

def check_result(chat_id):
    if chat_id not in users_states: return
    state = users_states[chat_id]
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": state["api_token"]})); ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": state["active_contract"]}))
        res = json.loads(ws.recv()).get("proposal_open_contract", {})
        ws.close()
        if res.get("is_expired") == 1:
            profit = float(res.get("profit", 0))
            new_state = users_states[chat_id].copy()
            new_state["is_trading"] = False
            if profit > 0:
                new_state["win_count"] += 1; new_state["current_stake"] = new_state["initial_stake"]; icon = "✅ ربح"
            else:
                new_state["loss_count"] += 1; new_state["current_stake"] *= 9; icon = "❌ خسارة"
            new_state["total_profit"] += profit
            users_states[chat_id] = new_state
            
            # فحص الـ TP
            if new_state["total_profit"] >= new_state["tp"]:
                bot.send_message(chat_id, f"🎯 مبروك! تم الوصول للهدف ({new_state['total_profit']:.2f}).\nتم تصفير بيانات الجلسة.")
                clear_user_session(chat_id, new_state["email"])
                return

            sync_to_cloud(chat_id)
            bot.send_message(chat_id, f"{icon} ({profit:.2f})\nإجمالي الأرباح: {new_state['total_profit']:.2f}")
    except: pass

# --- لوحة التحكم HTML ---
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>إدارة الصلاحيات</title>
<style>
    body { font-family: sans-serif; background: #f0f2f5; padding: 20px; text-align: center; }
    .card { background: white; padding: 20px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); max-width: 700px; margin: auto; }
    table { width: 100%; border-collapse: collapse; margin-top: 20px; }
    th, td { padding: 12px; border-bottom: 1px solid #eee; text-align: center; }
    th { background: #1a73e8; color: white; }
    .btn-upd { background: #28a745; color: white; border: none; padding: 8px 15px; border-radius: 5px; cursor: pointer; }
    .btn-stop { background: #dc3545; color: white; border: none; padding: 8px 15px; border-radius: 5px; cursor: pointer; }
    input[type="number"] { width: 45px; padding: 5px; border: 1px solid #ccc; border-radius: 4px; }
    select { padding: 5px; border-radius: 4px; }
</style>
</head>
<body>
    <div class="card">
        <h2>👥 قائمة التحكم بالمشتركين</h2>
        <table>
            <tr><th>المستخدم</th><th>المدة</th><th>الإجراء</th></tr>
            {% for email in emails %}
            <tr>
                <form method="POST" action="/update_expiry">
                    <td><strong>{{ email }}</strong><input type="hidden" name="email" value="{{ email }}"></td>
                    <td>
                        <input type="number" name="amount" value="1" min="1">
                        <select name="unit"><option value="hours">ساعة</option><option value="days">يوم</option></select>
                    </td>
                    <td>
                        <button type="submit" name="action" value="update" class="btn-upd">تحديث</button>
                        <button type="submit" name="action" value="cancel" class="btn-stop">إيقاف</button>
                    </td>
                </form>
            </tr>
            {% endfor %}
        </table>
    </div>
</body></html>
"""

@app.route('/admin')
def admin_panel():
    emails = []
    if os.path.exists("user_ids.txt"):
        with open("user_ids.txt", "r") as f:
            emails = [line.strip() for line in f.readlines() if line.strip()]
    return render_template_string(ADMIN_HTML, emails=emails)

@app.route('/update_expiry', methods=['POST'])
def update_expiry():
    email = request.form.get('email').lower()
    action = request.form.get('action')
    if action == "cancel":
        expiry_str = "2000-01-01 00:00"; msg = f"🚫 تم إيقاف صلاحية {email}."
    else:
        amount = int(request.form.get('amount')); unit = request.form.get('unit')
        exp = datetime.now() + (timedelta(hours=amount) if unit == "hours" else timedelta(days=amount))
        expiry_str = exp.strftime("%Y-%m-%d %H:%M"); msg = f"✅ تم تفعيل {email} حتى {expiry_str}"
    
    sessions_col.update_one({"email": email}, {"$set": {"expiry_date": expiry_str}}, upsert=True)
    return f"<div dir='rtl'><h3>{msg}</h3><br><a href='/admin'>العودة</a></div>"

# --- أوامر التلجرام ---
@bot.message_handler(commands=['start'])
def start(m):
    user_data = sessions_col.find_one({"chat_id": m.chat.id})
    if user_data and is_authorized(user_data['email']):
        users_states[m.chat.id] = user_data
        bot.send_message(m.chat.id, "أهلاً بك مجدداً! اختر نوع الحساب:", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰'))
    else:
        bot.send_message(m.chat.id, "👋 مرحباً بك! يرجى إدخال البريد الإلكتروني المعتمد:")
        bot.register_next_step_handler(m, login_process)

def login_process(m):
    email = m.text.strip().lower()
    if is_authorized(email):
        config = {"chat_id": m.chat.id, "email": email, "api_token": "", "initial_stake": 0.0, "current_stake": 0.0, "tp": 0.0, "currency": "USD", "is_running": False, "is_trading": False, "total_profit": 0.0, "win_count": 0, "loss_count": 0}
        users_states[m.chat.id] = config; sync_to_cloud(m.chat.id)
        bot.send_message(m.chat.id, "✅ تسجيل دخول ناجح!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰'))
    else:
        bot.send_message(m.chat.id, "🚫 عذراً، هذا البريد غير مسموح له بالدخول أو انتهت صلاحيته.")

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def mode(m):
    if m.chat.id not in users_states: return start(m)
    new_state = users_states[m.chat.id].copy(); new_state["currency"] = "USD" if "Demo" in m.text else "tUSDT"
    users_states[m.chat.id] = new_state
    bot.send_message(m.chat.id, "قم بإرسال الـ API Token:"); bot.register_next_step_handler(m, save_token)

def save_token(m):
    new_state = users_states[m.chat.id].copy(); new_state["api_token"] = m.text.strip(); users_states[m.chat.id] = new_state
    bot.send_message(m.chat.id, "أدخل مبلغ الصفقة الواحدة (Stake):"); bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    try:
        new_state = users_states[m.chat.id].copy(); val = float(m.text)
        new_state["initial_stake"] = val; new_state["current_stake"] = val; users_states[m.chat.id] = new_state
        bot.send_message(m.chat.id, "أدخل الربح المستهدف الإجمالي (Target Profit):"); bot.register_next_step_handler(m, save_tp)
    except: pass

def save_tp(m):
    try:
        new_state = users_states[m.chat.id].copy(); new_state["tp"] = float(m.text); new_state["is_running"] = True
        users_states[m.chat.id] = new_state; sync_to_cloud(m.chat.id)
        bot.send_message(m.chat.id, "🚀 البوت بدأ العمل ومراقبة السوق!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
    except: pass

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_call(m):
    email = users_states[m.chat.id].get("email") if m.chat.id in users_states else ""
    clear_user_session(m.chat.id, email)
    bot.send_message(m.chat.id, "🛑 تم إيقاف البوت ومسح بياناتك. يمكنك البدء من جديد عند الرغبة.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰'))

@app.route('/')
def home(): return "Bot is Alive and Running"

if __name__ == '__main__':
    # مزامنة الجلسات القديمة
    for doc in sessions_col.find(): 
        if "chat_id" in doc: users_states[doc['chat_id']] = doc
    
    # حل مشكلة الـ Port لـ Render
    render_port = int(os.environ.get("PORT", 10000))
    
    multiprocessing.Process(target=global_analysis, daemon=True).start()
    multiprocessing.Process(target=lambda: app.run(host='0.0.0.0', port=render_port), daemon=True).start()
    bot.infinity_polling()
