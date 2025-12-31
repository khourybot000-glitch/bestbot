from flask import Flask, jsonify, request
from flask_cors import CORS
import websocket, json, datetime

app = Flask(__name__)
CORS(app)

def get_prices_data(symbol, count):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"ticks_history": f"frx{symbol.replace('/', '')}", "count": count, "end": "latest", "style": "ticks"}))
        result = json.loads(ws.recv())
        ws.close()
        return result.get("history", {}).get("prices", [])
    except: return []

@app.route('/check_signal')
def check_signal():
    pair = request.args.get('pair', 'EURUSD')
    now = datetime.datetime.now()
    s = now.second

    # التحليل عند الثانية 58
    if s == 58:
        # جلب 30 تيك (الدقيقة كاملة حسب نظامك)
        prices = get_prices_data(pair, 30)
        if len(prices) >= 30:
            t0 = prices[0]   # نقطة البداية المشتركة
            t15 = prices[15] # نهاية الشمعة الصغيرة (الجزء)
            t29 = prices[29] # نهاية الشمعة الكبيرة (الكل)

            # 1. اتجاه الشمعة الصغيرة (الجزء من 0 لـ 15)
            # صاعدة إذا كان تيك 15 أكبر من تيك 0، وهابطة إذا كان أصغر
            dir_small = "up" if t15 > t0 else "down"
            
            # 2. اتجاه الشمعة الكبيرة (الكل من 0 لـ 29)
            # صاعدة إذا كان تيك 29 أكبر من تيك 0، وهابطة إذا كان أصغر
            dir_large = "up" if t29 > t0 else "down"

            # الشرط الجوهري: اتجاه الجزء (0-15) عكس اتجاه الكل (0-29)
            if dir_small != dir_large:
                # الصفقة تكون باتجاه الشمعة الكبيرة (التي ابتلعت الاتجاه الأول)
                action = "call" if dir_large == "up" else "put"
                return jsonify({
                    "status": "trade", 
                    "action": action
                })

    # فحص النتيجة (عند الثانية 58 من الدقيقة التالية) لربطها بالـ Bookmark
    if s == 58:
        prices = get_prices_data(pair, 35)
        if len(prices) >= 30:
            # مقارنة السعر الحالي بسعر الدخول قبل 30 تيك
            res = "call" if prices[-1] > prices[-30] else "put"
            return jsonify({"status": "check_result", "direction": res})

    return jsonify({"status": "scanning"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
