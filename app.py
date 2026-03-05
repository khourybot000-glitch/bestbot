import os, json, websocket, datetime
import pandas as pd
import numpy as np
from flask import Flask, render_template_string, jsonify, request

app = Flask(__name__)

# --- الإعدادات الفنية ---
PASSWORD = "KHOURYBOT"
DERIV_WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

def compute_logic(df):
    """تحليل 5 تيك للشمعة واختراق آخر 15 تيك"""
    if len(df) < 60: return "NONE"
    c = df['close']
    delta = c.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    ema50 = c.ewm(span=50, adjust=False).mean()
    
    curr_rsi, prev_rsi, older_rsi = rsi.iloc[-1], rsi.iloc[-2], rsi.iloc[-3]
    curr_price, curr_ema = c.iloc[-1], ema50.iloc[-1]
    
    # شرط الصعود: اختراق الـ 50 للأعلى في آخر 15 تيك + السعر فوق EMA50
    if (older_rsi <= 50 or prev_rsi <= 50) and curr_rsi > 50 and curr_price > curr_ema:
        return "BUY"
    # شرط الهبوط: اختراق الـ 50 للأسفل في آخر 15 تيك + السعر تحت EMA50
    if (older_rsi >= 50 or prev_rsi >= 50) and curr_rsi < 50 and curr_price < curr_ema:
        return "SELL"
    return "NONE"

