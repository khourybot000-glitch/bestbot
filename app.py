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
    m, s = now.minute, now.second

    # الشرط: الدقيقة يجب أن تكون نهاية دورة الـ 5 دقائق (4, 9, 14, 19...) والثانية 56
    if (m % 5 == 4) and s == 56:
        prices = get_prices_data(pair, 500)
        if len(prices) >= 400:
            cp = prices[-1] # السعر الحالي
            # حساب بداية شمعة الـ 5 دقائق الحالية (منذ 296 ثانية تقريباً)
            s_5m = (m % 5 * 60) + s 
            o_5m = prices[-s_5m] if len(prices) >= s_5m else prices[0]
            # بداية الدقيقة الأخيرة
            o_1m = prices[-s] if len(prices) >= s else prices[-60]
            
            d5 = "call" if cp > o_5m else "put"
            d1 = "call" if cp > o_1m else "put"
            
            # لا يدخل إلا إذا اتفقت شمعة الـ 5 دقائق مع شمعة الدقيقة الأخيرة
            if d5 == d1:
                return jsonify({"status": "trade", "action": d5})

    # فحص النتيجة عند الثانية 58 من الدقيقة التالية (دقيقة الصفر/الخامسة)
    if (m % 5 == 0) and s == 58:
        prices = get_prices_data(pair, 100)
        if len(prices) >= 61:
            res = "call" if prices[-1] > prices[-61] else "put"
            return jsonify({"status": "check_result", "direction": res})

    return jsonify({"status": "scanning"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
