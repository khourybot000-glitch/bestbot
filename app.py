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

def advanced_strategy(ticks):
    if len(ticks) < 60: return "NONE"
    
    # تقسيم الـ 60 تيك لنصفين
    first_half = ticks[-60:-30]  # من 60 إلى 30 تيك مضت
    second_half = ticks[-30:]    # آخر 30 تيك
    
    fh_trend = "UP" if first_half[-1] > first_half[0] else "DOWN"
    sh_trend = "UP" if second_half[-1] > second_half[0] else "DOWN"
    
    # تقسيم النصف الثاني (30 تيك) لـ 6 شموع (كل واحدة 5 تيكات)
    up_candles = 0
    down_candles = 0
    for i in range(0, 30, 5):
        candle = second_half[i:i+5]
        if candle[-1] > candle[0]: up_candles += 1
        elif candle[-1] < candle[0]: down_candles += 1
    
    # شرط الصعود (CALL): الأول هابط، الثاني صاعد، والزخم صاعد (>=4 شموع)
    if fh_trend == "DOWN" and sh_trend == "UP" and up_candles >= 4:
        return "CALL"
        
    # شرط الهبوط (PUT): الأول صاعد، الثاني هابط، والزخم هابط (>=4 شموع)
    if fh_trend == "UP" and sh_trend == "DOWN" and down_candles >= 4:
        return "PUT"
        
    return "NONE"

def delayed_check(uid, cid, token):
    # الانتظار 16 ثانية لضمان انتهاء عقد الـ 6 تيكات
    time.sleep(16)
    try:
        ws = websocket.create_connection(DERIV_WS, timeout=10)
        ws.send(json.dumps({"authorize": token}))
        ws.recv()
        ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": cid}))
        r = json.loads(ws.recv()); ws.close()
        c = r.get("proposal_open_contract")
        if c and c.get("is_sold"):
            p = float(c.get("profit", 0))
            u = users.find_one({"_id": ObjectId(uid)})
            if p > 0:
                log(uid, f"✅ WIN {p}$")
                users.update_one({"_id": ObjectId(uid)}, {"$set": {"contract": None, "stake": u["base"], "loss_seq": 0, "status": "searching"}, "$inc": {"wins": 1, "profit": p}})
            else:
                new_seq = u.get("loss_seq", 0) + 1
                if new_seq >= 2:
                    log(uid, f"🛑 STOPPED: 2 consecutive losses.")
                    users.update_one({"_id": ObjectId(uid)}, {"$set": {"contract": None, "status": "stopped", "reason": "Max losses reached"}})
                else:
                    ns = round(u["stake"] * 19, 2)
                    log(uid, f"❌ LOSS. Martingale: {ns}$")
                    users.update_one({"_id": ObjectId(uid)}, {"$set": {"contract": None, "stake": ns, "loss_seq": new_seq, "status": "searching"}, "$inc": {"losses": 1, "profit": p}})
    except: pass

def bot_worker(uid):
    log(uid, "⏱️ Sniper Ready - Syncing to Second 00")
    while True:
        u = users.find_one({"_id": ObjectId(uid)})
        if not u or u.get("status") == "stopped": break
        
        now = datetime.now()
        # التحليل حصراً عند الثانية 00
        if now.second == 0 and not u.get("contract"):
            try:
                ws = websocket.create_connection(DERIV_WS, timeout=7)
                ws.send(json.dumps({"authorize": u["token"]}))
                ws.recv()
                ws.send(json.dumps({"ticks_history": u["symbol"], "count": 70, "end": "latest", "style": "ticks"}))
                hist = json.loads(ws.recv())
                
                if "history" in hist:
                    sig = advanced_strategy(hist["history"]["prices"])
                    if sig != "NONE":
                        bar = "-0.5" if sig == "CALL" else "+0.5"
                        ws.send(json.dumps({
                            "buy": 1, "price": round(u["stake"], 2),
                            "parameters": {"amount": round(u["stake"], 2), "basis": "stake", "contract_type": sig, "currency": "USD", "duration": 6, "duration_unit": "t", "symbol": u["symbol"], "barrier": bar}
                        }))
                        res = json.loads(ws.recv())
                        if "buy" in res:
                            cid = res["buy"]["contract_id"]
                            log(uid, f"🎯 {sig} Entry at 00s | Strength Check Passed")
                            users.update_one({"_id": ObjectId(uid)}, {"$set": {"contract": cid, "status": "waiting"}})
                            Thread(target=delayed_check, args=(uid, cid, u["token"])).start()
                ws.close()
                time.sleep(2) # منع التكرار في نفس الثانية
            except: pass
        time.sleep(0.1)