def get_ohlc(prices, size):
    candles = []
    for i in range(0, len(prices), size):
        chunk = prices.iloc[i:i+size]
        if len(chunk) >= size:
            candles.append({'close': chunk.iloc[-1]['close']})
    return pd.DataFrame(candles)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <title>KHOURY TRIPLE PANEL</title>
    <style>
        :root { --bg: #06090f; --card: #0d1117; --blue: #58a6ff; --green: #238636; --red: #da3633; --gold: #d29922; }
        body { background: var(--bg); color: white; font-family: 'Segoe UI', Tahoma, sans-serif; margin: 0; padding: 20px; }
        .dashboard { display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; max-width: 1100px; margin: 40px auto; }
        
        .pair-column { background: var(--card); border: 1px solid #30363d; border-radius: 15px; padding: 15px; text-align: center; }
        .pair-header { font-size: 20px; font-weight: bold; color: var(--blue); border-bottom: 2px solid #21262d; padding-bottom: 10px; margin-bottom: 20px; }
        
        .signal-box { 
            height: 180px; width: 100%; background: #161b22; border-radius: 12px; 
            display: flex; flex-direction: column; justify-content: center; align-items: center;
            transition: 0.4s; border: 2px solid transparent; margin-bottom: 15px;
        }
        
        .buy-active { background: var(--green) !important; box-shadow: 0 0 30px var(--green); border-color: #fff; }
        .sell-active { background: var(--red) !important; box-shadow: 0 0 30px var(--red); border-color: #fff; }
        .sleep-active { background: var(--gold) !important; opacity: 0.6; }

        .sig-title { font-size: 24px; font-weight: 900; }
        .sig-timer { font-size: 14px; margin-top: 5px; opacity: 0.8; }
        .countdown { font-size: 35px; font-weight: bold; color: #8b949e; margin-bottom: 10px; }
        
        #login-screen { position:fixed; inset:0; background:var(--bg); z-index:1000; display:flex; flex-direction:column; justify-content:center; align-items:center; }
        input { padding: 12px; border-radius: 8px; border: 1px solid #30363d; background: #0d1117; color: white; text-align: center; width: 200px; }
    </style>
</head>
<body>

    <div id="login-screen">
        <h2 style="color:var(--blue)">KHOURY TRIPLE SNIPER</h2>
        <input type="password" id="pass" placeholder="كلمة المرور">
        <button style="margin-top:15px; padding:10px 30px; cursor:pointer;" onclick="login()">دخول</button>
    </div>

    <div id="main-ui" style="display:none">
        <div style="text-align:center; color:#8b949e;">فريم 5 تيك | انتظار 2 دقيقة | فحص عند الثاية :50</div>
        
        <div class="dashboard">
            <div class="pair-column">
                <div class="pair-header">EUR / USD</div>
                <div class="countdown" id="count-0">00</div>
                <div class="signal-box" id="box-0">
                    <span class="sig-title" id="text-0">SCANNING</span>
                    <span class="sig-timer" id="timer-0">---</span>
                </div>
                <div style="font-size:10px; color:#444;" id="status-0">جاهز</div>
            </div>

            <div class="pair-column">
                <div class="pair-header">GBP / USD</div>
                <div class="countdown" id="count-1">00</div>
                <div class="signal-box" id="box-1">
                    <span class="sig-title" id="text-1">SCANNING</span>
                    <span class="sig-timer" id="timer-1">---</span>
                </div>
                <div style="font-size:10px; color:#444;" id="status-1">جاهز</div>
            </div>

            <div class="pair-column">
                <div class="pair-header">EUR / JPY</div>
                <div class="countdown" id="count-2">00</div>
                <div class="signal-box" id="box-2">
                    <span class="sig-title" id="text-2">SCANNING</span>
                    <span class="sig-timer" id="timer-2">---</span>
                </div>
                <div style="font-size:10px; color:#444;" id="status-2">جاهز</div>
            </div>
        </div>
    </div>

    <script>
        const pairs = ["frxEURUSD", "frxGBPUSD", "frxEURJPY"];
        let lastSignals = [null, null, null];
        let sleepEnds = [0, 0, 0];
        let isSleeping = [false, false, false];

        function playSound(isBuy) {
            const ctx = new AudioContext();
            const osc = ctx.createOscillator();
            osc.frequency.value = isBuy ? 800 : 400;
            const g = ctx.createGain();
            g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 1);
            osc.connect(g); g.connect(ctx.destination);
            osc.start(); osc.stop(ctx.currentTime + 1);
        }

        function login() {
            if(document.getElementById('pass').value === "KHOURYBOT") {
                document.getElementById('login-screen').style.display = 'none';
                document.getElementById('main-ui').style.display = 'block';
                setInterval(run, 1000);
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
                
                if(data.signal !== "NONE" && data.signal !== lastSignals[i]) {
                    lastSignals[i] = data.signal;
                    playSound(data.signal === "BUY");
                    
                    // تفعيل المربع
                    const box = document.getElementById(`box-${i}`);
                    box.className = data.signal === "BUY" ? "signal-box buy-active" : "signal-box sell-active";
                    document.getElementById(`text-${i}`).innerText = data.signal === "BUY" ? "CALL 1M" : "PUT 1M";
                    
                    // بدء النوم (120 ثانية)
                    isSleeping[i] = true;
                    sleepEnds[i] = Date.now() + 120000;
                }
            } catch(e) {}
        }

        function run() {
            const now = Date.now();
            const s = new Date().getSeconds();
            const commonCountdown = (50 - s < 0 ? 60 + (50 - s) : 50 - s).toString().padStart(2, '0');

            for(let i=0; i<3; i++) {
                document.getElementById(`count-${i}`).innerText = commonCountdown;

                if(isSleeping[i]) {
                    const rem = Math.round((sleepEnds[i] - now) / 1000);
                    if(rem <= 0) {
                        isSleeping[i] = false;
                        document.getElementById(`box-${i}`).className = "signal-box";
                        document.getElementById(`text-${i}`).innerText = "SCANNING";
                        document.getElementById(`timer-${i}`).innerText = "---";
                    } else {
                        document.getElementById(`box-${i}`).className = "signal-box sleep-active";
                        document.getElementById(`text-${i}`).innerText = "SLEEPING";
                        document.getElementById(`timer-${i}`).innerText = rem + "s left";
                    }
                }

                if(s === 50 && !isSleeping[i]) trigger(i);
                if(s === 0 && !isSleeping[i]) {
                    document.getElementById(`box-${i}`).className = "signal-box";
                    document.getElementById(`text-${i}`).innerText = "SCANNING";
                }
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
        df_5t = get_ohlc(ticks, 5)
        signal = compute_logic(df_5t)
        return jsonify({"signal": signal})
    except: return jsonify({"signal": "NONE"})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
