from flask import Flask, jsonify, request
from flask_cors import CORS
import websocket, json, time, datetime

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

def analyze_strategy_v6(symbol):
    try:
        s = symbol.replace("/", "").replace(" ", "").upper()
        target = f"frx{s}"
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=20)
        # سحب 3000 تيك لرسم مستويات الدعم والمقاومة التاريخية
        ws.send(json.dumps({"ticks_history": target, "count": 3000, "end": "latest", "style": "ticks"}))
        result = json.loads(ws.recv())
        ws.close()
        
        ticks = [float(p) for p in result.get("history", {}).get("prices", [])]
        if len(ticks) < 30: return None, None

        current_p = ticks[-1]
        # استخراج مستويات الدعم والمقاومة من كامل البيانات ما عدا آخر 30 تيك
        historical_ticks = ticks[:-30]
        res_level = max(historical_ticks)
        sup_level = min(historical_ticks)
        
        last_30 = ticks[-30:]
        open_30 = last_30[0]
        close_30 = last_30[-1]

        # 1. تحليل اتجاه الـ 30 تيك
        trend_30 = "up" if close_30 > open_30 else "down"

        # 2. تحليل اتجاه آخر 5 تيكات (متتالية)
        last_5 = ticks[-5:]
        is_last_5_up = all(last_5[i] < last_5[i+1] for i in range(len(last_5)-1))
        is_last_5_down = all(last_5[i] > last_5[i+1] for i in range(len(last_5)-1))

        # 3. فحص الاختراق (Breakout Check)
        # اختراق مقاومة: كان تحتها وأصبح فوقها
        is_resistance_broken = (open_30 <= res_level) and (close_30 > res_level)
        # اختراق دعم: كان فوقه وأصبح تحته
        is_support_broken = (open_30 >= sup_level) and (close_30 < sup_level)

        # --- منطق القرار الصارم ---
        
        # دخول CALL: 
        # اتجاه 30 صاعد + آخر 5 صاعدين + بعيد عن المقاومة + لم يخترق المقاومة للتو
        if trend_30 == "up" and is_last_5_up and not is_resistance_broken:
            if close_30 < (res_level - 0.00003):
                return "call", current_p
            
        # دخول PUT: 
        # اتجاه 30 هابط + آخر 5 هابطين + بعيد عن الدعم + لم يخترق الدعم للتو
        elif trend_30 == "down" and is_last_5_down and not is_support_broken:
            if close_30 > (sup_level + 0.00003):
                return "put", current_p

        return None, None
    except:
        return None, None

@app.route('/check_signal')
def check_signal():
    global data_store
    pair = request.args.get('pair', 'EURUSD')
    now_ts, now_dt = time.time(), datetime.datetime.now()
    minute, second = now_dt.minute, now_dt.second

    if data_store["bot_stopped"]:
        return jsonify({"status": "target_reached", "msg": f"WIN: {data_store['win_count']}"})

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
                    if data_store["loss_count"] > 2: return jsonify({"status": "total_loss", "msg": "STOP"})
                data_store["is_waiting_result"] = False
                return jsonify({"status":"check_result","win":"true" if is_win else "false","msg":f"W:{data_store['win_count']}","next_clicks":data_store["next_clicks"]})
            except: pass
    else:
        if second == 56:
            if data_store["last_trade_minute"] != minute:
                action, entry_p = analyze_strategy_v6(pair)
                if action:
                    data_store.update({"is_waiting_result":True, "entry_price":entry_p, "last_trade_minute":minute, "last_action":action, "entry_time":now_ts})
                    return jsonify({"status": "trade", "action": action, "clicks": data_store["next_clicks"]})

    return jsonify({"status": "scanning"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
