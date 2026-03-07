import json
import websocket
import time
from datetime import datetime
from threading import Thread
from flask import Flask, render_template_string, jsonify, request
from pymongo import MongoClient
from bson.objectid import ObjectId

app = Flask(__name__)

MONGO_URI="mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
client=MongoClient(MONGO_URI)
db=client["KHOURY_V23"]
users=db["users"]

DERIV="wss://blue.derivws.com/websockets/v3?app_id=16929"

# ---------------- LOG ----------------
def log(uid,msg):
    t=datetime.now().strftime("%H:%M:%S")
    users.update_one(
        {"_id":ObjectId(uid)},
        {"$push":{"logs":{"$each":[f"[{t}] {msg}"],"$slice":-50}}}
    )

# ---------------- STRATEGY ----------------
def strategy(ticks):
    if len(ticks)<60:
        return "NONE"
    first=ticks[:30]
    second=ticks[30:]
    first_diff=first[-1]-first[0]
    second_diff=second[-1]-second[0]
    if first_diff>0 and second_diff<0:
        return "CALL"
    if first_diff<0 and second_diff>0:
        return "PUT"
    return "NONE"

# ---------------- OPEN TRADE ----------------
def open_trade(u,sig,uid,stake_override=None):
    try:
        ws=websocket.create_connection(DERIV)
        ws.send(json.dumps({"authorize":u["token"]}))
        auth=json.loads(ws.recv())
        cur=auth["authorize"]["currency"]
        # عكس الإشارة
        sig_opp="PUT" if sig=="CALL" else "CALL"
        barrier=0.5 if sig_opp=="PUT" else -0.5
        stake_amount=round(stake_override if stake_override else u["stake"],2)
        ws.send(json.dumps({
            "buy":1,
            "price":stake_amount,
            "parameters":{
                "amount":stake_amount,
                "basis":"stake",
                "contract_type":sig_opp,
                "currency":cur,
                "duration":5,
                "duration_unit":"t",
                "symbol":u["symbol"],
                "barrier":barrier
            }
        }))
        trade=json.loads(ws.recv())
        ws.close()
        if "buy" in trade:
            cid=trade["buy"]["contract_id"]
            log(uid,f"ENTER {sig_opp} {stake_amount}$")
            users.update_one(
                {"_id":ObjectId(uid)},
                {"$set":{"contract":cid,"last_signal":sig}}
            )
    except:
        pass

# ---------------- CHECK RESULT ----------------
def check(uid):
    u=users.find_one({"_id":ObjectId(uid)})
    if not u or not u.get("contract"):
        return
    while True:
        try:
            ws=websocket.create_connection(DERIV)
            ws.send(json.dumps({"authorize":u["token"]}))
            ws.recv()
            ws.send(json.dumps({
                "proposal_open_contract":1,
                "contract_id":u["contract"]
            }))
            r=json.loads(ws.recv())
            ws.close()
            c=r["proposal_open_contract"]
            if not c["is_sold"]:
                time.sleep(0.5)
                continue
            profit=round(float(c["profit"]),2)
            total=round(u["profit"]+profit,2)
            if profit>0:
                log(uid,f"WIN {profit}$")
                users.update_one(
                    {"_id":ObjectId(uid)},
                    {"$set":{
                        "contract":None,
                        "profit":total,
                        "wins":u["wins"]+1,
                        "stake":round(u["base"],2),
                        "loss_seq":0
                    }}
                )
            else:
                loss_seq=u["loss_seq"]+1
                stake=round(u["stake"]*10,2)
                log(uid,f"LOSS next {stake}$ (auto-martingale)")
                users.update_one(
                    {"_id":ObjectId(uid)},
                    {"$set":{"contract":None,"stake":stake,"loss_seq":loss_seq},
                     "$inc":{"losses":1,"profit":profit}}
                )
                # فتح المضاعفة فورًا
                open_trade(u,u["last_signal"],uid,stake_override=stake)
                if loss_seq>=2:
                    users.update_one(
                        {"_id":ObjectId(uid)},
                        {"$set":{"status":"stopped","reason":"2 losses"}}
                    )
            if total>=u["tp"]:
                users.update_one(
                    {"_id":ObjectId(uid)},
                    {"$set":{"status":"stopped","reason":"TP reached"}}
                )
            break
        except:
            time.sleep(0.5)

