import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

@app.route('/analyze', methods=['GET'])
def analyze():
    pair = request.args.get('pair')
    try:
        # طلب 5 شموع (ترتيب الـ API: 0 هي الأحدث، 4 هي الأقدم)
        url = f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={pair}&timeframe=M1&limit=5"
        resp = requests.get(url, timeout=5).json()
        if not resp.get("success"): return jsonify({"signal": None})
        
        data = resp["data"]
        
        # 1. الاتجاه العام لـ 5 شموع (فتح الأقدم index 4 vs إغلاق الأحدث index 0)
        overall_open = float(data[4]['open'])
        current_close = float(data[0]['close'])
        
        is_trend_up = current_close > overall_open
        is_trend_down = current_close < overall_open

        # 2. تحليل الشمعة الأخيرة (إغلاق 0 vs فتح 0)
        current_open = float(data[0]['open'])
        is_green = current_close > current_open
        is_red = current_close < current_open

        # القرار: توافق الاتجاه مع لون الشمعة
        if is_trend_up and is_green: return jsonify({"signal": "CALL"})
        if is_trend_down and is_red: return jsonify({"signal": "PUT"})
            
    except: pass
    return jsonify({"signal": None})

@app.route('/check', methods=['GET'])
def check():
    pair = request.args.get('pair')
    direction = request.args.get('direction')
    try:
        # لضمان النتيجة، نطلب قائمة الـ 5 شموع كاملة من جديد ونفحص index 0
        url = f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={pair}&timeframe=M1&limit=5"
        resp = requests.get(url, timeout=5).json()
        latest = resp['data'][0]
        
        c_close = float(latest['close'])
        c_open = float(latest['open'])
        
        won = (direction == "CALL" and c_close > c_open) or \
              (direction == "PUT" and c_close < c_open)
        return jsonify({"result": "WIN" if won else "LOSS"})
    except:
        return jsonify({"result": "ERROR"})

if __name__ == '__main__':
    import os
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
