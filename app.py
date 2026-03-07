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
db = client['KHOURY_V15_TP_LOSSES']
users_col = db['users']

DERIV_WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

# --- Logging ---
def add_log(uid, msg):
    now = datetime.now().strftime("%H:%M:%S")
    users_col.update_one(
        {"_id": ObjectId(uid)},
        {"$push": {"logs": {"$each": [f"[{now}] {msg}"], "$slice": -50}}}
    )

# --- Strategy ---
def compute_logic(ticks, last_signal):
    if len(ticks) < 15:
        return "NONE"
    candles = []
    for i in range(0, 15, 5):
        c = ticks[i:i+5]
        open_price = c[0]
        close_price = c[-1]
        candles.append({"open": open_price, "close": close_price})
    diffs = [c["close"] - c["open"] for c in candles]
    for i in range(1, len(diffs)):
        if abs(diffs[i]) < 0.3:
            return "NONE"
        if diffs[i] * diffs[i-1] > 0:
            return "NONE"
    last = diffs[-1]
    if last > 0:
        signal = "CALL"
    elif last < 0:
        signal = "PUT"
    else:
        signal = "NONE"
    # لا يسمح بإعادة نفس الإشارة مباشرة
    if signal == last_signal:
        return "NONE"
    return signal

# --- Check Trade Result ---
def check_status(uid):
    user = users_col.find_one({"_id": ObjectId(uid)})
    if not user or not user.get("active_contract"):
        return
    try:
        ws = websocket.create_connection(DERIV_WS_URL)
        ws.send(json.dumps({"authorize": user["token"]}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": user["active_contract"]}))
        res = json.loads(ws.recv())
        ws.close()
        contract = res.get("proposal_open_contract")
        if contract and contract.get("is_sold"):
            profit = round(float(contract.get("profit", 0)), 2)
            total = round(user.get("total_profit", 0) + profit, 2)
            if profit > 0:
                add_log(uid, f"WIN: +{profit}$")
                users_col.update_one(
                    {"_id": ObjectId(uid)},
                    {"$set": {
                        "active_contract": None,
                        "total_profit": total,
                        "wins": user.get("wins", 0) + 1,
                        "current_stake": round(user["base_stake"], 2),
                        "consecutive_losses": 0,
                        "last_signal": None
                    }}
                )
            else:
                losses = user.get("consecutive_losses", 0) + 1
                stake = round(user["current_stake"] * 10, 2)
                add_log(uid, f"LOSS! Next Stake: {stake}$")
                users_col.update_one(
                    {"_id": ObjectId(uid)},
                    {"$set": {
                        "active_contract": None,
                        "current_stake": stake,
                        "consecutive_losses": losses,
                        "last_signal": None
                    },
                     "$inc": {"losses": 1, "total_profit": profit}}
                )
                if losses >= 2:
                    add_log(uid, "STOP: 2 consecutive losses")
                    users_col.update_one(
                        {"_id": ObjectId(uid)},
                        {"$set": {"status": "stopped", "reason": "2 consecutive losses"}}
                    )
            # تحقق من TP المستخدم
            if total >= user.get("tp", 9999):
                add_log(uid, "STOP: Take Profit reached")
                users_col.update_one(
                    {"_id": ObjectId(uid)},
                    {"$set": {"status": "stopped", "reason": "Take Profit reached"}}
                )
    except:
        pass

# --- Bot Loop ---
def user_loop(uid):
    while True:
        user = users_col.find_one({"_id": ObjectId(uid)})
        if not user or user.get("status") != "running":
            break
        now = time.localtime()
        if now.tm_sec % 10 == 0:
            try:
                ws = websocket.create_connection(DERIV_WS_URL)
                ws.send(json.dumps({"ticks_history": user["symbol"], "count": 15, "end": "latest", "style": "ticks"}))
                data = json.loads(ws.recv())
                ws.close()
                if "history" in data:
                    last_signal = user.get("last_signal")
                    signal = compute_logic(data["history"]["prices"], last_signal)
                    if signal != "NONE":
                        ws = websocket.create_connection(DERIV_WS_URL)
                        ws.send(json.dumps({"authorize": user["token"]}))
                        auth = json.loads(ws.recv())
                        currency = auth["authorize"]["currency"]
                        barrier = 0.5 if signal == "PUT" else -0.5
                        ws.send(json.dumps({
                            "buy": 1,
                            "price": round(user["current_stake"], 2),
                            "parameters": {
                                "amount": round(user["current_stake"], 2),
                                "basis": "stake",
                                "contract_type": signal,
                                "currency": currency,
                                "duration": 5,
                                "duration_unit": "t",
                                "symbol": user["symbol"],
                                "barrier": barrier
                            }
                        }))
                        trade = json.loads(ws.recv())
                        if "buy" in trade:
                            add_log(uid, f"ENTER {signal}")
                            users_col.update_one({"_id": ObjectId(uid)}, {"$set": {"active_contract": trade["buy"]["contract_id"], "last_signal": signal}})
                        ws.close()
            except:
                pass
            time.sleep(1)
        time.sleep(0.5)
        check_status(uid)

# --- Resume Sessions ---
def resume_sessions():
    for user in users_col.find({"status": "running"}):
        Thread(target=user_loop, args=(str(user["_id"]),), daemon=True).start()

# --- Web UI ---
HTML = """
<!DOCTYPE html>
<html>
<head>
<style>
body{background:#0a0f14;color:white;font-family:sans-serif;display:flex;justify-content:center;padding:30px}
.card{background:#111827;padding:25px;border-radius:12px;width:380px}
input,select{width:100%;padding:10px;margin:6px 0;background:#000;color:#58a6ff;border:1px solid #333}
button{width:100%;padding:15px;border:none;border-radius:8px;font-weight:bold;margin-top:10px;cursor:pointer}
#logs{height:130px;overflow:auto;background:#000;padding:6px;font-size:12px;margin-top:10px}
#stats{margin-top:10px;font-size:14px;}
</style>
</head>
<body>
<div class="card">
<h2>KHOURY V15</h2>
<div id="loginDiv">
<input id="email" placeholder="Email">
<button onclick="login()">LOGIN</button>
</div>

<div id="settingsDiv" style="display:none">
<input id="token" placeholder="API Token">
<select id="symbol">
<option value="R_100">Volatility 100</option>
<option value="R_75">Volatility 75</option>
</select>
<input id="stake" type="number" value="1" step="0.01" placeholder="Initial Stake">
<input id="tp" type="number" value="10" step="0.01" placeholder="Take Profit">
<button onclick="startSession()">START</button>
</div>

<div id="statsDiv" style="display:none">
<div id="reasonDiv" style="display:none;color:red;margin-bottom:10px;"></div>
<button onclick="stopSession()">STOP</button>
<div id="stats"></div>
<div id="logs"></div>
<button id="newSessionBtn" style="display:none" onclick="newSession()">START NEW SESSION</button>
</div>
</div>

<script>
let email="",running=false
async function login(){
    email=document.getElementById("email").value
    if(!email) return
    let r=await fetch("/check/"+email)
    let d=await r.json()
    if(d.found && d.status=="running"){
        document.getElementById("loginDiv").style.display="none"
        document.getElementById("settingsDiv").style.display="none"
        document.getElementById("statsDiv").style.display="block"
        running=true
    }else{
        document.getElementById("loginDiv").style.display="none"
        document.getElementById("settingsDiv").style.display="block"
        running=false
    }
}
async function startSession(){
    let d = {
        email: email,
        token: document.getElementById("token").value,
        symbol: document.getElementById("symbol").value,
        stake: parseFloat(document.getElementById("stake").value),
        tp: parseFloat(document.getElementById("tp").value)
    }
    await fetch("/start", {method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(d)});
    running = true;
    document.getElementById("settingsDiv").style.display = "none";
    document.getElementById("statsDiv").style.display = "block";
}
async function stopSession(){
    await fetch("/stop",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({email:email})})
    running=false
    document.getElementById("reasonDiv").style.display="block"
    document.getElementById("reasonDiv").innerText="Session Stopped"
    document.getElementById("newSessionBtn").style.display="block"
}
async function newSession(){
    await fetch("/reset",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({email:email})})
    document.getElementById("settingsDiv").style.display="block"
    document.getElementById("statsDiv").style.display="none"
    document.getElementById("reasonDiv").style.display="none"
    document.getElementById("newSessionBtn").style.display="none"
}
setInterval(async()=>{
    if(!email) return
    let r=await fetch("/check/"+email)
    let d=await r.json()
    if(d.found){
        if(d.status=="stopped" && !running){
            document.getElementById("reasonDiv").style.display="block"
            document.getElementById("reasonDiv").innerText=d.reason
            document.getElementById("newSessionBtn").style.display="block"
        }
        document.getElementById("stats").innerHTML=`Wins ${d.wins} | Losses ${d.losses}<br>Profit ${d.total_profit.toFixed(2)}$ | Stake ${d.current_stake.toFixed(2)}$`
        document.getElementById("logs").innerHTML=d.logs.reverse().join("<br>")
    }
},1000)
</script>
</body>
</html>
"""

# --- Routes ---
@app.route("/")
def home(): return render_template_string(HTML)

@app.route("/check/<email>")
def check(email):
    u = users_col.find_one({"email": email})
    if u: u["_id"] = str(u["_id"]); return jsonify({"found": True, **u})
    return jsonify({"found": False})

@app.route("/start", methods=["POST"])
def start():
    d = request.json
    u = users_col.find_one({"email": d["email"]})
    if not u:
        uid = users_col.insert_one({
            "email": d["email"],
            "token": d["token"],
            "symbol": d["symbol"],
            "base_stake": round(float(d["stake"]), 2),
            "current_stake": round(float(d["stake"]), 2),
            "wins": 0,
            "losses": 0,
            "total_profit": 0.00,
            "consecutive_losses": 0,
            "active_contract": None,
            "logs": [],
            "status": "running",
            "reason": "",
            "last_signal": None,
            "tp": round(float(d["tp"]),2)
        }).inserted_id
        Thread(target=user_loop, args=(str(uid),), daemon=True).start()
    else:
        users_col.update_one({"email": d["email"]}, {"$set": {"status": "running", "tp": round(float(d["tp"]),2)}})
        Thread(target=user_loop, args=(str(u["_id"]),), daemon=True).start()
    return jsonify({"ok": True})

@app.route("/stop", methods=["POST"])
def stop():
    email = request.json["email"]
    users_col.update_one({"email": email}, {"$set": {"status": "stopped", "reason": "Stopped by user"}})
    return jsonify({"ok": True})

@app.route("/reset", methods=["POST"])
def reset():
    email = request.json["email"]
    users_col.delete_one({"email": email})
    return jsonify({"ok": True})

# --- Run App ---
if __name__ == "__main__":
    resume_sessions()
    app.run(host="0.0.0.0", port=5000)
