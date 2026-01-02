import websocket, json, time, threading
from flask import Flask
import telebot
from telebot import types

app = Flask(__name__)
bot = telebot.TeleBot("8305021112:AAEcxFTNhd4bzLuKz9KkMmZ4_XhL3_L2Mfs")

# تخزين البيانات
state = {
    "api_token": "", "stake": 0.0, "tp": 0.0, "currency": "USD",
    "is_running": False, "chat_id": None, "last_price": None,
    "total_profit": 0.0, "active_ws": None
}

@app.route('/')
def home():
    return "<h1>Digit Match R_10 (5 Ticks) Active</h1>"

def reset_and_stop(message_text):
    state.update({"api_token": "", "is_running": False, "last_price": None})
    if state["chat_id"]:
        bot.send_message(state["chat_id"], f"🛑 **Bot Stopped:** {message_text}")

def check_result(contract_id):
    try:
        time.sleep(16) # الانتظار المطلوب قبل التأكد
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": state["api_token"]}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
        res = json.loads(ws.recv())
        ws.close()
        
        profit = float(res.get("proposal_open_contract", {}).get("profit", 0))
        if profit < 0:
            reset_and_stop(f"Loss detected ({profit}). Wiping all data and shutting down.")
        else:
            state["total_profit"] += profit
            bot.send_message(state["chat_id"], f"✅ WIN! Profit: {profit}\nTotal: {round(state['total_profit'], 2)}")
            # إعادة تشغيل الاتصال بعد الانتظار
            threading.Thread(target=start_tracking).start()
    except Exception as e:
        reset_and_stop(f"Error checking result: {str(e)}")

def place_trade(action, amount, prev_p, curr_p):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": state["api_token"]}))
        ws.recv()
        
        # Barrier 0.6
        barrier = "-0.6" if action == "call" else "+0.6"
        
        trade_req = {
            "buy": 1, "price": float(amount),
            "parameters": {
                "amount": float(amount), "basis": "stake",
                "contract_type": "CALL" if action == "call" else "PUT",
                "currency": state["currency"], "duration": 5, "duration_unit": "t",
                "symbol": "R_10", "barrier": barrier
            }
        }
        ws.send(json.dumps(trade_req))
        res = json.loads(ws.recv())
        ws.close()
        
        if "buy" in res:
            cid = res["buy"]["contract_id"]
            bot.send_message(state["chat_id"], f"📥 Trade Entered: {action.upper()}\nBarrier: {barrier} | Duration: 5 Ticks")
            # قطع الاتصال والانتظار 16 ثانية
            threading.Thread(target=check_result, args=(cid,)).start()
            return True
        return False
    except:
        return False

def on_message(ws, message):
    data = json.loads(message)
    if "tick" in data:
        curr_p = data["tick"]["quote"]
        # تحويل السعر لـ 3 أرقام بعد الفاصلة (إضافة أصفار إذا نقصت)
        curr_str = "{:.3f}".format(curr_p)
        curr_d3 = int(curr_str[-1])
        
        if state["last_price"] is not None:
            prev_p = state["last_price"]
            prev_str = "{:.3f}".format(prev_p)
            prev_d3 = int(prev_str[-1])
            
            # الشرط: تطابق الرقم الأخير
            if curr_d3 == prev_d3:
                action = "call" if curr_p > prev_p else "put"
                # قطع الاتصال فوراً قبل الدخول لضمان عدم استقبال تكات أخرى
                ws.close()
                if not place_trade(action, state["stake"], prev_p, curr_p):
                    reset_and_stop("Failed to place trade.")
        
        state["last_price"] = curr_p

def start_tracking():
    if not state["is_running"]: return
    ws = websocket.WebSocketApp(
        "wss://blue.derivws.com/websockets/v3?app_id=16929",
        on_message=on_message,
        on_open=lambda ws: ws.send(json.dumps({"ticks": "R_10", "subscribe": 1}))
    )
    state["active_ws"] = ws
    ws.run_forever()

@bot.message_handler(commands=['start'])
def cmd_start(message):
    state["chat_id"] = message.chat.id
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('Demo 🛠️', 'Live 💰')
    bot.send_message(message.chat.id, "🎯 **Digit Logic R_10**\nStrategy: Match D3 -> 5 Ticks -> Single Loss Kill.", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def set_acc(message):
    state["currency"] = "USD" if "Demo" in message.text else "tUSDT"
    msg = bot.send_message(message.chat.id, "Enter API Token:")
    bot.register_next_step_handler(msg, set_token)

def set_token(message):
    state["api_token"] = message.text.strip()
    msg = bot.send_message(message.chat.id, "Enter Stake:")
    bot.register_next_step_handler(msg, set_stake)

def set_stake(message):
    state["stake"] = float(message.text)
    state["is_running"] = True
    bot.send_message(message.chat.id, "🚀 **Hunting Started...**")
    threading.Thread(target=start_tracking).start()

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000)).start()
    bot.infinity_polling()
