import requests
import time
import threading
import os
from datetime import datetime, timedelta
from flask import Flask
from pymongo import MongoClient

# --- سيرفر الويب لفتح الـ Port لـ UptimeRobot ---
app = Flask(__name__)

@app.route('/')
def home():
    return "KhouryBot M5 Strategy [Limit 14 Active]"

def run_flask():
    # Render يمرر البورت تلقائياً عبر PORT
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

# --- إعدادات البوت الخاصة بك ---
TOKEN = "8511172742:AAGqDR6vq4OIH5R_JbTp-YzFnnTCw2f5gF8"
CHAT_ID = "-1003731752986"
SYMBOL = "USDPKR_otc"
# الطلب دائماً لـ 14 شمعة
API_URL = f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={SYMBOL}&timeframe=M1&limit=14"

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

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"})
    except: pass

def analyze_4_candles_trend(data):
    try:
        # تحليل آخر 4 شموع (id 1 إلى id 4)
        open_4 = float(data[3]['open'])
        close_1 = float(data[0]['close'])
        if close_1 > open_4: return "UP"
        elif close_1 < open_4: return "DOWN"
    except: pass
    return None

def check_m5_result(data, direction):
    try:
        # التحقق من نتيجة 5 دقائق (أول 5 عناصر في القائمة التي سحبها البوت الآن)
        # افتتاح الشمعة الخامسة (id 5) وإغلاق الشمعة الأحدث (id 1)
        open_price = float(data[4]['open'])
        close_price = float(data[0]['close'])
        actual_dir = "UP" if close_price > open_price else "DOWN"
        return True if actual_dir == direction else False
    except: return False

last_trade = None

def bot_engine():
    global last_trade
    print("🚀 KhouryBot Engine [M5 - Dynamic Fetch] Started...")
    
    while True:
        now = datetime.now()
        
        # 1. لحظة التحليل (إرسال الإشارة)
        if now.minute % 15 == 14 and now.second == 20:
            try:
                # يطلب 14 شمعة "جديدة" للتحليل
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
                        # تحديد وقت التحقق القادم (بعد 5 دقائق + 6 ثوانٍ)
                        check_at = (now + timedelta(minutes=6)).replace(second=6).strftime('%H:%M:%S')
                        last_trade = {"entry": entry_t, "check_at": check_at, "dir": signal, "mtg": False}
                time.sleep(1)
            except: pass

        # 2. لحظة التحقق من النتيجة الأولى
        if last_trade and not last_trade['mtg']:
            if now.strftime('%H:%M:%S') == last_trade['check_at']:
                try:
                    # يطلب 14 شمعة "جديدة" للتحقق من النتيجة
                    res = requests.get(API_URL).json()
                    if check_m5_result(res['data'], last_trade['dir']):
                        s = update_stats("win")
                        send_telegram(f"✅ *WIN* - {SYMBOL}\n📈 Stats: W: {s['wins']} | L: {s['losses']}")
                        last_trade = None
                    else:
                        last_trade['mtg'] = True
                        # تحديد وقت التحقق للمضاعفة (بعد 5 دقائق إضافية)
                        new_check = (datetime.strptime(last_trade['check_at'], '%H:%M:%S') + timedelta(minutes=5)).strftime('%H:%M:%S')
                        last_trade['check_at'] = new_check
                except: pass
                time.sleep(1)

        # 3. لحظة التحقق من المارتينجال
        if last_trade and last_trade['mtg']:
            if now.strftime('%H:%M:%S') == last_trade['check_at']:
                try:
                    # يطلب 14 شمعة "جديدة" للتحقق من نتيجة المارتينجال
                    res = requests.get(API_URL).json()
                    if check_m5_result(res['data'], last_trade['dir']):
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
    # تشغيل Flask للسيرفر
    threading.Thread(target=run_flask, daemon=True).start()
    # تشغيل البوت
    bot_engine()
