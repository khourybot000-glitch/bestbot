import json, websocket, time, os
from datetime import datetime
from threading import Thread
from flask import Flask, render_template_string, jsonify, request
from pymongo import MongoClient
from bson.objectid import ObjectId

app = Flask(__name__)

# --- MongoDB ---
MONGO_URI = "mongodb+srv://charbelnk111_db_user:Mano123mano@cluster0.2gzqkc8.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client["KHOURY_BOT"]
users = db["users"]
DERIV_WS = "wss://blue.derivws.com/websockets/v3?app_id=16929"

def set_status(uid, status):
    users.update_one({"_id": ObjectId(uid)}, {"$set": {"status": status}})

def get_account_currency(token):
    """فتح اتصال مؤقت لجلب عملة الحساب وإغلاقه فوراً"""
    try:
        ws = websocket.create_connection(DERIV_WS, timeout=10)
        ws.send(json.dumps({"authorize": token}))
        res = json.loads(ws.recv())
        currency = res.get('authorize', {}).get('currency', 'USD')
        ws.close()
        return currency
    except: return "USD"

def bot_worker(uid):
    pending_direction = None
    
    while True:
        u = users.find_one({"_id": ObjectId(uid)})
        if not u or "STOPPED" in u.get("status"): break

        now = datetime.now()
        sec = now.second
        
        # استيقاظ البوت فقط عند الثواني المطلوبة (0, 10, 20, 30, 40, 50)
        if sec % 10 == 0:
            try:
                # فتح الاتصال "عند الحاجة" فقط
                ws = websocket.create_connection(DERIV_WS, timeout=10)
                ws.send(json.dumps({"authorize": u["token"]}))
                ws.recv() # تفعيل الـ Authorization

                # طلب التاريخ اللحظي (5 تيكات)
                ws.send(json.dumps({"ticks_history": u["symbol"], "count": 5, "end": "latest", "style": "ticks"}))
                res = json.loads(ws.recv())
                
                if "history" in res:
                    prices = res["history"]["prices"]
                    diff = round(prices[-1] - prices[0], 3)
                    current_dir = "CALL" if diff > 0 else "PUT"

                    # المنطق 1: البحث عن الانفجار (0.7)
                    if not pending_direction:
                        if abs(diff) >= 0.7:
                            pending_direction = current_dir
                            set_status(uid, f"SPIKE FOUND ({current_dir}) - WAITING NEXT 10S")
                        else:
                            set_status(uid, "SCANNING (IDLE)...")
                    
                    # المنطق 2: التأكيد والدخول
                    else:
                        if current_dir == pending_direction:
                            set_status(uid, f"CONFIRMED! EXECUTING {current_dir}")
                            barrier = "-0.7" if current_dir == "CALL" else "+0.7"
                            
                            buy_req = {
                                "buy": 1, "price": float(u["stake"]),
                                "parameters": {
                                    "amount": float(u["stake"]), "basis": "stake",
                                    "contract_type": current_dir, "currency": u["currency"],
                                    "duration": 6, "duration_unit": "t",
                                    "symbol": u["symbol"], "barrier": barrier
                                }
                            }
                            ws.send(json.dumps(buy_req))
                            buy_res = json.loads(ws.recv())
                            
                            if "buy" in buy_res:
                                cid = buy_res["buy"]["contract_id"]
                                ws.close() # غلق الاتصال فوراً بعد الشراء
                                
                                time.sleep(11) # انتظار النتيجة
                                
                                # فتح اتصال جديد "فقط" لمعرفة النتيجة
                                ws_check = websocket.create_connection(DERIV_WS, timeout=10)
                                ws_check.send(json.dumps({"authorize": u["token"]}))
                                ws_check.recv()
                                ws_check.send(json.dumps({"proposal_open_contract": 1, "contract_id": cid}))
                                poc = json.loads(ws_check.recv()).get("proposal_open_contract", {})
                                
                                if poc.get("is_sold"):
                                    profit = float(poc.get("profit", 0))
                                    if profit > 0:
                                        users.update_one({"_id": ObjectId(uid)}, {"$inc": {"wins": 1, "profit": profit}})
                                        set_status(uid, "WIN! BACK TO SLEEP")
                                    else:
                                        users.update_one({"_id": ObjectId(uid)}, {"$inc": {"losses": 1, "profit": -float(u["stake"])}})
                                        set_status(uid, "STOPPED (LOSS)")
                                        ws_check.close()
                                        break
                                ws_check.close()
                            pending_direction = None
                        else:
                            # إذا لم يتأكد الاتجاه، نلغي الإشارة أو نحدثها إذا كانت قوية
                            pending_direction = current_dir if abs(diff) >= 0.7 else None
                            set_status(uid, "CONFIRM FAILED - RESETTING")
                
                if ws: ws.close() # العودة للسكون التام
            except:
                pass
            
            time.sleep(1.5) # تجنب التكرار في نفس الثانية

        time.sleep(0.1) # نبض خفيف للمراقبة الزمنية

