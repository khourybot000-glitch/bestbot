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

# --- تغيير اسم قاعدة البيانات هنا ---
client = MongoClient(MONGO_URI)
db = client['KhouryBot_DB'] # تم تغيير الاسم هنا
signals_col = db['signals']
stats_col = db['stats']

app = Flask(__name__)

@app.route('/')
def home():
    return "KhouryBot is Live - Database: KhouryBot_DB", 200

def update_stats(result):
    field = "wins" if "WIN" in result else "losses"
    stats_col.update_one({"_id": "daily_stats"}, {"$inc": {field: 1}}, upsert=True)

def get_stats():
    stats = stats_col.find_one({"_id": "daily_stats"})
    if stats:
        return stats.get("wins", 0), stats.get("losses", 0)
    return 0, 0

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
    except: return None

def job():
    print("KhouryBot Core Started (Silent Martingale)...")
    
    while True:
        now_utc = datetime.now(pytz.utc)
        
        if now_utc.second == 10:
            # 1. فحص الصفقات المنتظرة للنتيجة (أساسية أو مضاعفة)
            target_time = (now_utc - timedelta(minutes=1)).strftime('%H:%M')
            pending_signal = signals_col.find_one({"entry_time": target_time, "status": "PENDING"})
            
            if pending_signal:
                candle = get_api_data()
                if candle:
                    open_p, close_p = float(candle['open']), float(candle['close'])
                    sent_dir = pending_signal['direction']
                    is_mtg_attempt = pending_signal.get('is_mtg', False)
                    
                    # هل الشمعة الحالية مطابقة للاتجاه؟
                    won_current = (sent_dir == "PUT" and close_p < open_p) or (sent_dir == "CALL" and close_p > open_p)
                    
                    if won_current:
                        res_label = "MTG WIN ✅" if is_mtg_attempt else "WIN ✅"
                        update_stats(res_label)
                        w, l = get_stats()
                        send_telegram_msg(f"🏁 *Result Update*\nDirection: {sent_dir}\nResult: {res_label}\n\n📊 Stats: W {w} | L {l}")
                        signals_col.update_one({"_id": pending_signal["_id"]}, {"$set": {"status": "COMPLETED"}})
                    
                    else:
                        if not is_mtg_attempt:
                            # خسارة أولى: تحديث الوقت للدقيقة القادمة للصمت والانتظار
                            new_entry_time = (now_utc + timedelta(minutes=1)).strftime('%H:%M')
                            signals_col.update_one({"_id": pending_signal["_id"]}, {"$set": {
                                "entry_time": new_entry_time,
                                "is_mtg": True
                            }})
                            print(f"Silent Martingale started for {new_entry_time}")
                        else:
                            # خسارة المضاعفة: إرسال النتيجة النهائية
                            res_label = "MTG LOSS ❌"
                            update_stats(res_label)
                            w, l = get_stats()
                            send_telegram_msg(f"🏁 *Result Update*\nDirection: {sent_dir}\nResult: {res_label}\n\n📊 Stats: W {w} | L {l}")
                            signals_col.update_one({"_id": pending_signal["_id"]}, {"$set": {"status": "COMPLETED"}})

            # 2. تحليل إشارة جديدة (منطق معكوس)
            next_min = (now_utc + timedelta(minutes=1)).strftime('%H:%M')
            if not signals_col.find_one({"entry_time": next_min}):
                candle = get_api_data()
                if candle:
                    high_p, low_p, close_p = float(candle['high']), float(candle['low']), float(candle['close'])
                    direction = "PUT" if close_p == high_p else ("CALL" if close_p == low_p else None)

                    if direction:
                        send_telegram_msg(f"⚠️ *Signal Alert*\nPair: Facebook inc otc\nDirection: {direction}\nEntry: {next_min}\n(UTC 0)")
                        signals_col.insert_one({
                            "entry_time": next_min,
                            "direction": direction,
                            "status": "PENDING",
                            "is_mtg": False,
                            "timestamp": now_utc
                        })
            
            time.sleep(2)
        time.sleep(0.5)

if __name__ == '__main__':
    threading.Thread(target=job, daemon=True).start()
    app.run(host='0.0.0.0', port=PORT)
