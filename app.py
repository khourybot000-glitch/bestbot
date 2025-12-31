from flask import Flask, jsonify, request
from flask_cors import CORS
import websocket, json, datetime

app = Flask(__name__)
CORS(app)

# مخزن البيانات والحالة
data_store = {
    "tick_0": None,
    "tick_30": None,
    "entry_price": None,
    "is_waiting_result": False
}

def get_current_price(symbol):
    try:
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"ticks": f"frx{symbol.replace('/', '')}", "subscribe": 0}))
        result = json.loads(ws.recv())
        ws.close()
        return result.get("tick", {}).get("quote")
    except: return None

@app.route('/check_signal')
def check_signal():
    global data_store
    pair = request.args.get('pair', 'EURUSD')
    now = datetime.datetime.now()
    s = now.second

    # --- القيد الصارم: إذا كان هناك صفقة، لا تسجل أي أسعار ---
    
    # 1. عند الثانية 0: يسجل السعر فقط إذا لم نكن ننتظر نتيجة صفقة سابقة
    if s == 0 and not data_store["is_waiting_result"]:
        data_store["tick_0"] = get_current_price(pair)

    # 2. عند الثانية 30: يسجل السعر فقط إذا لم نكن ننتظر نتيجة صفقة سابقة
    if s == 30 and not data_store["is_waiting_result"]:
        data_store["tick_30"] = get_current_price(pair)

    # 3. عند الثانية 58: (لحظة القرار أو لحظة النتيجة)
    if s == 58:
        current_58 = get_current_price(pair)
        
        # الحالة الأولى: إذا كنا ننتظر نتيجة (هنا الأولوية القصوى)
        if data_store["is_waiting_result"]:
            res_dir = "call" if current_58 > data_store["entry_price"] else "put"
            
            # بمجرد إرسال النتيجة، نقوم بتصفير كل شيء
            data_store["is_waiting_result"] = False
            data_store["entry_price"] = None
            data_store["tick_0"] = None  # لضمان عدم وجود بيانات قديمة
            data_store["tick_30"] = None
            
            return jsonify({"status": "check_result", "direction": res_dir})

        # الحالة الثانية: لا توجد صفقة، نقوم بالتحليل
        else:
            t0 = data_store["tick_0"]
            t30 = data_store["tick_30"]
            
            # لن يحلل إلا إذا توفرت أسعار الدقيقة الحالية (0 و 30)
            if t0 is not None and t30 is not None and current_58 is not None:
                is_small_up = t30 > t0
                is_small_down = t30 < t0
                is_large_up = current_58 > t0
                is_large_down = current_58 < t0

                action = None
                if is_small_up and is_large_down: action = "put"
                elif is_small_down and is_large_up: action = "call"

                if action:
                    data_store["is_waiting_result"] = True
                    data_store["entry_price"] = current_58
                    return jsonify({"status": "trade", "action": action})

    return jsonify({"status": "scanning"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