# --- UI ---
@app.route("/")
def home():
    return render_template_string("""
    <body style='background:#0d1117;color:white;text-align:center;font-family:sans-serif;padding:30px'>
        <h2 style='color:#58a6ff'>KHOURY SNIPER V51 - 00s ONLY</h2>
        <div id=login_div><input id=ev placeholder="Email"><br><br><button onclick="login()">LOGIN</button></div>
        <div id=settings style='display:none'><input id=tv placeholder="Token"><br><br><input id=sv value=0.35><br><br><input id=tpv value=10><br><br><button onclick="start()">START</button></div>
        <div id=stats style='display:none'>
            <h3 id=st></h3><p id=re style='color:red'></p>
            <div id=info style='font-size:20px'></div>
            <div id=lb style='text-align:left;background:black;height:250px;overflow:auto;margin:15px auto;max-width:400px;color:#39ff14;padding:10px;font-family:monospace'></div>
            <button onclick="stop()">STOP</button><button onclick="reset()" style='margin-left:10px'>RESET</button>
        </div>
        <script>
            let email="";
            async function login(){ email=document.getElementById('ev').value; let r=await fetch('/check/'+email); let d=await r.json(); document.getElementById('login_div').style.display='none'; if(d.found && d.status!=='stopped'){ document.getElementById('stats').style.display='block'; sync(); } else { document.getElementById('settings').style.display='block'; } }
            async function start(){ let d={email:email,token:document.getElementById('tv').value,symbol:'R_100',stake:parseFloat(document.getElementById('sv').value),tp:parseFloat(document.getElementById('tpv').value)}; await fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}); document.getElementById('settings').style.display='none'; document.getElementById('stats').style.display='block'; sync(); }
            async function stop(){ fetch('/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})}); }
            async function reset(){ if(confirm("Reset?")){ await fetch('/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})}); location.reload(); } }
            function sync(){ setInterval(async()=>{ let r=await fetch('/check/'+email); let d=await r.json(); if(d.found){ document.getElementById('st').innerText="STATUS: "+d.status.toUpperCase(); document.getElementById('re').innerText=d.reason||""; document.getElementById('info').innerText=`Profit: ${d.profit.toFixed(2)}$ | W: ${d.wins} L: ${d.losses}`; document.getElementById('lb').innerHTML=d.logs.reverse().join('<br>'); } },1000); }
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
    uid = users.insert_one({"email": d["email"], "token": d["token"], "symbol": d["symbol"], "base": d["stake"], "stake": d["stake"], "tp": d["tp"], "profit": 0.0, "wins": 0, "losses": 0, "loss_seq": 0, "contract": None, "logs": [], "status": "searching", "reason": ""}).inserted_id
    Thread(target=bot_worker, args=(str(uid),), daemon=True).start(); return jsonify({"ok": True})

@app.route("/stop", methods=["POST"])
def stop(): email = request.json["email"]; users.update_one({"email": email}, {"$set": {"status": "stopped", "reason": "User stop"}}); return jsonify({"ok": True})

@app.route("/reset", methods=["POST"])
def reset(): email = request.json["email"]; users.delete_one({"email": email}); return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
        
