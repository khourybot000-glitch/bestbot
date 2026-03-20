import requests
import time
import threading
import pytz
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from flask import Flask
from pymongo import MongoClient

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
stats_col = db['stats']

app = Flask(__name__)

# --- الدوال الحسابية اليدوية للمؤشرات ---
def get_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def get_macd(series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line

def get_stoch(df, k=14, d=3):
    low_min = df['low'].rolling(window=k).min()
    high_max = df['high'].rolling(window=k).max()
    stoch_k = 100 * (df['close'] - low_min) / (high_max - low_min)
    stoch_d = stoch_k.rolling(window=d).mean()
    return stoch_k, stoch_d

def get_bollinger(series, period=20, std=2):
    sma = series.rolling(window=period).mean()
    rstd = series.rolling(window=period).std()
    return sma, sma + (std * rstd), sma - (std * rstd)

@app.route('/')
def home():
    return "KhouryBot Radar (No pandas_ta) - Active", 200

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
        url = f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={pair_name}&timeframe=M1&limit=60"
        response = requests.get(url, timeout=5).json()
        if not response.get("success") or "data" not in response: return None, None
        
        df = pd.DataFrame(response["data"])
        for col in ['open', 'high', 'low', 'close']: df[col] = df[col].astype(float)
        df = df.sort_index(ascending=False).reset_index(drop=True)
        
        votes = {"CALL": 0, "PUT": 0}
        close = df['close']

        # 1. RSI
        votes["CALL" if get_rsi(close).iloc[-1] > 50 else "PUT"] += 1
        # 2. MACD
        m_line, s_line = get_macd(close)
        votes["CALL" if m_line.iloc[-1] > s_line.iloc[-1] else "PUT"] += 1
        # 3. STOCH
        pk, pd_line = get_stoch(df)
        votes["CALL" if pk.iloc[-1] > pd_line.iloc[-1] else "PUT"] += 1
        # 4. EMA 9/21
        votes["CALL" if close.ewm(span=9).mean().iloc[-1] > close.ewm(span=21).mean().iloc[-1] else "PUT"] += 1
        # 5. Bollinger
        mid, upper, lower = get_bollinger(close)
        votes["CALL" if close.iloc[-1] > mid.iloc[-1] else "PUT"] += 1
        # 6. Williams %R (Manual)
        high_14 = df['high'].rolling(14).max()
        low_14 = df['low'].rolling(14).min()
        wr = -100 * (high_14 - close) / (high_14 - low_14)
        votes["CALL" if wr.iloc[-1] > -50 else "PUT"] += 1
        # 7. CCI (Manual)
        tp = (df['high'] + df['low'] + close) / 3
        sma_tp = tp.rolling(14).mean()
        mad = tp.rolling(14).apply(lambda x: np.abs(x - x.mean()).mean())
        cci = (tp - sma_tp) / (0.015 * mad)
        votes["CALL" if cci.iloc[-1] > 0 else "PUT"] += 1
        # 8. ADX Simple Direction
        plus_di = (df['high'].diff() > df['low'].diff(periods=-1)).astype(int)
        votes["CALL" if plus_di.iloc[-1] == 1 else "PUT"] += 1
        # 9. PSAR Direction (Price vs Last Close)
        votes["CALL" if close.iloc[-1] > close.iloc[-2] else "PUT"] += 1

        last_o, last_c = df['open'].iloc[-1], df['close'].iloc[-1]
        
        if votes["CALL"] >= 5 and last_o > last_c: return "CALL", last_c
        if votes["PUT"] >= 5 and last_o < last_c: return "PUT", last_c
        
        return None, None
    except: return None, None

def job():
    while True:
        now_utc = datetime.now(pytz.utc)
        if now_utc.second == 10:
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

            if signals_col.count_documents({"status": "PENDING"}) == 0:
                for pair in PAIRS:
                    direction, entry_p = analyze_pair(pair)
                    if direction:
                        next_min = (now_utc + timedelta(minutes=1)).strftime('%H:%M')
                        msg = f"⚠️ *Signal Alert*\n\nPair: `{pair}`\nDirection: *{direction}*\nTime Frame: M1\nTime: {next_min}"
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
