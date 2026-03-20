import requests
import time
import threading
import pytz
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta
from flask import Flask
from pymongo import MongoClient

# --- الإعدادات ---
TELEGRAM_TOKEN = '8511172742:AAGqDR6vq4OIH5R_JbTp-YzFnnTCw2f5gF8'
CHAT_ID = '-1003731752986'
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
PORT = 8080

# قائمة الأزواج الـ 10
PAIRS = [
    "USDINR_otc", "USDARS_otc", "USDPKR_otc", "USDMXN_otc", 
    "USDNGN_otc", "FB_otc", "MSFT_otc", "USDBRL_otc", 
    "USDBDT_otc", "USDPHP_otc"
]

client = MongoClient(MONGO_URI)
db = client['Khoury123Bot_DB']
signals_col = db['signals']
stats_col = db['stats']

app = Flask(__name__)

@app.route('/')
def home():
    return "KhouryBot Radar M1 - Active", 200

def update_stats(result):
    field = "wins" if "WIN" in result else "losses"
    stats_col.update_one({"_id": "daily_stats"}, {"$inc": {field: 1}}, upsert=True)

def get_stats():
    stats = stats_col.find_one({"_id": "daily_stats"})
    return (stats.get("wins", 0), stats.get("losses", 0)) if stats else (0, 0)

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=5)
    except: pass

def analyze_pair(pair_name):
    try:
        # نطلب 60 شمعة لحساب المؤشرات رياضياً
        url = f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={pair_name}&timeframe=M1&limit=60"
        response = requests.get(url, timeout=5).json()
        if not response.get("success") or "data" not in response: return None, None
        
        df = pd.DataFrame(response["data"])
        for col in ['open', 'high', 'low', 'close']: df[col] = df[col].astype(float)
        df = df.sort_index(ascending=False).reset_index(drop=True)
        
        votes = {"CALL": 0, "PUT": 0}
        
        # حساب المؤشرات الـ 9 (Logic Binary)
        votes["CALL" if ta.rsi(df['close'], 14).iloc[-1] > 50 else "PUT"] += 1
        macd = ta.macd(df['close'])
        votes["CALL" if macd.iloc[-1, 0] > macd.iloc[-1, 2] else "PUT"] += 1
        stoch = ta.stoch(df['high'], df['low'], df['close'])
        votes["CALL" if stoch.iloc[-1, 0] > stoch.iloc[-1, 1] else "PUT"] += 1
        votes["CALL" if ta.ema(df['close'], 9).iloc[-1] > ta.ema(df['close'], 21).iloc[-1] else "PUT"] += 1
        bb = ta.bbands(df['close'])
        votes["CALL" if df['close'].iloc[-1] > bb.iloc[-1, 1] else "PUT"] += 1
        votes["CALL" if ta.willr(df['high'], df['low'], df['close']) > -50 else "PUT"] += 1
        votes["CALL" if ta.cci(df['high'], df['low'], df['close']) > 0 else "PUT"] += 1
        adx = ta.adx(df['high'], df['low'], df['close'])
        votes["CALL" if adx.iloc[-1, 1] > adx.iloc[-1, 2] else "PUT"] += 1
        psar = ta.psar(df['high'], df['low'], df['close'])
        votes["CALL" if df['close'].iloc[-1] > psar.iloc[-1, 0] else "PUT"] += 1

        # فحص الشمعة الأخيرة فقط للارتداد
        last_o, last_c = df['open'].iloc[-1], df['close'].iloc[-1]
        
        if votes["CALL"] >= 5 and last_o > last_c: return "CALL", last_c
        if votes["PUT"] >= 5 and last_o < last_c: return "PUT", last_c
        
        return None, None
    except: return None, None

def job():
    while True:
        now_utc = datetime.now(pytz.utc)
        if now_utc.second == 10:
            # 1. فحص النتيجة
            target_time = (now_utc - timedelta(minutes=1)).strftime('%H:%M')
            pending = signals_col.find_one({"entry_time": target_time, "status": "PENDING"})
            
            if pending:
                res = requests.get(f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={pending['pair']}&timeframe=M1&limit=1").json()
                if res.get("success"):
                    c = res["data"][0]
                    won = (pending['direction'] == "CALL" and float(c['close']) > float(c['open'])) or \
                          (pending['direction'] == "PUT" and float(c['close']) < float(c['open']))
                    res_txt = "WIN ✅" if won else "LOSS ❌"
                    update_stats(res_txt)
                    w, l = get_stats()
                    send_telegram_msg(f"🏁 *{pending['pair']}* | {pending['direction']} | {res_txt}\n📊 W:{w} L:{l}")
                    signals_col.update_one({"_id": pending["_id"]}, {"$set": {"status": "COMPLETED"}})

            # 2. تحليل الرادار (إشارة واحدة نظيفة)
            if signals_col.count_documents({"status": "PENDING"}) == 0:
                for pair in PAIRS:
                    direction, entry_p = analyze_pair(pair)
                    if direction:
                        next_min = (now_utc + timedelta(minutes=1)).strftime('%H:%M')
                        # الرسالة المطلوبة مع Time Frame
                        msg = (
                            f"⚠️ *Signal Alert*\n\n"
                            f"Pair: `{pair}`\n"
                            f"Direction: *{direction}*\n"
                            f"Time Frame: M1\n"
                            f"Time: {next_min}"
                        )
                        send_telegram_msg(msg)
                        signals_col.insert_one({
                            "entry_time": next_min, "pair": pair, "direction": direction,
                            "open_price": entry_p, "status": "PENDING", "timestamp": now_utc
                        })
                        break
            
            time.sleep(2)
        time.sleep(0.5)

if __name__ == '__main__':
    threading.Thread(target=job, daemon=True).start()
    app.run(host='0.0.0.0', port=PORT)
