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
        # الاتجاه العام (إغلاق الأحدث 0 vs فتح الأقدم 4) ليعبر عن حركة 5 دقائق
        is_trend_up = float(data[0]['close']) > float(data[3]['open'])
        is_trend_down = float(data[0]['close']) < float(data[3]['open'])
        
        # تأكيد الشمعة الحالية (لضمان وجود زخم في نفس الاتجاه)
        is_green = float(data[0]['close']) > float(data[0]['open'])
        is_red = float(data[0]['close']) < float(data[0]['open'])

        if is_trend_up and is_green: return jsonify({"signal": "DOWN"})
        if is_trend_down and is_red: return jsonify({"signal": "UP"})
    except: pass
    return jsonify({"signal": None})

@app.route('/check', methods=['GET'])
def check():
    pair = request.args.get('pair')
    direction = request.args.get('direction') 
    try:
        url = f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={pair}&timeframe=M1&limit=5"
        resp = requests.get(url, timeout=5).json()
        data = resp['data']
        
        # التعديل هنا: المقارنة بين إغلاق الشمعة 0 وفتح الشمعة 4
        current_close = float(data[0]['close'])
        start_open = float(data[4]['open'])
        
        # فحص النتيجة بناءً على اتجاه الـ 5 دقائق بالكامل
        won = (direction == "UP" and current_close > start_open) or \
              (direction == "DOWN" and current_close < start_open)
              
        return jsonify({"result": "WIN" if won else "LOSS"})
    except:
        return jsonify({"result": "ERROR"})

if __name__ == '__main__':
    import os
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
