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
API_URL = "https://mrbeaxt.site/Qx/Qx.php?format=json&pair=FB_otc&timeframe=M1&limit=1"
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
PORT = 8080

# إعداد قاعدة البيانات
client = MongoClient(MONGO_URI)
db = client['TradingBot']
signals_col = db['signals']
stats_col = db['stats'] # مجموعة الإحصائيات

app = Flask(__name__)

@app.route('/')
def home():
    return "Khourybot is Online with Stats!", 200

# --- وظائف الإحصائيات ---
def update_stats(result):
    # نستخدم معرف ثابت 'daily_stats' لتحديث نفس السجل دائماً
    if "WIN" in result:
        stats_col.update_one({"_id": "daily_stats"}, {"$inc": {"wins": 1}}, upsert=True)
    else:
        stats_col.update_one({"_id": "daily_stats"}, {"$inc": {"losses": 1}}, upsert=True)

def get_stats():
    stats = stats_col.find_one({"_id": "daily_stats"})
    if stats:
        return stats.get("wins", 0), stats.get("losses", 0)
    return 0, 0

# --- وظائف التلغرام ---
def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload)
    except: pass

def get_api_data():
    try:
        response = requests.get(API_URL).json()
        if response.get("success") and response.get("data"):
            return response["data"][0]
    except Exception as e:
        print(f"API Error: {e}")
    return None

# --- منطق البوت الأساسي ---
def job():
    print("Khourybot Core Started...")
    
    while True:
        now_utc = datetime.now(pytz.utc)
        
        if now_utc.second == 10:
            current_min_str = now_utc.strftime('%H:%M')
            
            # 1. التحقق من نتيجة الصفقة السابقة (عند الدقيقة +2 من التحليل)
            target_entry_time = (now_utc - timedelta(minutes=1)).strftime('%H:%M')
            pending_signal = signals_col.find_one({"entry_time": target_entry_time, "status": "PENDING"})
            
            if pending_signal:
                candle = get_api_data()
                if candle:
                    close_p = float(candle['close'])
                    high_p = float(candle['high'])
                    low_p = float(candle['low'])
                    
                    actual_dir = "CALL" if close_p == high_p else ("PUT" if close_p == low_p else "NONE")
                    result_text = "WIN ✅" if pending_signal['direction'] == actual_dir else "LOSS ❌"
                    
                    # تحديث الإحصائيات في MongoDB
                    update_stats(result_text)
                    w, l = get_stats()
                    
                    # إرسال النتيجة مع الإحصائيات
                    msg = (
                        f"🏁 *Result Update*\n"
                        f"Direction: {pending_signal['direction']}\n"
                        f"Result: {result_text}\n\n"
                        f"📊 *Current Stats*\n"
                        f"Total Wins: {w}\n"
                        f"Total Losses: {l}"
                    )
                    send_telegram_msg(msg)
                    signals_col.update_one({"_id": pending_signal["_id"]}, {"$set": {"status": result_text}})

            # 2. تحليل إشارة جديدة
            candle = get_api_data()
            if candle:
                close_p = float(candle['close'])
                high_p = float(candle['high'])
                low_p = float(candle['low'])
                
                direction = None
                if close_p == high_p: direction = "PUT"
                elif close_p == low_p: direction = "CALL"

                if direction:
                    entry_time = (now_utc + timedelta(minutes=1)).strftime('%H:%M')
                    msg = (
                        f"⚠️ *New Signal Alert*\n\n"
                        f"PAIR: Facebook inc otc\n"
                        f"Direction: {direction}\n"
                        f"Time Frame: M1\n"
                        f"Entry Time: {entry_time}\n"
                        f"(UTC 0)"
                    )
                    send_telegram_msg(msg)
                    signals_col.insert_one({
                        "entry_time": entry_time,
                        "direction": direction,
                        "status": "PENDING",
                        "timestamp": now_utc
                    })
            
            time.sleep(2)
        time.sleep(0.5)

if __name__ == '__main__':
    threading.Thread(target=job, daemon=True).start()
    app.run(host='0.0.0.0', port=PORT)
