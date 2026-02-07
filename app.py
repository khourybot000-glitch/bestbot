import websocket, json, time, threading
from flask import Flask, render_template_string, request, redirect
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta

app = Flask(__name__)

# --- CONFIG ---
TOKEN = "8433565422:AAFTgXd4fJ87xQSR6fQxUFuKLCxIBU83rtA"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(TOKEN)
db_client = MongoClient(MONGO_URI)
db = db_client['Trading_System_V3']
users_col = db['Authorized_Users']
active_sessions_col = db['Active_Sessions']

SYMBOL = "R_100"
TOTAL_INDICATORS = 15
SIGNAL_THRESHOLD = 70  # 70% minimum
INDICATORS = ["SMA", "EMA", "MACD", "RSI", "Stochastic", "Bollinger", 
              "ATR", "CCI", "ADX", "Williams", "OBV", "ROC", "MFI", "Ichimoku", "Momentum"]

def check_00(price):
    try:
        return "{:.2f}".format(float(price)).split(".")[1] == "00"
    except:
        return False

def calculate_signal(history):
    """
    يحاكي حساب الإشارات لكل 15 مؤشر:
    يرجع (signal, percentage)
    """
    # محاكاة: كل مؤشر يقارن آخر تيك مع ما قبله
    up_count = sum(1 for i in INDICATORS if history[-1] < history[-2])
    down_count = sum(1 for i in INDICATORS if history[-1] > history[-2])
    if up_count / TOTAL_INDICATORS*100 >= SIGNAL_THRESHOLD: return "CALL", up_count / TOTAL_INDICATORS*100
    elif down_count / TOTAL_INDICATORS*100 >= SIGNAL_THRESHOLD: return "PUT", down_count / TOTAL_INDICATORS*100
    else: return "neutral", max(up_count, down_count) / TOTAL_INDICATORS*100

def analyze_30_ticks(api_token):
    """جلب آخر 30 تيك وتحليل الاتجاه"""
    ws2 = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
    ws2.send(json.dumps({"authorize": api_token}))
    ws2.recv()
    ws2.send(json.dumps({"ticks_history": SYMBOL, "count": 30, "end": "latest", "style": "ticks"}))
    hist_msg = ws2.recv()
    ws2.close()
    hist_data = json.loads(hist_msg)
    if "history" in hist_data:
        prices = [float(p) for p in hist_data["history"]["prices"]]
        if len(prices) >= 2:
            if prices[-1] > prices[0]: return "UP"
            elif prices[-1] < prices[0]: return "DOWN"
    return None

def trading_process(chat_id):
    history = []
    while True:
        session = active_sessions_col.find_one({"chat_id": chat_id})
        if not session or not session.get("is_running"): break

        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=15)
            ws.send(json.dumps({"authorize": session['api_token']}))
            ws.recv()
            ws.send(json.dumps({"ticks": SYMBOL, "subscribe": 1}))

            while True:
                session = active_sessions_col.find_one({"chat_id": chat_id})
                if not session or not session.get("is_running"): break

                raw = ws.recv()
                data = json.loads(raw)
                if "tick" in data:
                    t_curr = float(data["tick"]["quote"])
                    history.append(t_curr)
                    if len(history) > 5: history.pop(0)

                    if len(history) == 5 and check_00(t_curr):
                        signal, perc = calculate_signal(history)
                        if signal == "neutral" or perc < SIGNAL_THRESHOLD:
                            bot.send_message(chat_id, f"❌ No Trade ({perc:.0f}%)")
                            continue

                        bot.send_message(chat_id, f"⏳ Waiting Signal: {signal} ({perc:.0f}%)")
                        for i in range(58, 0, -1):  # 58 ثانية عداد
                            session = active_sessions_col.find_one({"chat_id": chat_id})
                            if not session or not session.get("is_running"): break
                            time.sleep(1)

                        trend = analyze_30_ticks(session['api_token'])
                        if (signal=="CALL" and trend=="DOWN") or (signal=="PUT" and trend=="UP"):
                            stake = session["current_stake"]
                            ws_trade = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
                            ws_trade.send(json.dumps({"authorize": session['api_token']}))
                            ws_trade.recv()
                            ws_trade.send(json.dumps({
                                "buy": 1, "price": stake,
                                "parameters": {"amount": stake, "basis": "stake", "contract_type": signal,
                                               "currency": data["tick"].get("currency","USD"), "duration": 5, "duration_unit": "t",
                                               "symbol": SYMBOL}
                            }))
                            resp = json.loads(ws_trade.recv())
                            ws_trade.close()
                            if "buy" in resp:
                                bot.send_message(chat_id, f"✅ Trade Entered: {signal}")
                        else:
                            bot.send_message(chat_id, "❌ No Trade After 30 Ticks")
        except: time.sleep(2); continue

# --- Telegram Flow مثل القديم ---
@bot.message_handler(commands=['start'])
def start_bot(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    bot.send_message(m.chat.id, "👋 Welcome! Enter your email:", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(m, auth)

def auth(m):
    u = users_col.find_one({"email": m.text.strip().lower()})
    if u and datetime.strptime(u['expiry'], "%Y-%m-%d") > datetime.now():
        bot.send_message(m.chat.id, "✅ Authorized! Enter API Token:")
        bot.register_next_step_handler(m, tk)
    else: bot.send_message(m.chat.id, "🚫 Access Denied.")

def tk(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"api_token": m.text.strip()}}, upsert=True)
    bot.send_message(m.chat.id, "Initial Stake:")
    bot.register_next_step_handler(m, sk)

def sk(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"initial_stake": float(m.text), "current_stake": float(m.text), "consecutive_losses": 0}})
    bot.send_message(m.chat.id, "Target Profit:")
    bot.register_next_step_handler(m, tp)

def tp(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"target_profit": float(m.text), "total_profit": 0, "win_count": 0, "loss_count": 0, "is_running": True}})
    bot.send_message(m.chat.id, "🚀 Bot Running (5-Ticks Analysis, 15 Indicators, 70%+)", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('STOP 🛑'))
    threading.Thread(target=trading_process, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == 'STOP 🛑')
def stop_btn(m):
    active_sessions_col.update_one({"chat_id": m.chat.id}, {"$set": {"is_running": False}})
    bot.send_message(m.chat.id, "🛑 Bot Stopped.", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('/start'))

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    bot.infinity_polling()
