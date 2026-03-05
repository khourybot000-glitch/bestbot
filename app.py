import os, json, websocket, datetime
import pandas as pd
import numpy as np
from flask import Flask, render_template_string, jsonify, request

app = Flask(__name__)

# --- Technical Settings ---
PASSWORD = "KHOURYBOT"
DERIV_WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

def compute_50_indicators(df):
    """Deep analysis for micro-timeframes (5 & 2 ticks)."""
    if len(df) < 10: return "NEUTRAL"
    c, h, l, o = df['close'], df['high'], df['low'], df['open']
    
    score = 0
    # Technical Indicators optimized for noise reduction
    rsi = 100 - (100 / (1 + (c.diff().clip(lower=0).rolling(7).mean() / -c.diff().clip(upper=0).rolling(7).mean())))
    macd = c.ewm(span=5).mean() - c.ewm(span=13).mean()
    ema = c.ewm(span=10).mean()
    std = c.rolling(10).std()
    z_score = (c - ema) / std
    
    # Fast-response logic
    if rsi.iloc[-1] > 55: score += 1
    if macd.iloc[-1] > 0: score += 1
    if c.iloc[-1] > ema.iloc[-1]: score += 1
    if z_score.iloc[-1] > 0.5: score += 1
    if c.iloc[-1] > o.iloc[-1]: score += 1 # Price Action confirmation
    
    if score >= 4: return "UP"
    if score <= 1: return "DOWN"
    return "NEUTRAL"

