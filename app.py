import json
import websocket
import pandas as pd
import time
from datetime import datetime
from threading import Thread
from flask import Flask, render_template_string, jsonify, request
from pymongo import MongoClient
from bson.objectid import ObjectId

app = Flask(__name__)

# --- MongoDB ---
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client['KHOURY_V8']
users_col = db['users']

DERIV_WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

# --- Logging ---
def add_log(user_id, message):
    now = datetime.now().strftime("%H:%M:%S")
    users_col.update_one(
        {"_id": ObjectId(user_id)},
        {"$push": {"logs": {"$each": [f"[{now}] {message}"], "$slice": -20}}}
    )

# --- Strategy ---
def compute_logic(ticks):

    if len(ticks) < 240:
        return "NONE"

    start = ticks[0]
    end = ticks[-1]

    if end > start:
        trend = "UP"
    elif end < start:
        trend = "DOWN"
    else:
        return "NONE"

    if trend == "UP":
        return "PUT"
    else:
        return "CALL"

# --- Check Trade Result ---
def check_status(user_id):

    user = users_col.find_one({"_id": ObjectId(user_id)})
    if not user or not user.get("active_contract"):
        return

    ws = websocket.create_connection(DERIV_WS_URL)

    ws.send(json.dumps({"authorize": user['token']}))
    ws.recv()

    ws.send(json.dumps({
        "proposal_open_contract": 1,
        "contract_id": user['active_contract']
    }))

    res = json.loads(ws.recv())
    ws.close()

    contract = res.get("proposal_open_contract")

    if contract and contract.get("is_sold"):

        profit = float(contract.get("profit", 0))
        new_total = user.get('total_profit', 0) + profit

        if profit > 0:

            add_log(user_id, f"WIN +{profit}")

            users_col.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {
                    "active_contract": None,
                    "total_profit": new_total,
                    "wins": user.get('wins', 0) + 1,
                    "current_stake": user['base_stake'],
                    "consecutive_losses": 0
                }}
            )

        else:

            losses = user.get('consecutive_losses', 0) + 1
            new_stake = round(user['current_stake'] * 2.2, 2)

            add_log(user_id, f"LOSS next stake {new_stake}")

            if losses >= 4:

                add_log(user_id, "4 LOSSES BOT STOPPED")
                users_col.delete_one({"_id": ObjectId(user_id)})

            else:

                users_col.update_one(
                    {"_id": ObjectId(user_id)},
                    {"$set": {
                        "active_contract": None,
                        "total_profit": new_total,
                        "losses": user.get('losses', 0) + 1,
                        "current_stake": new_stake,
                        "consecutive_losses": losses
                    }}
                )

        if new_total >= user.get('tp', 9999):

            add_log(user_id, "TAKE PROFIT REACHED")
            users_col.delete_one({"_id": ObjectId(user_id)})

# --- Bot Loop ---
def user_loop(user_id):

    while True:

        try:

            user = users_col.find_one({"_id": ObjectId(user_id)})
            if not user:
                break

            if user.get("active_contract"):

                time.sleep(58)
                check_status(user_id)
                continue

            now = time.localtime()

            if now.tm_sec == 58 and now.tm_min % 5 == 3:

                ws = websocket.create_connection(DERIV_WS_URL, timeout=15)

                ws.send(json.dumps({
                    "ticks_history": user['symbol'],
                    "count": 240,
                    "end": "latest",
                    "style": "ticks"
                }))

                res = json.loads(ws.recv())
                ws.close()

                if 'history' in res:

                    signal = compute_logic(res['history']['prices'])

                    if signal != "NONE":

                        ws = websocket.create_connection(DERIV_WS_URL)

                        ws.send(json.dumps({"authorize": user['token']}))
                        auth = json.loads(ws.recv())

                        if "authorize" in auth:

                            currency = auth["authorize"]["currency"]

                            ws.send(json.dumps({
                                "buy": 1,
                                "price": user['current_stake'],
                                "parameters": {
                                    "amount": user['current_stake'],
                                    "basis": "stake",
                                    "contract_type": signal,
                                    "currency": currency,
                                    "duration": 54,
                                    "duration_unit": "s",
                                    "symbol": user['symbol']
                                }
                            }))

                            trade = json.loads(ws.recv())

                            if "buy" in trade:

                                add_log(user_id, f"ENTER {signal} {user['current_stake']}")

                                users_col.update_one(
                                    {"_id": ObjectId(user_id)},
                                    {"$set": {
                                        "active_contract": trade["buy"]["contract_id"]
                                    }}
                                )

                        ws.close()

                time.sleep(1)

            time.sleep(0.5)

        except:
            time.sleep(5)

# --- Web Interface ---
HTML = """
<!DOCTYPE html>
<html>
<head>
<style>
body{background:#05080a;color:#fff;font-family:sans-serif;padding:20px;display:flex;justify-content:center}
.card{background:#0d1117;padding:25px;border-radius:15px;width:350px;border:1px solid #30363d}
input, select{width:100%;padding:10px;margin:5px 0;background:#010409;color:#58a6ff;border:1px solid #30363d}
.btn{width:100%;padding:15px;border:none;border-radius:8px;font-weight:bold;cursor:pointer;margin-top:10px}
</style>
</head>
<body>

<div class="card">

<h2>KHOURY V8</h2>

<input type="email" id="email" placeholder="Email">

<div id="fields">

<input type="password" id="token" placeholder="API Token">

<select id="symbol">
<option value="R_100">Volatility 100</option>
<option value="R_75">Volatility 75</option>
</select>

<input type="number" id="stake" value="1">

<input type="number" id="tp" value="10">

</div>

<button class="btn" onclick="start()">START BOT</button>

<div id="stats"></div>
<div id="logs" style="height:120px;overflow:auto"></div>

<button class="btn" style="background:red" onclick="stop()">STOP BOT</button>

</div>

<script>

async function start(){

let d={
email:email.value,
token:token.value,
symbol:symbol.value,
stake:stake.value,
tp:tp.value
}

await fetch('/manage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)})

}

async function stop(){

await fetch('/manage',{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email.value})})

}

setInterval(async()=>{

let r=await fetch('/check/'+email.value)
let d=await r.json()

if(d.found){

stats.innerHTML=`Wins ${d.wins} | Losses ${d.losses}<br>Profit ${d.total_profit}`

logs.innerHTML=d.logs.reverse().join('<br>')

}

},1000)

</script>

</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(HTML)

@app.route('/check/<email>')
def check(email):

    u = users_col.find_one({"email": email})

    if u:
        u['_id'] = str(u['_id'])
        return jsonify({"found": True, **u})

    return jsonify({"found": False})

@app.route('/manage', methods=['POST','DELETE'])
def manage():

    if request.method == 'POST':

        req = request.json

        uid = users_col.insert_one({

            "email":req['email'],
            "token":req['token'],
            "symbol":req['symbol'],
            "base_stake":float(req['stake']),
            "current_stake":float(req['stake']),
            "tp":float(req['tp']),
            "total_profit":0.0,
            "wins":0,
            "losses":0,
            "consecutive_losses":0,
            "active_contract":None,
            "logs":[]
        }).inserted_id

        Thread(target=user_loop,args=(str(uid),),daemon=True).start()

    else:

        users_col.delete_one({"email": request.json['email']})

    return jsonify({"status":"ok"})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
