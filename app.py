from flask import Flask, jsonify, request
from flask_cors import CORS
import websocket
import json
import datetime

app = Flask(__name__)
# تفعيل CORS للسماح للمتصفح بالاتصال بالسيرفر بدون مشاكل أمنية
CORS(app)

def get_60s_analysis(symbol):
    """فتح اتصال سريع، تحليل اتجاهين متتاليين، ثم الإغلاق"""
    try:
        # الاتصال بـ Deriv عبر WebSocket
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=8)
        
        # طلب آخر 60 ثانية من الأسعار
        ws.send(json.dumps({
            "ticks_history": f"frx{symbol.replace('/', '')}",
            "adjust_start_time": 1,
            "count": 60,
            "end": "latest",
            "style": "ticks"
        }))
        
        result = json.loads(ws.recv())
        ws.close() # إغلاق الاتصال فوراً بعد استلام البيانات
        
        prices = result.get("history", {}).get("prices", [])
        
        if len(prices) >= 60:
            # تقسيم الدقيقة إلى نصفين (30 ثانية لكل نصف)
            first_half = prices[0:30]
            second_half = prices[30:60]

            # تحديد اتجاه النصف الأول
            first_trend = "up" if first_half[-1] > first_half[0] else "down"
            
            # تحديد اتجاه النصف الثاني
            second_trend = "up" if second_half[-1] > second_half[0] else "down"

            # الشرط: يجب أن يكون النصفان بنفس الاتجاه (صعود مستمر أو هبوط مستمر)
            if first_trend == "up" and second_trend == "up":
                return "call"
            elif first_trend == "down" and second_trend == "down":
                return "put"
                
        return None
    except Exception as e:
        print(f"Socket Error: {e}")
        return None

@app.route('/')
def home():
    return "KHOURY AI ENGINE V7.1 IS RUNNING", 200

@app.route('/check_signal')
def check_signal():
    pair = request.args.get('pair', 'EURUSD')
    now = datetime.datetime.now()
    s = now.second

    # تفعيل التحليل فقط في نافذة الثانية 56 لضمان دقة الشمعة القادمة
    if 55 <= s <= 58:
        action = get_60s_analysis(pair)
        if action:
            return jsonify({
                "status": "trade",
                "action": action,
                "pair": pair,
                "msg": f"Confirmed {action.upper()} trend in 30/30 window"
            })

    return jsonify({"status": "scanning", "sec": s})

if __name__ == '__main__':
    # تشغيل السيرفر على بورت 10000 المعتمد في Render
    app.run(host='0.0.0.0', port=10000)
