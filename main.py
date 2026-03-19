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
    return "KhouryBot V4 - No MTG - Second 06 Active"

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

# --- إعدادات البوت والاتصال ---
TOKEN = "8511172742:AAGqDR6vq4OIH5R_JbTp-YzFnnTCw2f5gF8"
CHAT_ID = "-1003731752986"
SYMBOL = "FB_otc"
API_URL = f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={SYMBOL}&timeframe=M1&limit=14"

# --- إعداد MongoDB للاحصائيات ---
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client['KhouryBotDB']
stats_col = db['trading_stats']

def update_stats(result_type):
    try:
        field = "wins" if result_type == "win" else "losses"
        stats_col.update_one({"_id": "global_stats"}, {"$inc": {field: 1}}, upsert=True)
        return stats_col.find_one({"_id": "global_stats"})
    except: return {"wins": 0, "losses": 0}

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"})
    except: pass

# دالة التحليل (استراتيجية الـ 4 شموع)
def analyze_4_candles(data):
    try:
        c1 = data[0] # الأحدث (id 1)
        c4 = data[3] # الأقدم (id 4)
        
        c1_open, c1_close = float(c1['open']), float(c1['close'])
        c4_open, c4_close = float(c4['open']), float(c4['close'])
        
        # شرط الصعود: القديمة حمراء، الحديثة خضراء، والاتجاه العام صاعد
        if (c4_open > c4_close) and (c1_close > c1_open) and (c1_close > c4_open):
            return "UP"
        # شرط الهبوط: القديمة خضراء، الحديثة حمراء، والاتجاه العام هابط
        if (c4_close > c4_open) and (c1_open > c1_close) and (c1_close < c4_open):
            return "DOWN"
    except: pass
    return None

# دالة النتيجة (تطلب بيانات فريش وتفحص آخر 5 شموع)
def get_fresh_m5_result(direction):
    try:
        res = requests.get(API_URL, timeout=12).json()
        if res.get('success'):
            data = res['data']
            # افتتاح id 5 وإغلاق id 1 (فترة الـ 5 دقائق)
            open_p = float(data[4]['open'])
            close_p = float(data[0]['close'])
            actual_dir = "UP" if close_p > open_p else "DOWN"
            return True if actual_dir == direction else False
    except: pass
    return False

last_trade = None

def bot_engine():
    global last_trade
    print("🚀 KhouryBot Engine [No MTG - 06s Mode] Started...")
    
    while True:
        now = datetime.now()
        current_time = now.strftime('%H:%M:%S')
        
        # 1. التحليل عند الدقيقة الرابعة (4, 9, 14...) والثانية 06
        if (now.minute + 1) % 5 == 0 and now.second == 6:
            try:
                res = requests.get(API_URL, timeout=10).json()
                if res.get('success'):
                    signal = analyze_4_candles(res['data'])
                    if signal:
                        entry_t = (now + timedelta(minutes=1)).replace(second=0).strftime('%H:%M')
                        # وقت النتيجة بعد 5 دقائق + 6 ثوانٍ (ليكون عند الثانية 06 بالضبط)
                        check_at = (now + timedelta(minutes=6)).replace(second=6).strftime('%H:%M:%S')
                        
                        emoji = "🟢 CALL" if signal == "UP" else "🔴 PUT"
                        msg = f"🎯 *KhouryBot Signal*\n📊 Asset: `{SYMBOL}`\n⏳ M5 | Signal: *{emoji}*\n⏱ Entry: `{entry_t}`\n🚫 *NO MTG*"
                        send_telegram(msg)
                        
                        last_trade = {"dir": signal, "check_at": check_at}
                        print(f"✅ Signal Sent. Check at {check_at}")
                time.sleep(1)
            except: pass

        # 2. التحقق من النتيجة عند الثانية 06 (طلب API جديد)
        if last_trade and current_time == last_trade['check_at']:
            print(f"🔔 Time to check! Fetching fresh data at {current_time}")
            is_win = get_fresh_m5_result(last_trade['dir'])
            
            if is_win:
                s = update_stats("win")
                send_telegram(f"✅ *WIN* - {SYMBOL}\n📈 W: {s['wins']} | L: {s['losses']}")
            else:
                s = update_stats("loss")
                send_telegram(f"❌ *LOSS* - {SYMBOL}\n📈 W: {s['wins']} | L: {s['losses']}")
            
            # تصفير الصفقة (بدون مضاعفة)
            last_trade = None
            time.sleep(1)

        time.sleep(0.5)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot_engine()
