import requests
import time
import threading
import os
from datetime import datetime, timedelta
from flask import Flask
from pymongo import MongoClient

# --- إعداد سيرفر الويب لفتح الـ Port ---
app = Flask(__name__)

@app.route('/')
def home():
    # هذا النص سيظهر عند فتح رابط Render في المتصفح
    return "KhouryBot Web Server is Running! Open Port Active."

def run_flask():
    # Render يمرر البورت تلقائياً عبر المتغير البيئي PORT
    port = int(os.environ.get("PORT", 5000))
    print(f"📡 Opening Port: {port}")
    app.run(host='0.0.0.0', port=port)

# --- إعدادات البوت والبيانات ---
TOKEN = "8511172742:AAH0fIuvTi4ZwWL28ncZb0bsXZv3rOf8pR8"
CHAT_ID = "-1003731752986"
SYMBOL = "USDPKR_otc"
API_URL = f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={SYMBOL}&timeframe=M1&limit=4"

# --- إعداد MongoDB ---
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client['KhouryBotDB']
stats_col = db['trading_stats']

def get_stats():
    stats = stats_col.find_one({"_id": "global_stats"})
    if not stats:
        initial = {"_id": "global_stats", "wins": 0, "losses": 0}
        stats_col.insert_one(initial)
        return initial
    return stats

def update_stats(result_type):
    if result_type == "win":
        stats_col.update_one({"_id": "global_stats"}, {"$inc": {"wins": 1}})
    else:
        stats_col.update_one({"_id": "global_stats"}, {"$inc": {"losses": 1}})
    return get_stats()

last_trade = None

def analyze_4_candles_trend(data):
    try:
        # id 1 هي data[0] | id 4 هي data[3]
        latest_c = data[0]
        fourth_c = data[3]
        open_4 = float(fourth_c['open'])
        close_1 = float(latest_c['close'])
        
        if close_1 > open_4: return "UP"
        elif close_1 < open_4: return "DOWN"
    except: pass
    return None

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"})
    except: pass

def bot_engine():
    global last_trade
    print(f"🚀 KhouryBot Engine Started...")
    
    while True:
        now = datetime.now()
        
        # 1. التحليل عند الدقيقة 14 (أو 29، 44، 59) والثانية 20
        if now.minute % 15 == 14 and now.second == 20:
            try:
                res = requests.get(API_URL, timeout=10).json()
                if res.get('success'):
                    signal = analyze_4_candles_trend(res['data'])
                    if signal:
                        entry_t = (now + timedelta(minutes=1)).replace(second=0).strftime('%H:%M')
                        emoji = "🟢 CALL" if signal == "UP" else "🔴 PUT"
                        msg = (f"🎯 *KhouryBot New Signal*\n\n"
                               f"📊 Asset: `{SYMBOL}`\n"
                               f"⏳ Timeframe: `M5`\n"
                               f"🚦 Signal: *{emoji}*\n"
                               f"⏱ Entry Time: `{entry_t}`\n\n"
                               f"⚠️ *USE 1 MTG*")
                        send_telegram(msg)
                        last_trade = {"entry": entry_t, "dir": signal, "mtg": False}
                time.sleep(1)
            except: pass

        # 2. التحقق من النتيجة (بعد دقيقة و 6 ثوانٍ)
        if last_trade and not last_trade['mtg']:
            check_t = (datetime.strptime(last_trade['entry'], '%H:%M') + timedelta(minutes=1)).strftime('%H:%M')
            if now.strftime('%H:%M') == check_t and now.second == 6:
                try:
                    res = requests.get(API_URL).json()
                    target = res['data'][0]
                    res_dir = "UP" if float(target['close']) > float(target['open']) else "DOWN"
                    if res_dir == last_trade['dir']:
                        s = update_stats("win")
                        send_telegram(f"✅ *WIN* - {SYMBOL}\n📈 Stats: W: {s['wins']} | L: {s['losses']}")
                        last_trade = None
                    else:
                        last_trade['mtg'] = True
                except: pass
                time.sleep(1)

        # 3. التحقق من المارتينجال (بعد دقيقتين و 6 ثوانٍ)
        if last_trade and last_trade['mtg']:
            mtg_t = (datetime.strptime(last_trade['entry'], '%H:%M') + timedelta(minutes=2)).strftime('%H:%M')
            if now.strftime('%H:%M') == mtg_t and now.second == 6:
                try:
                    res = requests.get(API_URL).json()
                    target = res['data'][0]
                    res_dir = "UP" if float(target['close']) > float(target['open']) else "DOWN"
                    if res_dir == last_trade['dir']:
                        s = update_stats("win")
                        send_telegram(f"⚖️ *MTG WIN* - {SYMBOL}\n📈 Stats: W: {s['wins']} | L: {s['losses']}")
                    else:
                        s = update_stats("loss")
                        send_telegram(f"❌ *MTG LOSS* - {SYMBOL}\n📈 Stats: W: {s['wins']} | L: {s['losses']}")
                    last_trade = None
                except: pass
                time.sleep(1)

        time.sleep(0.5)

if __name__ == "__main__":
    # تشغيل السيرفر في Thread منفصل لفتح البورت
    threading.Thread(target=run_flask).start()
    # تشغيل محرك البوت
    bot_engine()
