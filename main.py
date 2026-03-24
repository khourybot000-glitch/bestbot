import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
import pandas as pd

app = Flask(__name__)
CORS(app)

# دالة بسيطة لحساب EMA
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

# دالة لحساب SMA
def sma(series, period):
    return series.rolling(period).mean()

# دالة لحساب RSI سريع
def rsi(series, period=5):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / (avg_loss + 1e-6)
    return 100 - (100 / (1 + rs))

# دالة لحساب مؤشر Stochastic سريع
def stochastic(high, low, close, k_period=5):
    lowest_low = low.rolling(k_period).min()
    highest_high = high.rolling(k_period).max()
    return 100 * (close - lowest_low) / (highest_high - lowest_low + 1e-6)

# دالة Momentum
def momentum(series, period=5):
    return series - series.shift(period)

# دالة ATR سريع
def atr(high, low, close, period=5):
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()

@app.route('/analyze', methods=['GET'])
def analyze():
    pair = request.args.get('pair')
    try:
        # جلب بيانات M1 (آخر 25 شمعة)
        url = f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={pair}&timeframe=M1&limit=25"
        resp = requests.get(url, timeout=5).json()
        if not resp.get("success"):
            return jsonify({"signal": None})
        
        df = pd.DataFrame(resp["data"])
        df = df.astype(float)

        signals = []

        # 1. EMA5 vs EMA10
        if ema(df['close'], 5).iloc[-1] > ema(df['close'], 10).iloc[-1]:
            signals.append("UP")
        else:
            signals.append("DOWN")

        # 2. EMA10 vs EMA20
        if ema(df['close'], 10).iloc[-1] > ema(df['close'], 20).iloc[-1]:
            signals.append("UP")
        else:
            signals.append("DOWN")

        # 3. SMA5 vs SMA10
        if sma(df['close'], 5).iloc[-1] > sma(df['close'], 10).iloc[-1]:
            signals.append("UP")
        else:
            signals.append("DOWN")

        # 4. RSI5
        signals.append("UP" if rsi(df['close'], 5).iloc[-1] > 50 else "DOWN")

        # 5. Stochastic 5,3
        signals.append("UP" if stochastic(df['high'], df['low'], df['close'], 5).iloc[-1] > 50 else "DOWN")

        # 6. Momentum5
        signals.append("UP" if momentum(df['close'], 5).iloc[-1] > 0 else "DOWN")

        # 7. MACD fast=12, slow=26
        ema_fast = ema(df['close'], 12)
        ema_slow = ema(df['close'], 26)
        macd = ema_fast - ema_slow
        signals.append("UP" if macd.iloc[-1] > 0 else "DOWN")

        # 8. CCI5
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        mean_tp = typical_price.rolling(5).mean()
        mad = (typical_price - mean_tp).abs().rolling(5).mean()
        cci = (typical_price - mean_tp) / (0.015 * mad + 1e-6)
        signals.append("UP" if cci.iloc[-1] > 0 else "DOWN")

        # 9. Bollinger Bands 20,2
        sma20 = sma(df['close'], 20)
        std20 = df['close'].rolling(20).std()
        upper = sma20 + 2 * std20
        lower = sma20 - 2 * std20
        if df['close'].iloc[-1] > upper.iloc[-1]:
            signals.append("UP")
        elif df['close'].iloc[-1] < lower.iloc[-1]:
            signals.append("DOWN")
        else:
            signals.append("UP" if df['close'].iloc[-1] > sma20.iloc[-1] else "DOWN")

        # 10. ATR5 (سريع لتقلب السعر)
        atr_val = atr(df['high'], df['low'], df['close'], 5).iloc[-1]
        signals.append("UP" if df['close'].iloc[-1] > df['close'].iloc[-2] and atr_val > 0 else "DOWN")

        # حساب نسبة الاتفاق
        up_count = signals.count("UP")
        down_count = signals.count("DOWN")
        total = len(signals)

        if up_count / total >= 0.7:
            return jsonify({"signal": "UP"})
        elif down_count / total >= 0.7:
            return jsonify({"signal": "DOWN"})
        else:
            return jsonify({"signal": None})

    except Exception as e:
        print(e)
        return jsonify({"signal": None})


@app.route('/check', methods=['GET'])
def check():
    pair = request.args.get('pair')
    direction = request.args.get('direction') 
    try:
        url = f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={pair}&timeframe=M1&limit=6"
        resp = requests.get(url, timeout=5).json()
        data = resp['data']
        
        current_close = float(data[0]['open'])
        start_open = float(data[2]['open'])
        
        won = (direction == "UP" and current_close > start_open) or \
              (direction == "DOWN" and current_close < start_open)
              
        return jsonify({"result": "WIN" if won else "LOSS"})
    except:
        return jsonify({"result": "ERROR"})


if __name__ == '__main__':
    import os
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
