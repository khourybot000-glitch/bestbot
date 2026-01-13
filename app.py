import websocket, json, time, multiprocessing, os
from flask import Flask
import telebot
from telebot import types
from datetime import datetime

app = Flask(__name__)

# --- التوكن الجديد المحدث ---
TOKEN = "8264292822:AAFug51wkLe8OW2BWrRYUeZAkp5mP_I6YtM"
bot = telebot.TeleBot(TOKEN)
manager = multiprocessing.Manager()

def get_initial_state():
    return {
        "api_token": "", "initial_stake": 0.0, "current_stake": 0.0, "tp": 0.0, 
        "currency": "USD", "is_running": False, "chat_id": None,
        "total_profit": 0.0, "win_count": 0, "loss_count": 0, "is_trading": False,
        "consecutive_losses": 0, "last_trade_time": "",
        "active_contract": None, "start_time": 0, "last_type": "",
        "pending_martingale": False
    }

state = manager.dict(get_initial_state())

@app.route('/')
def home():
    status = "ACTIVE 🚀" if state["is_running"] else "STOPPED 🛑"
    return f"<h2>STATUS: {status}</h2><p>Wins: {state['win_count']} | Losses: {state['loss_count']} | Profit: {state['total_profit']:.2f}</p>"

def reset_and_stop(state_proxy, text):
    if state_proxy["chat_id"]:
        try:
            report = (f"📊 **SESSION SUMMARY**\n"
                      f"✅ Wins: {state_proxy['win_count']}\n"
                      f"❌ Losses: {state_proxy['loss_count']}\n"
                      f"💰 Profit: {state_proxy['total_profit']:.2f}\n"
                      f"🛑 {text}")
            bot.send_message(state_proxy["chat_id"], report, reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰'))
        except: pass
    initial = get_initial_state()
    for k, v in initial.items(): state_proxy[k] = v

def open_trade_raw(state_proxy, contract_type):
    try:
        # حاجز المسافة 2.0 كما طلبت
        barrier = "-2.0" if contract_type == "CALL" else "+2.0"
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": state_proxy["api_token"]}))
        ws.recv()
        
        req = {
            "proposal": 1, "amount": state_proxy["current_stake"], "basis": "stake", 
            "contract_type": contract_type, "currency": state_proxy["currency"], 
            "duration": 1, "duration_unit": "m", "symbol": "R_100", "barrier": barrier
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
                bot.send_message(state_proxy["chat_id"], f"🚀 **Trade Opened: {direction}**\n💰 Stake: {state_proxy['current_stake']}\n⏱ Duration: 1 min")
                ws.close()
                return True
        ws.close()
    except: pass
    return False

def check_result_logic(state_proxy):
    # مدة الانتظار للنتيجة 68 ثانية
    if not state_proxy["active_contract"] or time.time() - state_proxy["start_time"] < 68:
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
                state_proxy["pending_martingale"] = False
                icon = "✅"
            else:
                state_proxy["total_profit"] += profit
                state_proxy["loss_count"] += 1
                state_proxy["consecutive_losses"] += 1
                icon = "❌"
                
                if state_proxy["consecutive_losses"] >= 2:
                    reset_and_stop(state_proxy, "Stopped: 2 Consecutive Losses.")
                    return
                else:
                    # مضاعفة x19
                    state_proxy["current_stake"] = state_proxy["initial_stake"] * 19
                    state_proxy["pending_martingale"] = True 
                    bot.send_message(state_proxy["chat_id"], "⚠️ **Loss! Analyzing for a new signal for Martingale x19...**")

            bot.send_message(state_proxy["chat_id"], f"{icon} **Result: {profit:.2f}**\n✅ Wins: {state_proxy['win_count']} | ❌ Losses: {state_proxy['loss_count']}\n💰 Net: {state_proxy['total_profit']:.2f}")
            state_proxy["is_trading"] = False

            if state_proxy["total_profit"] >= state_proxy["tp"]:
                reset_and_stop(state_proxy, "Target Profit Reached!")
    except: pass

def execute_trade(state_proxy):
    now = datetime.now()
    # التحليل عند الثانية 00 فقط
    if not state_proxy["is_running"] or state_proxy["is_trading"] or now.second != 0:
        return
    
    time_key = f"{now.minute}:{now.second}"
    if state_proxy["last_trade_time"] == time_key: return

    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=8)
        # تحليل 30 تيك
        ws.send(json.dumps({"ticks_history": "R_100", "count": 30, "end": "latest", "style": "ticks"}))
        prices = json.loads(ws.recv()).get("history", {}).get("prices", [])
        ws.close()

        if len(prices) >= 30:
            diff = float(prices[-1]) - float(prices[0])
            target_type = ""
            
            # استراتيجية عكس الاتجاه (الفرق أكبر من 1.5)
            if diff >= 1.5: 
                target_type = "PUT"
            elif diff <= -1.5: 
                target_type = "CALL"
            
            if target_type:
                state_proxy["last_trade_time"] = time_key
                open_trade_raw(state_proxy, target_type)
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
    bot.send_message(m.chat.id, "🤖 **Contrarian Bot v4.1**\n- Token Updated.\n- Interval: :00 (30 Ticks)\n- Signal: Reverse on Diff > 1.5\n- Barrier: 2.0 | Martingale: x19", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰'))

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def ask_token(m):
    for k, v in get_initial_state().items(): state[k] = v
    state["chat_id"] = m.chat.id
    state["currency"] = "USD" if "Demo" in m.text else "tUSDT"
    bot.send_message(m.chat.id, "Enter API Token:")
    bot.register_next_step_handler(m, save_token)

def save_token(m):
    state["api_token"] = m.text.strip()
    bot.send_message(m.chat.id, "Stake:")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    try: state["initial_stake"] = float(m.text); state["current_stake"] = state["initial_stake"]
    except: return
    bot.send_message(m.chat.id, "Target Profit:")
    bot.register_next_step_handler(m, save_tp)

def save_tp(m):
    try: state["tp"] = float(m.text); state["is_running"] = True
    except: return
    bot.send_message(m.chat.id, "🚀 Running...", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_all(m): reset_and_stop(state, "Manual Stop.")

if __name__ == '__main__':
    multiprocessing.Process(target=main_loop, args=(state,), daemon=True).start()
    multiprocessing.Process(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
