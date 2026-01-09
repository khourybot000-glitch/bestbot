import websocket, json, time, multiprocessing, os
from flask import Flask
import telebot
from telebot import types
from datetime import datetime

app = Flask(__name__)
# تم تحديث التوكن هنا
bot = telebot.TeleBot("8469655661:AAE2hQMkI0UAbd8d3-Dl6TGIdLugH8jSlAo")

manager = multiprocessing.Manager()

def get_initial_state():
    return {
        "api_token": "", "initial_stake": 0.0, "current_stake": 0.0, "tp": 0.0, 
        "currency": "USD", "is_running": False, "chat_id": None,
        "total_profit": 0.0, "win_count": 0, "loss_count": 0, "is_trading": False,
        "consecutive_losses": 0, "last_trade_second": -1,
        "active_contract": None, "start_time": 0
    }

state = manager.dict(get_initial_state())

@app.route('/')
def home():
    return "BOT ACTIVE - TOKEN UPDATED - FAST x14 MODE"

def reset_and_stop(state_proxy, text):
    if state_proxy["chat_id"]:
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰')
        bot.send_message(state_proxy["chat_id"], f"🛑 {text}", reply_markup=markup)
    initial = get_initial_state()
    for k, v in initial.items(): state_proxy[k] = v

def check_result_logic(state_proxy):
    if not state_proxy["active_contract"]:
        return
    # وقت الانتظار 18 ثانية كما طلبت
    if time.time() - state_proxy["start_time"] < 18:
        return

    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": state_proxy["api_token"]}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": state_proxy["active_contract"]}))
        data = json.loads(ws.recv())
        ws.close()
        
        contract = data.get("proposal_open_contract", {})
        if contract.get("is_expired"):
            profit = float(contract.get("profit", 0))
            
            if profit > 0:
                state_proxy["total_profit"] += profit
                state_proxy["win_count"] += 1
                state_proxy["consecutive_losses"] = 0
                res_symbol = "✅"
                state_proxy["current_stake"] = state_proxy["initial_stake"]
            else:
                state_proxy["total_profit"] += profit
                state_proxy["loss_count"] += 1
                state_proxy["consecutive_losses"] += 1
                res_symbol = "❌"
                
                if state_proxy["consecutive_losses"] >= 2:
                    reset_and_stop(state_proxy, f"❌ Stop Loss (2 Losses).\nTotal Net: {state_proxy['total_profit']:.2f}")
                    return
                # المضاعفة ×14
                state_proxy["current_stake"] = state_proxy["initial_stake"] * 14
            
            stats_msg = (
                f"{res_symbol} **Result: {profit:.2f}**\n"
                f"━━━━━━━━━━━━━━\n"
                f"🏆 W: {state_proxy['win_count']} | 💀 L: {state_proxy['loss_count']}\n"
                f"💰 Total: {state_proxy['total_profit']:.2f} {state_proxy['currency']}\n"
                f"━━━━━━━━━━━━━━"
            )
            bot.send_message(state_proxy["chat_id"], stats_msg, parse_mode="Markdown")
            
            state_proxy["active_contract"] = None
            state_proxy["is_trading"] = False
            
            if state_proxy["total_profit"] >= state_proxy["tp"]:
                reset_and_stop(state_proxy, "Target Reached! 🎉")
    except:
        state_proxy["is_trading"] = False

def execute_trade(state_proxy):
    now = datetime.now()
    valid_seconds = [0, 10, 20, 30, 40, 50]
    
    if state_proxy["is_trading"] or now.second not in valid_seconds or state_proxy["last_trade_second"] == now.second:
        return

    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": state_proxy["api_token"]}))
        ws.recv()
        # تحليل 5 تيكات
        ws.send(json.dumps({"ticks_history": "R_100", "count": 5, "end": "latest", "style": "ticks"}))
        prices = json.loads(ws.recv()).get("history", {}).get("prices", [])
        ws.close()

        if len(prices) >= 5:
            diff = float(prices[-1]) - float(prices[0])
            if abs(diff) >= 0.8:
                state_proxy["is_trading"] = True
                state_proxy["last_trade_second"] = now.second
                
                # عكس الاتجاه: صاعد يدخل PUT+0.7 | هابط يدخل CALL-0.7
                if diff >= 0.8:
                    c_type, barr = "PUT", "+0.7"
                else:
                    c_type, barr = "CALL", "-0.7"

                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
                ws.send(json.dumps({"authorize": state_proxy["api_token"]}))
                ws.recv()
                
                # مدة الصفقة 6 تيكات
                req = {"proposal": 1, "amount": state_proxy["current_stake"], "basis": "stake",
                       "contract_type": c_type, "currency": state_proxy["currency"],
                       "duration": 6, "duration_unit": "t", "symbol": "R_100", "barrier": barr}
                
                ws.send(json.dumps(req))
                prop = json.loads(ws.recv()).get("proposal")
                
                if prop:
                    ws.send(json.dumps({"buy": prop["id"], "price": state_proxy["current_stake"]}))
                    buy_res = json.loads(ws.recv())
                    if "buy" in buy_res:
                        state_proxy["active_contract"] = buy_res["buy"]["contract_id"]
                        state_proxy["start_time"] = time.time()
                        bot.send_message(state_proxy["chat_id"], f"🚀 **{c_type} In** (Barr: {barr})\nStake: {state_proxy['current_stake']:.2f}")
                    else: state_proxy["is_trading"] = False
                else: state_proxy["is_trading"] = False
    except: state_proxy["is_trading"] = False

def main_loop(state_proxy):
    while True:
        if state_proxy["is_running"]:
            execute_trade(state_proxy)
            check_result_logic(state_proxy)
        time.sleep(0.5)

@bot.message_handler(commands=['start'])
def start(m):
    state["chat_id"] = m.chat.id
    bot.send_message(m.chat.id, "🤖 **Fast Sync v3**\nToken Updated | x14 Martingale", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰'))

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def mode(m):
    state["currency"] = "USD" if "Demo" in m.text else "tUSDT"
    bot.send_message(m.chat.id, "Token?")
    bot.register_next_step_handler(m, set_t)

def set_t(m):
    state["api_token"] = m.text.strip()
    bot.send_message(m.chat.id, "Stake?")
    bot.register_next_step_handler(m, set_s)

def set_s(m):
    try:
        state["initial_stake"] = float(m.text)
        state["current_stake"] = state["initial_stake"]
        bot.send_message(m.chat.id, "Target Profit?")
        bot.register_next_step_handler(m, set_tp)
    except: pass

def set_tp(m):
    try:
        state["tp"] = float(m.text)
        state["is_running"] = True
        bot.send_message(m.chat.id, "🚀 Running Fast Strategy...", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
    except: pass

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop(m): reset_and_stop(state, "Manual Stop.")

if __name__ == '__main__':
    multiprocessing.Process(target=main_loop, args=(state,)).start()
    multiprocessing.Process(target=lambda: app.run(host='0.0.0.0', port=10000)).start()
    bot.infinity_polling()
