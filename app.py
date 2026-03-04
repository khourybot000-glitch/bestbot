from flask import Flask, request, jsonify
import websocket, json, threading, random, time

app = Flask(__name__)

DERIV_WS = "wss://blue.derivws.com/websockets/v3?app_id=16929"
ASSETS = ["frxEURUSD", "frxEURJPY", "frxEURGBP", "frxUSDCAD", "frxUSDJPY"]

# ---------------- TICKS FETCH ----------------
def get_ticks(symbol, count=600):
    try:
        ws = websocket.create_connection(DERIV_WS, timeout=10)
        ws.send(json.dumps({"authorize": "0000000"}))
        ws.recv()
        ws.send(json.dumps({
            "ticks_history": symbol,
            "end": "latest",
            "count": count,
            "style": "ticks"
        }))
        res = json.loads(ws.recv())
        ws.close()
        if "history" in res and "prices" in res["history"]:
            return res["history"]["prices"]
        return []
    except Exception as e:
        print("Error fetching ticks:", e)
        return []

# ---------------- TICKS → CANDLES ----------------
def ticks_to_candles(ticks, per_candle=20):
    candles = []
    for i in range(0, len(ticks), per_candle):
        chunk = ticks[i:i+per_candle]
        if chunk:
            candles.append({
                "open": chunk[0],
                "close": chunk[-1],
                "high": max(chunk),
                "low": min(chunk)
            })
    return candles

# ---------------- 50 INDICATORS SIMULATION ----------------
def analyze_candles(candles):
    if not candles:
        return {"signal": "WAIT", "strategy": "NO DATA", "accuracy": 0}
    last = candles[-1]
    up_count = sum(1 for c in candles if c["close"]>c["open"])
    down_count = sum(1 for c in candles if c["close"]<c["open"])
    trend = "UP" if up_count > down_count else "DOWN"
    
    # Simulate 50 indicators voting
    indicators = []
    for i in range(50):
        weight = random.randint(80, 100)
        vote = "CALL" if trend=="UP" else "PUT"
        if random.random()<0.1: vote = "CALL" if vote=="PUT" else "PUT"
        indicators.append({"indicator": f"IND{i+1}", "vote": vote, "weight": weight})
    
    # Aggregate votes
    call_score = sum(ind["weight"] for ind in indicators if ind["vote"]=="CALL")
    put_score = sum(ind["weight"] for ind in indicators if ind["vote"]=="PUT")
    signal = "CALL" if call_score>put_score else "PUT"
    accuracy = int(max(call_score, put_score)/sum(ind["weight"] for ind in indicators)*100)
    
    # Pick a random strategy for display
    strategy = random.choice([
        "Order Blocks (SMC)", "Fair Value Gap (FVG)", "Liquidity Sweep",
        "Fibonacci 0.618", "Elliott Wave", "RSI Divergence"
    ])
    return {"signal": signal, "strategy": strategy, "accuracy": accuracy}

