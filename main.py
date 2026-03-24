import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
import pandas as pd

app = Flask(__name__)
CORS(app)

def calculate_rsi(df, period=14):
    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

@app.route('/analyze', methods=['GET'])
def analyze():
    pair = request.args.get('pair')
    try:
        url = f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={pair}&timeframe=M1&limit=30"
        resp = requests.get(url, timeout=5).json()
        if not resp.get("success"):
            return jsonify({"signal": None})
        
        data = resp["data"]
        df = pd.DataFrame(data)
        df['close'] = df['close'].astype(float)
        df = df.iloc[::-1].reset_index(drop=True)  # ترتيب من الأقدم للأحدث

        # حساب RSI
        df['rsi'] = calculate_rsi(df, 30)
        curr_rsi = df.iloc[-1]['rsi']

        # إشارة عند التشبع
        if curr_rsi < 30:    # تشبع بيعي → اشترِ
            return jsonify({"signal": "UP"})
        elif curr_rsi > 70:  # تشبع شرائي → بيع
            return jsonify({"signal": "DOWN"})

    except:
        pass

    return jsonify({"signal": None})


@app.route('/check', methods=['GET'])
def check():
    pair = request.args.get('pair')
    direction = request.args.get('direction') 
    try:
        url = f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={pair}&timeframe=M1&limit=17"
        resp = requests.get(url, timeout=5).json()
        data = resp['data']
        
        current_close = float(data[0]['open'])
        prev_open = float(data[15]['open'])
        
        won = (direction == "UP" and current_close > prev_open) or \
              (direction == "DOWN" and current_close < prev_open)
              
        return jsonify({"result": "WIN" if won else "LOSS"})
    except:
        return jsonify({"result": "ERROR"})


if __name__ == '__main__':
    import os
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
