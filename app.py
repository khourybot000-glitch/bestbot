import os, json, websocket, datetime
import pandas as pd
import numpy as np
from flask import Flask, render_template_string, jsonify, request

app = Flask(__name__)

# --- Technical Config ---
PASSWORD = "KHOURYBOT"
DERIV_WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

def compute_logic(df):
    """
    RSI 50 Cross Logic on the last 30-tick candle with EMA 50 Filter.
    """
    if len(df) < 52: return "NONE"
    
    c = df['close']
    
    # 1. RSI Calculation (14 periods)
    delta = c.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    # 2. EMA 50 Calculation
    ema50 = c.ewm(span=50, adjust=False).mean()
    
    # Values for the last completed candle and the one before it
    curr_rsi = rsi.iloc[-1]
    prev_rsi = rsi.iloc[-2]
    curr_price = c.iloc[-1]
    curr_ema = ema50.iloc[-1]
    
    # --- Strategy Execution ---
    
    # CALL: RSI crossed 50 UPWARDS + Price is ABOVE EMA 50
    if prev_rsi <= 50 < curr_rsi and curr_price > curr_ema:
        return "BUY"
    
    # PUT: RSI crossed 50 DOWNWARDS + Price is BELOW EMA 50
    if prev_rsi >= 50 > curr_rsi and curr_price < curr_ema:
        return "SELL"
        
    return "NONE"

