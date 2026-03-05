import os
import json
import websocket
import datetime
import pandas as pd
import numpy as np
from flask import Flask, render_template_string, jsonify, request

app = Flask(__name__)

# --- Technical Config ---
PASSWORD = "KHOURYBOT"
DERIV_WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

def compute_logic(df):
    """Direct Trend Following Logic"""
    if len(df) < 60: return "NONE"
    c = df['close']
    
    # RSI 14 Calculation
    delta = c.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    # EMA 50 Calculation
    ema50 = c.ewm(span=50, adjust=False).mean()
    
    curr_rsi = rsi.iloc[-1]
    prev_rsi = rsi.iloc[-2]
    older_rsi = rsi.iloc[-3]
    curr_price = c.iloc[-1]
    curr_ema = ema50.iloc[-1]
    
    # --- TREND LOGIC ---
    # CALL: RSI crosses 50 UP + Price is above EMA 50
    if (older_rsi <= 50 or prev_rsi <= 50) and curr_rsi > 50 and curr_price > curr_ema:
        return "BUY"
    
    # PUT: RSI crosses 50 DOWN + Price is below EMA 50
    if (older_rsi >= 50 or prev_rsi >= 50) and curr_rsi < 50 and curr_price < curr_ema:
        return "SELL"
        
    return "NONE"

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>KHOURY V5 SNIPER</title>
    <style>
        :root { --bg: #05080a; --card: #0d1117; --blue: #58a6ff; --green: #00ff88; --red: #ff3b3b; --gold: #ffae00; }
        body { background: var(--bg); color: white; font-family: 'Segoe UI', sans-serif; margin: 0; padding: 15px; display: flex; flex-direction: column; align-items: center; }
        .container { width: 100%; max-width: 480px; margin-top: 20px; }
        .setup-panel { background: var(--card); border: 1px solid #30363d; border-radius: 20px; padding: 20px; text-align: center; margin-bottom: 15px; }
        select { background: #000; color: white; border: 1px solid var(--blue); padding: 12px; border-radius: 10px; width: 100%; font-size: 16px; margin-top: 10px; cursor: pointer; outline: none; }
        .countdown { font-size: 90px; font-weight: 900; color: #58a6ff; margin: 15px 0; font-family: 'Courier New', monospace; text-shadow: 0 0 15px rgba(88,166,255,0.4); }
        .signal-box { 
            height: 320px; width: 100%; background: #161b22; border-radius: 25px; 
            display: flex; flex-direction: column; justify-content: center; align-items: center;
            transition: 0.5s cubic-bezier(0.175, 0.885, 0.32, 1.275); border: 2px solid #21262d; 
        }
        .buy-active { background: #064e3b !important; border-color: var(--green) !important; box-shadow: 0 0 50px rgba(0,255,136,0.3); }
        .sell-active { background: #7f1d1d !important; border-color: var(--red) !important; box-shadow: 0 0 50px rgba(255,59,59,0.3); }
        .sig-title { font-size: 55px; font-weight: 900; letter-spacing: 4px; }
        .entry-time { font-size: 22px; margin-top: 20px; background: #000; padding: 12px 25px; border-radius: 12px; color: #00d4ff; font-family: monospace; border: 1px solid #333; font-weight: bold; }
        .lockdown { font-size: 16px; margin-top: 20px; color: var(--gold); font-weight: bold; letter-spacing: 1px; }
        #login-screen { position:fixed; inset:0; background:var(--bg); z-index:2000; display:flex; flex-direction:column; justify-content:center; align-items:center; }
        input[type="password"] { padding: 15px; border-radius: 10px; border: 1px solid #333; background:#111; color:white; text-align:center; width: 260px; font-size: 18px; margin-bottom: 15px; }
        .btn { padding: 14px 60px; border-radius: 10px; border: none; background: var(--blue); color: black; font-weight: bold; cursor: pointer; font-size: 18px; }
    </style>
</head>
<body>
    <div id="login-screen">
        <h1 style="color:var(--blue); margin-bottom: 30px;">KHOURY SNIPER V5</h1>
        <input type="password" id="pass" placeholder="ACCESS CODE">
        <button class="btn" onclick="login()">ACTIVATE</button>
    </div>

    <div id="main-ui" style="display:none" class="container">
        <div class="setup-panel">
            <label style="color: #8b949e; font-size: 14px; font-weight: bold;">SELECT TRADING ASSET</label>
            <select id="asset-selector">
                <option value="frxEURUSD">EUR / USD</option>
                <option value="frxEURGBP">EUR / GBP</option>
                <option value="frxEURJPY">EUR / JPY</option>
                <option value="frxGBPUSD">GBP / USD</option>
                <option value="frxUSDJPY">USD / JPY</option>
            </select>
        </div>

        <div style="text-align: center;">
            <div class="countdown" id="timer">00</div>
        </div>

        <div class="signal-box" id="box">
            <span class="sig-title" id="text">SCANNING</span>
            <div class="entry-time" id="entry" style="display:none">ENTRY @ --:--:00</div>
            <div class="lockdown" id="lockdown" style="display:none">LOCKDOWN: 300s</div>
        </div>
        
        <p style="text-align: center; color: #8b949e; margin-top: 25px; font-size: 14px; line-height: 1.6;">
            <b>Analysis:</b> Second 00 | <b>Duration:</b> 5 MIN<br>
            <b>Strategy:</b> Direct Trend Momentum
        </p>
    </div>

    <script>
        let isSleeping = false;
        let sleepEnds = 0;
        let displayEntry = "";
        let displayType = "";

        function playSound(isBuy) {
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            const osc = ctx.createOscillator();
            const g = ctx.createGain();
            osc.frequency.setValueAtTime(isBuy ? 880 : 440, ctx.currentTime);
            g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 1.5);
            osc.connect(g); g.connect(ctx.destination);
            osc.start(); osc.stop(ctx.currentTime + 1.5);
        }

        function login() {
            if(document.getElementById('pass').value === "KHOURYBOT") {
                document.getElementById('login-screen').style.display = 'none';
                document.getElementById('main-ui').style.display = 'block';
                setInterval(engine, 1000);
            }
        }

        async function triggerScan() {
            const asset = document.getElementById('asset-selector').value;
            try {
                const res = await fetch('/scan', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ asset: asset })
                });
                const data = await res.json();
                
                if(data.signal !== "NONE") {
                    let now = new Date();
                    let entryDate = new Date(now.getTime() + 60000);
                    entryDate.setSeconds(0);
                    
                    displayEntry = entryDate.toTimeString().split(' ')[0];
                    displayType = data.signal;
                    
                    playSound(data.signal === "BUY");
                    isSleeping = true;
                    sleepEnds = Date.now() + 300000; 
                }
            } catch(e) {}
        }

        function engine() {
            const now = Date.now();
            const s = new Date().getSeconds();
            const cd = (60 - s) % 60;
            
            document.getElementById('timer').innerText = cd.toString().padStart(2, '0');
            const box = document.getElementById('box');
            const text = document.getElementById('text');
            const entryLabel = document.getElementById('entry');
            const lockLabel = document.getElementById('lockdown');

            if(isSleeping) {
                const rem = Math.round((sleepEnds - now) / 1000);
                if(rem <= 0) {
                    isSleeping = false;
                    box.className = "signal-box";
                    text.innerText = "SCANNING";
                    entryLabel.style.display = "none";
                    lockLabel.style.display = "none";
                } else {
                    box.className = displayType === "BUY" ? "signal-box buy-active" : "signal-box sell-active";
                    text.innerText = displayType === "BUY" ? "CALL 5M" : "PUT 5M";
                    entryLabel.style.display = "block";
                    entryLabel.innerText = "ENTRY @ " + displayEntry;
                    lockLabel.style.display = "block";
                    lockLabel.innerText = "LOCKDOWN: " + rem + "s";
                }
            }
            if(s === 0 && !isSleeping) triggerScan();
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index(): return render_template_string(HTML_TEMPLATE)

@app.route('/scan', methods=['POST'])
def scan():
    asset = request.json.get('asset')
    try:
        ws = websocket.create_connection(DERIV_WS_URL)
        ws.send(json.dumps({"ticks_history": asset, "count": 1000, "end": "latest", "style": "ticks"}))
        data = json.loads(ws.recv())
        ws.close()
        ticks = pd.DataFrame(data['history']['prices'], columns=['close'])
        
        # 5-Tick Aggregation
        candles = []
        for i in range(0, len(ticks), 5):
            chunk = ticks.iloc[i:i+5]
            if len(chunk) >= 5: candles.append({'close': chunk.iloc[-1]['close']})
        
        signal = compute_logic(pd.DataFrame(candles))
        return jsonify({"signal": signal})
    except:
        return jsonify({"signal": "NONE"})

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
