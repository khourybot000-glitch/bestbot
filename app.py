import websocket, json, time, threading
from flask import Flask
import telebot
from telebot import types

app = Flask(__name__)
# Your unique Telegram Bot Token
bot = telebot.TeleBot("8512996523:AAE7zpVXnsI7qv0NhWJIbjTyDOUyMM_AAR0")

state = {
    "api_token": "", "initial_stake": 0.0, "current_stake": 0.0, "tp": 0.0, 
    "currency": "USD", "is_running": False, "chat_id": None, 
    "last_d2": None, 
    "total_profit": 0.0, "win_count": 0, "loss_count": 0, "loss_streak": 0
}

@app.route('/')
def home():
    return "<h1>R_100 D2=9 Diff Bot is Active</h1>"

def reset_and_stop(message_text):
    state.update({
        "api_token": "", "is_running": False, "last_d2": None, 
        "loss_streak": 0, "win_count": 0, "loss_count": 0, "total_profit": 0.0
    })
    if state["chat_id"]:
        bot.send_message(state["chat_id"], f"🛑 **Bot Stopped:** {message_text}\n(Memory Cleared)", reply_markup=types.ReplyKeyboardRemove())

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def manual_stop(message):
    reset_and_stop("Manual stop requested by user.")

def check_result(contract_id):
    try:
        time.sleep(6) # 6 seconds wait as requested
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
            if state["loss_streak"] >= 2:
                reset_and_stop("Consecutive losses (2). Safety Shutdown.")
            else:
                state["current_stake"] = round(state["initial_stake"] * 14, 2)
                bot.send_message(state["chat_id"], f"❌ Loss! Next Stake (x14): **{state['current_stake']}**")
                threading.Thread(target=start_tracking).start()
        else:
            state["win_count"] += 1
            state["total_profit"] += profit
            state["loss_streak"] = 0
            state["current_stake"] = state["initial_stake"]
            
            stats = (f"✅ **WIN! +{round(profit, 2)}**\n"
                     f"🏆 Wins: {state['win_count']} | Losses: {state['loss_count']}\n"
                     f"💰 Total Profit: {round(state['total_profit'], 2)}")
            bot.send_message(state["chat_id"], stats)
            
            if state["total_profit"] >= state["tp"]:
                reset_and_stop(f"🎯 Target Reached: ${round(state['total_profit'], 2)}")
            else:
                threading.Thread(target=start_tracking).start()
    except:
        reset_and_stop("Connection Error while verifying result.")

def place_trade():
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": state["api_token"]}))
        ws.recv()
        
        trade_req = {
            "buy": 1, "price": float(state["current_stake"]),
            "parameters": {
                "amount": float(state["current_stake"]), "basis": "stake",
                "contract_type": "DIGITDIFF",
                "barrier": "0",
                "currency": state["currency"], "duration": 1, "duration_unit": "t",
                "symbol": "R_100"
            }
        }
        ws.send(json.dumps(trade_req))
        res = json.loads(ws.recv())
        ws.close()
        
        if "buy" in res:
            bot.send_message(state["chat_id"], f"📥 Trade Entered (D2 Sequence 9-9) | Stake: {state['current_stake']}")
            threading.Thread(target=check_result, args=(res["buy"]["contract_id"],)).start()
            return True
        return False
    except: return False

def on_message(ws, message):
    data = json.loads(message)
    if "tick" in data:
        curr_p = data["tick"]["quote"]
        # Extract the 2nd digit after decimal
        curr_str = "{:.2f}".format(curr_p)
        curr_d2 = int(curr_str[-1]) 
        
        if state["last_d2"] is not None and state["is_running"]:
            # Trigger: Previous D2 was 9 AND Current D2 is 9
            if state["last_d2"] == 9 and curr_d2 == 9:
                ws.close() 
                state["last_d2"] = None 
                if not place_trade():
                    reset_and_stop("Execution failed.")
                return 

        state["last_d2"] = curr_d2

def start_tracking():
    if not state["is_running"]: return
    state["last_d2"] = None
    ws = websocket.WebSocketApp(
        "wss://blue.derivws.com/websockets/v3?app_id=16929",
        on_message=on_message,
        on_open=lambda ws: ws.send(json.dumps({"ticks": "R_100", "subscribe": 1}))
    )
    ws.run_forever()

# --- Telegram Commands ---
@bot.message_handler(commands=['start'])
def cmd_start(message):
    state["chat_id"] = message.chat.id
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰')
    bot.send_message(message.chat.id, "🎯 **R_100 D2=9 Strategy**\nLogic: Digit Match 9-9\nType: Digit Diff 0\nDuration: 1 Tick", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def set_acc(message):
    state["currency"] = "USD" if "Demo" in message.text else "tUSDT"
    bot.send_message(message.chat.id, "Enter Deriv API Token:", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(message, set_token)

def set_token(message):
    state["api_token"] = message.text.strip()
    bot.send_message(message.chat.id, "Enter Initial Stake:")
    bot.register_next_step_handler(message, set_stake)

def set_stake(message):
    try:
        state["initial_stake"] = state["current_stake"] = float(message.text)
        bot.send_message(message.chat.id, "Enter Target Profit ($):")
        bot.register_next_step_handler(message, set_tp)
    except: bot.send_message(message.chat.id, "Invalid number.")

def set_tp(message):
    try:
        state["tp"] = float(message.text)
        state["is_running"] = True
        stop_markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑')
        bot.send_message(message.chat.id, "🚀 Running. Waiting for 9-9 sequence...", reply_markup=stop_markup)
        threading.Thread(target=start_tracking).start()
    except: bot.send_message(message.chat.id, "Invalid number.")

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000)).start()
    bot.infinity_polling()
