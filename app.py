import websocket, json, time, multiprocessing, os
from flask import Flask
import telebot
from telebot import types
from datetime import datetime

app = Flask(__name__)
# New Token Updated
TOKEN = "8264292822:AAGq50zM1ATRNSiJky8ikrfKhsb1xAI6Opo"
bot = telebot.TeleBot(TOKEN)
manager = multiprocessing.Manager()

def get_initial_state():
    return {
        "api_token": "", "initial_stake": 0.0, "current_stake": 0.0, "tp": 0.0, 
        "currency": "USD", "is_running": False, "chat_id": None,
        "total_profit": 0.0, "win_count": 0, "loss_count": 0, "is_trading": False,
        "consecutive_losses": 0, "last_trade_time": "",
        "active_contract": None, "start_time": 0, "last_type": ""
    }

state = manager.dict(get_initial_state())

@app.route('/')
def home():
    return "BOT STATUS: ACTIVE - TOKEN UPDATED - STATS FIXED"

def reset_and_stop(state_proxy, text):
    if state_proxy["chat_id"]:
        try:
            report = (f"📊 **SESSION SUMMARY**\n"
                      f"━━━━━━━━━━━━━━\n"
                      f"✅ Total Wins: {state_proxy['win_count']}\n"
                      f"❌ Total Losses: {state_proxy['loss_count']}\n"
                      f"💰 Final Profit: {state_proxy['total_profit']:.2f}\n"
                      f"━━━━━━━━━━━━━━\n"
                      f"🛑 {text}")
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰')
            bot.send_message(state_proxy["chat_id"], report, reply_markup=markup)
        except: pass
    
    state_proxy["is_running"] = False
    state_proxy["is_trading"] = False
    initial = get_initial_state()
    for k, v in initial.items(): state_proxy[k] = v

def open_trade_raw(state_proxy, contract_type):
    try:
        barrier = "-0.8" if contract_type == "CALL" else "+0.8"
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": state_proxy["api_token"]}))
        ws.recv()
        
        req = {
            "proposal": 1, "amount": state_proxy["current_stake"], "basis": "stake", 
            "contract_type": contract_type, "currency": state_proxy["currency"], 
            "duration": 5, "duration_unit": "t", "symbol": "R_100", "barrier": barrier
        }
        
        ws.send(json.dumps(req))
        prop = json.loads(ws.recv()).get("proposal")
        if prop:
            ws.send(json.dumps({"buy": prop["id"], "price": state_proxy["current_stake"]}))
            buy_res = json.loads(ws.recv())
            if "buy" in buy_res:
                state_proxy["active_contract"] = buy_res["buy"]["contract_id"]
                state_proxy["start_time"] = time.time()
                state_proxy["last_type"] = contract_type 
                state_proxy["is_trading"] = True
                
                direction = "CALL 📈" if contract_type == "CALL" else "PUT 📉"
                bot.send_message(state_proxy["chat_id"], f"🚀 **Trade Opened: {direction}**\n💰 Stake: {state_proxy['current_stake']}")
                ws.close()
                return True
        ws.close()
    except: pass
    return False

