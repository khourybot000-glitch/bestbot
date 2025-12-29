from flask import Flask, jsonify, request
from flask_cors import CORS
import websocket
import json
import datetime

app = Flask(__name__)
CORS(app)

def get_direction(symbol, count):
    try:
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
            # الاتجاه الفعلي للسوق
            return "call" if prices[-1] > prices[0] else "put"
    except:
        return None
    return None

@app.route('/check_signal')
def check_signal():
    pair = request.args.get('pair', 'EURUSD')
    now = datetime.datetime.now()
    m = now.minute
    s = now.second

    # 1. لحظة دخول الصفقة (الدقيقة 9:56)
    if m % 10 == 9 and s == 56:
        market_dir = get_direction(pair, 600) # تحليل الـ 10 دقائق
        if market_dir:
            # نحن ندخل عكس اتجاه السوق
            my_action = "put" if market_dir == "call" else "call"
            return jsonify({"status": "trade", "action": my_action})

    # 2. لحظة فحص النتيجة (الدقيقة 0:58)
    if m % 10 == 0 and s == 58:
        real_market_result = get_direction(pair, 60) # ما حدث فعلياً في أول دقيقة
        if real_market_result:
            # هام جداً: نرسل الاتجاه الحقيقي للسوق كما هو
            # البوت في المتصفح سيقارن (هل دخولي العكسي == ما حدث في السوق؟)
            return jsonify({"status": "check_result", "direction": real_market_result})

    return jsonify({"status": "scanning"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
