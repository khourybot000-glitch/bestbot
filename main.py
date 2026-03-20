import requests
import time
import threading
import pytz
from datetime import datetime, timedelta
from flask import Flask
from pymongo import MongoClient

# --- الإعدادات ---
TELEGRAM_TOKEN = '8511172742:AAGqDR6vq4OIH5R_JbTp-YzFnnTCw2f5gF8'
CHAT_ID = '-1003731752986'
# طلبنا limit=5 لجلب آخر 5 شموع
API_URL = "https://mrbeaxt.site/Qx/Qx.php?format=json&pair=FB_otc&timeframe=M1&limit=5"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
PORT = 8080

client = MongoClient(MONGO_URI)
db = client['KhouryBot_DB']
signals_col = db['signals']
stats_col = db['stats']

app = Flask(__name__)

@app.route('/')
def home():
    return "KhouryBot M5 Strategy - 5 Candles Trend", 200

def update_stats(result):
    field = "wins" if "WIN" in result else "losses"
    stats_col.update_one({"_id": "daily_stats"}, {"$inc": {field: 1}}, upsert=True)

def get_stats():
    stats = stats_col.find_one({"_id": "daily_stats"})
    if stats: return stats.get("wins", 0), stats.get("losses", 0)
    return 0, 0

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload)
    except: pass

def get_api_data():
    try:
        response = requests.get(API_URL).json()
        if response.get("success") and "data" in response:
            return response["data"] # نرجع القائمة كاملة (5 شموع)
    except: return None

def job():
    print("KhouryBot M5 Trend Strategy Started...")
    
    while True:
        now_utc = datetime.now(pytz.utc)
        
        if now_utc.second == 10:
            current_min_str = now_utc.strftime('%H:%M')
            
            # 1. فحص النتيجة (بعد مرور 5 دقائق من وقت الدخول)
            # إذا دخلنا 12:05، نفحص عند 12:10:10 (أي الدخول كان قبل 5 دقائق من الآن)
            target_entry_time = (now_utc - timedelta(minutes=5)).strftime('%H:%M')
            pending_signal = signals_col.find_one({"entry_time": target_entry_time, "status": "PENDING"})
            
            if pending_signal:
                # لجلب نتيجة صفقة 5 دقائق، نحتاج مقارنة السعر الآن بسعر الافتتاح المخزن
                candles = get_api_data()
                if candles:
                    current_close = float(candles[0]['close']) # إغلاق أحدث شمعة
                    open_at_entry = pending_signal['open_price']
                    direction = pending_signal['direction']
                    
                    result_text = "LOSS ❌"
                    if direction == "CALL" and current_close > open_at_entry:
                        result_text = "WIN ✅"
                    elif direction == "PUT" and current_close < open_at_entry:
                        result_text = "WIN ✅"
                    
                    update_stats(result_text)
                    w, l = get_stats()
                    send_telegram_msg(f"🏁 *M5 Result Update*\nDirection: {direction}\nResult: {result_text}\n\n📊 Stats: W {w} | L {l}")
                    signals_col.update_one({"_id": pending_signal["_id"]}, {"$set": {"status": "COMPLETED"}})

            # 2. تحليل 5 شموع لاتخاذ قرار جديد
            data = get_api_data()
            if data and len(data) == 5:
                # فحص إذا كانت الـ 5 شموع كلها نفس الاتجاه
                # ملاحظة: في الـ API عادة data[0] هي الأحدث
                directions = []
                for c in data:
                    if float(c['close']) > float(c['open']): directions.append("CALL")
                    elif float(c['close']) < float(c['open']): directions.append("PUT")
                    else: directions.append("DOJI")
                
                # التأكد أن الـ 5 عناصر متطابقة وليست Doji
                if all(d == "CALL" for d in directions):
                    final_dir = "CALL"
                elif all(d == "PUT" for d in directions):
                    final_dir = "PUT"
                else:
                    final_dir = None

                if final_dir:
                    entry_time = (now_utc + timedelta(minutes=1)).strftime('%H:%M')
                    # حفظ سعر الإغلاق الحالي ليكون هو سعر "الافتتاح" للصفقة القادمة
                    current_price = float(data[0]['close'])
                    
                    msg = (
                        f"⚠️ *Trend Alert (5 Candles)*\n\n"
                        f"PAIR: Facebook inc otc\n"
                        f"Direction: {final_dir}\n"
                        f"Duration: 5 Minutes\n"
                        f"Entry Time: {entry_time}\n"
                        f"(UTC 0)"
                    )
                    send_telegram_msg(msg)
                    signals_col.insert_one({
                        "entry_time": entry_time,
                        "open_price": current_price,
                        "direction": final_dir,
                        "status": "PENDING",
                        "timestamp": now_utc
                    })
            
            time.sleep(2)
        time.sleep(0.5)

if __name__ == '__main__':
    threading.Thread(target=job, daemon=True).start()
    app.run(host='0.0.0.0', port=PORT)
