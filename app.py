import websocket, json, time, multiprocessing, os
from flask import Flask
import telebot
from telebot import types
from datetime import datetime

app = Flask(__name__)
# التوكن الجديد
TOKEN = "8264292822:AAEwrtakT1F82KHqp9rN8e4LNe-s8oyRZGk"
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
    return "ENGINE ACTIVE - TOKEN UPDATED - MULTI-SEC MODE"

def reset_and_stop(state_proxy, text):
    if state_proxy["chat_id"]:
        try:
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰')
            bot.send_message(state_proxy["chat_id"], f"🛑 {text}", reply_markup=markup)
        except: pass
    initial = get_initial_state()
    for k, v in initial.items(): state_proxy[k] = v

def open_trade_raw(state_proxy, contract_type):
    try:
        barrier = "-0.7" if contract_type == "CALL" else "+0.7"
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": state_proxy["api_token"]}))
        ws.recv()
        req = {"proposal": 1, "amount": state_proxy["current_stake"], "basis": "stake", 
               "contract_type": contract_type, "currency": state_proxy["currency"], 
               "duration": 5, "duration_unit": "t", "symbol": "R_100", "barrier": barrier}
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
                bot.send_message(state_proxy["chat_id"], f"🚀 **Trade Sent ({contract_type})**\nStake: {state_proxy['current_stake']:.2f}")
                ws.close()
                return True
        ws.close()
    except: pass
    return False

def check_result_logic(state_proxy):
    if not state_proxy["active_contract"] or time.time() - state_proxy["start_time"] < 16:
        return
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": state_proxy["api_token"]}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": state_proxy["active_contract"]}))
        res = json.loads(ws.recv())
        ws.close()
        
        contract = res.get("proposal_open_contract", {})
        if contract.get("is_expired") == 1 or contract.get("status") != "open":
            profit = float(contract.get("profit", 0))
            
            # معالجة التعادل Doji
            if profit == 0:
                bot.send_message(state_proxy["chat_id"], "⚪ **DRAW (DOJI)**\nStake Reset - No Martingale.")
                state_proxy["current_stake"] = state_proxy["initial_stake"]
                state_proxy["active_contract"], state_proxy["is_trading"] = None, False
                return

            is_win = profit > 0
            if is_win:
                state_proxy["total_profit"] += profit
                state_proxy["win_count"] += 1
                state_proxy["consecutive_losses"] = 0
                state_proxy["current_stake"] = state_proxy["initial_stake"]
                res_icon = "✅"
            else:
                state_proxy["total_profit"] += profit
                state_proxy["loss_count"] += 1
                state_proxy["consecutive_losses"] += 1
                res_icon = "❌"

            # عرض الإحصائيات
            stats_msg = (
                f"{res_icon} **RESULT: {'WIN' if is_win else 'LOSS'} ({profit:.2f})**\n"
                f"━━━━━━━━━━━━━━\n"
                f"🏆 Wins: {state_proxy['win_count']} | 💀 Losses: {state_proxy['loss_count']}\n"
                f"💰 Total Net: {state_proxy['total_profit']:.2f} {state_proxy['currency']}\n"
                f"━━━━━━━━━━━━━━"
            )
            bot.send_message(state_proxy["chat_id"], stats_msg, parse_mode="Markdown")

            if not is_win:
                if state_proxy["consecutive_losses"] >= 2:
                    reset_and_stop(state_proxy, "Stopped after 2 consecutive losses.")
                    return
                state_proxy["current_stake"] = state_proxy["initial_stake"] * 29
                reverse_type = "PUT" if state_proxy["last_type"] == "CALL" else "CALL"
                if not open_trade_raw(state_proxy, reverse_type):
                    state_proxy["is_trading"] = False
            else:
                state_proxy["active_contract"], state_proxy["is_trading"] = None, False

            if state_proxy["total_profit"] >= state_proxy["tp"]:
                reset_and_stop(state_proxy, "Target Reached! 🎉")
    except: pass

def execute_trade(state_proxy):
    now = datetime.now()
    valid_seconds = [0,30]
    time_key = f"{now.minute}:{now.second}"
    
    if state_proxy["is_trading"] or now.second not in valid_seconds or state_proxy["last_trade_time"] == time_key:
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
            # الشرط الجديد: الفرق أكبر من 1
            if abs(diff) > 0.6:
                state_proxy["is_trading"] = True
                state_proxy["last_trade_time"] = time_key
                c_t = "CALL" if diff > 0.6 else "PUT"
                open_trade_raw(state_proxy, c_t)
    except: state_proxy["is_trading"] = False

def main_loop(state_proxy):
    while True:
        try:
            if state_proxy["is_running"]:
                execute_trade(state_proxy)
                check_result_logic(state_proxy)
            time.sleep(0.5)
        except: time.sleep(1)

# Handlers
@bot.message_handler(commands=['start'])
def welcome(m):
    state["chat_id"] = m.chat.id
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰')
    bot.send_message(m.chat.id, "👋 New Token Active! Analyzing at (0,10..50) with Diff > 1", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def ask_token(m):
    state["currency"] = "USD" if "Demo" in m.text else "tUSDT"
    bot.send_message(m.chat.id, "Please send your Deriv API Token:")
    bot.register_next_step_handler(m, save_token)

def save_token(m):
    state["api_token"] = m.text.strip()
    bot.send_message(m.chat.id, "Enter Initial Stake:")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    try:
        state["initial_stake"] = float(m.text)
        state["current_stake"] = state["initial_stake"]
        bot.send_message(m.chat.id, "Enter Target Profit:")
        bot.register_next_step_handler(m, save_tp)
    except: pass

def save_tp(m):
    try:
        state["tp"] = float(m.text)
        state["is_running"] = True
        bot.send_message(m.chat.id, "🚀 Bot Started!", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
    except: pass

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_all(m): reset_and_stop(state, "Manual Stop.")

if __name__ == '__main__':
    multiprocessing.Process(target=main_loop, args=(state,), daemon=True).start()
    multiprocessing.Process(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
