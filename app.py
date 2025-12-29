from flask import Flask, jsonify, request
from flask_cors import CORS
import websocket
import json
import datetime

app = Flask(__name__)
CORS(app)

market_data = {}

def get_deriv_price(symbol):
    # تحويل الرمز لصيغة Deriv (مثل EURUSD تصبح frxEURUSD)
    deriv_symbol = f"frx{symbol.replace('/', '')}"
    try:
        # استخدام الرابط الخاص بك
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
        ws.send(json.dumps({"ticks": deriv_symbol}))
        result = json.loads(ws.recv())
        ws.close()
        if "tick" in result:
            return result["tick"]["quote"]
    except Exception as e:
        print(f"Error fetching from Deriv: {e}")
    return None

@app.route('/signal')
def get_signal():
    pair = request.args.get('pair', 'EURUSD')
    now = datetime.datetime.now()
    m = now.minute
    s = now.second

    try:
        current_price = get_deriv_price(pair)
        if not current_price:
            return jsonify({"status": "error", "message": "Deriv Connection Failed"})

        if pair not in market_data:
            market_data[pair] = {'open10': None, 'open1': None}

        # تسجيل سعر الفتح للعشر دقائق
        if m % 10 == 0 and s == 0:
            market_data[pair]['open10'] = current_price
        
        # تسجيل سعر الفتح للدقيقة الأخيرة
        if m % 10 == 9 and s == 0:
            market_data[pair]['open1'] = current_price

        # تحليل الإشارة عند الثانية 58
        if m % 10 == 9 and s >= 58:
            d = market_data[pair]
            if d['open10'] and d['open1']:
                is_10m_up = current_price > d['open10']
                is_1m_up = current_price > d['open1']
                
                if is_10m_up and is_1m_up:
                    return jsonify({"status": "trade", "action": "put", "price": current_price})
                elif not is_10m_up and not is_1m_up:
                    return jsonify({"status": "trade", "action": "call", "price": current_price})
                
        return jsonify({"status": "scanning", "pair": pair, "price": current_price})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
