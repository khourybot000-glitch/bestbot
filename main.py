from flask import Flask, request, jsonify
import requests
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

def get_candles(pair):
    try:
        # طلب البيانات مع التأكد من جلب عدد كافٍ من الشموع
        url = f"https://mrbeaxt.site/Qx/Qx.php?pair={pair}&limit=5"
        response = requests.get(url, timeout=10)
        data = response.json()
        return data
    except Exception as e:
        print(f"Error: {e}")
        return None

@app.route('/analyze', methods=['GET'])
def analyze():
    pair = request.args.get('pair')
    data = get_candles(pair)
    
    if not data or len(data) < 2:
        return jsonify({"signal": None, "error": "Insufficient data"})

    # البحث عن الشموع بناءً على الـ ID لضمان الدقة المطلقة
    # m2_now (الشمعة الثانية - الأحدث) -> id: 1
    # m1_prev (الشمعة الأولى - السابقة) -> id: 2
    
    m2_now = next((c for c in data if int(c.get('id', 0)) == 1), None)
    m1_prev = next((c for c in data if int(c.get('id', 0)) == 2), None)

    if not m1_prev or not m2_now:
        # إذا لم يجد الـ IDs المطلوبة، نأخذ أول عنصرين كاحتياط
        m2_now = data[0]
        m1_prev = data[1]

    # استخراج الأسعار وتحويلها لأرقام
    open1, close1 = float(m1_prev['open']), float(m1_prev['close'])
    open2, close2 = float(m2_now['open']), float(m2_now['close'])

    # تحديد حالة الشمعة (Green = Close > Open)
    m1_is_up = close1 > open1    # صاعدة (الأولى)
    m1_is_down = close1 < open1  # هابطة (الأولى)
    
    m2_is_up = close2 > open2    # صاعدة (الثانية)
    m2_is_down = close2 < open2  # هابطة (الثانية)

    signal = None

    # الاستراتيجية المطلوبة:
    # 1. الأولى صاعدة + الثانية هابطة -> هبوط (DOWN)
    if m1_is_up and m2_is_down:
        signal = "DOWN"
    
    # 2. الأولى هابطة + الثانية صاعدة -> صعود (UP)
    elif m1_is_down and m2_is_up:
        signal = "UP"

    print(f"[{pair}] Analysis -> M1: {'UP' if m1_is_up else 'DOWN'} | M2: {'UP' if m2_is_up else 'DOWN'} | Signal: {signal}")

    return jsonify({"signal": signal})

@app.route('/check', methods=['GET'])
def check():
    pair = request.args.get('pair')
    direction = request.args.get('direction')
    data = get_candles(pair)
    if not data: return jsonify({"result": "ERROR"})

    # فحص نتيجة الشمعة الأحدث بعد انتهاء الصفقة (id: 1)
    last = next((c for c in data if int(c.get('id', 0)) == 1), data[0])
    is_up = float(last['close']) > float(last['open'])
    is_down = float(last['close']) < float(last['open'])

    result = "LOSS"
    if (direction == "UP" and is_up) or (direction == "DOWN" and is_down):
        result = "WIN"
    
    return jsonify({"result": result})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
