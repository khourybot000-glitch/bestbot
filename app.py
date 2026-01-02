import websocket, json, time, threading
from flask import Flask
import telebot
from telebot import types

app = Flask(__name__)
bot = telebot.TeleBot("8486698837:AAECvPrA_Xi8REaN6f60YZ3qB_rQXPTieEA")

state = {
    "api_token": "", "initial_stake": 0.0, "current_stake": 0.0, "tp": 0.0, 
    "currency": "USD", "is_running": False, "chat_id": None, "last_price": None,
    "total_profit": 0.0, "win_count": 0, "loss_count": 0, "loss_streak": 0
}

@app.route('/')
def home():
    return "<h1>Bot is Active</h1>"

# دالة مسح البيانات الشاملة
def reset_and_stop(message_text):
    state.update({
        "api_token": "", "is_running": False, "last_price": None, 
        "loss_streak": 0, "win_count": 0, "loss_count": 0, "total_profit": 0.0,
        "initial_stake": 0.0, "current_stake": 0.0
    })
    if state["chat_id"]:
        bot.send_message(state["chat_id"], f"🛑 {message_text}\n(تم مسح جميع البيانات بنجاح)", reply_markup=types.ReplyKeyboardRemove())

@bot.message_handler(func=lambda m: m.text == 'إيقاف 🛑')
def manual_stop(message):
    reset_and_stop("تم إيقاف البوت يدوياً بطلب منك.")

def check_result(contract_id):
    try:
        time.sleep(16)
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": state["api_token"]}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
        res = json.loads(ws.recv())
        ws.close()
        
        profit = float(res.get("proposal_open_contract", {}).get("profit", 0))
        
        if profit < 0:
            state["loss_count"] += 1
            state["loss_streak"] += 1
            if state["loss_streak"] >= 2:
                reset_and_stop("خسارة متتالية (2). توقف اضطراري لحماية الحساب.")
            else:
                state["current_stake"] = round(state["initial_stake"] * 24, 2)
                bot.send_message(state["chat_id"], f"❌ خسارة! دخول مضاعفة x24: **{state['current_stake']}**")
                threading.Thread(target=start_tracking).start()
        else:
            state["win_count"] += 1
            state["total_profit"] += profit
            state["loss_streak"] = 0
            state["current_stake"] = state["initial_stake"]
            
            stats = (f"✅ **صفقة رابحة! +{round(profit, 2)}**\n"
                     f"🏆 أرباح: {state['win_count']} | ❌ خسائر: {state['loss_count']}\n"
                     f"💰 الإجمالي الحالي: {round(state['total_profit'], 2)}")
            bot.send_message(state["chat_id"], stats)
            
            if state["total_profit"] >= state["tp"]:
                reset_and_stop(f"🎯 تم الوصول للهدف (TP): ${round(state['total_profit'], 2)}")
            else:
                threading.Thread(target=start_tracking).start()
    except:
        reset_and_stop("خطأ في نظام التحقق. توقف الأمان.")

def place_trade(action):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": state["api_token"]}))
        ws.recv()
        barrier = "-0.6" if action == "call" else "+0.6"
        trade_req = {
            "buy": 1, "price": float(state["current_stake"]),
            "parameters": {
                "amount": float(state["current_stake"]), "basis": "stake",
                "contract_type": "CALL" if action == "call" else "PUT",
                "currency": state["currency"], "duration": 5, "duration_unit": "t",
                "symbol": "R_100", "barrier": barrier
            }
        }
        ws.send(json.dumps(trade_req))
        res = json.loads(ws.recv())
        ws.close()
        if "buy" in res:
            bot.send_message(state["chat_id"], f"📥 تنفيذ صفقة {action.upper()}\nبمبلغ: {state['current_stake']}")
            threading.Thread(target=check_result, args=(res["buy"]["contract_id"],)).start()
            return True
        return False
    except: return False

def on_message(ws, message):
    data = json.loads(message)
    if "tick" in data:
        curr_p = data["tick"]["quote"]
        curr_d2 = int("{:.2f}".format(curr_p)[-1])
        if state["last_price"] is not None:
            prev_p = state["last_price"]
            prev_d2 = int("{:.2f}".format(prev_p)[-1])
            if curr_d2 == prev_d2 and state["is_running"]:
                action = "call" if curr_p > prev_p else "put"
                ws.close()
                if not place_trade(action): reset_and_stop("فشل تنفيذ الطلب.")
        state["last_price"] = curr_p

def start_tracking():
    if not state["is_running"]: return
    ws = websocket.WebSocketApp(
        "wss://blue.derivws.com/websockets/v3?app_id=16929",
        on_message=on_message,
        on_open=lambda ws: ws.send(json.dumps({"ticks": "R_100", "subscribe": 1}))
    )
    ws.run_forever()

@bot.message_handler(commands=['start'])
def cmd_start(message):
    state["chat_id"] = message.chat.id
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('Demo 🛠️', 'Live 💰')
    bot.send_message(message.chat.id, "🎯 **بوت R_100 المطور**\nالمضاعفة: x24 | التوقف: خسارتين متتاليتين", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def set_acc(message):
    state["currency"] = "USD" if "Demo" in message.text else "tUSDT"
    msg = bot.send_message(message.chat.id, "أدخل التوكن (API Token):")
    bot.register_next_step_handler(msg, set_token)

def set_token(message):
    state["api_token"] = message.text.strip()
    msg = bot.send_message(message.chat.id, "أدخل مبلغ الصفقة الأساسي:")
    bot.register_next_step_handler(msg, set_stake)

def set_stake(message):
    state["initial_stake"] = state["current_stake"] = float(message.text)
    msg = bot.send_message(message.chat.id, "أدخل الربح المستهدف (Take Profit):")
    bot.register_next_step_handler(msg, set_tp)

def set_tp(message):
    state["tp"] = float(message.text)
    state["is_running"] = True
    stop_markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    stop_markup.add('إيقاف 🛑')
    bot.send_message(message.chat.id, f"🚀 تم بدء العمل.\nالهدف: ${state['tp']}", reply_markup=stop_markup)
    threading.Thread(target=start_tracking).start()

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000)).start()
    bot.infinity_polling()
