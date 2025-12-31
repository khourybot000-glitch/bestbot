from flask import Flask, jsonify, request
from flask_cors import CORS
import websocket, json, time, datetime
import numpy as np

app = Flask(__name__)
CORS(app)

data_store = {
    "is_waiting_result": False,
    "entry_price": None,
    "last_trade_minute": -1,
    "last_action": None,
    "loss_count": 0,
    "next_clicks": 1,
    "win_count": 0,
    "target_wins": 15,
    "bot_stopped": False,
    "entry_time": 0 
}

def analyze_full_strategy(symbol):
    try:
        s = symbol.replace("/", "").replace(" ", "").upper()
        target = f"frx{s}"
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=20)
        # سحب 3000 تيك (100 شمعة × 30 تيك)
        ws.send(json.dumps({"ticks_history": target, "count": 3000, "end": "latest", "style": "ticks"}))
        result = json.loads(ws.recv())
        ws.close()
        
        ticks = [float(p) for p in result.get("history", {}).get("prices", [])]
        if len(ticks) < 1000: return None, None

        current_p = ticks[-1]

        # 1. تحديد مستويات الدعم والمقاومة الكبرى (100 شمعة)
        resistance_zone = max(ticks)
        support_zone = min(ticks)

        # 2. تحليل الزخم اللحظي (آخر 30 تيك مقسمة لـ 15 شمعة ميكرو)
        micro_changes = []
        for i in range(len(ticks)-30, len(ticks), 2):
            seg = ticks[i:i+2]
            micro_changes.append(1 if seg[-1] > seg[0] else -1)
        
        momentum = sum(micro_changes) # القوة الشرائية أو البيعية

        # --- منطق الدخول الصارم ---
        # دخول صعود: زخم قوي + بعيد عن المقاومة
        if momentum >= 6 and current_p < (resistance_zone - 0.00004):
            return "call", current_p
        
        # دخول هبوط: زخم ضعيف + بعيد عن الدعم
        elif momentum <= -6 and current_p > (support_zone + 0.00004):
            return "put", current_p

        return None, None
    except:
        return None, None

@app.route('/check_signal')
def check_signal():
    global data_store
    pair = request.args.get('pair', 'EURUSD')
    now_ts = time.time()
    now_dt = datetime.datetime.now()
    minute, second = now_dt.minute, now_dt.second

    if data_store["bot_stopped"]:
        return jsonify({"status": "target_reached", "msg": f"Target: {data_store['win_count']}"})

    # فحص النتيجة (بعد 60 ثانية من الدخول)
    if data_store["is_waiting_result"]:
        if (now_ts - data_store["entry_time"]) >= 58:
            try:
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=5)
                ws.send(json.dumps({"ticks": f"frx{pair.replace('/', '')}"}))
                curr_p = float(json.loads(ws.recv()).get("tick", {}).get("quote"))
                ws.close()
                
                is_win = (curr_p > data_store["entry_price"] and data_store["last_action"] == "call") or \
                         (curr_p < data_store["entry_price"] and data_store["last_action"] == "put")
                
                if is_win:
                    data_store["win_count"] += 1
                    data_store["loss_count"], data_store["next_clicks"] = 0, 1
                else:
                    data_store["loss_count"] += 1
                    data_store["next_clicks"] = 2 if data_store["loss_count"] == 1 else 6
                    if data_store["loss_count"] > 2: return jsonify({"status": "total_loss", "msg": "Stop"})

                data_store["is_waiting_result"] = False
                return jsonify({"status":"check_result","win":"true" if is_win else "false","msg":f"W:{data_store['win_count']}","next_clicks":data_store["next_clicks"]})
            except: pass

    # --- الدخول المبرمج عند الثانية 56 من كل دقيقة ---
    else:
        if second == 56:
            if data_store["last_trade_minute"] != minute:
                action, entry_p = analyze_full_strategy(pair)
                if action:
                    data_store.update({"is_waiting_result":True, "entry_price":entry_p, "last_trade_minute":minute, "last_action":action, "entry_time":now_ts})
                    return jsonify({"status": "trade", "action": action, "clicks": data_store["next_clicks"]})

    return jsonify({"status": "scanning"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
