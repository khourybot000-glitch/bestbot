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

    # الدخول: فقط عند الدقيقة 9 (نهاية الـ 10 دقائق) والثانية 56 تماماً
    if m % 10 == 9 and s == 56:
        action = get_direction(pair, 600)
        if action:
            return jsonify({"status": "trade", "action": action})

    # فحص النتيجة: عند الدقيقة 0 والثانية 58 (نهاية أول دقيقة)
    if m % 10 == 0 and s == 58:
        result_dir = get_direction(pair, 60)
        if result_dir:
            return jsonify({"status": "check_result", "direction": result_dir})

    return jsonify({"status": "scanning"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
