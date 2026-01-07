import websocket, json, time, threading, multiprocessing, os
from flask import Flask
import telebot
from telebot import types
from datetime import datetime

app = Flask(__name__)
# التوكن الجديد الخاص بك
bot = telebot.TeleBot("8240916502:AAFkQob4KSdnh5KbH5v3xi0FOSplE46Lp3I")

DB_FILE = "bot_state.json"

manager = multiprocessing.Manager()
state = manager.dict({
    "api_token": "", "initial_stake": 0.0, "current_stake": 0.0, "tp": 0.0, 
    "currency": "USD", "is_running": False, "chat_id": None,
    "total_profit": 0.0, "win_count": 0, "loss_count": 0, "is_trading": False,
    "consecutive_losses": 0 
})

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

def clear_db():
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)

@app.route('/')
def home():
    return "<h1>R_100 Strategy (Sec 0 | Martingale x14) Active</h1>"

def reset_and_stop(message_text):
    state["is_running"] = False
    state["is_trading"] = False
    clear_db()
    if state["chat_id"]:
        bot.send_message(state["chat_id"], f"🛑 **Bot Stopped:** {message_text}", reply_markup=types.ReplyKeyboardRemove())

def check_result(contract_id, token):
    try:
        time.sleep(16)
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": token}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
        res = json.loads(ws.recv())
        ws.close()
        
        contract = res.get("proposal_open_contract", {})
        profit = float(contract.get("profit", 0))
        state["total_profit"] += profit 

        if profit > 0:
            state["win_count"] += 1
            state["consecutive_losses"] = 0
            state["current_stake"] = state["initial_stake"]
            status = "✅ WIN!"
        else:
            state["loss_count"] += 1
            state["consecutive_losses"] += 1
            status = "❌ LOSS!"

        stats = (f"{status} **{profit:.2f}**\n━━━━━━━━━━━━━━\n"
                 f"🏆 Wins: {state['win_count']} | ❌ Losses: {state['loss_count']}\n"
                 f"💰 Net Profit: {state['total_profit']:.2f}\n━━━━━━━━━━━━━━")
        bot.send_message(state["chat_id"], stats)
        save_state()

        if state["consecutive_losses"] >= 2:
            reset_and_stop("Terminated after 2 losses (End of Martingale x14).")
        elif state["total_profit"] >= state["tp"]:
            reset_and_stop("Target Profit Reached! 🎯")
        else:
            if state["consecutive_losses"] == 1:
                state["current_stake"] = state["initial_stake"] * 14
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
        
        ws.send(json.dumps({"ticks_history": "R_100", "count": 30, "end": "latest", "style": "ticks"}))
        history = json.loads(ws.recv())
        prices = history.get("history", {}).get("prices", [])
        
        if len(prices) >= 30:
            diff = float(prices[-1]) - float(prices[0])
            contract_type, barrier = None, None
            
            if diff >= 0.5:
                contract_type, barrier = "CALL", "-0.5"
            elif diff <= -0.5:
                contract_type, barrier = "PUT", "+0.5"

            if contract_type:
                prop_req = {
                    "proposal": 1, "amount": state["current_stake"], "basis": "stake",
                    "contract_type": contract_type, "currency": state["currency"],
                    "duration": 5, "duration_unit": "t", "symbol": "R_100", "barrier": barrier
                }
                ws.send(json.dumps(prop_req))
                prop = json.loads(ws.recv()).get("proposal")
                
                if prop:
                    ws.send(json.dumps({"buy": prop["id"], "price": state["current_stake"]}))
                    buy_res = json.loads(ws.recv())
                    if "buy" in buy_res:
                        mode = "🔥 Martingale (x14)" if state["consecutive_losses"] == 1 else "📥 Primary"
                        bot.send_message(state["chat_id"], f"{mode} (Diff: {diff:.3f}). Entering {contract_type}...")
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
                if datetime.now().second == 0:
                    execute_strategy()
                    time.sleep(2)
            time.sleep(0.5)
        except: time.sleep(1)

@bot.message_handler(commands=['start'])
def cmd_start(message):
    state["chat_id"] = message.chat.id
    if load_state() and state["is_running"]:
        bot.send_message(message.chat.id, "♻️ **Bot Recovered!**\nWatching Sec 00 (Martingale x14).")
    else:
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰')
        bot.send_message(message.chat.id, "🤖 **R_100 (Martingale x14) Bot**", reply_markup=markup)

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
    bot.send_message(message.chat.id, "Enter TP ($):")
    bot.register_next_step_handler(message, set_tp)

def set_tp(message):
    state["tp"] = float(message.text)
    state["is_running"] = True
    state["consecutive_losses"] = 0
    save_state()
    bot.send_message(message.chat.id, "🚀 Running. Martingale x14 Active.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))

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
