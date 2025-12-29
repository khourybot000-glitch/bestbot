from flask import Flask, jsonify, request
from flask_cors import CORS
import websocket
import json
import datetime

app = Flask(__name__)
CORS(app)

def get_direction(symbol, count):
    try:
        # الاتصال بـ API دريف لجلب بيانات الشموع
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=8)
        ws.send(json.dumps({
            "ticks_history": f"frx{symbol.replace('/', '')}",
            "adjust_start_time": 1,
            "count": count,
            "end": "latest",
            "style": "ticks"
        }))
        result = json.loads(ws.recv())
        ws.close()
        prices = result.get("history", {}).get("prices", [])
        
        if len(prices) >= count:
            # المنطق العادي: صعود إذا كان السعر الحالي أكبر من السعر قبل 600 تيك
            return "call" if prices[-1] > prices[0] else "put"
    except Exception as e:
        print(f"Error: {e}")
        return None
    return None

@app.route('/check_signal')
def check_signal():
    pair = request.args.get('pair', 'EURUSD')
    now = datetime.datetime.now()
    m = now.minute
    s = now.second

    # --- لحظة دخول الصفقة (الدقيقة 9 والثانية 56) ---
    if m % 10 == 9 and s == 56:
        actual_dir = get_direction(pair, 600) # التحليل الفني الحقيقي
        if actual_dir:
            # عكس الإشارة: إذا كان السوق صاعداً ندخل هبوط (Put) والعكس صحيح
            action = "put" if actual_dir == "call" else "call"
            return jsonify({"status": "trade", "action": action})

    # --- لحظة فحص النتيجة (الدقيقة 0 والثانية 58) ---
    if m % 10 == 0 and s == 58:
        result_dir = get_direction(pair, 60) # اتجاه أول دقيقة فعلياً
        if result_dir:
            # نعكس النتيجة هنا أيضاً لتتوافق مع دخولنا العكسي في نظام المضاعفات
            reversed_result = "put" if result_dir == "call" else "call"
            return jsonify({"status": "check_result", "direction": reversed_result})

    return jsonify({"status": "scanning"})

if __name__ == '__main__':
    # تشغيل السيرفر على بورت 10000 المتوافق مع Render
    app.run(host='0.0.0.0', port=10000)
