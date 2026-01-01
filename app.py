import websocket, json, time, datetime, multiprocessing, math, threading
from flask import Flask
import telebot
from telebot import types

# --- الإعدادات ---
app = Flask(__name__)
# توكن التلغرام الخاص بك
bot = telebot.TeleBot("8537803087:AAGstLM6g2IA6JbrGi7YRXMzJnjjXXdaZ5E")

manager = multiprocessing.Manager()
shared_config = manager.dict({
    "api_token": "",
    "stake": 0.0,
    "tp": 0.0,
    "currency": "USD",
    "current_losses": 0,
    "win_count": 0,
    "loss_count": 0,
    "total_profit": 0.0,
    "is_running": False,
    "is_trading": False,
    "next_stake": 0.0,
    "chat_id": None
})

def reset_all_data():
    shared_config["api_token"] = ""
    shared_config["stake"] = 0.0
    shared_config["tp"] = 0.0
    shared_config["current_losses"] = 0
    shared_config["win_count"] = 0
    shared_config["loss_count"] = 0
    shared_config["total_profit"] = 0.0
    shared_config["is_running"] = False
    shared_config["is_trading"] = False
    shared_config["next_stake"] = 0.0

# --- دالات الاتصال ---

def round_val(val):
    return math.floor(val * 100) / 100

def get_connection_forever():
    while True:
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
            return ws
        except:
            time.sleep(5)

def place_trade(action, amount, token, currency):
    ws = get_connection_forever()
    try:
        ws.send(json.dumps({"authorize": token}))
        auth = json.loads(ws.recv())
        if "error" in auth: 
            ws.close()
            return None, auth["error"]["message"]

        ws.send(json.dumps({
            "buy": 1,
            "price": round_val(amount),
            "parameters": {
                "amount": round_val(amount),
                "basis": "stake",
                "contract_type": "CALL" if action == "call" else "PUT",
                "currency": currency,
                "duration": 5,
                "duration_unit": "m",
                "symbol": "R_100"
            }
        }))
        res = json.loads(ws.recv())
        ws.close()
        if "error" in res: return None, res["error"]["message"]
        return res["buy"]["contract_id"], None
    except:
        return None, "System Error"

def check_result(contract_id, token):
    ws = get_connection_forever()
    try:
        ws.send(json.dumps({"authorize": token}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
        res = json.loads(ws.recv())
        ws.close()
        data = res.get("proposal_open_contract", {})
        return data.get("status"), float(data.get("profit", 0))
    except:
        return "error", 0

# --- أوامر التلغرام ---

@bot.message_handler(commands=['start'])
def start(message):
    reset_all_data()
    shared_config["chat_id"] = message.chat.id
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add('Demo 🛠️', 'Live 💰')
    msg = bot.send_message(message.chat.id, "Welcome with Khoury Bot 🤖\nChoose account type:", reply_markup=markup)
    bot.register_next_step_handler(msg, step_account_type)

def step_account_type(message):
    if "Demo" in message.text:
        shared_config["currency"] = "USD"
    else:
        shared_config["currency"] = "tUSDT"
    msg = bot.send_message(message.chat.id, f"✅ Using {shared_config['currency']}\nEnter **API Token**:", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(msg, step_api)

def step_api(message):
    shared_config["api_token"] = message.text.strip()
    msg = bot.send_message(message.chat.id, "✅ Token Saved. Enter **Initial Stake**:")
    bot.register_next_step_handler(msg, step_stake)

def step_stake(message):
    try:
        s = float(message.text)
        shared_config["stake"] = shared_config["next_stake"] = round_val(s)
        msg = bot.send_message(message.chat.id, "✅ Stake Saved. Enter **Take Profit (TP)**:")
        bot.register_next_step_handler(msg, step_tp)
    except:
        bot.send_message(message.chat.id, "❌ Error. Try /start again.")

def step_tp(message):
    try:
        shared_config["tp"] = float(message.text)
        shared_config["is_running"] = True
        
        # إنشاء زر الإيقاف اليدوي
        stop_markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        stop_markup.add('Stop 🛑')
        
        bot.send_message(message.chat.id, f"🚀 **Bot Running!**\nUse the button below to stop manually.", reply_markup=stop_markup)
    except:
        bot.send_message(message.chat.id, "❌ Error. Try /start again.")

# التعامل مع زر الإيقاف اليدوي
@bot.message_handler(func=lambda message: message.text == 'Stop 🛑')
def manual_stop(message):
    reset_all_data()
    bot.send_message(message.chat.id, "🛑 **Manually Stopped!**\nAll data and stats have been wiped.", reply_markup=types.ReplyKeyboardRemove())

# --- محرك التداول ---

def trading_engine(config):
    last_minute = -1
    while True:
        if config["is_running"]:
            now = datetime.datetime.now()
            if now.second == 57 and now.minute != last_minute and not config["is_trading"]:
                config["is_trading"] = True
                ws = get_connection_forever()
                try:
                    ws.send(json.dumps({"ticks_history": "R_100", "count": 90, "end": "latest", "style": "ticks"}))
                    ticks = json.loads(ws.recv())["history"]["prices"]
                    ws.close()
                    c = [{"open": ticks[i], "close": ticks[i+29], "color": "G" if ticks[i+29] > ticks[i] else "R"} for i in [0, 30, 60]]
                    
                    action = None
                    if c[0]["color"] == "R" and c[1]["color"] == "G" and c[2]["color"] == "R" and c[2]["close"] < c[1]["open"]:
                        action = "put"
                    elif c[0]["color"] == "G" and c[1]["color"] == "R" and c[2]["color"] == "G" and c[2]["close"] > c[1]["open"]:
                        action = "call"

                    if action:
                        cid, err = place_trade(action, config["next_stake"], config["api_token"], config["currency"])
                        if cid:
                            last_minute = now.minute
                            bot.send_message(config["chat_id"], f"📥 **Trade Placed**: {action.upper()}\nStake: {config['next_stake']} {config['currency']}")
                            time.sleep(305)
                            status, profit_val = check_result(cid, config["api_token"])
                            config["total_profit"] = round_val(config["total_profit"] + profit_val)
                            
                            if profit_val > 0:
                                config["win_count"] += 1
                                config["current_losses"] = 0
                                config["next_stake"] = config["stake"]
                                res_emoji = "✅ **WIN**"
                            else:
                                config["loss_count"] += 1
                                config["current_losses"] += 1
                                config["next_stake"] = round_val(config["next_stake"] * 2.2)
                                res_emoji = "❌ **LOSS**"

                            msg = (f"{res_emoji}\nLast: {profit_val} {config['currency']}\n"
                                   f"Total: {round_val(config['total_profit'])} {config['currency']}\n"
                                   f"W: {config['win_count']} | L: {config['loss_count']}")
                            
                            should_reset = False
                            if config["current_losses"] >= 5:
                                msg += "\n⚠️ Stopped: 5 losses. Data wiped."
                                should_reset = True
                            elif config["total_profit"] >= config["tp"]:
                                msg += "\n🎯 TP Reached! Data wiped."
                                should_reset = True
                            
                            bot.send_message(config["chat_id"], msg)
                            if should_reset: reset_all_data()
                        
                        config["is_trading"] = False
                    else:
                        config["is_trading"] = False
                except: config["is_trading"] = False
        time.sleep(0.5)

@app.route('/')
def home(): return "Active", 200

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000)).start()
    threading.Thread(target=bot.infinity_polling).start()
    trading_engine(shared_config)
