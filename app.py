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

def log(uid, msg):
    t = datetime.now().strftime("%H:%M:%S")
    users.update_one({"_id": ObjectId(uid)}, {"$push": {"logs": {"$each": [f"[{t}] {msg}"], "$slice": -50}}})

def strategy_check(ticks):
    if len(ticks) < 17: return "NONE"
    recent = ticks[-17:]; res = []
    for i in range(0, 16, 4):
        o, c = recent[i], recent[i+4]
        if c > o: res.append("U")
        elif c < o: res.append("D")
        else: res.append("S")
    if res == ["U", "D", "U", "U"]: return "CALL"
    if res == ["D", "U", "D", "D"]: return "PUT"
    return "NONE"

def bot_worker(uid):
    while True:
        u = users.find_one({"_id": ObjectId(uid)})
        if not u or u.get("status") == "stopped": break
        
        if u["profit"] >= u["tp"]:
            log(uid, f"🏁 Target Reached: {u['tp']}$")
            users.update_one({"_id": ObjectId(uid)}, {"$set": {"status": "stopped"}})
            break

        if not u.get("contract"):
            try:
                ws = websocket.create_connection(DERIV_WS, timeout=7)
                ws.send(json.dumps({"authorize": u["token"]}))
                auth = json.loads(ws.recv())
                if "authorize" in auth:
                    ws.send(json.dumps({"ticks_history": u["symbol"], "count": 25, "end": "latest", "style": "ticks"}))
                    hist = json.loads(ws.recv())
                    if "history" in hist:
                        sig = strategy_check(hist["history"]["prices"])
                        if sig != "NONE":
                            bar = "-0.5" if sig == "CALL" else "+0.5"
                            ws.send(json.dumps({
                                "buy": 1, "price": round(u["stake"], 2),
                                "parameters": {"amount": round(u["stake"], 2), "basis": "stake", "contract_type": sig, "currency": auth["authorize"]["currency"], "duration": 6, "duration_unit": "t", "symbol": u["symbol"], "barrier": bar}
                            }))
                            res = json.loads(ws.recv())
                            if "buy" in res:
                                log(uid, f"🚀 {sig} Entered | Barrier: {bar}")
                                users.update_one({"_id": ObjectId(uid)}, {"$set": {"contract": res["buy"]["contract_id"], "status": "in trade"}})
                ws.close()
            except: pass
        check_result(uid)
        time.sleep(1)

def check_result(uid):
    u = users.find_one({"_id": ObjectId(uid)})
    if not u or not u.get("contract"): return
    try:
        ws = websocket.create_connection(DERIV_WS); ws.send(json.dumps({"authorize": u["token"]})); ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": u["contract"]}))
        r = json.loads(ws.recv()); ws.close()
        c = r.get("proposal_open_contract")
        if c and c.get("is_sold"):
            p = float(c.get("profit", 0))
            if p > 0:
                log(uid, f"✅ WIN {p}$")
                users.update_one({"_id": ObjectId(uid)}, {"$set": {"contract": None, "stake": u["base"], "status": "searching"}, "$inc": {"wins": 1, "profit": p}})
            else:
                ns = round(u["stake"] * 19, 2)
                log(uid, f"❌ LOSS. Next: {ns}$")
                users.update_one({"_id": ObjectId(uid)}, {"$set": {"contract": None, "stake": ns, "status": "searching"}, "$inc": {"losses": 1, "profit": p}})
    except: pass

@app.route("/")
def home():
    return render_template_string("""
    <body style='background:#0d1117;color:white;text-align:center;font-family:sans-serif;padding:30px'>
        <h2 style='color:#58a6ff'>KHOURY SNIPER V47</h2>
        
        <div id=login_div>
            <input id=email_val placeholder="Email Address" style='padding:12px;width:250px;border-radius:8px;border:none'><br><br>
            <button onclick="login()" style='padding:10px 30px;background:#238636;color:white;border:none;border-radius:5px;cursor:pointer;font-weight:bold'>LOGIN</button>
        </div>

        <div id=settings style='display:none;margin-top:20px;background:#161b22;padding:20px;border-radius:10px;max-width:400px;margin:auto'>
            <input id=token_val placeholder="API Token" type=password style='padding:10px;width:90%'><br><br>
            <input id=stake_val placeholder="Stake" type=number value=0.35 style='padding:10px;width:90%'><br><br>
            <input id=tp_val placeholder="Take Profit" type=number value=10 style='padding:10px;width:90%'><br><br>
            <button onclick="start()" style='padding:10px 40px;background:#238636;color:white;border:none;border-radius:8px;font-weight:bold'>START BOT</button>
        </div>

        <div id=stats style='display:none;margin-top:20px'>
            <h3 id=status_txt style='color:#00f3ff'>Searching...</h3>
            <div id=s_info style='font-size:20px;margin-bottom:15px'></div>
            <div id=logs_box style='text-align:left;background:black;padding:10px;height:250px;overflow:auto;max-width:400px;margin:auto;color:#39ff14;border:1px solid #333'></div><br>
            
            <button onclick="stop()" style='padding:12px 25px;background:#da3633;color:white;border:none;border-radius:8px;font-weight:bold;cursor:pointer'>STOP</button>
            <button onclick="reset()" style='padding:12px 25px;background:#333;color:white;border:none;border-radius:8px;font-weight:bold;cursor:pointer;margin-left:10px'>RESET</button>
        </div>

        <script>
            let email = "";
            async function login(){
                email = document.getElementById('email_val').value;
                if(!email) return;
                let r = await fetch('/check/'+email); let d = await r.json();
                document.getElementById('login_div').style.display='none';
                if(d.found && d.status !== 'stopped'){ document.getElementById('stats').style.display='block'; startSync(); }
                else { document.getElementById('settings').style.display='block'; }
            }

            async function start(){
                let d={ email: email, token: document.getElementById('token_val').value, symbol: 'R_100', stake: parseFloat(document.getElementById('stake_val').value), tp: parseFloat(document.getElementById('tp_val').value) };
                await fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
                document.getElementById('settings').style.display='none'; document.getElementById('stats').style.display='block';
                startSync();
            }

            async function stop(){ await fetch('/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})}); }
            async function reset(){ if(confirm("Reset all stats?")){ await fetch('/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})}); location.reload(); } }

            function startSync(){
                setInterval(async()=>{
                    let r = await fetch('/check/'+email); let d = await r.json();
                    if(d.found){
                        document.getElementById('status_txt').innerText = "STATUS: " + d.status.toUpperCase();
                        document.getElementById('status_txt').style.color = d.status === 'stopped' ? 'red' : '#00f3ff';
                        document.getElementById('s_info').innerText = `Profit: ${d.profit.toFixed(2)}$ | Wins: ${d.wins} | Losses: ${d.losses}`;
                        document.getElementById('logs_box').innerHTML = d.logs.reverse().join('<br>');
                    }
                }, 1000);
            }
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
    uid = users.insert_one({"email": d["email"], "token": d["token"], "symbol": d["symbol"], "base": d["stake"], "stake": d["stake"], "tp": d["tp"], "profit": 0.0, "wins": 0, "losses": 0, "contract": None, "logs": [], "status": "searching"}).inserted_id
    Thread(target=bot_worker, args=(str(uid),), daemon=True).start(); return jsonify({"ok": True})

@app.route("/stop", methods=["POST"])
def stop():
    email = request.json["email"]; users.update_one({"email": email}, {"$set": {"status": "stopped"}})
    return jsonify({"ok": True})

@app.route("/reset", methods=["POST"])
def reset():
    email = request.json["email"]; users.delete_one({"email": email})
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