# --- Web UI ---
@app.route("/")
def home():
    return render_template_string("""
    <body style='background:#0d1117;color:white;text-align:center;font-family:sans-serif;padding:30px'>
        <h2 style='color:#00e676'>ON-DEMAND ANALYZER V8</h2>
        <div id=login_div>
            <input id=ev placeholder="Email" style="padding:10px"><br><br>
            <button onclick="login()" style="padding:10px 20px">ACCESS BOT</button>
        </div>
        <div id=settings style='display:none'>
            <input id=tv placeholder="Token" style="width:280px; padding:10px"><br><br>
            <input id=stk_v value="1.0" type="number" style="padding:10px"><br><br>
            <button onclick="start()" style="background:#00e676; border:none; padding:10px 20px; border-radius:5px">START MONITORING</button>
        </div>
        <div id=stats style='display:none'>
            <div id=st style='font-size:26px; font-weight:bold; color:#00e676'>SLEEPING...</div>
            <div id=cur_info style='color:#8b949e; margin:10px'></div>
            <div id=info style='font-size:20px; margin:20px'></div>
            <button onclick="location.reload()" style="background:#ff5252; color:white; border:none; padding:10px 20px">STOP</button>
        </div>
        <script>
            let email = "";
            async function login(){
                email = document.getElementById('ev').value;
                let r = await fetch('/check/'+email);
                let d = await r.json();
                document.getElementById('login_div').style.display='none';
                if(d.found){ document.getElementById('stats').style.display='block'; sync(); }
                else { document.getElementById('settings').style.display='block'; }
            }
            async function start(){
                let t = document.getElementById('tv').value;
                let s = document.getElementById('stk_v').value;
                await fetch('/start', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email:email, token:t, stake:s, symbol:'R_100'})});
                document.getElementById('settings').style.display='none';
                document.getElementById('stats').style.display='block';
                sync();
            }
            function sync(){
                setInterval(async()=>{
                    let r = await fetch('/check/'+email);
                    let d = await r.json();
                    if(d.found){
                        document.getElementById('st').innerText = d.status;
                        document.getElementById('cur_info').innerText = "Currency: " + (d.currency || "---");
                        document.getElementById('info').innerText = `Profit: ${d.profit.toFixed(2)}$ | W: ${d.wins} L: ${d.losses}`;
                    }
                }, 1000);
            }
        </script>
    </body>
    """)

@app.route("/check/<email>")
def check(email):
    u = users.find_one({"email": email})
    if u: u["_id"]=str(u["_id"]); return jsonify({"found": True, **u})
    return jsonify({"found": False})

@app.route("/start", methods=["POST"])
def start():
    d = request.json
    currency = get_account_currency(d["token"])
    users.delete_one({"email": d["email"]})
    uid = users.insert_one({
        "email": d["email"], "token": d["token"], "symbol": d["symbol"], 
        "stake": float(d["stake"]), "currency": currency,
        "profit": 0.0, "wins": 0, "losses": 0, "status": "WAITING FOR NEXT CYCLE..."
    }).inserted_id
    Thread(target=bot_worker, args=(str(uid),), daemon=True).start()
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