# ---------------- BOT LOOP ----------------
def bot(uid):
    while True:
        u=users.find_one({"_id":ObjectId(uid)})
        if not u or u["status"]!="running":
            break
        # متابعة الصفقة المفتوحة
        if u.get("contract"):
            check(uid)
            time.sleep(0.5)
            continue
        # فتح صفقة جديدة فور ظهور إشارة مناسبة
        try:
            ws=websocket.create_connection(DERIV)
            ws.send(json.dumps({
                "ticks_history":u["symbol"],
                "count":60,
                "end":"latest",
                "style":"ticks"
            }))
            r=json.loads(ws.recv())
            ws.close()
            if "history" not in r:
                time.sleep(0.5)
                continue
            sig=strategy(r["history"]["prices"])
            if sig!="NONE":
                users.update_one({"_id":ObjectId(uid)},{"$set":{"last_signal":sig}})
                open_trade(u,sig,uid)
        except:
            time.sleep(0.5)
        time.sleep(0.5)

# ---------------- RESUME ----------------
def resume():
    for u in users.find({"status":"running"}):
        Thread(target=bot,args=(str(u["_id"]),),daemon=True).start()

# ---------------- WEB ----------------
HTML="""
<html>
<body style='background:#0b0f14;color:white;font-family:sans-serif'>
<h2>KHOURY BOT V23</h2>
Email<br>
<input id=email>
<button onclick=login()>LOGIN</button>

<div id=settings style="display:none">
Token<br><input id=token>
Symbol<br>
<select id=symbol>
<option value=R_100>V100</option>
<option value=R_75>V75</option>
</select>
Stake<br><input id=stake value=1>
TP<br><input id=tp value=10>
<button onclick=start()>START</button>
</div>

<div id=stats style="display:none">
<div id=reason style=color:red></div>
<button onclick=stop()>STOP</button>
<div id=s></div>
<div id=l style="height:150px;overflow:auto;background:black"></div>
<button onclick=news()>Start New Session</button>
</div>

<script>
let email=""
async function login(){
    email=document.getElementById("email").value
    let r=await fetch("/check/"+email)
    let d=await r.json()
    if(d.found){stats.style.display="block"}else{settings.style.display="block"}
}
async function start(){
    let data={
        email:email,
        token:token.value,
        symbol:symbol.value,
        stake:parseFloat(stake.value),
        tp:parseFloat(tp.value)
    }
    await fetch("/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)})
    settings.style.display="none"
    stats.style.display="block"
}
async function stop(){
    await fetch("/stop",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({email:email})})
}
async function news(){
    await fetch("/reset",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({email:email})})
    location.reload()
}
setInterval(async()=>{
    if(!email)return
    let r=await fetch("/check/"+email)
    let d=await r.json()
    if(!d.found)return
    s.innerHTML="Wins "+d.wins+" Losses "+d.losses+" Profit "+d.profit.toFixed(2)
    l.innerHTML=d.logs.reverse().join("<br>")
    reason.innerText=d.reason
},1000)
</script>
</body>
</html>
"""

# ---------------- ROUTES ----------------
@app.route("/")
def home():
    return render_template_string(HTML)

@app.route("/check/<email>")
def check_email(email):
    u=users.find_one({"email":email})
    if u:
        u["_id"]=str(u["_id"])
        return jsonify({"found":True,**u})
    return jsonify({"found":False})

@app.route("/start",methods=["POST"])
def start():
    d=request.json
    uid=users.insert_one({
        "email":d["email"],
        "token":d["token"],
        "symbol":d["symbol"],
        "base":round(d["stake"],2),
        "stake":round(d["stake"],2),
        "tp":round(d["tp"],2),
        "profit":0.0,
        "wins":0,
        "losses":0,
        "loss_seq":0,
        "contract":None,
        "last_signal":None,
        "logs":[],
        "status":"running",
        "reason":""
    }).inserted_id
    Thread(target=bot,args=(str(uid),),daemon=True).start()
    return jsonify({"ok":True})

@app.route("/stop",methods=["POST"])
def stop():
    email=request.json["email"]
    users.update_one({"email":email},{"$set":{"status":"stopped","reason":"manual stop"}})
    return jsonify({"ok":True})

@app.route("/reset",methods=["POST"])
def reset():
    email=request.json["email"]
    users.delete_one({"email":email})
    return jsonify({"ok":True})

if __name__=="__main__":
    resume()
    app.run(host="0.0.0.0",port=5000)