def get_ohlc(prices, size):
    candles = []
    for i in range(0, len(prices), size):
        chunk = prices.iloc[i:i+size]
        if len(chunk) >= size:
            candles.append({'open':chunk.iloc[0]['close'], 'high':chunk['close'].max(), 'low':chunk['close'].min(), 'close':chunk.iloc[-1]['close']})
    return pd.DataFrame(candles)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>KHOURY MICRO-SNIPER (5/2 TICKS)</title>
    <style>
        :root { --neon-green: #00ff88; --neon-red: #ff3b3b; --neon-blue: #00d4ff; --bg-dark: #020508; }
        body { background: var(--bg-dark); color: white; font-family: 'Inter', sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        .container { width: 380px; background: #0b0f1a; border-radius: 35px; padding: 30px; border: 1px solid #1f2633; text-align: center; }
        .timer-box { font-size: 55px; font-weight: 900; color: var(--neon-blue); text-shadow: 0 0 20px var(--neon-blue); }
        .circle { width: 160px; height: 160px; border-radius: 50%; margin: 25px auto; border: 4px solid #1f2633; display: flex; flex-direction: column; justify-content: center; align-items: center; transition: 0.4s; }
        .buy-glow { border-color: var(--neon-green); box-shadow: 0 0 60px var(--neon-green); }
        .sell-glow { border-color: var(--neon-red); box-shadow: 0 0 60px var(--neon-red); }
        .btn { width: 100%; padding: 16px; border-radius: 12px; border: none; background: linear-gradient(135deg, #00d4ff, #7000ff); color: white; font-weight: bold; cursor: pointer; }
        select { width:100%; padding:12px; background:#161b26; color:var(--neon-green); border:1px solid #2d3545; border-radius:10px; font-weight:bold; outline:none; text-align: center; }
        #status { font-size: 12px; color: #666; margin: 15px 0; height: 35px; }
        .mode-tag { font-size: 9px; color: #ffae00; border: 1px solid #ffae00; padding: 2px 8px; border-radius: 5px; text-transform: uppercase; }
    </style>
</head>
<body>
    <div id="login-screen" style="position:fixed; inset:0; background:var(--bg-dark); z-index:100; display:flex; flex-direction:column; justify-content:center; align-items:center;">
        <h1 style="color:var(--neon-blue); font-weight: 900;">MICRO SNIPER AI</h1>
        <input type="password" id="pass" style="padding:15px; border-radius:12px; text-align:center; background:#111; color:white;" placeholder="PASSWORD">
        <button class="btn" style="width:200px; margin-top:20px" onclick="login()">ACTIVATE</button>
    </div>

    <div class="container" id="bot-ui" style="display:none">
        <div class="mode-tag">High-Frequency 5/2 Ticks</div>
        <div id="live-clock" style="font-size:13px; color:var(--neon-blue); margin-top:5px;">00:00:00</div>
        <div class="timer-box" id="countdown">00</div>
        
        <div id="glow-circle" class="circle">
            <span id="icon" style="font-size:55px">🎯</span>
            <span id="sig-text" style="font-weight:900; font-size:22px">HUNTING</span>
        </div>

        <select id="asset">
            <option value="frxEURUSD">EUR/USD</option>
            <option value="frxEURGBP">EUR/GBP</option>
            <option value="frxEURJPY">EUR/JPY</option>
        </select>

        <div id="status">System Online...</div>
        <div style="font-size:10px; color: var(--neon-blue);" id="memory">Ready for first flip</div>
    </div>

    <script>
        let lastSignalType = null; 

        function playSignalSound(isBuy) {
            const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            const oscillator = audioCtx.createOscillator();
            const gainNode = audioCtx.createGain();
            oscillator.type = 'sine';
            oscillator.frequency.setValueAtTime(isBuy ? 880 : 660, audioCtx.currentTime); 
            gainNode.gain.setValueAtTime(0.1, audioCtx.currentTime);
            gainNode.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 1);
            oscillator.connect(gainNode);
            gainNode.connect(audioCtx.destination);
            oscillator.start(); oscillator.stop(audioCtx.currentTime + 1);
        }

        function login() {
            if(document.getElementById('pass').value === "KHOURYBOT") {
                document.getElementById('login-screen').style.display = 'none';
                document.getElementById('bot-ui').style.display = 'block';
                startClock();
            }
        }

        function startClock() {
            setInterval(() => {
                const now = new Date();
                const s = now.getSeconds();
                document.getElementById('live-clock').innerText = now.toLocaleTimeString();
                let d = 50 - s; if(d < 0) d = 60 + d;
                document.getElementById('countdown').innerText = d.toString().padStart(2, '0');
                if(s === 50) trigger();
                if(s === 0) resetUI();
            }, 1000);
        }

        async function trigger() {
            document.getElementById('status').innerText = "Scanning Micro-Ticks...";
            try {
                const res = await fetch('/scan', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ asset: document.getElementById('asset').value })
                });
                const data = await res.json();
                
                if(data.signal !== "NONE") {
                    if (data.signal === lastSignalType) {
                        document.getElementById('status').innerText = `Skipped: Duplicate ${data.signal} direction.`;
                        return; 
                    }

                    lastSignalType = data.signal;
                    document.getElementById('memory').innerText = `Required: ${lastSignalType === 'BUY' ? 'SELL' : 'BUY'}`;
                    
                    document.getElementById('glow-circle').className = data.signal === "BUY" ? "circle buy-glow" : "circle sell-glow";
                    document.getElementById('sig-text').innerText = data.signal === "BUY" ? "CALL" : "PUT";
                    document.getElementById('sig-text').style.color = data.signal === "BUY" ? "var(--neon-green)" : "var(--neon-red)";
                    document.getElementById('icon').innerText = data.signal === "BUY" ? "▲" : "▼";
                    
                    playSignalSound(data.signal === "BUY");
                    document.getElementById('status').innerText = "MICRO SIGNAL DETECTED!";
                } else {
                    document.getElementById('status').innerText = "No Confluence.";
                }
            } catch(e) { document.getElementById('status').innerText = "Sync Error"; }
        }

        function resetUI() {
            document.getElementById('glow-circle').className = "circle";
            document.getElementById('sig-text').innerText = "HUNTING";
            document.getElementById('sig-text').style.color = "white";
            document.getElementById('icon').innerText = "🎯";
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
        ws.send(json.dumps({"ticks_history": asset, "count": 1000, "end": "latest", "style": "ticks"}))
        data = json.loads(ws.recv())
        ws.close()
        
        ticks = pd.DataFrame(data['history']['prices'], columns=['close'])
        
        # Dual-Micro-Stage Analysis Only (5 & 2 ticks)
        f5 = compute_50_indicators(get_ohlc(ticks, 5))
        f2 = compute_50_indicators(get_ohlc(ticks, 2))
        
        if f5 == f2 == "UP":
            return jsonify({"signal": "BUY"})
        if f5 == f2 == "DOWN":
            return jsonify({"signal": "SELL"})

        return jsonify({"signal": "NONE"})
    except:
        return jsonify({"signal": "NONE"})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
