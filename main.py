import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

@app.route('/analyze', methods=['GET'])
def analyze():
    pair = request.args.get('pair')
    try:
        url = f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={pair}&timeframe=M5&limit=5"
        data = requests.get(url, timeout=5).json()['data']
        closes = [float(d['close']) for d in data]
        opens = [float(d['open']) for d in data]
        
        sma_3 = sum(closes[:3]) / 3
        momentum = closes[0] - closes[4]
        
        # منطق الارتداد
        is_call = (closes[0] > sma_3 and momentum > 0 and opens[0] > closes[0])
        is_put = (closes[0] < sma_3 and momentum < 0 and opens[0] < closes[0])
        
        if is_call: return jsonify({"signal": "CALL"})
        if is_put: return jsonify({"signal": "PUT"})
    except: pass
    return jsonify({"signal": None})

@app.route('/check', methods=['GET'])
def check():
    pair = request.args.get('pair')
    direction = request.args.get('direction')
    try:
        url = f"https://mrbeaxt.site/Qx/Qx.php?format=json&pair={pair}&timeframe=M1&limit=1"
        c = requests.get(url, timeout=5).json()['data'][0]
        won = (direction == "CALL" and float(c['close']) > float(c['open'])) or \
              (direction == "PUT" and float(c['close']) < float(c['open']))
        return jsonify({"result": "WIN" if won else "LOSS"})
    except: pass
    return jsonify({"result": "ERROR"})

if __name__ == '__main__':
    import os
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
