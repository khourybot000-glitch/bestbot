from flask import Flask, request, jsonify
import requests
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

def get_candles(pair):
    try:
        # جلب آخر شمعتين فقط للتحليل
        url = f"https://mrbeaxt.site/Qx/Qx.php?pair={pair}&limit=2"
        response = requests.get(url, timeout=10)
        data = response.json()
        return data
    except:
        return None

@app.route('/analyze', methods=['GET'])
def analyze():
    pair = request.args.get('pair')
    data = get_candles(pair)
    
    if not data or len(data) < 2:
        return jsonify({"signal": None, "error": "No data"})

    # الشمعة 2 هي الأحدث (الحالية)، الشمعة 1 هي السابقة
    c2 = data[0] # Current (M2)
    c1 = data[1] # Previous (M1)

    # تحديد لون الشموع
    c1_up = float(c1['close']) > float(c1['open'])
    c1_down = float(c1['close']) < float(c1['open'])
    
    c2_up = float(c2['close']) > float(c2['open'])
    c2_down = float(c2['close']) < float(c2['open'])

    signal = None

    # تطبيق الإستراتيجية المطلوبة
    # 1. صاعدة ثم هابطة -> هبوط
    if c1_up and c2_down:
        signal = "DOWN"
    
    # 2. هابطة ثم صاعدة -> صعود
    elif c1_down and c2_up:
        signal = "UP"

    return jsonify({"signal": signal})

@app.route('/check', methods=['GET'])
def check():
    pair = request.args.get('pair')
    direction = request.args.get('direction')
    data = get_candles(pair)
    
    if not data:
        return jsonify({"result": "ERROR"})

    # فحص الشمعة الأخيرة بعد دخول الصفقة
    last_candle = data[0]
    is_up = float(last_candle['close']) > float(last_candle['open'])
    is_down = float(last_candle['close']) < float(last_candle['open'])

    result = "LOSS"
    if (direction == "UP" and is_up) or (direction == "DOWN" and is_down):
        result = "WIN"
    
    return jsonify({"result": result})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
