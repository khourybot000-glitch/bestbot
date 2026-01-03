import websocket, json, time, threading
from flask import Flask
import telebot
from telebot import types

app = Flask(__name__)
# Replace with your actual Telegram Bot Token
bot = telebot.TeleBot("8459148778:AAE6_DL8oMYayXVvHTVKNfAUZmI6WfxOZ5M")

state = {
    "api_token": "", "initial_stake": 0.0, "current_stake": 0.0, "tp": 0.0, 
    "currency": "USD", "is_running": False, "chat_id": None, 
    "tick_history": [], 
    "total_profit": 0.0, "win_count": 0, "loss_count": 0, "loss_streak": 0
}

@app.route('/')
def home():
    return "<h1>R_100 5-Tick Gap Strategy is Running</h1>"

def reset_and_stop(message_text):
    state.update({
        "api_token": "", "is_running": False, "tick_history": [], 
        "loss_streak": 0, "win_count": 0, "loss_count": 0, "total_profit": 0.0,
        "initial_stake": 0.0, "current_stake": 0.0
    })
    if state["chat_id"]:
        bot.send_message(state["chat_id"], f"🛑 **Bot Stopped:** {message_text}\n(Memory Cleared)", reply_markup=types.ReplyKeyboardRemove())

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def manual_stop(message):
    reset_and_stop("Manual stop requested.")

def check_result(contract_id):
    try:
        time.sleep(120)
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": state["api_token"]}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
        res = json.loads(ws.recv())
        ws.close()
        
        profit = float(res.get("proposal_open_contract", {}).get("profit", 0))
        
        if profit < 0:
            state["loss_count"] += 1
            state["loss_streak"] += 1
            if state["loss_streak"] >= 1:
                reset_and_stop("Max losses (1) reached. Safety Shutdown.")
            else:
                state["current_stake"] = round(state["initial_stake"] * 24, 2)
                bot.send_message(state["chat_id"], f"❌ Loss! Martingale x24: **{state['current_stake']}**")
                threading.Thread(target=start_tracking).start()
        else:
            state["win_count"] += 1
            state["total_profit"] += profit
            state["loss_streak"] = 0
            state["current_stake"] = state["initial_stake"]
            
            stats = (f"✅ **WIN! +{round(profit, 2)}**\n"
                     f"🏆 Wins: {state['win_count']} | ❌ Losses: {state['loss_count']}\n"
                     f"💰 Total Profit: {round(state['total_profit'], 2)}")
            bot.send_message(state["chat_id"], stats)
            
            if state["total_profit"] >= state["tp"]:
                reset_and_stop(f"🎯 TP Reached: ${round(state['total_profit'], 2)}")
            else:
                threading.Thread(target=start_tracking).start()
    except:
        reset_and_stop("Connection Error during result check.")

def place_trade(action):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": state["api_token"]}))
        ws.recv()
        
        barrier = "-1" if action == "call" else "+1"
        
        trade_req = {
            "buy": 1, "price": float(state["current_stake"]),
            "parameters": {
                "amount": float(state["current_stake"]), "basis": "stake",
                "contract_type": "CALL" if action == "put" else "PUT",
                "currency": state["currency"], "duration": 6, "duration_unit": "t",
                "symbol": "R_100", "barrier": barrier
            }
        }
        ws.send(json.dumps(trade_req))
        res = json.loads(ws.recv())
        ws.close()
        
        if "buy" in res:
            bot.send_message(state["chat_id"], f"📥 {action.upper()} Executed (Stake: {state['current_stake']})")
            threading.Thread(target=check_result, args=(res["buy"]["contract_id"],)).start()
            return True
        return False
    except: return False

def on_message(ws, message):
    data = json.loads(message)
    if "tick" in data:
        curr_p = data["tick"]["quote"]
        state["tick_history"].append(curr_p)
        
        # Keep only the last 5 ticks
        if len(state["tick_history"]) > 5:
            state["tick_history"].pop(0)
            
        if len(state["tick_history"]) == 5 and state["is_running"]:
            t1 = state["tick_history"][0] # 1st Tick
            t5 = state["tick_history"][4] # 5th Tick
            
            gap = t5 - t1
            action = None
            
            # Condition: Gap must be greater than 0.6 (positive or negative)
            if gap > 0.6:
                action = "call"
            elif gap < -0.6:
                action = "put"
            
            if action:
                ws.close() # Disconnect immediately
                state["tick_history"] = [] # Clear history for next cycle
                if not place_trade(action):
                    reset_and_stop("Trade execution failed.")
                    
def start_tracking():
    if not state["is_running"]: return
    state["tick_history"] = []
    ws = websocket.WebSocketApp(
        "wss://blue.derivws.com/websockets/v3?app_id=16929",
        on_message=on_message,
        on_open=lambda ws: ws.send(json.dumps({"ticks": "R_100", "subscribe": 1}))
    )
    ws.run_forever()

@bot.message_handler(commands=['start'])
def cmd_start(message):
    state["chat_id"] = message.chat.id
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('Demo 🛠️', 'Live 💰')
    bot.send_message(message.chat.id, "🎯 **R_100 Gap Strategy (5-Ticks)**\nCondition: |T5 - T1| > 0.6\nDuration: 5 Ticks", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def set_acc(message):
    state["currency"] = "USD" if "Demo" in message.text else "tUSDT"
    bot.send_message(message.chat.id, "Enter API Token:", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(message, set_token)

def set_token(message):
    state["api_token"] = message.text.strip()
    bot.send_message(message.chat.id, "Enter Initial Stake:")
    bot.register_next_step_handler(message, set_stake)

def set_stake(message):
    state["initial_stake"] = state["current_stake"] = float(message.text)
    bot.send_message(message.chat.id, "Enter Take Profit Target ($):")
    bot.register_next_step_handler(message, set_tp)

def set_tp(message):
    state["tp"] = float(message.text)
    state["is_running"] = True
    stop_markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    stop_markup.add('STOP 🛑')
    bot.send_message(message.chat.id, f"🚀 Analyzing Gap between T1 and T5...\nTarget: ${state['tp']}", reply_markup=stop_markup)
    threading.Thread(target=start_tracking).start()

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000)).start()
    bot.infinity_polling()
