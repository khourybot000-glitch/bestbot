import websocket, json, time, datetime, multiprocessing, threading
from flask import Flask
import telebot
from telebot import types

app = Flask(__name__)
# Your Telegram Bot Token
bot = telebot.TeleBot("8444912981:AAEaU8YoqZD2ryb6x8dQBh0-aii0-fr4thY")

manager = multiprocessing.Manager()
shared_config = manager.dict({
    "api_token": "", "stake": 0.0, "tp": 0.0, "currency": "USD",
    "current_losses": 0, "win_count": 0, "loss_count": 0,
    "total_profit": 0.0, "is_running": False, "is_trading": False,
    "next_stake": 0.0, "chat_id": None
})

def reset_all_data():
    shared_config.update({
        "api_token": "", "stake": 0.0, "tp": 0.0, "current_losses": 0,
        "win_count": 0, "loss_count": 0, "total_profit": 0.0,
        "is_running": False, "is_trading": False, "next_stake": 0.0
    })

def quick_request(request_data):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps(request_data))
        response = json.loads(ws.recv())
        ws.close()
        return response
    except: return None

def place_trade_on_demand(action, amount, token, currency):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": token}))
        auth = json.loads(ws.recv())
        if "error" in auth: 
            ws.close()
            return None, auth["error"]["message"]
        
        ws.send(json.dumps({
            "buy": 1, "price": float(amount),
            "parameters": {
                "amount": float(amount), "basis": "stake",
                "contract_type": "CALL" if action == "call" else "PUT",
                "currency": currency, "duration": 1, "duration_unit": "m", "symbol": "R_100"
            }
        }))
        res = json.loads(ws.recv())
        ws.close()
        if "buy" in res: return res["buy"]["contract_id"], None
        return None, res.get("error", {}).get("message")
    except Exception as e: return None, str(e)

@bot.message_handler(commands=['start'])
def start(message):
    reset_all_data()
    shared_config["chat_id"] = message.chat.id
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add('Demo 🛠️', 'Live 💰')
    bot.send_message(message.chat.id, "🤖 **30-Tick Strategy Bot**\n\nSelect Account Type:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def step_account(message):
    shared_config["currency"] = "USD" if "Demo" in message.text else "tUSDT"
    msg = bot.send_message(message.chat.id, f"✅ Selected: {shared_config['currency']}\nEnter **API Token**:", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(msg, step_api)

def step_api(message):
    shared_config["api_token"] = message.text.strip()
    msg = bot.send_message(message.chat.id, "Enter **Initial Stake**:")
    bot.register_next_step_handler(msg, step_stake)

def step_stake(message):
    try:
        shared_config["stake"] = shared_config["next_stake"] = float(message.text)
        msg = bot.send_message(message.chat.id, "Enter **Take Profit (TP)**:")
        bot.register_next_step_handler(msg, step_tp)
    except: bot.send_message(message.chat.id, "Error! Send /start")

def step_tp(message):
    try:
        shared_config["tp"] = float(message.text)
        shared_config["is_running"] = True
        stop_markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        stop_markup.add('Stop 🛑')
        bot.send_message(message.chat.id, "⚡ **Bot Running!** Analyzing at :58 sec.", reply_markup=stop_markup)
    except: bot.send_message(message.chat.id, "Error! Send /start")

@bot.message_handler(func=lambda m: m.text == 'Stop 🛑')
def manual_stop(message):
    reset_all_data()
    bot.send_message(message.chat.id, "🛑 Bot Stopped & Data Wiped.", reply_markup=types.ReplyKeyboardRemove())

def trading_engine(config):
    last_min = -1
    while True:
        if config["is_running"]:
            now = datetime.datetime.now()
            if now.second == 58 and now.minute != last_min:
                last_min = now.minute 
                
                # Connection 1: Fetch Ticks
                data = quick_request({"ticks_history": "R_100", "count": 30, "end": "latest", "style": "ticks"})
                
                if data and "history" in data:
                    ticks = data["history"]["prices"]
                    t1, t15, t30 = ticks[0], ticks[14], ticks[29]
                    
                    action = None
                    if t15 > t1 and t30 > t15: action = "call"
                    elif t15 < t1 and t30 < t15: action = "put"
                    
                    if action:
                        # Connection 2: Place Trade
                        cid, err = place_trade_on_demand(action, config["next_stake"], config["api_token"], config["currency"])
                        if cid:
                            bot.send_message(config["chat_id"], f"📥 **Entered {action.upper()}**\nWaiting 70s for result...")
                            
                            time.sleep(70) # Wait 70 seconds
                            
                            # Connection 3: Check Result
                            try:
                                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
                                ws.send(json.dumps({"authorize": config["api_token"]}))
                                ws.recv()
                                ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": cid}))
                                res = json.loads(ws.recv())
                                ws.close()
                                
                                profit = float(res.get("proposal_open_contract", {}).get("profit", 0))
                                config["total_profit"] += profit
                                
                                if profit > 0:
                                    config["win_count"] += 1
                                    config["current_losses"] = 0
                                    config["next_stake"] = config["stake"]
                                    res_txt = "✅ WIN"
                                else:
                                    config["loss_count"] += 1
                                    config["current_losses"] += 1
                                    config["next_stake"] = round(config["next_stake"] * 2.2, 2)
                                    res_txt = "❌ LOSS"
                                
                                stats_msg = (
                                    f"Result: {res_txt} ({profit})\n"
                                    f"Total Profit: {round(config['total_profit'], 2)}\n"
                                    f"Wins: {config['win_count']} | Losses: {config['loss_count']}\n"
                                    f"Consecutive Losses: {config['current_losses']}/5"
                                )
                                bot.send_message(config["chat_id"], stats_msg)
                                
                                if config["current_losses"] >= 5 or config["total_profit"] >= config["tp"]:
                                    reason = "Max Losses" if config["current_losses"] >= 5 else "Take Profit reached"
                                    bot.send_message(config["chat_id"], f"🏁 **Session Finished!**\nReason: {reason}\nData cleared.")
                                    reset_all_data()
                            except: pass
        time.sleep(0.5)

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000)).start()
    bot.remove_webhook()
    threading.Thread(target=bot.infinity_polling).start()
    trading_engine(shared_config)
