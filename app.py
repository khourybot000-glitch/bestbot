import websocket, json, time, threading
from flask import Flask
import telebot
from telebot import types
from datetime import datetime

app = Flask(__name__)
# التوكن الجديد الذي أرسلته
bot = telebot.TeleBot("8585114777:AAE6mJQrvtV0nedYofEsvvEWXITt3byWwUI")

state = {
    "api_token": "", "initial_stake": 0.0, "current_stake": 0.0, "tp": 0.0, 
    "currency": "USD", "is_running": False, "chat_id": None,
    "total_profit": 0.0, "win_count": 0, "loss_count": 0, "is_trading": False
}

@app.route('/')
def home():
    return "<h1>R_100 (Diff < 0.2) Bot Active</h1>"

def reset_and_stop(message_text):
    # تصفير ومسح جميع البيانات لضمان التوقف النهائي
    state.update({
        "api_token": "", "is_running": False, "is_trading": False,
        "total_profit": 0.0, "win_count": 0, "loss_count": 0
    })
    if state["chat_id"]:
        bot.send_message(state["chat_id"], f"🛑 **Bot Stopped:** {message_text}", reply_markup=types.ReplyKeyboardRemove())

def check_result(contract_id):
    try:
        time.sleep(16) # انتظار 16 ثانية للنتيجة
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": state["api_token"]}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
        res = json.loads(ws.recv())
        ws.close()
        
        contract = res.get("proposal_open_contract", {})
        profit = float(contract.get("profit", 0))
        state["total_profit"] += profit 

        status_icon = "✅ WIN!" if profit >= 0 else "❌ LOSS!"
        msg = f"{status_icon} **{profit:.2f}**"
        
        # تحديث عداد الفوز/الخسارة للعرض فقط
        current_wins = state['win_count'] + (1 if profit >= 0 else 0)
        current_losses = state['loss_count'] + (0 if profit >= 0 else 1)

        stats = (f"{msg}\n━━━━━━━━━━━━━━\n"
                 f"🏆 Wins: {current_wins} | ❌ Losses: {current_losses}\n"
                 f"💰 Net Profit: {state['total_profit']:.2f}\n━━━━━━━━━━━━━━")
        
        bot.send_message(state["chat_id"], stats)

        if profit < 0:
            # التوقف ومسح البيانات فوراً عند أول خسارة
            reset_and_stop("Terminated after 1 loss as requested.")
            return
        else:
            state["win_count"] += 1
            state["is_trading"] = False
            if state["total_profit"] >= state["tp"]:
                reset_and_stop("Target Profit Reached! 🎯")
                
    except Exception:
        time.sleep(5)
        threading.Thread(target=check_result, args=(contract_id,)).start()

def execute_strategy():
    if state["is_trading"] or not state["is_running"]: return
    state["is_trading"] = True
    
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"authorize": state["api_token"]}))
        ws.recv()
        
        ws.send(json.dumps({"ticks_history": "R_100", "count": 5, "end": "latest", "style": "ticks"}))
        history = json.loads(ws.recv())
        prices = history.get("history", {}).get("prices", [])
        
        if len(prices) < 5:
            state["is_trading"] = False
            ws.close()
            return

        diff = float(prices[-1]) - float(prices[0])
        abs_diff = abs(diff) 
        contract_type, barrier = None, None

        # شرطك الجديد: الدخول فقط إذا كان الفرق أصغر من 0.2
        if 0 < abs_diff < 0.2:
            if diff > 0:
                contract_type, barrier = "CALL", "-0.9"
            else:
                contract_type, barrier = "PUT", "+0.9"

        if contract_type:
            proposal_req = {
                "proposal": 1, "amount": state["initial_stake"], "basis": "stake",
                "contract_type": contract_type, "currency": state["currency"],
                "duration": 5, "duration_unit": "t", "symbol": "R_100", "barrier": barrier
            }
            ws.send(json.dumps(proposal_req))
            prop_res = json.loads(ws.recv())
            
            if "proposal" in prop_res:
                ws.send(json.dumps({"buy": prop_res["proposal"]["id"], "price": state["initial_stake"]}))
                buy_res = json.loads(ws.recv())
                if "buy" in buy_res:
                    bot.send_message(state["chat_id"], f"📥 Signal (Diff: {diff:.3f} < 0.2). Entering {contract_type}...")
                    threading.Thread(target=check_result, args=(buy_res["buy"]["contract_id"],)).start()
                else: state["is_trading"] = False
            else: state["is_trading"] = False
        else:
            state["is_trading"] = False # الفرق أكبر من 0.2، لا دخول
            
        ws.close()
    except Exception:
        state["is_trading"] = False

def scheduler_loop():
    while state["is_running"]:
        if datetime.now().second == 10:
            threading.Thread(target=execute_strategy).start()
            time.sleep(2) 
        time.sleep(0.5)

# --- Telegram Bot Interface ---
@bot.message_handler(commands=['start'])
def cmd_start(message):
    state["chat_id"] = message.chat.id
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('Demo 🛠️', 'Live 💰')
    bot.send_message(message.chat.id, "🤖 **R_100 (Small Movement) Strategy**\n- Analyze: Second 10\n- Condition: Diff < 0.2\n- 1 Loss = Immediate Termination", reply_markup=markup)

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
    try:
        state["initial_stake"] = float(message.text)
        bot.send_message(message.chat.id, "Enter TP ($):")
        bot.register_next_step_handler(message, set_tp)
    except: bot.send_message(message.chat.id, "Invalid number.")

def set_tp(message):
    try:
        state["tp"] = float(message.text)
        state["is_running"] = True
        state["is_trading"] = False
        bot.send_message(message.chat.id, "🚀 Running. Analysis at Sec 10.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
        threading.Thread(target=scheduler_loop).start()
    except: bot.send_message(message.chat.id, "Invalid number.")

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def manual_stop(message):
    reset_and_stop("Manual stop requested.")

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000)).start()
    bot.infinity_polling()