def get_ohlc(prices, size):
    candles = []
    for i in range(0, len(prices), size):
        chunk = prices.iloc[i:i+size]
        if len(chunk) >= size:
            candles.append({
                'open': chunk.iloc[0]['close'],
                'high': chunk['close'].max(),
                'low': chunk['close'].min(),
                'close': chunk.iloc[-1]['close']
            })
    return pd.DataFrame(candles)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>KHOURY SNIPER PRO - V3</title>
    <style>
        :root { --neon-green: #00ff88; --neon-red: #ff3b3b; --neon-blue: #00d4ff; --bg-dark: #020508; --gold: #ffae00; }
        body { background: var(--bg-dark); color: white; font-family: 'Inter', sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        .container { width: 380px; background: #0b0f1a; border-radius: 35px; padding: 30px; border: 1px solid #1f2633; text-align: center; position: relative; }
        .timer-box { font-size: 60px; font-weight: 900; color: var(--neon-blue); }
        .circle { width: 170px; height: 170px; border-radius: 50%; margin: 25px auto; border: 4px solid #1f2633; display: flex; flex-direction: column; justify-content: center; align-items: center; transition: 0.5s; }
        .buy-glow { border-color: var(--neon-green); box-shadow: 0 0 60px var(--neon-green); }
        .sell-glow { border-color: var(--neon-red); box-shadow: 0 0 60px var(--neon-red); }
        .freeze-mode { border-color: var(--gold); opacity: 0.5; filter: grayscale(0.5); }
        .btn { width: 100%; padding: 16px; border-radius: 12px; border: none; background: linear-gradient(135deg, #00d4ff, #7000ff); color: white; font-weight: bold; cursor: pointer; }
        #status { font-size: 12px; color: #666; margin: 15px 0; height: 35px; }
        #freeze-status { color: var(--gold); font-size: 12px; font-weight: bold; margin-bottom: 10px; display: none; }
        .badge { font-size: 10px; border: 1px solid #333; padding: 4px 8px; border-radius: 5px; color: #888; }
    </style>
</head>
<body>
    <div id="login-screen" style="position:fixed; inset:0; background:var(--bg-dark); z-index:100; display:flex; flex-direction:column; justify-content:center; align-items:center;">
        <h2 style="color:var(--neon-blue)">KHOURY SNIPER AI</h2>
        <input type="password" id="pass" style="padding:15px; border-radius:12px; text-align:center; background:#111; color:white; border:1px solid #333;" placeholder="PASSWORD">
        <button class="btn" style="width:220px; margin-top:20px" onclick="login()">ACTIVATE</button>
    </div>

    <div class="container" id="bot-ui" style="display:none">
        <div id="freeze-status">ANALYSIS FROZEN - TRADE IN PROGRESS</div>
        <div class="badge">30-TICK / 5-MIN EXPR</div>
        <div class="timer-box" id="countdown">00</div>
        
        <div id="glow-circle" class="circle">
            <span id="icon" style="font-size:60px">📡</span>
            <span id="sig-text" style="font-weight:900; letter-spacing:1px;">SCANNING</span>
        </div>

        <select id="asset" style="width:100%; padding:12px; background:#161b26; color:white; border-radius:10px; border:1px solid #2d3545; outline:none;">
            <option value="frxEURUSD">EUR/USD</option>
            <option value="frxEURGBP">EUR/GBP</option>
            <option value="frxEURJPY">EUR/JPY</option>
        </select>

        <div id="status">Waiting for next scan...</div>
    </div>

    <script>
        let lastSignalDirection = null;
        let isFrozen = false;
        let freezeEndTime = 0;

        function playAlert(isBuy) {
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.frequency.setValueAtTime(isBuy ? 880 : 440, ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 2);
            osc.connect(gain); gain.connect(ctx.destination);
            osc.start(); osc.stop(ctx.currentTime + 2);
        }

        function login() {
            if(document.getElementById('pass').value === "KHOURYBOT") {
                document.getElementById('login-screen').style.display = 'none';
                document.getElementById('bot-ui').style.display = 'block';
                runClock();
            }
        }

        function runClock() {
            setInterval(() => {
                const now = Date.now();
                const s = new Date().getSeconds();
                document.getElementById('countdown').innerText = (50 - s < 0 ? 60 + (50 - s) : 50 - s).toString().padStart(2, '0');

                if (isFrozen) {
                    if (now >= freezeEndTime) {
                        isFrozen = false;
                        document.getElementById('freeze-status').style.display = 'none';
                        document.getElementById('glow-circle').className = "circle";
                        document.getElementById('status').innerText = "System back online.";
                    } else {
                        let rem = Math.round((freezeEndTime - now) / 1000);
                        document.getElementById('status').innerText = `Analysis locked for ${rem}s`;
                    }
                }

                if (s === 50 && !isFrozen) triggerScan();
                if (s === 0 && !isFrozen) resetUI();
            }, 1000);
        }

        async function triggerScan() {
            document.getElementById('status').innerText = "Analyzing 30-Tick Candle...";
            try {
                const res = await fetch('/scan', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ asset: document.getElementById('asset').value })
                });
                const data = await res.json();
                
                if(data.signal !== "NONE") {
                    if(data.signal === lastSignalDirection) {
                        document.getElementById('status').innerText = "Duplicate signal skipped.";
                        return;
                    }

                    // Signal Found
                    lastSignalDirection = data.signal;
                    document.getElementById('glow-circle').className = data.signal === "BUY" ? "circle buy-glow" : "circle sell-glow";
                    document.getElementById('sig-text').innerText = data.signal === "BUY" ? "CALL 5M" : "PUT 5M";
                    playAlert(data.signal === "BUY");

                    // Start 5-Minute Freeze
                    isFrozen = true;
                    freezeEndTime = Date.now() + 300000; 
                    document.getElementById('freeze-status').style.display = 'block';
                    document.getElementById('glow-circle').classList.add('freeze-mode');
                } else {
                    document.getElementById('status').innerText = "No crossover detected.";
                }
            } catch(e) { document.getElementById('status').innerText = "Sync Error"; }
        }

        function resetUI() {
            document.getElementById('glow-circle').className = "circle";
            document.getElementById('sig-text').innerText = "SCANNING";
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index(): return render_template_string(HTML_TEMPLATE)

@app.route('/scan', methods=['POST'])
def scan():
    asset = request.json.get('asset', 'frxEURUSD')
    try:
        ws = websocket.create_connection(DERIV_WS_URL)
        ws.send(json.dumps({"ticks_history": asset, "count": 2000, "end": "latest", "style": "ticks"}))
        data = json.loads(ws.recv())
        ws.close()
        
        ticks = pd.DataFrame(data['history']['prices'], columns=['close'])
        
        # Build 30-tick candles
        df_30t = get_ohlc(ticks, 30)
        
        # Compute Strategy
        signal = compute_logic(df_30t)
        
        return jsonify({"signal": signal})
    except:
        return jsonify({"signal": "NONE"})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
