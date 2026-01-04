import websocket, json, time, threading
from flask import Flask
import telebot
from telebot import types

app = Flask(__name__)
# التوكن الجديد الذي أرسلته
bot = telebot.TeleBot("8046176828:AAGoqg6qJDGVYxuvLMN2sfxgNCw62voIaOo")

state = {
    "api_token": "", "initial_stake": 0.0, "current_stake": 0.0, "tp": 0.0, 
    "currency": "USD", "is_running": False, "chat_id": None, 
    "last_d3": None, "total_profit": 0.0, "win_count": 0, "loss_count": 0, "loss_streak": 0,
    "is_trading": False  # قفل الأمان لمنع تكرار الصفقات في نفس اللحظة
}

@app.route('/')
def home():
    return "<h1>Bot R_10 D3=8-8 is Active</h1>"

def reset_and_stop(message_text):
    state.update({
        "api_token": "", "is_running": False, "last_d3": None, "is_trading": False,
        "loss_streak": 0, "win_count": 0, "loss_count": 0, "total_profit": 0.0
    })
    if state["chat_id"]:
        bot.send_message(state["chat_id"], f"🛑 **Bot Stopped:** {message_text}", reply_markup=types.ReplyKeyboardRemove())

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def manual_stop(message):
    reset_and_stop("Manual stop requested.")

def check_result(contract_id):
    try:
        time.sleep(7) # وقت كافٍ لتسجيل النتيجة في السيرفر
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": state["api_token"]}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
        res = json.loads(ws.recv())
        ws.close()
        
        contract = res.get("proposal_open_contract", {})
        payout_profit = float(contract.get("profit", 0))
        state["total_profit"] += payout_profit 

        if payout_profit < 0:
            state["loss_count"] += 1
            state["loss_streak"] += 1
            msg = f"❌ **LOSS! {payout_profit:.2f}**"
        else:
            state["win_count"] += 1
            state["loss_streak"] = 0
            msg = f"✅ **WIN! +{payout_profit:.2f}**"
            
        stats = (f"{msg}\n━━━━━━━━━━━━━━\n"
                 f"🏆 Wins: {state['win_count']} | ❌ Losses: {state['loss_count']}\n"
                 f"💰 Net Profit: {state['total_profit']:.2f}\n━━━━━━━━━━━━━━")
        bot.send_message(state["chat_id"], stats)

        if state["loss_streak"] >= 2:
            reset_and_stop("Max loss streak reached (Safety Stop).")
        elif state["total_profit"] >= state["tp"]:
            reset_and_stop("Target Profit Reached! 🎯")
        else:
            # تحديث مبلغ الرهان القادم
            state["current_stake"] = round(state["initial_stake"] * 14, 2) if state["loss_streak"] == 1 else state["initial_stake"]
            # فتح القفل والعودة للمراقبة
            state["is_trading"] = False
            threading.Thread(target=start_tracking).start()

    except:
        time.sleep(5)
        threading.Thread(target=check_result, args=(contract_id,)).start()

def place_trade():
    if state["is_trading"] or not state["is_running"]: return 
    state["is_trading"] = True # تفعيل القفل فوراً
    
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": state["api_token"]}))
        ws.recv()
        
        trade_req = {
            "buy": 1, "price": float(state["current_stake"]),
            "parameters": {
                "amount": float(state["current_stake"]), "basis": "stake",
                "contract_type": "DIGITDIFF", "barrier": "7", 
                "currency": state["currency"], "duration": 1, "duration_unit": "t", "symbol": "R_10"
            }
        }
        ws.send(json.dumps(trade_req))
        res = json.loads(ws.recv())
        ws.close()
        
        if "buy" in res:
            bot.send_message(state["chat_id"], f"📥 Signal Found (8-8). Entering Trade...")
            threading.Thread(target=check_result, args=(res["buy"]["contract_id"],)).start()
        else:
            state["is_trading"] = False # فتح القفل في حال فشل الشراء
    except:
        state["is_trading"] = False

def on_message(ws, message):
    if state["is_trading"]: return # منع التحليل أثناء تنفيذ صفقة
    
    data = json.loads(message)
    if "tick" in data:
        curr_p = data["tick"]["quote"]
        curr_str = "{:.3f}".format(curr_p)
        curr_d3 = int(curr_str[-1]) 
        
        if state["last_d3"] == 8 and curr_d3 == 8:
            ws.close()
            place_trade()
            return
        state["last_d3"] = curr_d3

def start_tracking():
    if state["is_trading"] or not state["is_running"]: return
    
    try:
        state["last_d3"] = None
        ws = websocket.WebSocketApp(
            "wss://blue.derivws.com/websockets/v3?app_id=16929",
            on_message=on_message,
            on_open=lambda ws: ws.send(json.dumps({"ticks": "R_10", "subscribe": 1}))
        )
        ws.run_forever()
    except:
        if state["is_running"] and not state["is_trading"]:
            time.sleep(5)
            start_tracking()

# --- Telegram Bot Interface ---
@bot.message_handler(commands=['start'])
def cmd_start(message):
    state["chat_id"] = message.chat.id
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰')
    bot.send_message(message.chat.id, "🎯 **R_10 | D3: 8-8 | Differ 7**\nMulti-trade protection active.", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def set_acc(message):
    state["currency"] = "USD" if "Demo" in message.text else "tUSDT"
    bot.send_message(message.chat.id, "Enter API Token:")
    bot.register_next_step_handler(message, set_token)

def set_token(message):
    state["api_token"] = message.text.strip()
    bot.send_message(message.chat.id, "Enter Initial Stake:")
    bot.register_next_step_handler(message, set_stake)

def set_stake(message):
    try:
        state["initial_stake"] = state["current_stake"] = float(message.text)
        bot.send_message(message.chat.id, "Enter TP ($):")
        bot.register_next_step_handler(message, set_tp)
    except: bot.send_message(message.chat.id, "Invalid number.")

def set_tp(message):
    try:
        state["tp"] = float(message.text)
        state["is_running"] = True
        state["is_trading"] = False
        bot.send_message(message.chat.id, "🚀 Running. Single trade mode enabled.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=start_tracking).start()
    except: bot.send_message(message.chat.id, "Invalid number.")

if __name__ == '__main__':
    # تشغيل Flask في خيط منفصل و bot polling في الخيط الرئيسي
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000)).start()
    bot.infinity_polling()
