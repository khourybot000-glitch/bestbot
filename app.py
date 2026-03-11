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

# دالة مخصصة لفحص النتيجة بعد 16 ثانية من دخول الصفقة
def delayed_check(uid, cid, token):
    time.sleep(16) # الانتظار لضمان انتهاء الصفقة (6 تيكات)
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
                users.update_one({"_id": ObjectId(uid)}, {"$set": {"contract": None, "stake": u["base"], "status": "searching"}, "$inc": {"wins": 1, "profit": p}})
            else:
                ns = round(u["stake"] * 19, 2)
                log(uid, f"❌ LOSS. Martingale: {ns}$")
                users.update_one({"_id": ObjectId(uid)}, {"$set": {"contract": None, "stake": ns, "status": "searching"}, "$inc": {"losses": 1, "profit": p}})
        else:
            # إذا لم تنتهِ بعد، نعيد المحاولة بعد 2 ثانية
            Thread(target=delayed_check, args=(uid, cid, token)).start()
    except: pass

def bot_worker(uid):
    log(uid, "🤖 Bot Active - Syncing with seconds 0,10,20,30,40,50")
    while True:
        u = users.find_one({"_id": ObjectId(uid)})
        if not u or u.get("status") == "stopped": break
        
        now = datetime.now()
        # التحليل فقط عند الثواني المحددة
        if now.second in [0, 10, 20, 30, 40, 50] and not u.get("contract"):
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
                                cid = res["buy"]["contract_id"]
                                log(uid, f"🎯 {sig} Entry at {now.second}s | ID: {cid}")
                                users.update_one({"_id": ObjectId(uid)}, {"$set": {"contract": cid, "status": "waiting result"}})
                                # بدء خيط الفحص المؤجل بعد 16 ثانية
                                Thread(target=delayed_check, args=(uid, cid, u["token"])).start()
                ws.close()
                time.sleep(1.1) # لمنع تكرار التحليل في نفس الثانية
            except: pass
        
        time.sleep(0.1) # فحص الوقت بدقة عالية

# --- الواجهة البرمجية (نفس النسخة V47 مع الأزرار) ---
@app.route("/")
def home():
    return render_template_string("""
    <body style='background:#0d1117;color:white;text-align:center;font-family:sans-serif;padding:30px'>
        <h2 style='color:#58a6ff'>KHOURY SNIPER V48</h2>
        <div id=login_div><input id=email_val placeholder="Email"><br><br><button onclick="login()">LOGIN</button></div>
        <div id=settings style='display:none'><input id=token_val placeholder="Token"><br><br><input id=stake_val value=0.35><br><br><input id=tp_val value=10><br><br><button onclick="start()">START</button></div>
        <div id=stats style='display:none'><h3 id=status_txt></h3><div id=s_info></div><div id=logs_box style='text-align:left;background:black;height:250px;overflow:auto;margin:15px auto;max-width:400px;color:#39ff14'></div><button onclick="stop()">STOP</button><button onclick="reset()" style='margin-left:10px'>RESET</button></div>
        <script>
            let email="";
            async function login(){ email=document.getElementById('email_val').value; let r=await fetch('/check/'+email); let d=await r.json(); document.getElementById('login_div').style.display='none'; if(d.found && d.status!=='stopped'){ document.getElementById('stats').style.display='block'; startSync(); } else { document.getElementById('settings').style.display='block'; } }
            async function start(){ let d={email:email,token:document.getElementById('token_val').value,symbol:'R_100',stake:parseFloat(document.getElementById('stake_val').value),tp:parseFloat(document.getElementById('tp_val').value)}; await fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}); document.getElementById('settings').style.display='none'; document.getElementById('stats').style.display='block'; startSync(); }
            async function stop(){ await fetch('/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})}); }
            async function reset(){ if(confirm("Reset?")){ await fetch('/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})}); location.reload(); } }
            function startSync(){ setInterval(async()=>{ let r=await fetch('/check/'+email); let d=await r.json(); if(d.found){ document.getElementById('status_txt').innerText="STATUS: "+d.status.toUpperCase(); document.getElementById('s_info').innerText=`Profit: ${d.profit.toFixed(2)}$ | W: ${d.wins} L: ${d.losses}`; document.getElementById('logs_box').innerHTML=d.logs.reverse().join('<br>'); } },1000); }
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
def stop(): email = request.json["email"]; users.update_one({"email": email}, {"$set": {"status": "stopped"}}); return jsonify({"ok": True})

@app.route("/reset", methods=["POST"])
def reset(): email = request.json["email"]; users.delete_one({"email": email}); return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
