import websocket, json, time, multiprocessing, os
from flask import Flask
import telebot
from telebot import types
from datetime import datetime

app = Flask(__name__)

# --- التوكن الجديد المحدث ---
TOKEN = "8264292822:AAH7ZkcZiueWIsvEYn8EN8T43kvuGKxNzt0"
bot = telebot.TeleBot(TOKEN)
manager = multiprocessing.Manager()

def get_initial_state():
    """دالة لتعريف الحالة الصفرية (المسح الكامل)"""
    return {
        "api_token": "", "initial_stake": 0.0, "current_stake": 0.0, "tp": 0.0, 
        "currency": "USD", "is_running": False, "chat_id": None,
        "total_profit": 0.0, "win_count": 0, "loss_count": 0, "is_trading": False,
        "consecutive_losses": 0, "active_contract": None, "start_time": 0
    }

state = manager.dict(get_initial_state())

@app.route('/')
def home():
    status = "RUNNING 🚀" if state["is_running"] else "IDLE/STOPPED 🛑"
    return f"<h2>STATUS: {status}</h2><p>Wins: {state['win_count']} | Losses: {state['loss_count']}</p>"

def reset_and_stop(state_proxy, text):
    """دالة المسح الكامل للبيانات وإعادة التصفير"""
    if state_proxy["chat_id"]:
        try:
            report = (f"🛑 **SESSION TERMINATED**\n"
                      f"━━━━━━━━━━━━━━\n"
                      f"✅ Total Wins: {state_proxy['win_count']}\n"
                      f"❌ Total Losses: {state_proxy['loss_count']}\n"
                      f"💰 Final Profit: {state_proxy['total_profit']:.2f}\n"
                      f"📝 Reason: {text}\n"
                      f"━━━━━━━━━━━━━━\n"
                      f"🔄 All data wiped. Restart required.")
            bot.send_message(state_proxy["chat_id"], report, reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰'))
        except: pass
    
    # مسح شامل لجميع البيانات في الذاكرة
    initial = get_initial_state()
    for k, v in initial.items():
        state_proxy[k] = v

def open_digit_trade(state_proxy):
    if state_proxy["consecutive_losses"] >= 3:
        reset_and_stop(state_proxy, "3 Consecutive Losses.")
        return False
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": state_proxy["api_token"]}))
        ws.recv()
        
        req = {
            "proposal": 1, "amount": state_proxy["current_stake"], "basis": "stake", 
            "contract_type": "DIGITOVER", "barrier": "1", "currency": state_proxy["currency"], 
            "duration": 1, "duration_unit": "t", "symbol": "R_100"
        }
        
        ws.send(json.dumps(req))
        res = json.loads(ws.recv())
        prop = res.get("proposal")
        
        if prop:
            ws.send(json.dumps({"buy": prop["id"], "price": state_proxy["current_stake"]}))
            buy_res = json.loads(ws.recv())
            if "buy" in buy_res:
                state_proxy["active_contract"] = buy_res["buy"]["contract_id"]
                state_proxy["start_time"] = time.time()
                state_proxy["is_trading"] = True
                bot.send_message(state_proxy["chat_id"], f"🚀 **Trade Opened (Over 1)**\n💰 Stake: {state_proxy['current_stake']:.2f}")
                ws.close()
                return True
        ws.close()
    except: pass
    return False

def check_digit_result(state_proxy):
    if not state_proxy["active_contract"] or time.time() - state_proxy["start_time"] < 6:
        return
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": state_proxy["api_token"]}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": state_proxy["active_contract"]}))
        res = json.loads(ws.recv())
        ws.close()
        
        contract = res.get("proposal_open_contract", {})
        if contract.get("is_expired") == 1:
            state_proxy["active_contract"] = None 
            profit = float(contract.get("profit", 0))
            is_win = profit > 0
            
            if is_win:
                state_proxy["total_profit"] += profit
                state_proxy["win_count"] += 1
                state_proxy["consecutive_losses"] = 0
                state_proxy["current_stake"] = state_proxy["initial_stake"]
                icon = "✅ WIN"
            else:
                state_proxy["total_profit"] += profit
                state_proxy["loss_count"] += 1
                state_proxy["consecutive_losses"] += 1
                state_proxy["current_stake"] = float(state_proxy["current_stake"]) * 5
                icon = "❌ LOSS"

            result_msg = (f"{icon} ({profit:.2f})\n"
                          f"━━━━━━━━━━━━━━\n"
                          f"✅ Wins: {state_proxy['win_count']}\n"
                          f"❌ Losses: {state_proxy['loss_count']}\n"
                          f"🔥 Consecutive Losses: {state_proxy['consecutive_losses']}/3\n"
                          f"💰 Total Profit: {state_proxy['total_profit']:.2f}\n"
                          f"━━━━━━━━━━━━━━")
            bot.send_message(state_proxy["chat_id"], result_msg)
            
            state_proxy["is_trading"] = False

            # فحص شروط الإيقاف والمسح
            if state_proxy["consecutive_losses"] >= 3:
                reset_and_stop(state_proxy, "Target Loss Reached (3 Losses).")
            elif state_proxy["total_profit"] >= state_proxy["tp"]:
                reset_and_stop(state_proxy, "Target Profit Reached.")
    except: pass

def execute_digit_logic(state_proxy):
    if not state_proxy["is_running"] or state_proxy["is_trading"] or state_proxy["consecutive_losses"] >= 3:
        return
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=8)
        ws.send(json.dumps({"ticks_history": "R_100", "count": 2, "end": "latest", "style": "ticks"}))
        prices = json.loads(ws.recv()).get("history", {}).get("prices", [])
        ws.close()
        if len(prices) >= 2:
            t1, t2 = float(prices[0]), float(prices[1])
            t2_str = f"{t2:.2f}"
            if "." in t2_str and t2_str.split(".")[1][1] == "7" and t2 < t1:
                open_digit_trade(state_proxy)
    except: pass

def main_loop(state_proxy):
    while True:
        try:
            if state_proxy["is_running"]:
                if state_proxy["is_trading"]: check_digit_result(state_proxy)
                else: execute_digit_logic(state_proxy)
            time.sleep(0.4)
        except: time.sleep(1)

@bot.message_handler(commands=['start'])
def welcome(m):
    state["chat_id"] = m.chat.id
    bot.send_message(m.chat.id, "🤖 **Digit Over 1 v7.0**\n- Auto-Wipe on Stop/TP/SL\n- Martingale: x5 (Recursive)\n- Limit: 3 Losses", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰'))

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def ask_token(m):
    initial = get_initial_state()
    for k, v in initial.items(): state[k] = v
    state["chat_id"] = m.chat.id
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
    bot.send_message(m.chat.id, "🚀 Bot Started...", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_all(m):
    reset_and_stop(state, "Manual Stop Button Pressed.")

if __name__ == '__main__':
    multiprocessing.Process(target=main_loop, args=(state,), daemon=True).start()
    multiprocessing.Process(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
