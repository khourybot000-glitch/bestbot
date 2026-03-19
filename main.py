import requests
import time
import threading
import os
from datetime import datetime, timedelta
from flask import Flask
from pymongo import MongoClient

app = Flask(__name__)

@app.route('/')
def home():
    return "KhouryBot V5.1 - FB_OTC Dedicated [ACTIVE]"

# --- قاموس تحويل الاسم للظهور بشكل احترافي ---
ASSET_MAPPING = {
    "FB_OTC": "Facebook Inc OTC"
}

# --- إعدادات البوت والزوج الموحد ---
TOKEN = "8511172742:AAGqDR6vq4OIH5R_JbTp-YzFnnTCw2f5gF8"
CHAT_ID = "-1003731752986"
SYMBOL = "FB_OTC"  # الزوج الوحيد الذي سيعمل عليه البوت
API_URL = f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={SYMBOL}&timeframe=M1&limit=14"

# --- إعداد MongoDB ---
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

def analyze_logic(data):
    try:
        # تحليل id 1 و id 4 لإرسال الإشارة
        c1, c4 = data[0], data[3]
        c1_o, c1_c = float(c1['open']), float(c1['close'])
        c4_o, c4_c = float(c4['open']), float(c4['close'])
        
        # الاتجاه العام (إغلاق الحديثة مقارنة بافتتاح القديمة)
        if (c4_o > c4_c) and (c1_c > c1_o) and (c1_c > c4_o): return "UP"
        if (c4_c > c4_o) and (c1_o > c1_c) and (c1_c < c4_o): return "DOWN"
    except: pass
    return None

def get_m1_result_id1(direction):
    """يطلب API جديد ويفحص id 1 (الشمعة التي أغلقت لتوها)"""
    try:
        res = requests.get(API_URL, timeout=10).json()
        if res.get('success'):
            id1 = res['data'][0]
            o, c = float(id1['open']), float(id1['close'])
            actual = "UP" if c > o else "DOWN"
            if c == o: actual = "TIE"
            return True if actual == direction else False
    except: pass
    return None

last_trade = None

def bot_engine():
    global last_trade
    print(f"🚀 KhouryBot Engine Dedicated to {SYMBOL} Started...")
    
    while True:
        now = datetime.now()
        current_hm = now.strftime('%H:%M')
        current_sec = now.second
        
        # 1. التحليل والإرسال (ثانية 06)
        if current_sec == 6 and not last_trade:
            try:
                res = requests.get(API_URL, timeout=10).json()
                if res.get('success'):
                    signal = analyze_logic(res['data'])
                    if signal:
                        entry_t = (now + timedelta(minutes=1)).replace(second=0).strftime('%H:%M')
                        res_t = (now + timedelta(minutes=2)).strftime('%H:%M')
                        
                        readable_name = ASSET_MAPPING.get(SYMBOL, SYMBOL)
                        emoji = "🟢 CALL" if signal == "UP" else "🔴 PUT"
                        
                        msg = (f"🎯 *M1 Signal*\n"
                               f"📊 Asset: `{readable_name}`\n"
                               f"🚦 Signal: *{emoji}*\n"
                               f"⏱ Entry Time: `{entry_t}`\n"
                               f"🚫 *NO MTG*")
                        send_telegram(msg)
                        
                        last_trade = {"dir": signal, "check_at": res_t, "done": False}
                time.sleep(1)
            except: pass

        # 2. التحقق من النتيجة (يطلب API جديد ويفحص id 1)
        if last_trade and current_hm == last_trade['check_at'] and current_sec >= 6:
            if not last_trade['done']:
                win_status = get_m1_result_id1(last_trade['dir'])
                readable_name = ASSET_MAPPING.get(SYMBOL, SYMBOL)
                
                if win_status is not None:
                    s = update_stats("win" if win_status else "loss")
                    status_txt = "✅ *WIN*" if win_status else "❌ *LOSS*"
                    
                    send_telegram(f"{status_txt} - {readable_name}\n📈 Stats: W: {s['wins']} | L: {s['losses']}")
                    
                    last_trade['done'] = True
                    last_trade = None # جاهز للإشارة التالية
                time.sleep(1)

        time.sleep(0.5)

if __name__ == "__main__":
    # تشغيل سيرفر الويب
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000))), daemon=True).start()
    # تشغيل محرك البوت
    bot_engine()
