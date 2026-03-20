import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

@app.route('/analyze', methods=['GET'])
def analyze():
    pair = request.args.get('pair')
    try:
        url = f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={pair}&timeframe=M1&limit=5"
        resp = requests.get(url, timeout=5).json()
        if not resp.get("success"): return jsonify({"signal": None})
        
        data = resp["data"]
        # الاتجاه العام (فتح الأقدم 4 vs إغلاق الأحدث 0)
        is_trend_up = float(data[0]['close']) > float(data[4]['open'])
        is_trend_down = float(data[0]['close']) < float(data[4]['open'])
        
        # تأكيد الشمعة الحالية (إغلاق 0 vs فتح 0)
        is_green = float(data[0]['close']) > float(data[0]['open'])
        is_red = float(data[0]['close']) < float(data[0]['open'])

        if is_trend_up and is_green: return jsonify({"signal": "UP"})
        if is_trend_down and is_red: return jsonify({"signal": "DOWN"})
    except: pass
    return jsonify({"signal": None})

@app.route('/check', methods=['GET'])
def check():
    pair = request.args.get('pair')
    direction = request.args.get('direction') # سيستقبل UP أو DOWN
    try:
        url = f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={pair}&timeframe=M1&limit=5"
        resp = requests.get(url, timeout=5).json()
        latest = resp['data'][0]
        
        c_close = float(latest['close'])
        c_open = float(latest['open'])
        
        # فحص النتيجة بناءً على المسميات الجديدة
        won = (direction == "UP" and c_close > c_open) or \
              (direction == "DOWN" and c_close < c_open)
        return jsonify({"result": "WIN" if won else "LOSS"})
    except:
        return jsonify({"result": "ERROR"})

if __name__ == '__main__':
    import os
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
