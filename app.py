import json, websocket, time
from datetime import datetime
from threading import Thread
from flask import Flask, render_template_string, jsonify, request
from pymongo import MongoClient
from bson.objectid import ObjectId

app = Flask(__name__)

# --- MongoDB ---
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI); db = client["KHOURY_BOT"]; users = db["users"]
DERIV_WS = "wss://blue.derivws.com/websockets/v3?app_id=16929"

def set_status(uid, status):
    users.update_one({"_id": ObjectId(uid)}, {"$set": {"status": status}})

def get_last_digit_from_price(price):
    return int("{:.2f}".format(float(price))[-1])

def get_least_digit(prices):
    digits = [get_last_digit_from_price(p) for p in prices]
    counts = {i: digits.count(i) for i in range(10)}
    return min(counts, key=counts.get)

def execute_trade(token, symbol, stake, digit, currency):
    try:
        ws = websocket.create_connection(DERIV_WS, timeout=10)
        ws.send(json.dumps({"authorize": token})); ws.recv()
        ws.send(json.dumps({"buy": 1, "price": round(stake, 2), "parameters": {"amount": round(stake, 2), "basis": "stake", "contract_type": "DIGITDIFF", "currency": currency, "duration": 1, "duration_unit": "t", "symbol": symbol, "barrier": str(digit)}}))
        res = json.loads(ws.recv()); ws.close()
        return res.get("buy", {}).get("contract_id")
    except: return None

def check_result(token, cid):
    try:
        ws = websocket.create_connection(DERIV_WS, timeout=10)
        ws.send(json.dumps({"authorize": token})); ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": cid}))
        r = json.loads(ws.recv()); ws.close()
        return float(r["proposal_open_contract"]["profit"]) if r.get("proposal_open_contract", {}).get("is_sold") else 0
    except: return 0

def bot_worker(uid):
    while True:
        u = users.find_one({"_id": ObjectId(uid)})
        if not u or u.get("status") == "stopped": break
        
        if datetime.now().second == 0 and not u.get("contract"):
            try:
                set_status(uid, "ANALYZING")
                ws = websocket.create_connection(DERIV_WS, timeout=10)
                ws.send(json.dumps({"authorize": u["token"]})); ws.recv()
                ws.send(json.dumps({"ticks_history": u["symbol"], "count": 100, "end": "latest", "style": "ticks"}))
                hist = json.loads(ws.recv()); ws.close()
                target = get_least_digit(hist["history"]["prices"])
                
                set_status(uid, "WAITING")
                ws = websocket.create_connection(DERIV_WS, timeout=30)
                ws.send(json.dumps({"authorize": u["token"]})); ws.recv()
                ws.send(json.dumps({"ticks": u["symbol"]}))
                
                cid = None
                while True:
                    msg = json.loads(ws.recv())
                    if "tick" in msg and get_last_digit_from_price(msg["tick"]["quote"]) == target:
                        set_status(uid, "IN TRADE")
                        cid = execute_trade(u["token"], u["symbol"], u["stake"], target, "USD")
                        ws.close(); break
                
                if cid:
                    time.sleep(6)
                    profit = check_result(u["token"], cid)
                    if profit > 0:
                        users.update_one({"_id": ObjectId(uid)}, {"$set": {"stake": u["base"]}, "$inc": {"wins": 1, "profit": profit}})
                    else:
                        users.update_one({"_id": ObjectId(uid)}, {"$inc": {"losses": 1, "profit": -u["stake"]}})
                        new_stake = round(u["stake"] * 19, 2)
                        cid2 = execute_trade(u["token"], u["symbol"], new_stake, target, "USD")
                        time.sleep(6)
                        profit2 = check_result(u["token"], cid2)
                        if profit2 > 0:
                            users.update_one({"_id": ObjectId(uid)}, {"$set": {"stake": u["base"]}, "$inc": {"wins": 1, "profit": profit2}})
                        else:
                            users.update_one({"_id": ObjectId(uid)}, {"$set": {"status": "stopped"}, "$inc": {"losses": 1, "profit": -new_stake}})
                            break
            except: pass
        time.sleep(0.5)

@app.route("/")
def home():
    return render_template_string("""
    <body style='background:#0d1117;color:white;text-align:center;font-family:sans-serif;padding:30px'>
        <h2 style='color:#58a6ff'>KHOURY SNIPER - V51</h2>
        <div id=login_div><input id=ev placeholder="Email"><br><br><button onclick="login()">LOGIN</button></div>
        <div id=settings style='display:none'><input id=tv placeholder="Token"><br><br><input id=sv value=0.35><br><br><button onclick="start()">START</button></div>
        <div id=stats style='display:none'>
            <div style='font-size:30px; font-weight:bold; color:yellow' id=st></div>
            <div id=info style='font-size:20px; margin:20px 0'></div>
            <button onclick="stop()">STOP</button><button onclick="reset()" style='margin-left:10px'>RESET</button>
        </div>
        <script>
            let email="";
            async function login(){ email=document.getElementById('ev').value; let r=await fetch('/check/'+email); let d=await r.json(); document.getElementById('login_div').style.display='none'; if(d.found){ document.getElementById('stats').style.display='block'; sync(); } else { document.getElementById('settings').style.display='block'; } }
            async function start(){ let d={email:email,token:document.getElementById('tv').value,symbol:'R_100',stake:parseFloat(document.getElementById('sv').value)}; await fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}); document.getElementById('settings').style.display='none'; document.getElementById('stats').style.display='block'; sync(); }
            async function stop(){ fetch('/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})}); location.reload(); }
            async function reset(){ if(confirm("Reset?")){ await fetch('/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})}); location.reload(); } }
            function sync(){ setInterval(async()=>{ let r=await fetch('/check/'+email); let d=await r.json(); if(d.found){ document.getElementById('st').innerText=d.status.toUpperCase(); document.getElementById('info').innerText=`Profit: ${d.profit.toFixed(2)}$ | W: ${d.wins} L: ${d.losses}`; } },1000); }
        </script>
    </body>
    """)

@app.route("/check/<email>")
def check_email(email):
    u = users.find_one({"email": email})
    if u: u["_id"]=str(u["_id"]); return jsonify({"found": True, **u})
    return jsonify({"found": False})

@app.route("/start", methods=["POST"])
def start():
    d = request.json; users.delete_one({"email": d["email"]})
    users.insert_one({"email": d["email"], "token": d["token"], "symbol": d["symbol"], "base": d["stake"], "stake": d["stake"], "profit": 0.0, "wins": 0, "losses": 0, "status": "ANALYZING"})
    Thread(target=bot_worker, args=(str(users.find_one({"email": d["email"]})["_id"]),), daemon=True).start(); return jsonify({"ok": True})

@app.route("/stop", methods=["POST"])
def stop(): email = request.json["email"]; users.update_one({"email": email}, {"$set": {"status": "stopped"}}); return jsonify({"ok": True})

@app.route("/reset", methods=["POST"])
def reset(): email = request.json["email"]; users.delete_one({"email": email}); return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
