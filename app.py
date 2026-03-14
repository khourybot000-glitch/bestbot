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

def get_least_digit(prices):
    """تحليل الرقم الأقل تكراراً في آخر 100 تيك"""
    digits = [int(str(p).split('.')[-1][-1]) for p in prices]
    counts = {i: digits.count(i) for i in range(10)}
    return min(counts, key=counts.get)

def delayed_check(uid, cid, token):
    # الانتظار 6 ثوانٍ لضمان انتهاء عقد الـ 1 تيك وقطع الاتصال
    time.sleep(6)
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
                ns = round(u["stake"] * 14, 2)
                log(uid, f"❌ LOSS. Martingale x14: {ns}$")
                users.update_one({"_id": ObjectId(uid)}, {"$set": {"contract": None, "stake": ns, "status": "searching"}, "$inc": {"losses": 1, "profit": p}})
    except: pass

def bot_worker(uid):
    log(uid, "⏱️ Sniper Ready - Syncing to Second 00")
    while True:
        u = users.find_one({"_id": ObjectId(uid)})
        if not u or u.get("status") == "stopped": break
        
        now = datetime.now()
        # التحليل حصراً عند الثانية 00
        if now.second == 0 and not u.get("contract"):
            ws = None
            try:
                ws = websocket.create_connection(DERIV_WS, timeout=10)
                # جلب العملة والبيانات تلقائياً
                ws.send(json.dumps({"authorize": u["token"]}))
                auth_data = json.loads(ws.recv())
                currency = auth_data.get("authorize", {}).get("currency", "USD")
                
                # جلب 100 تيك للتحليل
                ws.send(json.dumps({"ticks_history": u["symbol"], "count": 100, "end": "latest", "style": "ticks"}))
                hist = json.loads(ws.recv())
                
                if "history" in hist:
                    target_digit = get_least_digit(hist["history"]["prices"])
                    log(uid, f"📊 Analysis: Least Digit [{target_digit}] | Waiting...")
                    
                    # الاشتراك في التيكات لانتظار ظهور الرقم
                    ws.send(json.dumps({"ticks": u["symbol"]}))
                    
                    start_wait = time.time()
                    while True:
                        # إذا تأخر الرقم أكثر من 50 ثانية نخرج لنبدأ تحليل جديد في الدقيقة التالية
                        if time.time() - start_wait > 50: break
                        
                        msg = json.loads(ws.recv())
                        if "tick" in msg:
                            last_digit = int(str(msg["tick"]["quote"])[-1])
                            if last_digit == target_digit:
                                # دخول الصفقة DIGITDIFF
                                ws.send(json.dumps({
                                    "buy": 1, "price": round(u["stake"], 2),
                                    "parameters": {
                                        "amount": round(u["stake"], 2), "basis": "stake", 
                                        "contract_type": "DIGITDIFF", "currency": currency, 
                                        "duration": 1, "duration_unit": "t", 
                                        "symbol": u["symbol"], "barrier": str(target_digit)
                                    }
                                }))
                                res = json.loads(ws.recv())
                                if "buy" in res:
                                    cid = res["buy"]["contract_id"]
                                    log(uid, f"🎯 Digit {target_digit} Spotted - Entry Taken")
                                    users.update_one({"_id": ObjectId(uid)}, {"$set": {"contract": cid, "status": "waiting"}})
                                    
                                    # قطع الاتصال فوراً بعد الشراء كما طلبت
                                    ws.close(); ws = None
                                    Thread(target=delayed_check, args=(uid, cid, u["token"])).start()
                                    break
                if ws: ws.close()
                time.sleep(2) # منع التكرار في نفس الثانية 00
            except: 
                if ws: ws.close()
        time.sleep(0.1)

# --- UI (نفس الـ HTML الذي أرسلته أنت دون تعديل) ---
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
