import websocket, json, time, threading
from flask import Flask
import telebot
from telebot import types

app = Flask(__name__)
# Replace with your actual Telegram Bot Token
bot = telebot.TeleBot("7883993519:AAH9iiUPN5QzzPun5TP1x__uQ4OIIYQJzAo")

state = {
    "api_token": "", "initial_stake": 0.0, "current_stake": 0.0, "tp": 0.0, 
    "currency": "USD", "is_running": False, "chat_id": None, 
    "tick_history": [], 
    "total_profit": 0.0, "win_count": 0, "loss_count": 0, "loss_streak": 0
}

@app.route('/')
def home():
    return "<h1>R_100 Power Strategy Bot is Running</h1>"

def reset_and_stop(message_text):
    state.update({
        "api_token": "", "is_running": False, "tick_history": [], 
        "loss_streak": 0, "win_count": 0, "loss_count": 0, "total_profit": 0.0,
        "initial_stake": 0.0, "current_stake": 0.0
    })
    if state["chat_id"]:
        bot.send_message(state["chat_id"], f"🛑 **Bot Stopped:** {message_text}\n(All data has been wiped)", reply_markup=types.ReplyKeyboardRemove())

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def manual_stop(message):
    reset_and_stop("Manual stop requested by user.")

def check_result(contract_id):
    try:
        time.sleep(16) # Wait for contract to settle (5 ticks + buffer)
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
                reset_and_stop("Consecutive losses (2). Safety shutdown to protect balance.")
            else:
                state["current_stake"] = round(state["initial_stake"] * 24, 2)
                bot.send_message(state["chat_id"], f"❌ Loss! Next Martingale x24 Stake: **{state['current_stake']}**")
                threading.Thread(target=start_tracking).start()
        else:
            state["win_count"] += 1
            state["total_profit"] += profit
            state["loss_streak"] = 0
            state["current_stake"] = state["initial_stake"]
            
            stats = (f"✅ **WIN! +{round(profit, 2)}**\n"
                     f"━━━━━━━━━━━━━━\n"
                     f"🏆 Wins: {state['win_count']} | ❌ Losses: {state['loss_count']}\n"
                     f"💰 Total Profit: {round(state['total_profit'], 2)}\n"
                     f"━━━━━━━━━━━━━━")
            bot.send_message(state["chat_id"], stats)
            
            if state["total_profit"] >= state["tp"]:
                reset_and_stop(f"🎯 Take Profit Target Reached: ${round(state['total_profit'], 2)}")
            else:
                threading.Thread(target=start_tracking).start()
    except Exception as e:
        reset_and_stop(f"Connection error while checking results: {str(e)}")

def place_trade(action):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": state["api_token"]}))
        ws.recv()
        
        # Barrier logic: Higher (+0.6) for Put / Lower (-0.6) for Call
        barrier = "-0.6" if action == "call" else "+0.6"
        
        trade_req = {
            "buy": 1, "price": float(state["current_stake"]),
            "parameters": {
                "amount": float(state["current_stake"]), "basis": "stake",
                "contract_type": "CALL" if action == "call" else "PUT",
                "currency": state["currency"], "duration": 5, "duration_unit": "t",
                "symbol": "R_100", "barrier": barrier
            }
        }
        ws.send(json.dumps(trade_req))
        res = json.loads(ws.recv())
        ws.close()
        
        if "buy" in res:
            bot.send_message(state["chat_id"], f"📥 {action.upper()} order executed at {state['current_stake']}")
            threading.Thread(target=check_result, args=(res["buy"]["contract_id"],)).start()
            return True
        return False
    except: return False

def on_message(ws, message):
    data = json.loads(message)
    if "tick" in data:
        curr_p = data["tick"]["quote"]
        state["tick_history"].append(curr_p)
        
        if len(state["tick_history"]) > 3:
            state["tick_history"].pop(0)
            
        if len(state["tick_history"]) == 3 and state["is_running"]:
            t1, t2, t3 = state["tick_history"]
            
            # Gap Power Filter: Gap between T3 and T2 must be > 0.1
            diff = abs(t3 - t2)
            action = None
            
            if diff > 0.1:
                # PUT Condition: T2 is a peak
                if t2 > t1 and t2 > t3:
                    action = "put"
                # CALL Condition: T2 is a valley
                elif t2 < t1 and t2 < t3:
                    action = "call"
            
            if action:
                ws.close() # Close and reset for fresh analysis after trade
                state["tick_history"] = [] 
                if not place_trade(action):
                    reset_and_stop("Execution failed. Stopping for safety.")
                    
def start_tracking():
    if not state["is_running"]: return
    state["tick_history"] = []
    ws = websocket.WebSocketApp(
        "wss://blue.derivws.com/websockets/v3?app_id=16929",
        on_message=on_message,
        on_open=lambda ws: ws.send(json.dumps({"ticks": "R_100", "subscribe": 1}))
    )
    ws.run_forever()

# --- Telegram Bot Commands ---

@bot.message_handler(commands=['start'])
def cmd_start(message):
    state["chat_id"] = message.chat.id
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('Demo 🛠️', 'Live 💰')
    bot.send_message(message.chat.id, "🎯 **R_100 Power Logic Bot**\nStrategy: 3-Tick Pivot (T1-T2-T3)\nFilter: Gap > 0.1 | Martingale: x24", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def set_acc(message):
    state["currency"] = "USD" if "Demo" in message.text else "tUSDT"
    bot.send_message(message.chat.id, "Enter your API Token:", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(message, set_token)

def set_token(message):
    state["api_token"] = message.text.strip()
    bot.send_message(message.chat.id, "Enter Initial Stake Amount:")
    bot.register_next_step_handler(message, set_stake)

def set_stake(message):
    try:
        state["initial_stake"] = state["current_stake"] = float(message.text)
        bot.send_message(message.chat.id, "Enter Take Profit Target ($):")
        bot.register_next_step_handler(message, set_tp)
    except: bot.send_message(message.chat.id, "Invalid number. Start again.")

def set_tp(message):
    try:
        state["tp"] = float(message.text)
        state["is_running"] = True
        stop_markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        stop_markup.add('STOP 🛑')
        bot.send_message(message.chat.id, f"🚀 Hunting started on R_100...\nTarget: ${state['tp']} | Power Filter: > 0.1", reply_markup=stop_markup)
        threading.Thread(target=start_tracking).start()
    except: bot.send_message(message.chat.id, "Invalid number. Start again.")

if __name__ == '__main__':
    # Start Flask on port 10000 for hosting (e.g., Render)
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000)).start()
    bot.infinity_polling()
