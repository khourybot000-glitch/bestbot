import websocket, json, time, multiprocessing, os
from flask import Flask
import telebot
from telebot import types

app = Flask(__name__)

# --- التوكن الجديد المحدث ---
TOKEN = "8264292822:AAHsCvrUccWSbPwECmiOK6ccDpIhbmcD17U"
bot = telebot.TeleBot(TOKEN)
manager = multiprocessing.Manager()

def get_initial_state():
    return {
        "api_token": "", "initial_stake": 0.0, "current_stake": 0.0, "tp": 0.0, 
        "currency": "USD", "is_running": False, "chat_id": None,
        "total_profit": 0.0, "win_count": 0, "loss_count": 0, "is_trading": False,
        "consecutive_losses": 0, "active_contract": None, "start_time": 0
    }

state = manager.dict(get_initial_state())

@app.route('/')
def home():
    return "Strict Sequence Bot v8.4 is Running"

def reset_and_stop(state_proxy, text):
    if state_proxy["chat_id"]:
        try:
            report = (f"🛑 **BOT STOPPED**\n"
                      f"━━━━━━━━━━━━━━\n"
                      f"✅ Wins: {state_proxy['win_count']}\n"
                      f"❌ Losses: {state_proxy['loss_count']}\n"
                      f"💰 Profit: {state_proxy['total_profit']:.2f}\n"
                      f"📝 Reason: {text}\n"
                      f"━━━━━━━━━━━━━━\n"
                      f"🔄 Data Reset.")
            bot.send_message(state_proxy["chat_id"], report, reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰'))
        except: pass
    initial = get_initial_state()
    for k, v in initial.items(): state_proxy[k] = v

def open_trade(state_proxy, contract_type):
    if state_proxy["consecutive_losses"] >= 2:
        reset_and_stop(state_proxy, "SL: 2 Consecutive Losses.")
        return False
    try:
        # الحاجز: -0.8 للـ CALL و +0.8 للـ PUT
        barrier = "-0.8" if contract_type == "CALL" else "+0.8"
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": state_proxy["api_token"]}))
        ws.recv()
        
        req = {
            "proposal": 1, "amount": state_proxy["current_stake"], "basis": "stake", 
            "contract_type": contract_type, "barrier": barrier, "currency": state_proxy["currency"], 
            "duration": 5, "duration_unit": "t", "symbol": "R_100"
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
                bot.send_message(state_proxy["chat_id"], f"🚀 **Strict Entry: {contract_type}**\n💰 Stake: {state_proxy['current_stake']:.2f}")
                ws.close()
                return True
        ws.close()
    except: pass
    return False

def check_result(state_proxy):
    # انتظار 18 ثانية كما طلبت
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
                # مضاعفة x19
                state_proxy["current_stake"] = float(state_proxy["current_stake"]) * 19
                icon = "❌ LOSS"

            result_msg = (f"{icon} ({profit:.2f})\n"
                          f"━━━━━━━━━━━━━━\n"
                          f"✅ Wins: {state_proxy['win_count']}\n"
                          f"❌ Losses: {state_proxy['loss_count']}\n"
                          f"💰 Net: {state_proxy['total_profit']:.2f}\n"
                          f"━━━━━━━━━━━━━━")
            bot.send_message(state_proxy["chat_id"], result_msg)
            state_proxy["is_trading"] = False

            if state_proxy["consecutive_losses"] >= 2:
                reset_and_stop(state_proxy, "Reached 2 Consecutive Losses.")
            elif state_proxy["total_profit"] >= state_proxy["tp"]:
                reset_and_stop(state_proxy, "Target Profit Reached.")
    except: pass

def execute_logic(state_proxy):
    if not state_proxy["is_running"] or state_proxy["is_trading"]:
        return
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=8)
        ws.send(json.dumps({"ticks_history": "R_100", "count": 4, "end": "latest", "style": "ticks"}))
        prices = json.loads(ws.recv()).get("history", {}).get("prices", [])
        ws.close()

        if len(prices) >= 4:
            t1, t2, t3, t4 = prices[0], prices[1], prices[2], prices[3]
            
            # شرط CALL: هبوط متسلسل صارم T1 > T2 > T3 > T4
            if t1 > t2 > t3 > t4:
                open_trade(state_proxy, "CALL")
            
            # شرط PUT: صعود متسلسل صارم T1 < T2 < T3 < T4
            elif t1 < t2 < t3 < t4:
                open_trade(state_proxy, "PUT")
    except: pass

def main_loop(state_proxy):
    while True:
        try:
            if state_proxy["is_running"]:
                if state_proxy["is_trading"]: check_result(state_proxy)
                else: execute_logic(state_proxy)
            time.sleep(0.5) 
        except: time.sleep(1)

@bot.message_handler(commands=['start'])
def welcome(m):
    state["chat_id"] = m.chat.id
    bot.send_message(m.chat.id, "🤖 **Strict Sequence Bot v8.4**\n- Logical: T1 < T2 < T3 < T4\n- Barrier: 0.8 | Duration: 5t\n- Martingale: x19 | SL: 2 Losses", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰'))

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def ask_token(m):
    initial = get_initial_state()
    for k, v in initial.items(): state[k] = v
    state["chat_id"] = m.chat.id
    state["currency"] = "USD" if "Demo" in m.text else "tUSDT"
    bot.send_message(m.chat.id, "API Token:")
    bot.register_next_step_handler(m, save_token)

def save_token(m):
    state["api_token"] = m.text.strip()
    bot.send_message(m.chat.id, "Initial Stake:")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    try: state["initial_stake"] = float(m.text); state["current_stake"] = state["initial_stake"]
    except: return
    bot.send_message(m.chat.id, "Target Profit:")
    bot.register_next_step_handler(m, save_tp)

def save_tp(m):
    try: state["tp"] = float(m.text); state["is_running"] = True
    except: return
    bot.send_message(m.chat.id, "🚀 Running Strictly...")

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_all(m): reset_and_stop(state, "Manual Stop.")

if __name__ == '__main__':
    multiprocessing.Process(target=main_loop, args=(state,), daemon=True).start()
    multiprocessing.Process(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
