from flask import Flask, jsonify, request
from flask_cors import CORS
import websocket, json, datetime

app = Flask(__name__)
CORS(app)

# مخزن البيانات والحالة
data_store = {
    "is_waiting_result": False,
    "entry_price": None,
    "last_trade_period": -1  # تخزين رقم الربع ساعة لمنع التكرار
}

def get_ticks_analysis(symbol, count=450):
    try:
        s = symbol.replace("/", "").replace(" ", "").upper()
        target = f"frx{s}"
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        
        request_data = {
            "ticks_history": target,
            "count": count,
            "end": "latest",
            "style": "ticks"
        }
        ws.send(json.dumps(request_data))
        result = json.loads(ws.recv())
        ws.close()
        
        prices = result.get("history", {}).get("prices", [])
        if len(prices) >= count:
            first_tick = float(prices[0])
            last_tick = float(prices[-1])
            if last_tick > first_tick: return "call", last_tick
            if last_tick < first_tick: return "put", last_tick
        return None, None
    except:
        return None, None

@app.route('/check_signal')
def check_signal():
    global data_store
    pair = request.args.get('pair', 'EURUSD')
    now = datetime.datetime.now()
    minute = now.minute
    second = now.second
    
    # تحديد رقم الربع ساعة الحالية (0, 15, 30, 45)
    current_period = (minute // 15) * 15
    # تحديد الدقيقة الأخيرة من الربع ساعة
    target_minutes = [14, 29, 44, 59]

    # --- 1. قسم النتيجة (مع نافذة سماح 5 ثوانٍ) ---
    if data_store["is_waiting_result"]:
        # السماح بطلب النتيجة من الثانية 58 حتى الثانية 03 من الدقيقة التالية
        if 58 <= second <= 59 or 0 <= second <= 3:
            try:
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=5)
                ws.send(json.dumps({"ticks": f"frx{pair.replace('/', '')}"}))
                res_data = json.loads(ws.recv())
                ws.close()
                
                current_p = float(res_data.get("tick", {}).get("quote"))
                res_dir = "call" if current_p > data_store["entry_price"] else "put"
                
                # تصفير الحالة للعودة للتحليل
                data_store["is_waiting_result"] = False
                data_store["entry_price"] = None
                print(f"[{pair}] Result Sent (Window active).")
                return jsonify({"status": "check_result", "direction": res_dir})
            except:
                pass

    # --- 2. قسم التحليل والدخول (مع نافذة سماح 5 ثوانٍ) ---
    else:
        # التحليل فقط في الدقائق المستهدفة وبدءاً من الثانية 57 حتى الثانية 02 من الدقيقة التالية
        is_in_time_window = (minute in target_minutes and second >= 57) or (minute == (current_period + 15) % 60 and second <= 2)
        
        if is_in_time_window:
            # التأكد أننا لم ندخل صفقة لهذا الربع ساعة بالفعل
            if data_store["last_trade_period"] != current_period:
                direction, entry_p = get_ticks_analysis(pair, 450)
                
                if direction:
                    data_store["is_waiting_result"] = True
                    data_store["entry_price"] = entry_p
                    data_store["last_trade_period"] = current_period
                    print(f"[{pair}] Trade Sent at second {second}. Window handled delay.")
                    return jsonify({"status": "trade", "action": direction})

    return jsonify({"status": "scanning"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
