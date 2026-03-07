import json
import websocket
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
db = client['KHOURY_V9']
users_col = db['users']

DERIV_WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

# --- Logs ---
def add_log(uid,msg):
    now=datetime.now().strftime("%H:%M:%S")
    users_col.update_one({"_id":ObjectId(uid)},{"$push":{"logs":{"$each":[f"[{now}] {msg}"],"$slice":-30}}})

# --- Strategy ---
def compute_logic(ticks):

    if len(ticks)<240:
        return "NONE"

    start=ticks[0]
    end=ticks[-1]

    if end>start:
        return "PUT"
    elif end<start:
        return "CALL"

    return "NONE"

# --- Check Result ---
def check_status(uid):

    user=users_col.find_one({"_id":ObjectId(uid)})
    if not user or not user.get("active_contract"):
        return

    ws=websocket.create_connection(DERIV_WS_URL)

    ws.send(json.dumps({"authorize":user["token"]}))
    ws.recv()

    ws.send(json.dumps({
        "proposal_open_contract":1,
        "contract_id":user["active_contract"]
    }))

    res=json.loads(ws.recv())
    ws.close()

    contract=res.get("proposal_open_contract")

    if contract and contract.get("is_sold"):

        profit=float(contract.get("profit",0))
        total=user.get("total_profit",0)+profit

        if profit>0:

            add_log(uid,f"WIN {profit}")

            users_col.update_one({"_id":ObjectId(uid)},{"$set":{
                "active_contract":None,
                "total_profit":total,
                "wins":user.get("wins",0)+1,
                "current_stake":user["base_stake"],
                "consecutive_losses":0
            }})

        else:

            losses=user.get("consecutive_losses",0)+1
            stake=round(user["current_stake"]*2.2,2)

            add_log(uid,f"LOSS next {stake}")

            if losses>=4:

                add_log(uid,"STOP AFTER 4 LOSSES")

                users_col.update_one({"_id":ObjectId(uid)},{"$set":{
                    "status":"stopped"
                }})

            else:

                users_col.update_one({"_id":ObjectId(uid)},{"$set":{
                    "active_contract":None,
                    "losses":user.get("losses",0)+1,
                    "current_stake":stake,
                    "consecutive_losses":losses
                }})

        if total>=user.get("tp",9999):

            add_log(uid,"TAKE PROFIT")

            users_col.update_one({"_id":ObjectId(uid)},{"$set":{
                "status":"stopped"
            }})

# --- Bot Loop ---
def user_loop(uid):

    while True:

        try:

            user=users_col.find_one({"_id":ObjectId(uid)})

            if not user or user.get("status")!="running":
                break

            if user.get("active_contract"):

                time.sleep(58)
                check_status(uid)
                continue

            now=time.localtime()

            if now.tm_sec==58 and now.tm_min%5==3:

                ws=websocket.create_connection(DERIV_WS_URL)

                ws.send(json.dumps({
                    "ticks_history":user["symbol"],
                    "count":240,
                    "end":"latest",
                    "style":"ticks"
                }))

                data=json.loads(ws.recv())
                ws.close()

                if "history" in data:

                    signal=compute_logic(data["history"]["prices"])

                    if signal!="NONE":

                        ws=websocket.create_connection(DERIV_WS_URL)

                        ws.send(json.dumps({"authorize":user["token"]}))
                        auth=json.loads(ws.recv())

                        currency=auth["authorize"]["currency"]

                        ws.send(json.dumps({
                            "buy":1,
                            "price":user["current_stake"],
                            "parameters":{
                                "amount":user["current_stake"],
                                "basis":"stake",
                                "contract_type":signal,
                                "currency":currency,
                                "duration":54,
                                "duration_unit":"s",
                                "symbol":user["symbol"]
                            }
                        }))

                        trade=json.loads(ws.recv())

                        if "buy" in trade:

                            add_log(uid,f"ENTER {signal}")

                            users_col.update_one({"_id":ObjectId(uid)},{"$set":{
                                "active_contract":trade["buy"]["contract_id"]
                            }})

                        ws.close()

                time.sleep(1)

            time.sleep(0.5)

        except:
            time.sleep(5)

