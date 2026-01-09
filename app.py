import websocket, json, time, multiprocessing, os
from flask import Flask
import telebot
from telebot import types
from datetime import datetime

# إعدادات البوت والسررفر
app = Flask(__name__)
bot = telebot.TeleBot("8539423725:AAG_PbXMzqjWDwFBtQbZqPGLyGQHzsLQHfI")

# استخدام Manager لمشاركة البيانات بين العمليات المختلفة (Processes)
manager = multiprocessing.Manager()

def get_initial_state():
    return {
        "api_token": "", "initial_stake": 0.0, "current_stake": 0.0, "tp": 0.0, 
        "currency": "USD", "is_running": False, "chat_id": None,
        "total_profit": 0.0, "win_count": 0, "loss_count": 0, "is_trading": False,
        "consecutive_losses": 0, "last_trade_minute": -1
    }

state = manager.dict(get_initial_state())

@app.route('/')
def home():
    return "BOT ALIVE - FULL MULTIPROCESSING MODE"

def reset_and_stop(state_proxy, message_text):
    if state_proxy["chat_id"]:
        try: bot.send_message(state_proxy["chat_id"], f"🛑 {message_text}")
        except: pass
    initial = get_initial_state()
    for key in initial: state_proxy[key] = initial[key]

# عملية مستقلة لفحص النتيجة (Process)
def check_result_process(contract_id, token, state_proxy):
    try:
        time.sleep(40) # الانتظار المطلوب
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
        ws.send(json.dumps({"authorize": token}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
        res = json.loads(ws.recv())
        ws.close()
        
        contract = res.get("proposal_open_contract", {})
        profit = float(contract.get("profit", 0))

        if profit > 0:
            state_proxy["total_profit"] += profit 
            state_proxy["win_count"] += 1
            state_proxy["consecutive_losses"] = 0
            state_proxy["current_stake"] = state_proxy["initial_stake"]
            bot.send_message(state_proxy["chat_id"], f"✅ WIN! Profit: {profit:.2f}\nTotal: {state_proxy['total_profit']:.2f}")
            
            if state_proxy["total_profit"] >= state_proxy["tp"]:
                reset_and_stop(state_proxy, "Target Reached!")
            else:
                state_proxy["is_trading"] = False
        else:
            state_proxy["total_profit"] += profit
            state_proxy["loss_count"] += 1
            state_proxy["consecutive_losses"] += 1
            bot.send_message(state_proxy["chat_id"], f"❌ LOSS! Net: {state_proxy['total_profit']:.2f}")
            
            if state_proxy["consecutive_losses"] >= 2:
                reset_and_stop(state_proxy, "2 Consecutive Losses. Stopped.")
            else:
                state_proxy["current_stake"] = state_proxy["initial_stake"] * 19
                state_proxy["is_trading"] = False
    except Exception as e:
        bot.send_message(state_proxy["chat_id"], "⚠️ Error in result process. Resetting lock...")
        state_proxy["is_trading"] = False

# تنفيذ الاستراتيجية
def execute_strategy(state_proxy):
    current_min = datetime.now().minute
    if state_proxy["is_trading"] or not state_proxy["is_running"] or state_proxy["last_trade_minute"] == current_min:
        return

    state_proxy["is_trading"] = True
    state_proxy["last_trade_minute"] = current_min
    
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
        ws.send(json.dumps({"authorize": state_proxy["api_token"]}))
        ws.recv()
        ws.send(json.dumps({"ticks_history": "R_100", "count": 15, "end": "latest", "style": "ticks"}))
        prices = json.loads(ws.recv()).get("history", {}).get("prices", [])
        ws.close()

        if len(prices) >= 15:
            diff = float(prices[-1]) - float(prices[0])
            if abs(diff) >= 0.8:
                c_type, barr = ("CALL", "-1.0") if diff >= 0.8 else ("PUT", "+1.0")
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
                ws.send(json.dumps({"authorize": state_proxy["api_token"]}))
                ws.recv()
                
                prop_req = {
                    "proposal": 1, "amount": state_proxy["current_stake"], "basis": "stake",
                    "contract_type": c_type, "currency": state_proxy["currency"],
                    "duration": 30, "duration_unit": "s", "symbol": "R_100", "barrier": barr
                }
                
                ws.send(json.dumps(prop_req))
                prop = json.loads(ws.recv()).get("proposal")
                if prop:
                    ws.send(json.dumps({"buy": prop["id"], "price": state_proxy["current_stake"]}))
                    buy_res = json.loads(ws.recv())
                    if "buy" in buy_res:
                        bot.send_message(state_proxy["chat_id"], f"🚀 Trade Started (30s): {c_type}\nDiff: {diff:.3f}")
                        # تشغيل عملية مستقلة لفحص النتيجة بدلاً من threading
                        p = multiprocessing.Process(target=check_result_process, args=(buy_res["buy"]["contract_id"], state_proxy["api_token"], state_proxy))
                        p.start()
                        return
        state_proxy["is_trading"] = False
    except:
        state_proxy["is_trading"] = False

# محرك الجدولة (Process مستقل)
def scheduler_process(state_proxy):
    while True:
        if state_proxy["is_running"] and not state_proxy["is_trading"]:
            now = datetime.now()
            if now.second == 30 and state_proxy["last_trade_minute"] != now.minute:
                execute_strategy(state_proxy)
        time.sleep(0.5)

# --- معالجات التلجرام ---
@bot.message_handler(commands=['start'])
def cmd_start(message):
    state["chat_id"] = message.chat.id
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰')
    bot.send_message(message.chat.id, "🤖 **Multiprocess Engine Active**\nStable & Independent Logic.", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def set_acc(message):
    state["currency"] = "USD" if "Demo" in message.text else "tUSDT"
    bot.send_message(message.chat.id, "Enter API Token:")
    bot.register_next_step_handler(message, lambda m: set_token(m))

def set_token(message):
    state["api_token"] = message.text.strip()
    bot.send_message(message.chat.id, "Enter Stake:")
    bot.register_next_step_handler(message, lambda m: set_stake(m))

def set_stake(message):
    try:
        state["initial_stake"] = float(message.text)
        state["current_stake"] = state["initial_stake"]
        bot.send_message(message.chat.id, "Enter Target Profit:")
        bot.register_next_step_handler(message, lambda m: set_tp(m))
    except: bot.send_message(message.chat.id, "Invalid Stake.")

def set_tp(message):
    try:
        state["tp"] = float(message.text)
        state["is_running"] = True
        bot.send_message(message.chat.id, "🚀 Running...")
    except: bot.send_message(message.chat.id, "Invalid TP.")

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def manual_stop(message):
    reset_and_stop(state, "Stopped Manually.")

if __name__ == '__main__':
    # تشغيل محرك الجدولة في عملية مستقلة
    sched_p = multiprocessing.Process(target=scheduler_process, args=(state,))
    sched_p.daemon = True
    sched_p.start()
    
    # تشغيل سيرفر ويب في عملية مستقلة لضمان استقرار Flask
    flask_p = multiprocessing.Process(target=lambda: app.run(host='0.0.0.0', port=10000))
    flask_p.daemon = True
    flask_p.start()
    
    # تشغيل البوت في العملية الرئيسية
    bot.infinity_polling(timeout=60, long_polling_timeout=30)
