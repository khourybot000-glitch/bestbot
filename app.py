from flask import Flask, jsonify, request
from flask_cors import CORS
import websocket
import json
import datetime

app = Flask(__name__)
CORS(app)

# مخزن ديناميكي لكل الأزواج
db = {}

def get_deriv_price(symbol):
    try:
        # اتصال سريع للحصول على السعر اللحظي من Deriv
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=5)
        ws.send(json.dumps({"ticks": f"frx{symbol.replace('/', '')}"}))
        r = json.loads(ws.recv())
        ws.close()
        return r["tick"]["quote"] if "tick" in r else None
    except:
        return None

@app.route('/check_signal')
def check_signal():
    # استلام الزوج المختار من المتصفح
    pair = request.args.get('pair')
    if not pair:
        return jsonify({"status": "error", "message": "No pair selected"})

    now = datetime.datetime.now()
    m, s = now.minute, now.second
    
    # إنشاء مخزن بيانات خاص بهذا الزوج إذا لم يكن موجوداً
    if pair not in db:
        db[pair] = {"open10": None, "open1": None, "last_trade_min": -1}

    # 1. تسجيل سعر فتح الـ 10 دقائق (عند الدقائق 00, 10, 20...)
    if m % 10 == 0 and s <= 2:
        price = get_deriv_price(pair)
        if price: 
            db[pair]["open10"] = price
            print(f"[{pair}] Recorded 10m Open: {price}")

    # 2. تسجيل سعر فتح الدقيقة التاسعة (عند الدقائق 09, 19, 29...)
    if m % 10 == 9 and s <= 2:
        price = get_deriv_price(pair)
        if price: 
            db[pair]["open1"] = price
            print(f"[{pair}] Recorded 1m Open: {price}")

    # 3. تحليل الإشارة عند الثانية 56 من الدقيقة التاسعة
    if m % 10 == 9 and s >= 56 and db[pair]["last_trade_min"] != m:
        o10 = db[pair]["open10"]
        o1 = db[pair]["open1"]
        current_price = get_deriv_price(pair)
        
        if o10 and o1 and current_price:
            signal = ""
            # شروط الاستراتيجية الخاصة بك
            if current_price > o10 and current_price > o1: signal = "put"
            elif current_price < o10 and current_price < o1: signal = "call"
            
            if signal:
                db[pair]["last_trade_min"] = m 
                return jsonify({
                    "status": "trade", 
                    "action": signal, 
                    "pair": pair,
                    "details": f"Price:{current_price} | O10:{o10} | O1:{o1}"
                })

    return jsonify({
        "status": "scanning", 
        "pair": pair,
        "time": f"{m}:{s}",
        "o10": db[pair]["open10"], 
        "o1": db[pair]["open1"]
    })

@app.route('/')
def home():
    return "KHOURY BOT V3.9 - Multi-Pair Engine Active", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
