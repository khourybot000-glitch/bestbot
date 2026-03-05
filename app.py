import os
import json
import websocket
import datetime
import pandas as pd
import numpy as np
from flask import Flask, render_template_string, jsonify, request

app = Flask(__name__)

# --- Configuration ---
PASSWORD = "KHOURYBOT"
DERIV_WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

def compute_logic(df):
    """Inverted Signal Logic for Reversals"""
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
    
    curr_rsi, prev_rsi, older_rsi = rsi.iloc[-1], rsi.iloc[-2], rsi.iloc[-3]
    curr_price, curr_ema = c.iloc[-1], ema50.iloc[-1]
    
    # --- Inverted Logic ---
    # RSI crosses 50 UP + Price > EMA -> Predict Drop (SELL)
    if (older_rsi <= 50 or prev_rsi <= 50) and curr_rsi > 50 and curr_price > curr_ema:
        return "SELL"
    
    # RSI crosses 50 DOWN + Price < EMA -> Predict Rise (BUY)
    if (older_rsi >= 50 or prev_rsi >= 50) and curr_rsi < 50 and curr_price < curr_ema:
        return "BUY"
        
    return "NONE"

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>KHOURY ZERO-SECOND PRO</title>
    <style>
        :root { --bg: #05080a; --card: #0d1117; --blue: #58a6ff; --green: #00ff88; --red: #ff3b3b; --gold: #ffae00; }
        body { background: var(--bg); color: white; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 15px; }
        .dashboard { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 15px; max-width: 1200px; margin: 30px auto; }
        .pair-column { background: var(--card); border: 1px solid #30363d; border-radius: 20px; padding: 25px; text-align: center; box-shadow: 0 4px 20px rgba(0,0,0,0.5); }
        .pair-header { font-size: 26px; font-weight: bold; color: var(--blue); margin-bottom: 10px; border-bottom: 1px solid #222; padding-bottom: 15px; }
        .countdown { font-size: 55px; font-weight: 900; color: #58a6ff; margin-bottom: 15px; font-family: 'Courier New', monospace; }
        .signal-box { 
            height: 250px; width: 100%; background: #161b22; border-radius: 15px; 
            display: flex; flex-direction: column; justify-content: center; align-items: center;
            transition: 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275); border: 2px solid #21262d; 
        }
        .buy-active { background: #064e3b !important; border-color: var(--green) !important; box-shadow: 0 0 30px rgba(0,255,136,0.3); }
        .sell-active { background: #7f1d1d !important; border-color: var(--red) !important; box-shadow: 0 0 30px rgba(255,59,59,0.3); }
        .sig-title { font-size: 38px; font-weight: 900; letter-spacing: 2px; }
        .entry-time { font-size: 19px; margin-top: 15px; background: #000; padding: 12px 20px; border-radius: 10px; color: #00d4ff; font-family: monospace; border: 1px solid #333; font-weight: bold; }
        .cooldown-info { font-size: 14px; margin-top: 15px; color: var(--gold); font-weight: bold; text-transform: uppercase; }
        
        #login-screen { position:fixed; inset:0; background:var(--bg); z-index:2000; display:flex; flex-direction:column; justify-content:center; align-items:center; }
        input { padding: 15px; border-radius: 10px; border: 1px solid #333; background:#111; color:white; text-align:center; width: 260px; font-size: 18px; margin-bottom: 15px; outline: none; }
        input:focus { border-color: var(--blue); }
        .btn-login { padding: 14px 60px; border-radius: 10px; border: none; background: var(--blue); color: black; font-weight: bold; cursor: pointer; font-size: 18px; transition: 0.2s; }
        .btn-login:hover { opacity: 0.8; transform: scale(1.05); }
    </style>
</head>
<body>

    <div id="login-screen">
        <h1 style="color:var(--blue); margin-bottom: 30px; letter-spacing: 2px;">KHOURY AI SNIPER</h1>
        <input type="password" id="pass" placeholder="ENTER ACCESS CODE">
        <button class="btn-login" onclick="login()">ACTIVATE SYSTEM</button>
    </div>

    <div id="main-ui" style="display:none">
        <div style="text-align:center; color:#8b949e; font-size:15px; margin-bottom:10px; font-weight: bold;">
            ANALYSIS: SECOND 00 | ENTRY: NEXT MINUTE | LOGIC: INVERTED (REVERSAL)
        </div>
        
        <div class="dashboard">
            <div class="pair-column">
                <div class="pair-header">EUR / USD</div>
                <div class="countdown" id="count-0">00</div>
                <div class="signal-box" id="box-0">
                    <span class="sig-title" id="text-0">SCANNING</span>
                    <div class="entry-time" id="entry-0" style="display:none">ENTRY @ --:--:00</div>
                    <div class="cooldown-info" id="wait-0" style="display:none">COOLDOWN: 120s</div>
                </div>
            </div>

            <div class="pair-column">
                <div class="pair-header">EUR / GBP</div>
                <div class="countdown" id="count-1">00</div>
                <div class="signal-box" id="box-1">
                    <span class="sig-title" id="text-1">SCANNING</span>
                    <div class="entry-time" id="entry-1" style="display:none">ENTRY @ --:--:00</div>
                    <div class="cooldown-info" id="wait-1" style="display:none">COOLDOWN: 120s</div>
                </div>
            </div>

            <div class="pair-column">
                <div class="pair-header">EUR / JPY</div>
                <div class="countdown" id="count-2">00</div>
                <div class="signal-box" id="box-2">
                    <span class="sig-title" id="text-2">SCANNING</span>
                    <div class="entry-time" id="entry-2" style="display:none">ENTRY @ --:--:00</div>
                    <div class="cooldown-info" id="wait-2" style="display:none">COOLDOWN: 120s</div>
                </div>
            </div>
        </div>
    </div>

    <script>
        const pairs = ["frxEURUSD", "frxEURGBP", "frxEURJPY"];
        let isSleeping = [false, false, false];
        let sleepEnds = [0, 0, 0];
        let displayEntry = ["", "", ""];
        let displaySignal = ["", "", ""];

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
                setInterval(run, 1000);
            } else {
                alert("Incorrect Password!");
            }
        }

        async function trigger(i) {
            try {
                const res = await fetch('/scan', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ asset: pairs[i] })
                });
                const data = await res.json();
                
                if(data.signal !== "NONE") {
                    let now = new Date();
                    let entryDate = new Date(now.getTime() + 60000);
                    entryDate.setSeconds(0);
                    
                    displayEntry[i] = entryDate.toTimeString().split(' ')[0];
                    displaySignal[i] = data.signal;
                    
                    playSound(data.signal === "BUY");
                    isSleeping[i] = true;
                    sleepEnds[i] = Date.now() + 120000;
                }
            } catch(e) { console.error("API Error"); }
        }

        function run() {
            const now = Date.now();
            const s = new Date().getSeconds();
            const cd = (60 - s) % 60;

            for(let i=0; i<3; i++) {
                document.getElementById(`count-${i}`).innerText = cd.toString().padStart(2, '0');
                const box = document.getElementById(`box-${i}`);
                const text = document.getElementById( `text-${i}`);
                const entryLabel = document.getElementById(`entry-${i}`);
                const waitLabel = document.getElementById(`wait-${i}`);

                if(isSleeping[i]) {
                    const rem = Math.round((sleepEnds[i] - now) / 1000);
                    if(rem <= 0) {
                        isSleeping[i] = false;
                        box.className = "signal-box";
                        text.innerText = "SCANNING";
                        entryLabel.style.display = "none";
                        waitLabel.style.display = "none";
                    } else {
                        box.className = displaySignal[i] === "BUY" ? "signal-box buy-active" : "signal-box sell-active";
                        text.innerText = displaySignal[i] === "BUY" ? "CALL 1M" : "PUT 1M";
                        entryLabel.style.display = "block";
                        entryLabel.innerText = "ENTRY @ " + displayEntry[i];
                        waitLabel.style.display = "block";
                        waitLabel.innerText = "COOLDOWN: " + rem + "s";
                    }
                }
                
                // Trigger Scan at Second 00
                if(s === 0 && !isSleeping[i]) trigger(i);
            }
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
        
        # 5-Tick OHLC Logic
        candles = []
        for i in range(0, len(ticks), 5):
            chunk = ticks.iloc[i:i+5]
            if len(chunk) >= 5: candles.append({'close': chunk.iloc[-1]['close']})
        df_5t = pd.DataFrame(candles)
        
        signal = compute_logic(df_5t)
        return jsonify({"signal": signal})
    except:
        return jsonify({"signal": "NONE"})

if __name__ == "__main__":
    # Dynamically fetch port for hosting providers
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