# ---------------- FLASK ROUTES ----------------
@app.route("/")
def index():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>KHOURY BOT PRO</title>
<style>
body{background:#020508;color:white;font-family:sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;overflow:hidden;}
.login-box{width:320px;padding:30px;background:#080c14;border-radius:20px;border:2px solid #00d4ff;text-align:center;box-shadow:0 0 20px #00d4ff;}
.login-input{width:100%;padding:12px;margin:20px 0;background:transparent;border:1px solid #333;color:white;border-radius:10px;text-align:center;}
.main-btn{width:100%;background:linear-gradient(90deg,#00ff88,#00d4ff);color:black;border:none;padding:15px;border-radius:15px;font-size:16px;font-weight:800;cursor:pointer;text-transform:uppercase;box-shadow:0 0 15px rgba(0,255,136,0.3);}
.main-btn:active{transform:scale(0.98);}
#main-ui{display:none;width:380px;background:#080c14;border-radius:35px;padding:25px;position:relative;overflow:hidden;border:2px solid rgba(255,255,255,0.05);}
.asset-box{background:rgba(255,255,255,0.05);padding:12px;border-radius:15px;margin-bottom:20px;}
#asset-select{width:100%;background:transparent;border:none;color:#00ff88;font-size:15px;font-weight:900;outline:none;text-shadow:0 0 8px #00ff88;}
.signal-card{background:rgba(0,0,0,0.5);border-radius:25px;padding:35px 15px;text-align:center;margin-bottom:20px;border:1px solid rgba(255,255,255,0.05);}
.main-circle{width:140px;height:140px;border-radius:50%;margin:0 auto;display:flex;flex-direction:column;justify-content:center;align-items:center;border:2px solid rgba(255,255,255,0.1);transition:0.5s ease;}
.buy-active{border-color:#00ff88;box-shadow:0 0 40px #00ff88;color:#00ff88;}
.sell-active{border-color:#ff3b3b;box-shadow:0 0 40px #ff3b3b;color:#ff3b3b;}
.stats-row{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:15px 0;}
.stat-item{background:rgba(255,255,255,0.03);padding:10px;border-radius:12px;text-align:center;font-size:11px;border:1px solid rgba(255,255,255,0.05);}
</style>
</head>
<body>
<div id="login-face">
    <div class="login-box">
        <h2 style="color:#00d4ff;">KHOURY BOT PRO</h2>
        <input type="password" id="pass-input" class="login-input" placeholder="Enter Password">
        <button class="main-btn" onclick="checkPass()">Unlock AI Engine</button>
    </div>
</div>
<div id="main-ui">
    <div class="asset-box">
        <select id="asset-select">
            <option value="frxEURUSD">EUR/USD</option>
            <option value="frxEURJPY">EUR/JPY</option>
            <option value="frxEURGBP">EUR/GBP</option>
            <option value="frxUSDCAD">USD/CAD</option>
            <option value="frxUSDJPY">USD/JPY</option>
        </select>
    </div>
    <div class="signal-card">
        <div id="circle" class="main-circle">
            <span id="arrow" style="font-size:50px;">📡</span>
            <span id="name" style="font-size:16px;font-weight:bold;color:#00d4ff;">READY</span>
        </div>
        <div id="strategy-flash" style="font-size:9px;color:#00d4ff;margin-top:10px;height:12px;"></div>
        <div id="msg" style="margin-top:10px;font-weight:900;letter-spacing:2px;color:#444">AWAITING SCAN</div>
    </div>
    <button class="main-btn" onclick="generateSignal()">🚀 GENERATE SIGNAL 🚀</button>
    <div class="stats-row">
        <div class="stat-item"><p style="color:#888">ACCURACY💪</p><b id="acc-v" style="color:#00ff88">0%</b></div>
    </div>
</div>
<script>
function checkPass(){
    if(document.getElementById('pass-input').value==="KHOURYBOT"){
        document.getElementById('login-face').style.display='none';
        document.getElementById('main-ui').style.display='block';
    }else alert("Wrong Password!");
}
async function generateSignal(){
    const asset=document.getElementById('asset-select').value;
    const btn=document.querySelector('.main-btn');
    btn.disabled=true;
    document.getElementById('strategy-flash').innerText="Fetching ticks...";
    try{
        const res=await fetch('/get_signal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({asset})});
        const data=await res.json();
        document.getElementById('name').innerText=data.signal;
        document.getElementById('msg').innerText=data.signal=="CALL"?"GO UP":"GO DOWN";
        document.getElementById('arrow').innerText=data.signal=="CALL"?"↑":"↓";
        document.getElementById('acc-v').innerText=data.accuracy+"%";
        document.getElementById('strategy-flash').innerText=data.strategy;
        document.documentElement.style.setProperty('--current-glow',data.signal=="CALL"?"#00ff88":"#ff3b3b");
    }catch(e){alert("Error fetching signal!");console.log(e);}
    finally{btn.disabled=false;}
}
</script>
</body>
</html>
"""

@app.route("/get_signal", methods=["POST"])
def get_signal():
    asset = request.json.get("asset")
    if asset not in ASSETS:
        return jsonify({"error":"Invalid asset"})
    ticks = get_ticks(asset, 600)
    candles = ticks_to_candles(ticks, 20)
    result = analyze_candles(candles)
    return jsonify(result)

if __name__=="__main__":
    app.run(host="0.0.0.0", port=10000)
