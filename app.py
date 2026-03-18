import json, websocket, time, os
from datetime import datetime
from threading import Thread
from flask import Flask, render_template_string, jsonify, request
from pymongo import MongoClient
from bson.objectid import ObjectId

app = Flask(__name__)

# --- MongoDB Configuration ---
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client["KHOURY_BOT"]
users = db["users"]
DERIV_WS = "wss://blue.derivws.com/websockets/v3?app_id=16929"

def set_status(uid, status):
    users.update_one({"_id": ObjectId(uid)}, {"$set": {"status": status}})

def get_ws_connection(token):
    try:
        ws = websocket.create_connection(DERIV_WS, timeout=10)
        ws.send(json.dumps({"authorize": token}))
        ws.recv()
        return ws
    except: return None

def bot_worker(uid):
    last_price = None
    
    while True:
        u = users.find_one({"_id": ObjectId(uid)})
        # التوقف إذا كانت الحالة STOPPED أو إذا خسرنا صفقة سابقاً
        if not u or "STOPPED" in u.get("status"): break

        # 1. فتح اتصال سريع لمراقبة التيك الحالي
        ws = get_ws_connection(u["token"])
        if not ws:
            time.sleep(0.5)
            continue

        try:
            ws.send(json.dumps({"ticks": u["symbol"], "count": 1}))
            res = json.loads(ws.recv())
            
            if "tick" in res:
                current_price = float(res["tick"]["quote"])
                
                if last_price is not None:
                    diff = current_price - last_price
                    abs_diff = abs(diff)

                    # الشرط المطلوب: فرق 0.2 أو أكثر
                    if abs_diff >= 0.2:
                        direction = "CALL" if diff > 0 else "PUT"
                        barrier = "-0.7" if direction == "CALL" else "+0.7"
                        
                        set_status(uid, f"SPIKE {diff:.2f}! ENTERING {direction}")
                        print(f"🚀 Detected Spike: {diff:.2f}. Executing {direction} with Barrier {barrier}")

                        # 2. تنفيذ الصفقة (6 تيكات)
                        buy_req = {
                            "buy": 1, "price": float(u["stake"]),
                            "parameters": {
                                "amount": float(u["stake"]), "basis": "stake",
                                "contract_type": direction, "currency": "USD",
                                "duration": 6, "duration_unit": "t",
                                "symbol": u["symbol"], "barrier": barrier
                            }
                        }
                        ws.send(json.dumps(buy_req))
                        buy_res = json.loads(ws.recv())
                        
                        if "buy" in buy_res:
                            cid = buy_res["buy"]["contract_id"]
                            # 3. قطع الاتصال فوراً لتجنب التشويش
                            ws.close()
                            
                            # 4. انتظار انتهاء الـ 6 تيكات (حوالي 12 ثانية للأمان)
                            time.sleep(12) 
                            
                            # 5. فتح اتصال جديد لفحص النتيجة
                            ws_res = get_ws_connection(u["token"])
                            if ws_res:
                                ws_res.send(json.dumps({"proposal_open_contract": 1, "contract_id": cid}))
                                r = json.loads(ws_res.recv())
                                poc = r.get("proposal_open_contract", {})
                                
                                if poc.get("is_sold"):
                                    profit = float(poc.get("profit", 0))
                                    if profit > 0:
                                        users.update_one({"_id": ObjectId(uid)}, {
                                            "$inc": {"wins": 1, "profit": profit}
                                        })
                                        set_status(uid, "WIN! SCANNING TICKS...")
                                    else:
                                        # الخسارة تعني التوقف النهائي
                                        users.update_one({"_id": ObjectId(uid)}, {
                                            "$inc": {"losses": 1, "profit": -float(u["stake"])}
                                        })
                                        set_status(uid, "STOPPED (LOSS DETECTED)")
                                        ws_res.close()
                                        break # إنهاء الخيط (Thread)
                                ws_res.close()
                        else:
                            print(f"❌ Buy Error: {buy_res.get('error', {}).get('message')}")
                
                last_price = current_price
            
            if ws: ws.close() # غلق الاتصال الدوري
        except Exception as e:
            print(f"❌ Loop Error: {e}")
            if ws: ws.close()
        
        time.sleep(0.1) # سرعة عالية جداً في المراقبة

# --- Flask Web Interface ---
@app.route("/")
def home():
    return render_template_string("""
    <body style='background:#0d1117;color:white;text-align:center;font-family:sans-serif;padding:30px'>
        <h2 style='color:#00e676'>SPIKE SNIPER V4 (DIFF 0.2)</h2>
        <div id=login_div><input id=ev placeholder="Email"><br><br><button onclick="login()">LOGIN</button></div>
        <div id=stats style='display:none'>
            <div style='font-size:32px; font-weight:bold' id=st></div>
            <div id=info style='font-size:20px; margin:20px 0; background:#161b22; padding:15px; border-radius:10px'></div>
            <button onclick="stop()" style="background:#ff5252;color:white;border:none;padding:10px 20px;border-radius:5px">STOP</button>
            <button onclick="reset()" style='margin-left:10px;padding:10px 20px;border-radius:5px'>RESET</button>
        </div>
        <div id=settings style='display:none'>
            <input id=tv placeholder="Deriv Token"><br><br>
            <input id=sv value=1.0 placeholder="Stake"><br><br>
            <button onclick="start()" style="background:#00e676;padding:10px 20px;border:none;border-radius:5px">START SNIPING</button>
        </div>
        <script>
            let email="";
            async function login(){ email=document.getElementById('ev').value; let r=await fetch('/check/'+email); let d=await r.json(); document.getElementById('login_div').style.display='none'; if(d.found){ document.getElementById('stats').style.display='block'; sync(); } else { document.getElementById('settings').style.display='block'; } }
            async function start(){ 
                let d={email:email, token:document.getElementById('tv').value, symbol:'R_100', stake:parseFloat(document.getElementById('sv').value)}; 
                await fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}); 
                document.getElementById('settings').style.display='none'; document.getElementById('stats').style.display='block'; sync(); 
            }
            async function stop(){ fetch('/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})}); location.reload(); }
            async function reset(){ if(confirm("Reset Data?")){ await fetch('/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})}); location.reload(); } }
            function sync(){ setInterval(async()=>{ let r=await fetch('/check/'+email); let d=await r.json(); if(d.found){ document.getElementById('st').innerText=d.status; document.getElementById('info').innerText=`Profit: ${d.profit.toFixed(2)}$ | W: ${d.wins} L: ${d.losses}`; document.getElementById('st').style.color = d.status.includes("STOPPED") ? "red" : "#00e676"; } },1000); }
        </script>
    </body>
    """)

@app.route("/check/<email>")
def check_email(email):
    u = users.find_one({"email": email}); return jsonify({"found": True, **u}) if u else jsonify({"found": False})

@app.route("/start", methods=["POST"])
def start():
    d = request.json; users.delete_one({"email": d["email"]})
    uid = users.insert_one({"email": d["email"], "token": d["token"], "symbol": d["symbol"], "base_stake": d["stake"], "stake": d["stake"], "profit": 0.0, "wins": 0, "losses": 0, "status": "SCANNING FOR SPIKES..."}).inserted_id
    Thread(target=bot_worker, args=(str(uid),), daemon=True).start()
    return jsonify({"ok": True})

@app.route("/stop", methods=["POST"])
def stop(): email = request.json["email"]; users.update_one({"email": email}, {"$set": {"status": "STOPPED"}}); return jsonify({"ok": True})

@app.route("/reset", methods=["POST"])
def reset(): email = request.json["email"]; users.delete_one({"email": email}); return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