# --- Resume Sessions ---
def resume_sessions():

    for user in users_col.find({"status":"running"}):
        Thread(target=user_loop,args=(str(user["_id"]),),daemon=True).start()

# --- Web UI ---
HTML="""
<!DOCTYPE html>
<html>
<head>
<style>
body{background:#0a0f14;color:white;font-family:sans-serif;display:flex;justify-content:center;padding:30px}
.card{background:#111827;padding:25px;border-radius:12px;width:360px}
input,select{width:100%;padding:10px;margin:6px 0;background:#000;color:#58a6ff;border:1px solid #333}
button{width:100%;padding:15px;border:none;border-radius:8px;font-weight:bold;margin-top:10px;cursor:pointer}
#logs{height:130px;overflow:auto;background:#000;padding:6px;font-size:12px;margin-top:10px}
</style>
</head>

<body>

<div class="card">

<h2>KHOURY BOT</h2>

<input id="email" placeholder="Email">

<input id="token" placeholder="API Token">

<select id="symbol">
<option value="R_100">Volatility 100</option>
<option value="R_75">Volatility 75</option>
</select>

<input id="stake" value="1" type="number">
<input id="tp" value="10" type="number">

<button id="mainBtn" onclick="toggle()">START</button>

<div id="stats"></div>

<div id="logs"></div>

</div>

<script>

let running=false

async function toggle(){

if(!running){

let d={
email:email.value,
token:token.value,
symbol:symbol.value,
stake:stake.value,
tp:tp.value
}

await fetch("/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(d)})

running=true
mainBtn.innerText="STOP"
mainBtn.style.background="red"

}else{

await fetch("/stop",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({email:email.value})})

running=false
mainBtn.innerText="START"
mainBtn.style.background="green"

}

}

setInterval(async()=>{

if(!email.value) return

let r=await fetch("/check/"+email.value)
let d=await r.json()

if(d.found){

running=d.status=="running"

mainBtn.innerText=running?"STOP":"START"

stats.innerHTML=`Wins ${d.wins} | Losses ${d.losses}<br>Profit ${d.total_profit}`

logs.innerHTML=d.logs.reverse().join("<br>")

}

},1000)

</script>

</body>
</html>
"""

# --- Routes ---
@app.route("/")
def home():
    return render_template_string(HTML)

@app.route("/check/<email>")
def check(email):

    u=users_col.find_one({"email":email})

    if u:
        u["_id"]=str(u["_id"])
        return jsonify({"found":True,**u})

    return jsonify({"found":False})

@app.route("/start",methods=["POST"])
def start():

    d=request.json

    u=users_col.find_one({"email":d["email"]})

    if not u:

        uid=users_col.insert_one({
            "email":d["email"],
            "token":d["token"],
            "symbol":d["symbol"],
            "base_stake":float(d["stake"]),
            "current_stake":float(d["stake"]),
            "tp":float(d["tp"]),
            "wins":0,
            "losses":0,
            "total_profit":0,
            "consecutive_losses":0,
            "active_contract":None,
            "logs":[],
            "status":"running"
        }).inserted_id

        Thread(target=user_loop,args=(str(uid),),daemon=True).start()

    else:

        users_col.update_one({"email":d["email"]},{"$set":{"status":"running"}})

        Thread(target=user_loop,args=(str(u["_id"]),),daemon=True).start()

    return jsonify({"ok":True})

@app.route("/stop",methods=["POST"])
def stop():

    email=request.json["email"]

    users_col.update_one({"email":email},{"$set":{"status":"stopped"}})

    return jsonify({"ok":True})

# --- Run ---
if __name__=="__main__":

    resume_sessions()

    app.run(host="0.0.0.0",port=5000)
