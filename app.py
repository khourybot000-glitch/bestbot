from flask import Flask, jsonify, request
from flask_cors import CORS
import websocket, json, time, datetime

app = Flask(__name__)
CORS(app)

# دالة لإعادة ضبط البيانات للصفر
def get_initial_data():
    return {
        "is_waiting_result": False,
        "entry_price": None,
        "last_trade_minute": -1,
        "last_action": None,
        "loss_count": 0,
        "next_clicks": 1,
        "win_count": 0,
        "target_wins": 15,
        "bot_stopped": False,
        "entry_time": 0,
        "last_request_time": time.time()
    }

data_store = get_initial_data()

def find_recent_color_levels(ticks_list):
    # تحويل التيكات لـ 20 شمعة (30 تيك لكل شمعة)
    candles = []
    for i in range(0, len(ticks_list), 30):
        seg = ticks_list[i:i+30]
        if len(seg) == 30:
            color = "green" if seg[-1] > seg[0] else "red"
            candles.append({
                "high": max(seg), 
                "low": min(seg), 
                "color": color
            })
    
    recent_res = None
    recent_sup = None
    
    # 1. البحث عن أقرب مقاومة (خضراء تليها حمراء) في الـ 19 شمعة السابقة
    for i in range(len(candles)-2, 0, -1):
        if candles[i-1]["color"] == "green" and candles[i]["color"] == "red":
            recent_res = max(candles[i-1]["high"], candles[i]["high"])
            break
            
    # 2. البحث عن أقرب دعم (حمراء تليها خضراء) في الـ 19 شمعة السابقة
    for i in range(len(candles)-2, 0, -1):
        if candles[i-1]["color"] == "red" and candles[i]["color"] == "green":
            recent_sup = min(candles[i-1]["low"], candles[i]["low"])
            break
            
    return recent_res, recent_sup

@app.route('/check_signal')
def check_signal():
    global data_store
    
    # ميزة الحذف التلقائي بعد دقيقتين خمول
    current_time = time.time()
    if current_time - data_store["last_request_time"] > 120:
        data_store = get_initial_data()
    data_store["last_request_time"] = current_time

    pair = request.args.get('pair', 'EURUSD')
    now_dt = datetime.datetime.now()
    minute, second = now_dt.minute, now_dt.second

    if data_store["bot_stopped"]:
        return jsonify({"status": "target_reached", "msg": f"Target: {data_store['win_count']}"})

    # فحص النتيجة بعد مرور 5 دقائق (300 ثانية)
    if data_store["is_waiting_result"]:
        if (current_time - data_store["entry_time"]) >= 298:
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
                    if data_store["loss_count"] > 2: 
                        data_store["bot_stopped"] = True
                        return jsonify({"status": "total_loss", "msg": "STOP"})
                
                data_store["is_waiting_result"] = False
                return jsonify({"status":"check_result","win":"true" if is_win else "false","msg":f"W:{data_store['win_count']}","next_clicks":data_store["next_clicks"]})
            except: pass
    else:
        # التحليل عند الثانية 56 من كل دقيقة
        if second == 56:
            if data_store["last_trade_minute"] != minute:
                try:
                    s = pair.replace("/", "").replace(" ", "").upper()
                    target = f"frx{s}"
                    ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=20)
                    ws.send(json.dumps({"ticks_history": target, "count": 600, "end": "latest", "style": "ticks"}))
                    result = json.loads(ws.recv())
                    ws.close()
                    
                    ticks = [float(p) for p in result.get("history", {}).get("prices", [])]
                    res_level, sup_level = find_color_based_levels(ticks)
                    
                    last_open = ticks[-30] # بداية الدقيقة الأخيرة
                    last_close = ticks[-1] # إغلاق الدقيقة الأخيرة
                    
                    action = None
                    # شرط اختراق المقاومة (شمعة صاعدة)
                    if res_level and last_open <= res_level and last_close > res_level:
                        action = "call"
                    # شرط اختراق الدعم (شمعة هابطة)
                    elif sup_level and last_open >= sup_level and last_close < sup_level:
                        action = "put"

                    if action:
                        data_store.update({
                            "is_waiting_result": True,
                            "entry_price": last_close,
                            "last_trade_minute": minute,
                            "last_action": action,
                            "entry_time": current_time
                        })
                        return jsonify({"status": "trade", "action": action, "clicks": data_store["next_clicks"]})
                except: pass

    return jsonify({"status": "scanning"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
