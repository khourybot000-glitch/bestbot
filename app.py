import websocket, json, time, multiprocessing, os
from flask import Flask
import telebot
from telebot import types
from datetime import datetime

# إعداد Flask
app = Flask(__name__)

# التوكن الجديد الخاص بك
TOKEN = "8264292822:AAHWATeqibvCTXV3UOJoa9VBh7S6jymU2C4"
bot = telebot.TeleBot(TOKEN)

# مدير الذاكرة المشتركة
manager = multiprocessing.Manager()

def get_initial_state():
    return {
        "api_token": "", "initial_stake": 0.0, "current_stake": 0.0, "tp": 0.0, 
        "currency": "USD", "is_running": False, "chat_id": None,
        "total_profit": 0.0, "win_count": 0, "loss_count": 0, "is_trading": False,
        "last_trade_minute": -1, "active_contract": None, "start_time": 0
    }

state = manager.dict(get_initial_state())

@app.route('/')
def home():
    return "BOT IS ALIVE AND LISTENING"

# --- دالات التحكم ---
def reset_and_stop(state_proxy, text):
    if state_proxy["chat_id"]:
        try:
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰')
            bot.send_message(state_proxy["chat_id"], f"🛑 {text}", reply_markup=markup)
        except: pass
    initial = get_initial_state()
    for k, v in initial.items(): state_proxy[k] = v

def check_result_logic(state_proxy):
    if not state_proxy["active_contract"] or time.time() - state_proxy["start_time"] < 18:
        return
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": state_proxy["api_token"]}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": state_proxy["active_contract"]}))
        res = json.loads(ws.recv())
        ws.close()
        
        contract = res.get("proposal_open_contract", {})
        if contract.get("is_expired"):
            profit = float(contract.get("profit", 0))
            if profit > 0:
                state_proxy["total_profit"] += profit
                state_proxy["win_count"] += 1
                msg = f"✅ **WIN: {profit:.2f}**\nNet: {state_proxy['total_profit']:.2f}"
                bot.send_message(state_proxy["chat_id"], msg, parse_mode="Markdown")
                state_proxy["active_contract"], state_proxy["is_trading"] = None, False
                if state_proxy["total_profit"] >= state_proxy["tp"]:
                    reset_and_stop(state_proxy, "Target Reached! 🎉")
            else:
                state_proxy["total_profit"] += profit
                reset_and_stop(state_proxy, f"❌ **LOSS!** ({profit:.2f})\nStopped after 1 loss.")
    except: pass

def execute_trade(state_proxy):
    now = datetime.now()
    if state_proxy["is_trading"] or now.second != 0 or state_proxy["last_trade_minute"] == now.minute:
        return
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=8)
        ws.send(json.dumps({"authorize": state_proxy["api_token"]}))
        ws.recv()
        ws.send(json.dumps({"ticks_history": "R_100", "count": 5, "end": "latest", "style": "ticks"}))
        prices = json.loads(ws.recv()).get("history", {}).get("prices", [])
        ws.close()

        if len(prices) >= 5:
            diff = float(prices[-1]) - float(prices[0])
            if abs(diff) >= 0.8:
                state_proxy["is_trading"], state_proxy["last_trade_minute"] = True, now.minute
                c_t, br = ("CALL", "-1.0") if diff >= 0.8 else ("PUT", "+1.0")
                
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
                ws.send(json.dumps({"authorize": state_proxy["api_token"]}))
                ws.recv()
                req = {"proposal": 1, "amount": state_proxy["current_stake"], "basis": "stake", 
                       "contract_type": c_t, "currency": state_proxy["currency"], 
                       "duration": 5, "duration_unit": "t", "symbol": "R_100", "barrier": br}
                ws.send(json.dumps(req))
                prop = json.loads(ws.recv()).get("proposal")
                if prop:
                    ws.send(json.dumps({"buy": prop["id"], "price": state_proxy["current_stake"]}))
                    buy_res = json.loads(ws.recv())
                    if "buy" in buy_res:
                        state_proxy["active_contract"], state_proxy["start_time"] = buy_res["buy"]["contract_id"], time.time()
                        bot.send_message(state_proxy["chat_id"], f"🚀 **{c_t} Sent** (00s)")
                    else: state_proxy["is_trading"] = False
                else: state_proxy["is_trading"] = False
    except: state_proxy["is_trading"] = False

# --- حلقة التداول ---
def main_loop(state_proxy):
    while True:
        try:
            if state_proxy["is_running"]:
                execute_trade(state_proxy)
                check_result_logic(state_proxy)
            time.sleep(1)
        except: time.sleep(2)

# --- معالجات التلجرام ---
@bot.message_handler(commands=['start'])
def welcome(m):
    state["chat_id"] = m.chat.id
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰')
    bot.send_message(m.chat.id, "👋 اهلا بك! اختر نوع الحساب للبدء:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def ask_token(m):
    state["currency"] = "USD" if "Demo" in m.text else "tUSDT"
    bot.send_message(m.chat.id, "ارسل API Token الخاص بالمنصة:")
    bot.register_next_step_handler(m, save_token)

def save_token(m):
    state["api_token"] = m.text.strip()
    bot.send_message(m.chat.id, "ما هو مبلغ الصفقة (Stake)؟")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    try:
        state["initial_stake"] = float(m.text)
        state["current_stake"] = state["initial_stake"]
        bot.send_message(m.chat.id, "ما هو الهدف الربحي (Target Profit)؟")
        bot.register_next_step_handler(m, save_tp)
    except: bot.send_message(m.chat.id, "يرجى ارسال رقم صحيح.")

def save_tp(m):
    try:
        state["tp"] = float(m.text)
        state["is_running"] = True
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑')
        bot.send_message(m.chat.id, f"✅ تم التشغيل!\nالتحليل عند الثانية 00\nالهدف: {state['tp']}", reply_markup=markup)
    except: pass

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_all(m):
    reset_and_stop(state, "تم إيقاف البوت يدوياً.")

if __name__ == '__main__':
    # تشغيل محرك التداول في الخلفية
    p = multiprocessing.Process(target=main_loop, args=(state,), daemon=True)
    p.start()
    
    # تشغيل Flask في الخلفية (اختياري لـ Render)
    f = multiprocessing.Process(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True)
    f.start()
    
    # تشغيل البوت في العملية الأساسية
    print("Bot is polling...")
    bot.infinity_polling()
