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
        url = f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={pair}&timeframe=M1&limit=50"
        resp = requests.get(url, timeout=5).json()
        if not resp.get("success"):
            return jsonify({"signal": None})
        
        data = resp["data"]

        # تحويل ل DataFrame
        df = pd.DataFrame(data)
        df['close'] = df['close'].astype(float)

        # حساب EMA20
        df['ema20'] = calculate_ema(df, 20)

        # نفس تحليلك
        is_trend_up = float(data[0]['close']) > float(data[1]['open'])
        is_trend_down = float(data[0]['close']) < float(data[1]['open'])

        is_green = float(data[1]['close']) > float(data[1]['open'])
        is_red = float(data[1]['close']) < float(data[1]['open'])

        current_close = float(data[0]['close'])
        current_ema20 = df.iloc[0]['ema20']

        # فلترة EMA20
        if is_trend_up and is_red and current_close > current_ema20:
            return jsonify({"signal": "UP"})

        if is_trend_down and is_green and current_close < current_ema20:
            return jsonify({"signal": "DOWN"})

    except:
        pass

    return jsonify({"signal": None})


@app.route('/check', methods=['GET'])
def check():
    pair = request.args.get('pair')
    direction = request.args.get('direction') 
    try:
        url = f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={pair}&timeframe=M1&limit=5"
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
