import websocket, json, time, multiprocessing, os
from flask import Flask
import telebot
from telebot import types

app = Flask(__name__)

# --- Updated API Token ---
TOKEN = "8264292822:AAHGrSANOzjs5Xnlssj_yIIgLaT4OM0Df-4"
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
    return "Pattern Bot v10.6 - DIGITOVER 1 Active"

def reset_and_stop(state_proxy, text):
    if state_proxy["chat_id"]:
        try:
            report = (f"🛑 **BOT STOPPED**\n"
                      f"━━━━━━━━━━━━━━\n"
                      f"✅ Wins: {state_proxy['win_count']}\n"
                      f"❌ Losses: {state_proxy['loss_count']}\n"
                      f"💰 Total Profit: {state_proxy['total_profit']:.2f}\n"
                      f"📝 Reason: {text}")
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰')
            bot.send_message(state_proxy["chat_id"], report, reply_markup=markup)
        except: pass
    initial = get_initial_state()
    for k, v in initial.items(): state_proxy[k] = v

def get_second_decimal(price):
    try:
        s_price = "{:.2f}".format(price)
        return int(s_price.split('.')[1][1])
    except: return None

def open_trade(state_proxy, ws_persistent):
    if state_proxy["consecutive_losses"] >= 2:
        reset_and_stop(state_proxy, "Stop Loss: 2 Consecutive Losses")
        return False
    try:
        req = {
            "proposal": 1, "amount": state_proxy["current_stake"], "basis": "stake", 
            "contract_type": "DIGITOVER", "barrier": "1", "currency": state_proxy["currency"], 
            "duration": 1, "duration_unit": "t", "symbol": "R_100"
        }
        ws_persistent.send(json.dumps(req))
        res = json.loads(ws_persistent.recv())
        prop = res.get("proposal")
        if prop:
            ws_persistent.send(json.dumps({"buy": prop["id"], "price": state_proxy["current_stake"]}))
            buy_res = json.loads(ws_persistent.recv())
            if "buy" in buy_res:
                state_proxy["active_contract"] = buy_res["buy"]["contract_id"]
                state_proxy["start_time"] = time.time()
                state_proxy["is_trading"] = True
                bot.send_message(state_proxy["chat_id"], "🚀 **Trade Executed: OVER 1**\n🔌 Disconnecting for 8s wait...")
                return True
    except: pass
    return False

def check_result(state_proxy):
    if not state_proxy["active_contract"] or time.time() - state_proxy["start_time"] < 8:
        return
    try:
        ws_temp = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws_temp.send(json.dumps({"authorize": state_proxy["api_token"]}))
        ws_temp.recv()
        ws_temp.send(json.dumps({"proposal_open_contract": 1, "contract_id": state_proxy["active_contract"]}))
        res = json.loads(ws_temp.recv())
        ws_temp.close()
        
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
                state_proxy["current_stake"] = float(state_proxy["current_stake"]) * 9
                icon = "❌ LOSS"

            stats_msg = (f"{icon} ({profit:.2f})\n"
                         f"━━━━━━━━━━━━━━\n"
                         f"🟢 Wins: {state_proxy['win_count']}\n"
                         f"🔴 Losses: {state_proxy['loss_count']}\n"
                         f"💰 Net Profit: {state_proxy['total_profit']:.2f}\n"
                         f"━━━━━━━━━━━━━━")
            bot.send_message(state_proxy["chat_id"], stats_msg)
            state_proxy["is_trading"] = False

            if state_proxy["consecutive_losses"] >= 2:
                reset_and_stop(state_proxy, "Reached 2 Consecutive Losses")
            elif state_proxy["total_profit"] >= state_proxy["tp"]:
                reset_and_stop(state_proxy, "Target Profit Reached")
    except: pass

def main_loop(state_proxy):
    ws_persistent = None
    while True:
        try:
            if state_proxy["is_running"]:
                if state_proxy["is_trading"]:
                    if ws_persistent: ws_persistent.close(); ws_persistent = None
                    check_result(state_proxy)
                else:
                    if ws_persistent is None:
                        ws_persistent = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
                        ws_persistent.send(json.dumps({"authorize": state_proxy["api_token"]}))
                        ws_persistent.recv()
                    
                    ws_persistent.send(json.dumps({"ticks_history": "R_100", "count": 3, "end": "latest", "style": "ticks"}))
                    prices = json.loads(ws_persistent.recv()).get("history", {}).get("prices", [])
                    
                    if len(prices) >= 3:
                        t1, t2, t3 = [get_second_decimal(p) for p in prices]
                        if (t1 == 9 and t2 == 8 and t3 == 9) or (t1 == 8 and t2 == 9 and t3 == 8):
                            if open_trade(state_proxy, ws_persistent):
                                ws_persistent.close(); ws_persistent = None
            time.sleep(0.5) 
        except:
            if ws_persistent: ws_persistent.close()
            ws_persistent = None
            time.sleep(1)

@bot.message_handler(commands=['start'])
def welcome(m):
    state["chat_id"] = m.chat.id
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰')
    bot.send_message(m.chat.id, "🤖 **Pattern Bot v10.6**\n- Multiplier: x9\n- Contract: OVER 1\n- Logic: [9-8-9] or [8-9-8]", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def ask_token(m):
    state["chat_id"] = m.chat.id
    state["currency"] = "USD" if "Demo" in m.text else "tUSDT"
    bot.send_message(m.chat.id, "Enter your API Token:")
    bot.register_next_step_handler(m, save_token)

def save_token(m):
    state["api_token"] = m.text.strip()
    bot.send_message(m.chat.id, "Enter Initial Stake:")
    bot.register_next_step_handler(m, save_stake)

def save_stake(m):
    try: state["initial_stake"] = float(m.text); state["current_stake"] = state["initial_stake"]
    except: return
    bot.send_message(m.chat.id, "Enter Target Profit:")
    bot.register_next_step_handler(m, save_tp)

def save_tp(m):
    try: 
        state["tp"] = float(m.text)
        state["is_running"] = True
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑')
        bot.send_message(m.chat.id, "🚀 Running Digit Strategy...", reply_markup=markup)
    except: return

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_all(m): reset_and_stop(state, "Manual Stop")

if __name__ == '__main__':
    multiprocessing.Process(target=main_loop, args=(state,), daemon=True).start()
    multiprocessing.Process(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
