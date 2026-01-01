import websocket, json, time, datetime, multiprocessing, threading
from flask import Flask
import telebot
from telebot import types

app = Flask(__name__)
# IMPORTANT: Put your VALID Telegram Bot Token here
bot = telebot.TeleBot("8507813174:AAGxJpaSuq1UuedYHAZU9yBJAoFS-g1PAOU")

manager = multiprocessing.Manager()
shared_config = manager.dict({
    "api_token": "", "stake": 0.0, "tp": 0.0, "currency": "USD",
    "current_losses": 0, "win_count": 0, "loss_count": 0,
    "total_profit": 0.0, "is_running": False, "next_stake": 0.0, 
    "chat_id": None
})

# --- Flask Route for UptimeRobot ---
@app.route('/')
def home():
    return "<h1>Bot is Online</h1><p>Strategy: 60-Ticks Precise (30-15-15)</p>"

def reset_all_data():
    shared_config.update({
        "api_token": "", "stake": 0.0, "tp": 0.0, "current_losses": 0,
        "win_count": 0, "loss_count": 0, "total_profit": 0.0,
        "is_running": False, "next_stake": 0.0
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
    except: return None, "Network Error"

@bot.message_handler(commands=['start'])
def start(message):
    reset_all_data()
    shared_config["chat_id"] = message.chat.id
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add('Demo 🛠️', 'Live 💰')
    bot.send_message(message.chat.id, "🤖 **Momentum Reversal Bot**\nIndexing: 0-29-44-59\nStrategy: 30-15-15", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def step_account(message):
    shared_config["currency"] = "USD" if "Demo" in message.text else "tUSDT"
    msg = bot.send_message(message.chat.id, f"✅ {shared_config['currency']} selected.\nEnter **API Token**:", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(msg, step_api)

def step_api(message):
    shared_config["api_token"] = message.text.strip()
    msg = bot.send_message(message.chat.id, "Enter Initial Stake:")
    bot.register_next_step_handler(msg, step_stake)

def step_stake(message):
    try:
        shared_config["stake"] = shared_config["next_stake"] = float(message.text)
        msg = bot.send_message(message.chat.id, "Enter Take Profit Goal:")
        bot.register_next_step_handler(msg, step_tp)
    except: bot.send_message(message.chat.id, "Error! Start /start")

def step_tp(message):
    try:
        shared_config["tp"] = float(message.text)
        shared_config["is_running"] = True
        stop_markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        stop_markup.add('Stop 🛑')
        bot.send_message(message.chat.id, "⚡ **Bot Running!** Monitoring trends every minute.", reply_markup=stop_markup)
    except: bot.send_message(message.chat.id, "Error! Start /start")

@bot.message_handler(func=lambda m: m.text == 'Stop 🛑')
def manual_stop(message):
    reset_all_data()
    bot.send_message(message.chat.id, "🛑 Bot Stopped & Data Wiped.", reply_markup=types.ReplyKeyboardRemove())

def trading_engine(config):
    last_min = -1
    MIN_MOVE = 0.2

    while True:
        if config["is_running"]:
            now = datetime.datetime.now()
            # Analysis at second 58
            if now.second == 58 and now.minute != last_min:
                last_min = now.minute
                
                # Fetch 60 ticks (Indexes 0 to 59)
                data = quick_request({"ticks_history": "R_100", "count": 60, "end": "latest", "style": "ticks"})
                
                if data and "history" in data:
                    p = data["history"]["prices"]
                    
                    # PRECISE MAPPING:
                    # C1 (30 ticks): index 0 (open) to index 29 (close)
                    # C2 (15 ticks): index 29 (open) to index 44 (close)
                    # C3 (15 ticks): index 44 (open) to index 59 (close)
                    
                    move_c1 = p[29] - p[0]
                    move_c2 = p[44] - p[29]
                    move_c3 = p[59] - p[44]
                    
                    action = None
                    # CALL: C1 Bearish, C2 Bullish > 0.2, C3 Bullish > 0.2
                    if move_c1 < 0 and move_c2 > MIN_MOVE and move_c3 > MIN_MOVE:
                        action = "call"
                    # PUT: C1 Bullish, C2 Bearish < -0.2, C3 Bearish < -0.2
                    elif move_c1 > 0 and move_c2 < -MIN_MOVE and move_c3 < -MIN_MOVE:
                        action = "put"
                    
                    if action:
                        cid, err = place_trade_on_demand(action, config["next_stake"], config["api_token"], config["currency"])
                        if cid:
                            bot.send_message(config["chat_id"], f"📥 **{action.upper()}** (Pattern Detected)\nC1: {round(move_c1,3)} | C2: {round(move_c2,3)} | C3: {round(move_c3,3)}")
                            
                            # Wait 70s for result
                            time.sleep(70)
                            
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
                                
                                stats = (f"Result: {res_txt} ({profit})\n"
                                         f"Wins: {config['win_count']} | Losses: {config['loss_count']}\n"
                                         f"Total Profit: {round(config['total_profit'], 2)}\n"
                                         f"Loss Streak: {config['current_losses']}/5")
                                bot.send_message(config["chat_id"], stats)
                                
                                if config["current_losses"] >= 5 or config["total_profit"] >= config["tp"]:
                                    reason = "Max Loss" if config["current_losses"] >= 5 else "TP Reached"
                                    bot.send_message(config["chat_id"], f"🏁 **Session Finished!**\nReason: {reason}\nData wiped.")
                                    reset_all_data()
                            except: pass
        time.sleep(0.1)

if __name__ == '__main__':
    # Start Flask for UptimeRobot
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000)).start()
    bot.remove_webhook()
    # Start Telegram Polling
    threading.Thread(target=bot.infinity_polling).start()
    # Start Trading Engine
    trading_engine(shared_config)