def check_result_logic(state_proxy):
    if not state_proxy["active_contract"] or time.time() - state_proxy["start_time"] < 15:
        return
    
    current_contract_id = state_proxy["active_contract"]
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": state_proxy["api_token"]}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": current_contract_id}))
        res = json.loads(ws.recv())
        ws.close()
        
        contract = res.get("proposal_open_contract", {})
        if contract.get("is_expired") == 1:
            state_proxy["active_contract"] = None 
            profit = float(contract.get("profit", 0))
            
            if profit == 0:
                bot.send_message(state_proxy["chat_id"], "⚪ **DRAW (Doji)**")
                state_proxy["is_trading"] = False
                return 

            is_win = profit > 0
            if is_win:
                state_proxy["total_profit"] += profit
                state_proxy["win_count"] += 1
                state_proxy["consecutive_losses"] = 0
                state_proxy["current_stake"] = state_proxy["initial_stake"]
                icon, res_text = "✅", "WIN"
            else:
                state_proxy["total_profit"] += profit
                state_proxy["loss_count"] += 1
                state_proxy["consecutive_losses"] += 1
                icon, res_text = "❌", "LOSS"

            # Improved Stats UI
            stats_msg = (f"{icon} **Result: {res_text} ({profit:.2f})**\n"
                         f"━━━━━━━━━━━━━━\n"
                         f"✅ Wins: {state_proxy['win_count']}\n"
                         f"❌ Losses: {state_proxy['loss_count']}\n"
                         f"💰 Net: {state_proxy['total_profit']:.2f}\n"
                         f"━━━━━━━━━━━━━━")
            bot.send_message(state_proxy["chat_id"], stats_msg)

            if not is_win:
                if state_proxy["consecutive_losses"] >= 2:
                    reset_and_stop(state_proxy, "Stopped: 2 Consecutive Losses.")
                else:
                    state_proxy["current_stake"] = state_proxy["initial_stake"] * 19
                    new_type = "PUT" if state_proxy["last_type"] == "PUT" else "CALL"
                    bot.send_message(state_proxy["chat_id"], "⚠️ **Martingale x29 (Reverse Direction)**")
                    open_trade_raw(state_proxy, new_type)
            else:
                state_proxy["is_trading"] = False

            if state_proxy["total_profit"] >= state_proxy["tp"]:
                reset_and_stop(state_proxy, "Target Profit Reached!")
    except: pass

def execute_trade(state_proxy):
    now = datetime.now()
    if not state_proxy["is_running"] or state_proxy["is_trading"] or now.second != 0:
        return
    
    time_key = f"{now.minute}:{now.second}"
    if state_proxy["last_trade_time"] == time_key: return

    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=8)
        ws.send(json.dumps({"ticks_history": "R_100", "count": 30, "end": "latest", "style": "ticks"}))
        prices = json.loads(ws.recv()).get("history", {}).get("prices", [])
        ws.close()

        if len(prices) >= 30:
            diff = float(prices[-1]) - float(prices[0])
            if diff >= 0.5:
                state_proxy["last_trade_time"] = time_key
                open_trade_raw(state_proxy, "CALL")
            elif diff <= -0.5:
                state_proxy["last_trade_time"] = time_key
                open_trade_raw(state_proxy, "PUT")
    except: pass

def main_loop(state_proxy):
    while True:
        try:
            if state_proxy["is_running"]:
                execute_trade(state_proxy)
                check_result_logic(state_proxy)
            time.sleep(0.1)
        except: time.sleep(1)

@bot.message_handler(commands=['start'])
def welcome(m):
    state["chat_id"] = m.chat.id
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰')
    bot.send_message(m.chat.id, "🤖 **Professional Engine Active**\nStats fixed: Win/Loss counters now appear together.", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def ask_token(m):
    state["currency"] = "USD" if "Demo" in m.text else "tUSDT"
    bot.send_message(m.chat.id, "Enter API Token:")
    bot.register_next_step_handler(m, save_token)

def save_token(m):
    state["api_token"] = m.text.strip()
    bot.send_message(m.chat.id, "Stake Amount:")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    try: state["initial_stake"] = float(m.text); state["current_stake"] = state["initial_stake"]
    except: return
    bot.send_message(m.chat.id, "Target Profit:")
    bot.register_next_step_handler(m, save_tp)

def save_tp(m):
    try: state["tp"] = float(m.text); state["is_running"] = True
    except: return
    bot.send_message(m.chat.id, "🚀 Monitoring Market...", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_all(m): reset_and_stop(state, "Manual Termination.")

if __name__ == '__main__':
    multiprocessing.Process(target=main_loop, args=(state,), daemon=True).start()
    multiprocessing.Process(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
