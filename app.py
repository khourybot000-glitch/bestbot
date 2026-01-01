import websocket, json, time, datetime, multiprocessing, math, threading
from flask import Flask
import telebot

# --- الإعدادات ---
app = Flask(__name__)
# استبدل TOKEN_HERE بتوكن بوت التلغرام من BotFather
bot = telebot.TeleBot("7964867752:AAGbokJYY-qrHWp8tXP_vfxEHxf1fh-0u6o")

manager = multiprocessing.Manager()
shared_config = manager.dict({
    "api_token": "",
    "stake": 0.0,
    "tp": 0.0,
    "current_losses": 0,
    "win_count": 0,
    "loss_count": 0,
    "total_profit": 0.0,
    "is_running": False,
    "next_stake": 0.0,
    "chat_id": None
})

# --- دالات المساعدة ---
def round_val(val):
    return math.floor(val * 100) / 100

def get_connection_forever(timeout=15):
    """محاولة الاتصال المستمر بالسيرفر"""
    while True:
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=timeout)
            return ws
        except:
            time.sleep(5)

def place_trade(action, amount, token):
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
                "currency": "USD",
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
    shared_config["chat_id"] = message.chat.id
    shared_config["is_running"] = False
    msg = bot.send_message(message.chat.id, "Welcome with Khoury Bot 🤖\nPlease enter your **Deriv API Token**:")
    bot.register_next_step_handler(msg, step_api)

def step_api(message):
    shared_config["api_token"] = message.text.strip()
    msg = bot.send_message(message.chat.id, "✅ API Saved. Enter **Initial Stake**:")
    bot.register_next_step_handler(msg, step_stake)

def step_stake(message):
    try:
        s = float(message.text)
        shared_config["stake"] = shared_config["next_stake"] = round_val(s)
        msg = bot.send_message(message.chat.id, "✅ Stake Saved. Enter **Take Profit (TP)**:")
        bot.register_next_step_handler(msg, step_tp)
    except:
        msg = bot.send_message(message.chat.id, "❌ Error. Enter Stake again:")
        bot.register_next_step_handler(message, step_stake)

def step_tp(message):
    try:
        shared_config["tp"] = float(message.text)
        shared_config["is_running"] = True
        shared_config["win_count"], shared_config["loss_count"] = 0, 0
        shared_config["current_losses"], shared_config["total_profit"] = 0, 0.0
        bot.send_message(message.chat.id, "🚀 **Bot Started on R_100!**")
    except:
        msg = bot.send_message(message.chat.id, "❌ Error. Enter TP again:")
        bot.register_next_step_handler(message, step_tp)

# --- محرك التداول ---
def trading_engine(config):
    last_minute = -1
    while True:
        if config["is_running"]:
            now = datetime.datetime.now()
            if now.second == 57 and now.minute != last_minute:
                ws = get_connection_forever()
                try:
                    ws.send(json.dumps({"ticks_history": "R_100", "count": 90, "end": "latest", "style": "ticks"}))
                    ticks = json.loads(ws.recv())["history"]["prices"]
                    ws.close()
                    c = []
                    for i in range(0, 90, 30):
                        s = ticks[i:i+30]
                        c.append({"open": s[0], "close": s[-1], "color": "G" if s[-1] > s[0] else "R"})
                    
                    action = None
                    if c[0]["color"] == "R" and c[1]["color"] == "G" and c[2]["color"] == "R" and c[2]["close"] < c[1]["open"]:
                        action = "put"
                    elif c[0]["color"] == "G" and c[1]["color"] == "R" and c[2]["color"] == "G" and c[2]["close"] > c[1]["open"]:
                        action = "call"

                    if action:
                        cid, err = place_trade(action, config["next_stake"], config["api_token"])
                        if cid:
                            last_minute = now.minute
                            bot.send_message(config["chat_id"], f"📥 **New Trade**\nAction: {action.upper()}\nStake: ${config['next_stake']}")
                            time.sleep(305)
                            status, profit = check_result(cid, config["api_token"])
                            config["total_profit"] = round_val(config["total_profit"] + profit)
                            
                            if profit > 0:
                                config["win_count"] += 1
                                config["current_losses"] = 0
                                config["next_stake"] = config["stake"]
                                res_emoji = "✅ **WIN**"
                            else:
                                config["loss_count"] += 1
                                config["current_losses"] += 1
                                config["next_stake"] = round_val(config["next_stake"] * 2.2)
                                res_emoji = "❌ **LOSS**"

                            profit_text = f"+{config['total_profit']}" if config['total_profit'] > 0 else f"{config['total_profit']}"
                            msg = f"{res_emoji}\nWin: {config['win_count']} | Loss: {config['loss_count']}\nProfit: {profit_text}$"
                            
                            if config["current_losses"] >= 5:
                                config["is_running"] = False
                                msg += "\n⚠️ Stopped: 5 consecutive losses."
                            elif config["total_profit"] >= config["tp"]:
                                config["is_running"] = False
                                msg += "\n🎯 Target Reached! Bot Stopped."
                            
                            bot.send_message(config["chat_id"], msg)
                except: pass
        time.sleep(0.5)

@app.route('/')
def home(): return "Bot is Active", 200

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000)).start()
    threading.Thread(target=bot.infinity_polling).start()
    trading_engine(shared_config)
