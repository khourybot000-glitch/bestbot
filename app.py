from flask import Flask, jsonify, request
from flask_cors import CORS
import websocket, json, datetime

app = Flask(__name__)
CORS(app)

def get_prices_data(symbol, count):
    try:
        # تحويل اسم الزوج ليتناسب مع Deriv (مثلاً EURUSD تصبح frxEURUSD)
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
    m = now.minute

    # --- القسم الأول: تحليل الابتلاع ودخول الصفقة (عند الثانية 58) ---
    if s == 58:
        # جلب 30 تيك (تمثل دقيقة واحدة عندك)
        prices = get_prices_data(pair, 30)
        if len(prices) >= 30:
            # تقسيم الدقيقة لشمعتين (كل شمعة 15 تيك تقريباً)
            o1, c1 = prices[0], prices[15]
            o2, c2 = prices[15], prices[29]

            action = ""
            # منطق الشمعة الابتلاعية
            if (c2 > o2) and (c1 < o1) and (c2 > o1): # ابتلاع شرائي
                action = "call"
            elif (c2 < o2) and (c1 > o1) and (c2 < o1): # ابتلاع بيعي
                action = "put"
            
            if action:
                return jsonify({"status": "trade", "action": action})

    # --- القسم الثاني: فحص نتيجة الصفقة السابقة (عند الثانية 58) ---
    # الـ Bookmark ينتظر "check_result" ليقرر المضاعفة القادمة
    if s == 58:
        # بما أن الدقيقة 30 تيك، نقارن السعر الحالي بالسعر قبل 30 تيك
        prices = get_prices_data(pair, 35)
        if len(prices) >= 30:
            # تحديد اتجاه السعر في الدقيقة الماضية
            res_dir = "call" if prices[-1] > prices[-30] else "put"
            return jsonify({
                "status": "check_result", 
                "direction": res_dir
            })

    return jsonify({"status": "scanning"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
