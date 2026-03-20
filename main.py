import requests
import time
import threading
import pytz
from datetime import datetime, timedelta
from flask import Flask
from pymongo import MongoClient
from concurrent.futures import ThreadPoolExecutor

# --- الإعدادات ---
TELEGRAM_TOKEN = '8511172742:AAGqDR6vq4OIH5R_JbTp-YzFnnTCw2f5gF8'
CHAT_ID = '-1003731752986'
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
PORT = 8080

PAIRS = [
    "USDINR_otc", "USDARS_otc", "USDPKR_otc", "USDMXN_otc", 
    "USDNGN_otc", "FB_otc", "MSFT_otc", "USDBRL_otc", 
    "USDBDT_otc", "USDPHP_otc"
]

client = MongoClient(MONGO_URI)
db = client['KhouryBot_DB']
signals_col = db['signals']

app = Flask(__name__)

# --- محرك التحليل اليدوي (Manual Logic) ---
def calculate_logic(data):
    closes = [float(d['close']) for d in data]
    opens = [float(d['open']) for d in data]
    highs = [float(d['high']) for d in data]
    lows = [float(d['low']) for d in data]

    votes = {"CALL": 0, "PUT": 0}
    # EMA 5/20
    votes["CALL" if (sum(closes[:5])/5) > (sum(closes[:20])/20) else "PUT"] += 1
    # RSI 14
    up = sum([max(0, closes[i] - closes[i+1]) for i in range(14)])
    dn = sum([max(0, closes[i+1] - closes[i]) for i in range(14)])
    rsi = 100 - (100 / (1 + (up/dn if dn != 0 else 100)))
    votes["CALL" if rsi > 50 else "PUT"] += 1
    # Price Direction
    votes["CALL" if closes[0] > opens[0] else "PUT"] += 2
    # High/Low Trend
    votes["CALL" if highs[0] > highs[1] else "PUT"] += 1
    votes["CALL" if lows[0] > lows[1] else "PUT"] += 1

    last_red = opens[0] > closes[0]
    last_green = opens[0] < closes[0]

    if votes["CALL"] >= 4 and last_red: return "CALL", closes[0]
    if votes["PUT"] >= 4 and last_green: return "PUT", closes[0]
    return None, None

def send_msg(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except: pass

# --- وظيفة فحص الزوج الواحد (سيتم تشغيلها بالتوازي) ---
def process_pair(pair, now_str):
    try:
        url = f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={pair}&timeframe=M1&limit=30"
        resp = requests.get(url, timeout=5).json()
        if resp.get("success") and "data" in resp:
            direction, price = calculate_logic(resp["data"])
            if direction:
                # التحقق من عدم تكرار الإشارة لنفس الزوج في نفس الدقيقة
                if not signals_col.find_one({"pair": pair, "entry_time": now_str}):
                    msg = f"⚠️ *Signal Alert*\n\nPair: `{pair}`\nDirection: *{direction}*\nTime Frame: M1\nTime: {now_str}"
                    send_msg(msg)
                    signals_col.insert_one({
                        "entry_time": now_str, "pair": pair, "direction": direction,
                        "open_price": price, "status": "PENDING", "timestamp": datetime.now()
                    })
    except: pass

def job():
    while True:
        now_utc = datetime.now(pytz.utc)
        if now_utc.second == 10:
            next_min = (now_utc + timedelta(minutes=1)).strftime('%H:%M')
            
            # --- نظام التوازي الحقيقي (Parallel Processing) ---
            # فتح 10 مسارات (Threads) لـ 10 أزواج في نفس اللحظة
            with ThreadPoolExecutor(max_workers=len(PAIRS)) as executor:
                for pair in PAIRS:
                    executor.submit(process_pair, pair, next_min)
            
            # فحص نتائج الصفقات السابقة
            target_prev = (now_utc - timedelta(minutes=1)).strftime('%H:%M')
            pending_signals = list(signals_col.find({"entry_time": target_prev, "status": "PENDING"}))
            for ps in pending_signals:
                try:
                    res = requests.get(f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={ps['pair']}&timeframe=M1&limit=1").json()
                    if res.get("success"):
                        c = res["data"][0]
                        won = (ps['direction'] == "CALL" and float(c['close']) > float(c['open'])) or \
                              (ps['direction'] == "PUT" and float(c['close']) < float(c['open']))
                        send_msg(f"🏁 *{ps['pair']}* | {ps['direction']} | {'WIN ✅' if won else 'LOSS ❌'}")
                        signals_col.update_one({"_id": ps["_id"]}, {"$set": {"status": "COMPLETED"}})
                except: pass
            
            time.sleep(2)
        time.sleep(0.5)

@app.route('/')
def home(): return "KhouryBot Parallel Radar Active", 200

if __name__ == '__main__':
    threading.Thread(target=job, daemon=True).start()
    app.run(host='0.0.0.0', port=PORT)
