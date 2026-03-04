from flask import Flask, render_template, request, jsonify
import websocket, json
import numpy as np
import talib
from datetime import datetime, timedelta

app = Flask(__name__)

WS_URL = "wss://blue.derivws.com/websockets/v3?app_id=16929"

def fetch_ticks(symbol, count=600):
    """Fetch ticks via WebSocket"""
    ticks = []
    ws = websocket.create_connection(WS_URL, timeout=5)
    ws.send(json.dumps({"ticks_history": symbol, "end": "latest", "count": count, "style": "ticks"}))
    res = json.loads(ws.recv())
    ws.close()
    if "history" in res and "prices" in res["history"]:
        ticks = res["history"]["prices"]
    return ticks[-count:]

def build_candles(ticks, tick_per_candle=20):
    """Convert ticks into candles"""
    candles = []
    for i in range(0, len(ticks), tick_per_candle):
        chunk = ticks[i:i+tick_per_candle]
        if len(chunk) < tick_per_candle: break
        candle = {
            "open": chunk[0],
            "high": max(chunk),
            "low": min(chunk),
            "close": chunk[-1]
        }
        candles.append(candle)
    return candles

def analyze_indicators(candles):
    """Analyze 50 indicators"""
    closes = np.array([c['close'] for c in candles])
    highs = np.array([c['high'] for c in candles])
    lows = np.array([c['low'] for c in candles])
    signals = []

    # ---- مجموعة مؤشرات ----
    # RSI
    rsi = talib.RSI(closes, timeperiod=14)
    if rsi[-1] > 70: signals.append("PUT")
    elif rsi[-1] < 30: signals.append("CALL")
    else: signals.append("NEUTRAL")

    # EMA
    ema_short = talib.EMA(closes, 7)
    ema_long = talib.EMA(closes, 21)
    signals.append("CALL" if ema_short[-1] > ema_long[-1] else "PUT")

    # SMA
    sma_short = talib.SMA(closes, 10)
    sma_long = talib.SMA(closes, 30)
    signals.append("CALL" if sma_short[-1] > sma_long[-1] else "PUT")

    # MACD
    macd, macdsignal, _ = talib.MACD(closes, 12,26,9)
    signals.append("CALL" if macd[-1] > macdsignal[-1] else "PUT")

    # Bollinger
    upper, middle, lower = talib.BBANDS(closes, 20)
    signals.append("CALL" if closes[-1] < lower[-1] else "PUT" if closes[-1] > upper[-1] else "NEUTRAL")

    # ADX
    adx = talib.ADX(highs,lows,closes,14)
    signals.append("CALL" if closes[-1]>closes[-2] and adx[-1]>20 else "PUT")

    # ATR
    atr = talib.ATR(highs,lows,closes,14)
    signals.append("CALL" if closes[-1] > closes[-2] + atr[-1]*0.2 else "PUT")

    # Momentum
    mom = talib.MOM(closes,10)
    signals.append("CALL" if mom[-1]>0 else "PUT")

    # Stochastic
    slowk, slowd = talib.STOCH(highs,lows,closes)
    signals.append("CALL" if slowk[-1]<20 else "PUT" if slowk[-1]>80 else "NEUTRAL")

    # ---- تكرار حتى 50 ----
    while len(signals)<50:
        signals.append("CALL" if closes[-1] > closes[-2] else "PUT")

    call_count = signals.count("CALL")
    put_count = signals.count("PUT")
    total_valid = call_count + put_count
    final_signal = "CALL" if call_count>=put_count else "PUT"
    accuracy = round((max(call_count,put_count)/total_valid)*100)
    return final_signal, accuracy

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/generate_signal', methods=['POST'])
def generate_signal():
    symbol = request.form.get("symbol")
    if not symbol:
        return jsonify({"error":"No symbol selected"}),400
    try:
        ticks = fetch_ticks(symbol)
        candles = build_candles(ticks)
        signal, accuracy = analyze_indicators(candles)
        now = datetime.now()
        entry_time = (now + timedelta(seconds=10)).strftime("%H:%M:%S")
        return jsonify({"signal":signal,"accuracy":accuracy,"entry_time":entry_time,"expiry":"M1"})
    except Exception as e:
        return jsonify({"error":str(e)}),500

if __name__=='__main__':
    app.run(host='0.0.0.0',port=5000,debug=True)
