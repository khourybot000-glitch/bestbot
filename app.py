from flask import Flask, jsonify, request
from flask_cors import CORS
import websocket, json, datetime

app = Flask(__name__)
CORS(app)

data_store = {
    "is_waiting_result": False,
    "entry_price": None,
    "last_trade_period": -1,
    "last_action": None,
    "loss_count": 0,
    "next_clicks": 1,
    "win_count": 0,
    "target_wins": 15,
    "bot_stopped": False
}

def get_ticks_analysis(symbol, count=450):
    try:
        s = symbol.replace("/", "").replace(" ", "").upper()
        target = f"frx{s}"
        ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        ws.send(json.dumps({"ticks_history": target, "count": count, "end": "latest", "style": "ticks"}))
        result = json.loads(ws.recv())
        ws.close()
        prices = result.get("history", {}).get("prices", [])
        if len(prices) >= count:
            first, last = float(prices[0]), float(prices[-1])
            if last > first: return "call", last
            if last < first: return "put", last
        return None, None
    except: return None, None

@app.route('/check_signal')
def check_signal():
    global data_store
    pair = request.args.get('pair', 'EURUSD')
    now = datetime.datetime.now()
    minute, second = now.minute, now.second
    current_period = (minute // 15) * 15
    target_minutes = [14, 29, 44, 59]

    if data_store["bot_stopped"]:
        return jsonify({"status": "target_reached", "msg": f"GOAL: {data_store['win_count']} WINS"})

    if data_store["is_waiting_result"]:
        if (second >= 58) or (second <= 3):
            try:
                ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=5)
                ws.send(json.dumps({"ticks": f"frx{pair.replace('/', '')}"}))
                res_data = json.loads(ws.recv())
                ws.close()
                current_p = float(res_data.get("tick", {}).get("quote"))
                market_dir = "call" if current_p > data_store["entry_price"] else "put"
                is_win = (market_dir == data_store["last_action"])
                
                if is_win:
                    data_store["win_count"] += 1
                    data_store["loss_count"] = 0
                    data_store["next_clicks"] = 1
                    status_text = f"💰 PROFIT! ({data_store['win_count']}/15)"
                    if data_store["win_count"] >= data_store["target_wins"]:
                        data_store["bot_stopped"] = True
                else:
                    data_store["loss_count"] += 1
                    status_text = f"❌ LOSS #{data_store['loss_count']}"
                    if data_store["loss_count"] == 1: data_store["next_clicks"] = 2
                    elif data_store["loss_count"] == 2: data_store["next_clicks"] = 6
                    else:
                        return jsonify({"status": "total_loss", "msg": "STOP: MAX LOSS REACHED"})

                return jsonify({
                    "status": "check_result",
                    "win": "true" if is_win else "false",
                    "msg": status_text,
                    "next_clicks": data_store["next_clicks"],
                    "target_met": "true" if data_store["bot_stopped"] else "false"
                })
            except: pass
            finally: data_store["is_waiting_result"] = False

    else:
        is_in_time = (minute in target_minutes and second >= 57) or (minute == (current_period + 15) % 60 and second <= 1)
        if is_in_time and data_store["last_trade_period"] != current_period:
            direction, entry_p = get_ticks_analysis(pair, 450)
            if direction:
                opposite = "put" if direction == "call" else "call"
                data_store.update({"is_waiting_result": True, "entry_price": entry_p, "last_trade_period": current_period, "last_action": opposite})
                return jsonify({"status": "trade", "action": opposite, "clicks": data_store["next_clicks"]})

    return jsonify({"status": "scanning"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
