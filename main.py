import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
import pandas as pd

app = Flask(__name__)
CORS(app)

def calculate_ema(df, period):
    return df['close'].ewm(span=period, adjust=False).mean()

@app.route('/analyze', methods=['GET'])
def analyze():
    pair = request.args.get('pair')
    try:
        url = f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={pair}&timeframe=M1&limit=30"
        resp = requests.get(url, timeout=5).json()
        
        if not resp.get("success"):
            return jsonify({"signal": None})
        
        data = resp["data"]

        # تحويل البيانات
        df = pd.DataFrame(data)
        df['close'] = df['close'].astype(float)

        # 🔥 إصلاح ترتيب الشموع
        df = df.iloc[::-1].reset_index(drop=True)

        # حساب EMA2 و EMA5
        df['ema2'] = calculate_ema(df, 2)
        df['ema5'] = calculate_ema(df, 5)

        # آخر شمعتين
        prev = df.iloc[-2]
        curr = df.iloc[-1]

        # ✅ تقاطع صعود (EMA2 يقطع EMA5 للأعلى)
        if prev['ema2'] < prev['ema5'] and curr['ema2'] > curr['ema5']:
            return jsonify({"signal": "UP"})

        # ✅ تقاطع هبوط (EMA2 يقطع EMA5 للأسفل)
        elif prev['ema2'] > prev['ema5'] and curr['ema2'] < curr['ema5']:
            return jsonify({"signal": "DOWN"})

    except:
        pass

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
        prev_open = float(data[3]['open'])

        won = (direction == "UP" and current_close > prev_open) or \
              (direction == "DOWN" and current_close < prev_open)

        return jsonify({"result": "WIN" if won else "LOSS"})

    except:
        return jsonify({"result": "ERROR"})


if __name__ == '__main__':
    import os
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
