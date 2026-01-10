import websocket, json, time, multiprocessing, os
from flask import Flask
import telebot
from telebot import types
from datetime import datetime

# Flask Setup for Render
app = Flask(__name__)

# UPDATED TOKEN
TOKEN = "8264292822:AAFWqocz_vR3CqAVy9n-gvlDe_6wMQvXiFg"
bot = telebot.TeleBot(TOKEN)

manager = multiprocessing.Manager()

def get_initial_state():
    return {
        "api_token": "", "initial_stake": 0.0, "current_stake": 0.0, "tp": 0.0, 
        "currency": "USD", "is_running": False, "chat_id": None,
        "total_profit": 0.0, "win_count": 0, "loss_count": 0, "is_trading": False,
        "consecutive_losses": 0, "last_trade_minute": -1,
        "active_contract": None, "start_time": 0
    }

state = manager.dict(get_initial_state())

@app.route('/')
def home():
    return "BOT ENGINE ACTIVE - TOKEN UPDATED - 30s MODE"

# --- Control & Reset ---
def reset_and_stop(state_proxy, text):
    if state_proxy["chat_id"]:
        try:
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰')
            bot.send_message(state_proxy["chat_id"], f"🛑 {text}", reply_markup=markup)
        except: pass
    initial = get_initial_state()
    for k, v in initial.items(): state_proxy[k] = v

# --- Logic to check Win/Loss ---
def check_result_logic(state_proxy):
    # Wait 40 seconds before checking as requested
    if not state_proxy["active_contract"] or time.time() - state_proxy["start_time"] < 40:
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
                state_proxy["consecutive_losses"] = 0
                state_proxy["current_stake"] = state_proxy["initial_stake"]
                res_icon = "✅"
            else:
                state_proxy["total_profit"] += profit
                state_proxy["loss_count"] += 1
                state_proxy["consecutive_losses"] += 1
                res_icon = "❌"
                
                if state_proxy["consecutive_losses"] >= 2:
                    reset_and_stop(state_proxy, f"❌ STOPPED: 2 Consecutive Losses.\nNet: {state_proxy['total_profit']:.2f}")
                    return
                
                # Martingale x19
                state_proxy["current_stake"] = state_proxy["initial_stake"] * 19
            
            stats_msg = (
                f"{res_icon} **RESULT: {'WIN' if profit > 0 else 'LOSS'} ({profit:.2f})**\n"
                f"━━━━━━━━━━━━━━\n"
                f"🏆 Wins: {state_proxy['win_count']} | 💀 Losses: {state_proxy['loss_count']}\n"
                f"💰 Total Net: {state_proxy['total_profit']:.2f} {state_proxy['currency']}\n"
                f"━━━━━━━━━━━━━━"
            )
            bot.send_message(state_proxy["chat_id"], stats_msg, parse_mode="Markdown")
            state_proxy["active_contract"], state_proxy["is_trading"] = None, False
            
            if state_proxy["total_profit"] >= state_proxy["tp"]:
                reset_and_stop(state_proxy, "Target Profit Reached! 🎉")
    except: pass

# --- Signal & Trade Execution ---
def execute_trade(state_proxy):
    now = datetime.now()
    if state_proxy["is_trading"] or now.second != 0 or state_proxy["last_trade_minute"] == now.minute:
        return
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=8)
        ws.send(json.dumps({"authorize": state_proxy["api_token"]}))
        ws.recv()
        # Analyze 15 Ticks as requested
        ws.send(json.dumps({"ticks_history": "R_100", "count": 15, "end": "latest", "style": "ticks"}))
        prices = json.loads(ws.recv()).get("history", {}).get("prices", [])
        ws.close()

        if len(prices) >= 15:
            diff = float(prices[-1]) - float(prices[0])
            if abs(diff) >= 0.8:
                state_proxy["is_trading"], state_proxy["last_trade_minute"] = True, now.minute
                # Trend Following logic
                c_t, br = ("CALL", "-1.0") if diff >= 0.8 else ("PUT", "+1.0")
                
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
                ws.send(json.dumps({"authorize": state_proxy["api_token"]}))
                ws.recv()
                # 30 Seconds Duration as requested
                req = {"proposal": 1, "amount": state_proxy["current_stake"], "basis": "stake", 
                       "contract_type": c_t, "currency": state_proxy["currency"], 
                       "duration": 30, "duration_unit": "s", "symbol": "R_100", "barrier": br}
                ws.send(json.dumps(req))
                prop = json.loads(ws.recv()).get("proposal")
                if prop:
                    ws.send(json.dumps({"buy": prop["id"], "price": state_proxy["current_stake"]}))
                    buy_res = json.loads(ws.recv())
                    if "buy" in buy_res:
                        state_proxy["active_contract"], state_proxy["start_time"] = buy_res["buy"]["contract_id"], time.time()
                        bot.send_message(state_proxy["chat_id"], f"🚀 **{c_t} Trade Sent**\nStake: {state_proxy['current_stake']:.2f}")
                    else: state_proxy["is_trading"] = False
                else: state_proxy["is_trading"] = False
    except: state_proxy["is_trading"] = False

def main_loop(state_proxy):
    while True:
        try:
            if state_proxy["is_running"]:
                execute_trade(state_proxy)
                check_result_logic(state_proxy)
            time.sleep(0.5)
        except: time.sleep(1)

# --- Telegram Handlers ---
@bot.message_handler(commands=['start'])
def welcome(m):
    state["chat_id"] = m.chat.id
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰')
    bot.send_message(m.chat.id, "👋 Welcome! Select account type:", reply_markup=markup)

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
        bot.send_message(m.chat.id, "Enter Target Profit (TP):")
        bot.register_next_step_handler(m, save_tp)
    except: bot.send_message(m.chat.id, "Invalid number.")

def save_tp(m):
    try:
        state["tp"] = float(m.text)
        state["is_running"] = True
        bot.send_message(m.chat.id, f"✅ Running!\nAnalyzing 15 ticks at 00s\nMartingale: x19", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
    except: pass

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_all(m):
    reset_and_stop(state, "Manual Stop.")

if __name__ == '__main__':
    # Start Trading Engine
    p = multiprocessing.Process(target=main_loop, args=(state,), daemon=True)
    p.start()
    
    # Start Web Server for Render
    f = multiprocessing.Process(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True)
    f.start()
    
    # Start Telegram Listener
    bot.infinity_polling()
