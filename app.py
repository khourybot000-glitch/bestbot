import websocket, json, time, threading, multiprocessing, os
from flask import Flask
import telebot
from telebot import types
from datetime import datetime

app = Flask(__name__)
# التوكن الجديد الذي زودتني به
bot = telebot.TeleBot("8289154786:AAHQHyj5E3gQ1x0lJTvskJxuixgXFI8KX_c")

DB_FILE = "bot_state.json"

manager = multiprocessing.Manager()
state = manager.dict({
    "api_token": "", "initial_stake": 0.0, "tp": 0.0, 
    "currency": "USD", "is_running": False, "chat_id": None,
    "total_profit": 0.0, "win_count": 0, "loss_count": 0, "is_trading": False
})

def save_state():
    with open(DB_FILE, "w") as f:
        json.dump(dict(state), f)

def load_state():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                saved_data = json.load(f)
                state.update(saved_data)
            return True
        except: return False
    return False

def clear_db():
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)

@app.route('/')
def home():
    return "<h1>R_100 Safe Strategy (Barrier 1.0) Active</h1>"

def reset_and_stop(message_text):
    state["is_running"] = False
    state["is_trading"] = False
    clear_db()
    if state["chat_id"]:
        bot.send_message(state["chat_id"], f"🛑 **Bot Stopped:** {message_text}", reply_markup=types.ReplyKeyboardRemove())

def check_result(contract_id, token):
    try:
        # انتظار 18 ثانية لضمان انتهاء الـ 6 تيكات
        time.sleep(18)
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": token}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
        res = json.loads(ws.recv())
        ws.close()
        
        contract = res.get("proposal_open_contract", {})
        profit = float(contract.get("profit", 0))
        state["total_profit"] += profit 

        if profit > 0:
            state["win_count"] += 1
            status = "✅ WIN!"
            
            stats = (f"{status} **{profit:.2f}**\n━━━━━━━━━━━━━━\n"
                     f"🏆 Wins: {state['win_count']} | ❌ Losses: {state['loss_count']}\n"
                     f"💰 Net Profit: {state['total_profit']:.2f}\n━━━━━━━━━━━━━━")
            bot.send_message(state["chat_id"], stats)
            save_state()

            if state["total_profit"] >= state["tp"]:
                reset_and_stop("Target Profit Reached! 🎯")
            else:
                state["is_trading"] = False
        else:
            state["loss_count"] += 1
            # التوقف فوراً عند أول خسارة كما طلبت
            reset_and_stop(f"Loss detected ({profit:.2f}). Protection mode triggered: Bot Terminated.")

    except Exception as e:
        print(f"Check Error: {e}")
        state["is_trading"] = False

def execute_strategy():
    if state["is_trading"] or not state["is_running"]: return
    state["is_trading"] = True
    
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"authorize": state["api_token"]}))
        ws.recv()
        
        ws.send(json.dumps({"ticks_history": "R_100", "count": 30, "end": "latest", "style": "ticks"}))
        history = json.loads(ws.recv())
        prices = history.get("history", {}).get("prices", [])
        
        if len(prices) >= 30:
            diff = float(prices[-1]) - float(prices[0])
            contract_type, barrier = None, None
            
            # شرط الاتجاه (0.5) والحاجز (1.0)
            if diff >= 0.5:
                contract_type, barrier = "CALL", "-1.0"
            elif diff <= -0.5:
                contract_type, barrier = "PUT", "+1.0"

            if contract_type:
                prop_req = {
                    "proposal": 1, "amount": state["initial_stake"], "basis": "stake",
                    "contract_type": contract_type, "currency": state["currency"],
                    "duration": 6, "duration_unit": "t", "symbol": "R_100", "barrier": barrier
                }
                ws.send(json.dumps(prop_req))
                prop = json.loads(ws.recv()).get("proposal")
                
                if prop:
                    ws.send(json.dumps({"buy": prop["id"], "price": state["initial_stake"]}))
                    buy_res = json.loads(ws.recv())
                    if "buy" in buy_res:
                        bot.send_message(state["chat_id"], f"📥 Signal @ Sec 00 (Diff: {diff:.3f}). Entering {contract_type}...")
                        threading.Thread(target=check_result, args=(buy_res["buy"]["contract_id"], state["api_token"])).start()
                        ws.close()
                        return
        ws.close()
        state["is_trading"] = False
    except:
        state["is_trading"] = False

def scheduler_process(state_proxy):
    while True:
        try:
            if state_proxy["is_running"] and not state_proxy["is_trading"]:
                if datetime.now().second == 0:
                    execute_strategy()
                    time.sleep(2)
            time.sleep(0.5)
        except: time.sleep(1)

@bot.message_handler(commands=['start'])
def cmd_start(message):
    state["chat_id"] = message.chat.id
    if load_state() and state["is_running"]:
        bot.send_message(message.chat.id, "♻️ **Session Recovered!**\nWatching Sec 00. Bot will stop on first loss.")
    else:
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰')
        bot.send_message(message.chat.id, "🤖 **R_100 High-Safety Bot**\nSettings: Barrier 1.0 | 6 Ticks | Stop on Loss.", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ['Demo 🛠️', 'Live 💰'])
def set_acc(message):
    state["currency"] = "USD" if "Demo" in message.text else "tUSDT"
    bot.send_message(message.chat.id, "Enter API Token:")
    bot.register_next_step_handler(message, set_token)

def set_token(message):
    state["api_token"] = message.text.strip()
    bot.send_message(message.chat.id, "Enter Stake:")
    bot.register_next_step_handler(message, set_stake)

def set_stake(message):
    state["initial_stake"] = float(message.text)
    bot.send_message(message.chat.id, "Enter Target Profit ($):")
    bot.register_next_step_handler(message, set_tp)

def set_tp(message):
    state["tp"] = float(message.text)
    state["is_running"] = True
    save_state()
    bot.send_message(message.chat.id, "🚀 Bot Started Successfully.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def manual_stop(message):
    reset_and_stop("Manual stop.")

if __name__ == '__main__':
    load_state()
    p = multiprocessing.Process(target=scheduler_process, args=(state,))
    p.daemon = True
    p.start()
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000)).start()
    bot.infinity_polling()
