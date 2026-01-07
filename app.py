import websocket, json, time, threading, multiprocessing, os
from flask import Flask
import telebot
from telebot import types
from datetime import datetime

app = Flask(__name__)
# التوكن الجديد
bot = telebot.TeleBot("8517505223:AAH3SzafXDBydMpPE-ykDtkkKUILNcX17Ao")

DB_FILE = "bot_state.json"

manager = multiprocessing.Manager()

def get_initial_state():
    return {
        "api_token": "", "initial_stake": 0.0, "current_stake": 0.0, "tp": 0.0, 
        "currency": "USD", "is_running": False, "chat_id": None,
        "total_profit": 0.0, "win_count": 0, "loss_count": 0, "is_trading": False,
        "consecutive_losses": 0
    }

state = manager.dict(get_initial_state())

def save_state():
    with open(DB_FILE, "w") as f:
        json.dump(dict(state), f)

def load_state():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                saved_data = json.load(f)
                state.update(saved_data)
            return True
        except: return False
    return False

def reset_and_stop(message_text):
    if state["chat_id"]:
        bot.send_message(state["chat_id"], f"🛑 **Bot Stopped & Reset:** {message_text}", reply_markup=types.ReplyKeyboardRemove())
    
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    
    initial = get_initial_state()
    for key in initial:
        state[key] = initial[key]

def check_result(contract_id, token):
    try:
        # مدة الانتظار المطلوبة 40 ثانية
        time.sleep(40) 
        
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": token}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
        res = json.loads(ws.recv())
        ws.close()
        
        contract = res.get("proposal_open_contract", {})
        profit = float(contract.get("profit", 0))

        if profit > 0:
            state["total_profit"] += profit 
            state["win_count"] += 1
            state["consecutive_losses"] = 0
            state["current_stake"] = state["initial_stake"]
            status = "✅ WIN!"
            
            stats = (f"{status} **{profit:.2f}**\n━━━━━━━━━━━━━━\n"
                     f"💰 Net Profit: {state['total_profit']:.2f}\n"
                     f"🏆 Wins: {state['win_count']} | ❌ Loss: {state['loss_count']}\n━━━━━━━━━━━━━━")
            bot.send_message(state["chat_id"], stats)

            if state["total_profit"] >= state["tp"]:
                reset_and_stop(f"Target Profit Reached ({state['total_profit']:.2f})!")
            else:
                save_state()
                state["is_trading"] = False
        else:
            state["total_profit"] += profit
            state["loss_count"] += 1
            state["consecutive_losses"] += 1
            
            bot.send_message(state["chat_id"], f"❌ LOSS! ({profit:.2f})")

            if state["consecutive_losses"] >= 2:
                reset_and_stop("2 Consecutive Losses. Emergency Wipe Triggered.")
            else:
                # مضاعفة x19
                state["current_stake"] = state["initial_stake"] * 19
                save_state()
                state["is_trading"] = False
    except:
        state["is_trading"] = False

def execute_strategy():
    if state["is_trading"] or not state["is_running"]: return
    state["is_trading"] = True
    
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": state["api_token"]}))
        ws.recv()
        
        # تحليل آخر 15 تكة
        ws.send(json.dumps({"ticks_history": "R_100", "count": 15, "end": "latest", "style": "ticks"}))
        history = json.loads(ws.recv())
        prices = history.get("history", {}).get("prices", [])
        
        if len(prices) >= 15:
            diff = float(prices[-1]) - float(prices[0])
            contract_type, barrier = None, None
            
            # شرط القوة 0.8 والحاجز 1.0
            if diff >= 0.5:
                contract_type, barrier = "CALL", "-1.0"
            elif diff <= -0.5:
                contract_type, barrier = "PUT", "+1.0"

            if contract_type:
                prop_req = {
                    "proposal": 1, "amount": state["current_stake"], "basis": "stake",
                    "contract_type": contract_type, "currency": state["currency"],
                    "duration": 6, "duration_unit": "t", "symbol": "R_100", "barrier": barrier
                }
                ws.send(json.dumps(prop_req))
                prop = json.loads(ws.recv()).get("proposal")
                
                if prop:
                    ws.send(json.dumps({"buy": prop["id"], "price": state["current_stake"]}))
                    buy_res = json.loads(ws.recv())
                    if "buy" in buy_res:
                        mode = "🔥 Martingale" if state["consecutive_losses"] == 1 else "📥 Primary"
                        bot.send_message(state["chat_id"], f"{mode} Trade (Diff: {diff:.3f}) @ Sec 30")
                        threading.Thread(target=check_result, args=(buy_res["buy"]["contract_id"], state["api_token"])).start()
                        ws.close()
                        return
        ws.close()
        state["is_trading"] = False
    except:
        state["is_trading"] = False

def scheduler_process(state_proxy):
    while True:
        try:
            if state_proxy["is_running"] and not state_proxy["is_trading"]:
                # التوقيت عند الثانية 30
                if datetime.now().second == 30:
                    execute_strategy()
                    time.sleep(2)
            time.sleep(0.5)
        except: time.sleep(1)

@bot.message_handler(commands=['start'])
def cmd_start(message):
    state["chat_id"] = message.chat.id
    if load_state() and state["is_running"]:
        bot.send_message(message.chat.id, "♻️ **Session Recovered.** Sec 30 Mode active.")
    else:
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰')
        bot.send_message(message.chat.id, "🤖 **R_100 Strategy Bot**\n- Sec: 30\n- Analysis: 15 Ticks\n- Diff: 0.8\n- Martingale: x19", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def set_acc(message):
    state["currency"] = "USD" if "Demo" in message.text else "tUSDT"
    bot.send_message(message.chat.id, "Enter API Token:")
    bot.register_next_step_handler(message, set_token)

def set_token(message):
    state["api_token"] = message.text.strip()
    bot.send_message(message.chat.id, "Enter Stake:")
    bot.register_next_step_handler(message, set_stake)

def set_stake(message):
    state["initial_stake"] = float(message.text)
    state["current_stake"] = state["initial_stake"]
    bot.send_message(message.chat.id, "Enter Target Profit:")
    bot.register_next_step_handler(message, set_tp)

def set_tp(message):
    state["tp"] = float(message.text)
    state["is_running"] = True
    save_state()
    bot.send_message(message.chat.id, "🚀 Bot Active. Waiting for Sec 30.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def manual_stop(message):
    reset_and_stop("Manual stop.")

if __name__ == '__main__':
    load_state()
    p = multiprocessing.Process(target=scheduler_process, args=(state,))
    p.daemon = True
    p.start()
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000)).start()
    bot.infinity_polling()
