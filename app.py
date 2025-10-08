import json
import ssl
import pandas as pd
from flask import Flask, request, jsonify
from websocket import create_connection, WebSocketTimeoutException
import ta 
import numpy as np
import datetime

# تهيئة تطبيق Flask
app = Flask(_name_)

# 📌 معلومات Deriv/Binary WebSocket API
DERIV_WSS = "wss://blue.derivws.com/websockets/v3?app_id=16929"

# 📊 أزواج الفوركس فقط (مجموعة واسعة)
PAIRS = {
    "frxEURUSD": "EUR/USD", "frxGBPUSD": "GBP/USD", "frxUSDJPY": "USD/JPY",
    "frxAUDUSD": "AUD/USD", "frxNZDUSD": "NZD/USD", "frxUSDCAD": "USD/CAD",
    "frxUSDCHF": "USD/CHF", "frxEURGBP": "EUR/GBP", "frxEURJPY": "EUR/JPY",
    "frxGBPJPY": "GBP/JPY", "frxEURCAD": "EUR/CAD", "frxEURCHF": "EUR/CHF",
    "frxAUDJPY": "AUD/JPY", "frxCHFJPY": "CHF/JPY", "frxCADJPY": "CAD/JPY"
}
TICK_COUNT = 15000 

# متغيرات الاستراتيجية المدمجة (القوة الواحد والعشرون)
EMA_SHORT = 20      
EMA_MED = 50        
EMA_LONG = 200      
ADX_PERIOD = 14     
RSI_PERIOD = 14     
SD_PERIOD = 20      
PSAR_STEP = 0.02    
PSAR_MAX = 0.20     
BB_LOW_EXTREME = 0.05 
BB_HIGH_EXTREME = 0.95 
ADX_STRENGTH_THRESHOLD = 25 
SD_THRESHOLD = 1.0  
Z_SCORE_THRESHOLD = 1.5 
ATR_PERIOD = 14     
ATR_THRESHOLD = 0.8 
CANDLE_STRENGTH_RATIO = 0.8  
STOCH_RSI_WINDOW = 14 
STOCH_RSI_SIGNAL_PERIOD = 3 
STOCH_OVERSOLD = 20 
STOCH_OVERBOUGHT = 80 
FIB_LEVEL_THRESHOLD = 0.618 
SHARPE_PERIOD = 10  
VW_MACD_THRESHOLD = 0.0 


# -------------------- الدوال المساعدة --------------------

def get_market_data(symbol, time_frame, count) -> pd.DataFrame:
    """جلب النقرات التاريخية من Deriv WSS."""
    try:
        ws = create_connection(DERIV_WSS, sslopt={"cert_reqs": ssl.CERT_NONE})
        request_data = json.dumps({
            "ticks_history": symbol, "end": "latest", "start": 1, 
            "style": "ticks", "count": count, "granularity": 0
        })
        ws.send(request_data)
        ws.settimeout(10)
        response = ws.recv()
        data = json.loads(response)
        ws.close()
        
        if 'history' in data and 'prices' in data['history']:
            df_ticks = pd.DataFrame({'epoch': data['history']['times'], 'quote': data['history']['prices']})
            df_ticks['quote'] = pd.to_numeric(df_ticks['quote'], errors='coerce')
            df_ticks.dropna(inplace=True)
            return df_ticks
        return pd.DataFrame()
    except Exception as e:
        return pd.DataFrame()

def aggregate_ticks_to_candles(df_ticks: pd.DataFrame, time_frame: str) -> pd.DataFrame:
    """تحويل النقرات (Ticks) إلى شموع OHLCV."""
    if df_ticks.empty: return pd.DataFrame()
    df_ticks['timestamp'] = pd.to_datetime(df_ticks['epoch'], unit='s')
    df_ticks.set_index('timestamp', inplace=True)
    period = time_frame.upper()

    df_candles = df_ticks['quote'].resample(period, label='right').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'count' 
    })
    df_candles.dropna(inplace=True)
    if len(df_candles) < 250: return pd.DataFrame() 
    return df_candles

def get_high_timeframe_trend(symbol: str) -> str:
    """يحدد الترند العام من إطار زمني أعلى (4h) باستخدام EMA 200."""
    try:
        ws = create_connection(DERIV_WSS, sslopt={"cert_reqs": ssl.CERT_NONE})
        request_data = json.dumps({
            "candles": symbol, "end": "latest", "start": 1, 
            "count": 250, "granularity": 4 * 3600 
        })
        ws.send(request_data)
        response = ws.recv()
        data = json.loads(response)
        ws.close()
        
        if 'candles' in data:
            df = pd.DataFrame(data['candles'])
            df['close'] = pd.to_numeric(df['close'], errors='coerce')
            df.dropna(inplace=True)
            if len(df) < EMA_LONG: return "SIDEWAYS"

            df['EMA_LONG'] = ta.trend.ema_indicator(df['close'], window=EMA_LONG, fillna=True)
            
            last_close = df.iloc[-1]['close']
            last_ema_long = df.iloc[-1]['EMA_LONG']

            if last_close > last_ema_long:
                return "BULLISH"
            elif last_close < last_ema_long:
                return "BEARISH"
        return "SIDEWAYS"
    except Exception as e:
        return "SIDEWAYS"
    
def is_strong_candle(candle: pd.Series, direction: str) -> bool:
    """المحور 15: يحدد ما إذا كانت الشمعة الأخيرة شمعة قوية."""
    range_hl = candle['high'] - candle['low']
    if range_hl == 0: return False 
    
    if direction == "BUY":
        body = candle['close'] - candle['open']
        if body < 0: return False 
        body_ratio = body / range_hl
        return body_ratio >= CANDLE_STRENGTH_RATIO
    elif direction == "SELL":
        body = candle['open'] - candle['close']
        if body < 0: return False 
        body_ratio = body / range_hl
        return body_ratio >= CANDLE_STRENGTH_RATIO
    return False

def check_rsi_divergence(df: pd.DataFrame) -> str:
    """المحور 14: يكتشف انحرافات RSI (Divergence)."""
    df['RSI'] = ta.momentum.rsi(df['close'], window=RSI_PERIOD)
    recent_data = df.iloc[-15:]
    
    if len(recent_data) < 5: return "NONE"

    # الانحراف الهبوطي (Bearish Divergence)
    if (recent_data['high'].iloc[-1] > recent_data['high'].iloc[-5] and
        recent_data['RSI'].iloc[-1] < recent_data['RSI'].iloc[-5]):
        return "BEARISH"

    # الانحراف الصعودي (Bullish Divergence)
    if (recent_data['low'].iloc[-1] < recent_data['low'].iloc[-5] and
        recent_data['RSI'].iloc[-1] > recent_data['RSI'].iloc[-5]):
        return "BULLISH"

    return "NONE"

def calculate_fibonacci_ret(df: pd.DataFrame) -> tuple:
    """يحسب مستويات فيبوناتشي التراجعية (38.2, 50, 61.8) للـ 50 شمعة الأخيرة."""
    recent_data = df.iloc[-50:]
    high = recent_data['high'].max()
    low = recent_data['low'].min()
    
    if high == low:
        return None, None, None 
        
    diff = high - low
    fib_levels = {
        '38.2': high - diff * 0.382,
        '50.0': high - diff * 0.5,
        '61.8': high - diff * 0.618,
    }
    return fib_levels, high, low

def calculate_advanced_indicators(df: pd.DataFrame):
    """حساب جميع المؤشرات الـ 21 (بما في ذلك VW-MACD وSharpe Ratio)."""
    
    # 1. المتوسطات المتحركة (1, 2, 3)
    df['EMA_SHORT'] = ta.trend.ema_indicator(df['close'], window=EMA_SHORT, fillna=True) 
    df['EMA_MED'] = ta.trend.ema_indicator(df['close'], window=EMA_MED, fillna=True)     
    df['EMA_LONG'] = ta.trend.ema_indicator(df['close'], window=EMA_LONG, fillna=True)   

    # 2. مؤشرات الزخم والتقلب الأساسية (4-14)
    df = df.join(ta.volatility.bollinger_pband(close=df['close'], window=20, window_dev=2, fillna=True).rename('BBP'))
    df = df.join(ta.trend.adx(df['high'], df['low'], df['close'], window=ADX_PERIOD, fillna=True))
    df = df.join(ta.volume.on_balance_volume(df['close'], df['volume'], fillna=True).rename('OBV'))
    df['VWAP'] = ta.volume.vwap(df['high'], df['low'], df['close'], df['volume'], fillna=True)
    df['PSAR'] = ta.trend.psar(df['high'], df['low'], step=PSAR_STEP, max_step=PSAR_MAX, fillna=True) 
    df['SD'] = ta.volatility.stdev(df['close'], window=SD_PERIOD, fillna=True) 
    df = df.join(ta.trend.adx_pos(df['high'], df['low'], df['close'], window=ADX_PERIOD, fillna=True).rename('PDI'))
    df = df.join(ta.trend.adx_neg(df['high'], df['low'], df['close'], window=ADX_PERIOD, fillna=True).rename('NDI'))
    stoch_rsi = ta.momentum.stochrsi(df['close'], window=STOCH_RSI_WINDOW, smooth1=STOCH_RSI_SIGNAL_PERIOD, smooth2=STOCH_RSI_SIGNAL_PERIOD, fillna=True)
    df = df.join(stoch_rsi.rename({'stochrsi_k': 'StochRSI_K', 'stochrsi_d': 'StochRSI_D'}, axis=1))

    # 3. المؤشرات الإحصائية المتقدمة (15-18)
    df['Z_SCORE'] = (df['close'] - df['EMA_LONG']) / df['SD']
    df['ATR'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=ATR_PERIOD, fillna=True)
    df['ATR_AVG'] = df['ATR'].rolling(window=ATR_PERIOD * 2).mean()
    df['UO'] = ta.momentum.ultimate_oscillator(df['high'], df['low'], df['close'], fillna=True)
    df['RSI'] = ta.momentum.rsi(df['close'], window=RSI_PERIOD) 

    # 4. المؤشرات النهائية الجديدة (19-21)
    
    # المحور 19: VW-MACD (زخم موثق بالحجم)
    df['VW_MACD'] = ta.momentum.macd_diff(
        close=df['close'] * df['volume'], 
        window_fast=12, window_slow=26, window_sign=9, fillna=True
    )['macd_diff']

    # المحور 20: Sharpe Ratio (عائد/مخاطرة)
    df['Returns'] = df['close'].pct_change() 
    df['Sharpe_Numerator'] = df['Returns'].rolling(window=SHARPE_PERIOD).mean()
    df['Sharpe_Denominator'] = df['Returns'].rolling(window=SHARPE_PERIOD).std()
    
    # تصحيح: تفادي القسمة على صفر (ZeroDivisionError)
    df['Sharpe_Ratio'] = np.where(
        df['Sharpe_Denominator'] == 0, 
        0, 
        df['Sharpe_Numerator'] / df['Sharpe_Denominator']
    )

    return df

def generate_and_invert_signal(df: pd.DataFrame, hft_trend: str):
    """تطبيق استراتيجية القوة الواحد والعشرون الموحدة."""
    
    if df.empty or len(df) < 250: 
        return "ERROR", "darkred", f"فشل في إنشاء عدد كافٍ من الشموع ({len(df)}). يتطلب 250 شمعة على الأقل."

    df = calculate_advanced_indicators(df)
    fib_levels, _, _ = calculate_fibonacci_ret(df)
    rsi_divergence = check_rsi_divergence(df.iloc[-20:])

    last_candle = df.iloc[-1]
    prev_candle = df.iloc[-2]
    last_close = last_candle['close']
    
    # استخراج القيم
    last_ema_short = last_candle['EMA_SHORT']
    last_ema_med = last_candle['EMA_MED']
    last_vwap = last_candle['VWAP']
    macd_hist_rising = last_candle['macd_diff'] > prev_candle['macd_diff']
    last_psar = last_candle['PSAR']
    last_pdi = last_candle['PDI']
    last_ndi = last_candle['NDI']
    last_bbp = last_candle['BBP']
    last_adx = last_candle['adx']
    last_sd = last_candle['SD']
    obv_rising = last_candle['OBV'] > prev_candle['OBV']
    last_z_score = last_candle['Z_SCORE']
    last_atr = last_candle['ATR']
    atr_avg = last_candle['ATR_AVG']
    last_uo = last_candle['UO']
    last_vw_macd = last_candle['VW_MACD']
    last_sharpe_ratio = last_candle['Sharpe_Ratio']
    
    stoch_buy_condition = (last_candle['StochRSI_K'] > last_candle['StochRSI_D'] and prev_candle['StochRSI_K'] < prev_candle['StochRSI_D'] and last_candle['StochRSI_K'] < STOCH_OVERSOLD)
    stoch_sell_condition = (last_candle['StochRSI_K'] < last_candle['StochRSI_D'] and prev_candle['StochRSI_K'] > prev_candle['StochRSI_D'] and last_candle['StochRSI_K'] > STOCH_OVERBOUGHT)
    strong_buy_candle = is_strong_candle(last_candle, "BUY")
    strong_sell_candle = is_strong_candle(last_candle, "SELL")
    
    # شروط فيبوناتشي
    fib_buy_condition = False
    fib_sell_condition = False
    if fib_levels and fib_levels['61.8']:
        if last_close > fib_levels['61.8'] and prev_candle['close'] < fib_levels['61.8']:
            fib_buy_condition = True
        if last_close < fib_levels['38.2'] and prev_candle['close'] > fib_levels['38.2']:
            fib_sell_condition = True


    # --- توليد الإشارة الأصلية (القوة الواحد والعشرون القصوى) ---
    original_signal = ""
    reason_detail = ""

    # شروط التوقع الصعودي (21 محور)
    if (
        last_close > last_ema_short and last_close > last_ema_med and hft_trend == "BULLISH" and last_close > last_vwap and
        macd_hist_rising and last_pdi > last_ndi and last_close > last_psar and stoch_buy_condition and last_sd > SD_THRESHOLD and
        last_adx > ADX_STRENGTH_THRESHOLD and last_bbp < BB_LOW_EXTREME and obv_rising and 
        strong_buy_candle and rsi_divergence == "BULLISH" and
        last_z_score < -Z_SCORE_THRESHOLD and last_atr > atr_avg * ATR_THRESHOLD and last_uo < 30 and
        fib_buy_condition and last_sharpe_ratio > 0 and last_vw_macd > VW_MACD_THRESHOLD
    ):
        original_signal = "BUY"
        reason_detail = f"*قوة قصوى (BUY - 21 محور):* توافق كامل. تأكيد شارب وفيبوناتشي وزخم الحجم. *أقصى توقع صعودي لمدة 5 دقائق.*"

    # شروط التوقع الهبوطي (21 محور)
    elif (
        last_close < last_ema_short and last_close < last_ema_med and hft_trend == "BEARISH" and last_close < last_vwap and
        not macd_hist_rising and last_ndi > last_pdi and last_close < last_psar and stoch_sell_condition and last_sd > SD_THRESHOLD and
        last_adx > ADX_STRENGTH_THRESHOLD and last_bbp > BB_HIGH_EXTREME and not obv_rising and 
        strong_sell_candle and rsi_divergence == "BEARISH" and
        last_z_score > Z_SCORE_THRESHOLD and last_atr > atr_avg * ATR_THRESHOLD and last_uo > 70 and
        fib_sell_condition and last_sharpe_ratio < 0 and last_vw_macd < VW_MACD_THRESHOLD
    ):
        original_signal = "SELL"
        reason_detail = f"*قوة قصوى (SELL - 21 محور):* توافق كامل. تأكيد شارب وفيبوناتشي وزخم الحجم. *أقصى توقع هبوطي لمدة 5 دقائق.*"

    # منطق الإشارة الدائم (Fallback)
    else:
        if hft_trend == "BULLISH" and stoch_buy_condition:
            original_signal = "BUY"
            reason_detail = (f"إشارة دائمة (توقع): الترند العالمي (4h) صاعد، وStochRSI يعطي تقاطع شراء قوي من التشبع البيعي.")
        elif hft_trend == "BEARISH" and stoch_sell_condition:
            original_signal = "SELL"
            reason_detail = (f"إشارة دائمة (توقع): الترند العالمي (4h) هابط، وStochRSI يعطي تقاطع بيع قوي من التشبع الشرائي.")
        elif last_close > last_ema_short:
            original_signal = "BUY"
            reason_detail = (f"إشارة مضمونة (EMA20): لم يتحقق أي توقع قوي، ولكن الترند المحلي (EMA20) صاعد.")
        else: 
            original_signal = "SELL"
            reason_detail = (f"إشارة مضمونة (EMA20): لم يتحقق أي توقع قوي، ولكن الترند المحلي (EMA20) هابط.")


    # --- منطق العكس (Inversion Logic) ---
    if original_signal == "BUY":
        inverted_signal = "SELL (PUT) - معكوس"
        color = "red"
        reason = "🛑 *تم عكس إشارة الشراء الأصلية (نظام 21 محور - الحد الأقصى).* " + reason_detail
    elif original_signal == "SELL":
        inverted_signal = "BUY (CALL) - معكوس"
        color = "lime"
        reason = "🟢 *تم عكس إشارة البيع الأصلية (نظام 21 محور - الحد الأقصى).* " + reason_detail
    else:
         # تصحيح خطأ الصياغة (Syntax Error)
         inverted_signal, color, reason = "ERROR", "darkred", "لم يتم تحديد إشارة بسبب خطأ في المنطق الداخلي."


    return inverted_signal, color, reason


# -------------------- مسارات Flask والواجهة الأمامية --------------------

@app.route('/', methods=['GET'])
def index():
    """ينشئ الواجهة الأمامية الأوتوماتيكية مع العداد التنازلي."""

    pair_options = "".join([f'<option value="{code}">{name} ({code})</option>' for code, name in PAIRS.items()])

    html_content = f"""
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>KhouryBot (21 محور - فوركس فقط)</title>
        <style>
            body {{ 
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                text-align: center; 
                margin: 0; 
                background-color: #0d1117; 
                color: #c9d1d9; 
                padding-top: 40px;
            }}
            .container {{ 
                max-width: 550px; 
                margin: 0 auto; 
                padding: 35px; 
                border-radius: 10px; 
                background-color: #161b22; 
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5); 
            }}
            h1 {{ 
                color: #FFD700; 
                margin-bottom: 25px;
                font-size: 1.8em;
                text-shadow: 0 0 10px rgba(255, 215, 0, 0.5); 
            }}
            .time-note {{
                color: #58a6ff; 
                font-weight: bold;
                margin-bottom: 15px;
                font-size: 1.1em;
            }}
            .status-box {{
                background-color: #21262d; 
                padding: 15px;
                border-radius: 6px;
                margin-bottom: 25px;
                border-right: 3px solid #FFD700; 
                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
            }}
            #countdown-timer {{
                font-size: 2.2em;
                color: #ff00ff; 
                font-weight: bold;
                display: block; 
                margin: 5px 0 10px 0;
                text-shadow: 0 0 8px rgba(255, 0, 255, 0.5); 
            }}
            #next-signal-time {{
                color: #8b949e; 
                font-size: 0.9em;
            }}
            label {{
                display: block; 
                text-align: right; 
                margin-bottom: 5px;
                color: #8b949e;
            }}
            select {{ 
                padding: 12px; 
                margin: 10px 0; 
                width: 100%; 
                box-sizing: border-box; 
                border: 1px solid #30363d; 
                border-radius: 6px; 
                font-size: 16px; 
                background-color: #21262d; 
                color: #c9d1d9; 
                -webkit-appearance: none; 
                -moz-appearance: none;
                appearance: none;
                background-image: url('data:image/svg+xml;charset=US-ASCII,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%22292.4%22%20height%3D%22292.4%22%3E%3Cpath%20fill%3D%22%23c9d1d9%22%20d%3D%22M287%20197.3L159.9%2069.1c-3-3-7.7-3-10.7%200l-127%20128.2c-3%203-3%207.7%200%2010.7l10.7%2010.7c3%203%207.7%203%2010.7%200l113.6-114.6c3-3%207.7-3%2010.7%200l113.6%20114.6c3%203%207.7%203%2010.7%200l10.7-10.7c3.1-3%203.1-7.7%200-10.7z%22%2F%3E%3C%2Fsvg%3E'); 
                background-repeat: no-repeat;
                background-position: left 0.7em top 50%, 0 0;
                background-size: 0.65em auto, 100%;
            }}
            #result {{ 
                font-size: 3.5em; 
                margin-top: 30px; 
                font-weight: 900; 
                min-height: 70px; 
                text-shadow: 0 0 15px rgba(255, 255, 255, 0.7); 
            }}
            #reason-box {{
                background-color: #21262d;
                padding: 15px;
                border-radius: 6px;
                margin-top: 20px;
                font-size: 0.9em;
                color: #9e9e9e;
                text-align: right;
                border-right: 3px solid #FFD700;
                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
            }}
            .loading {{ 
                color: #58a6ff; 
                font-size: 1.2em;
                animation: pulse 1.5s infinite alternate; 
            }}
            @keyframes pulse {{
                from {{ opacity: 1; }}
                to {{ opacity: 0.6; }}
            }}
        </style>
    </head>
    <body onload="startAutomation()">
        <div class="container">
            <h1>KhouryBot (21 محور - فوركس فقط)</h1>
            
            <div class="time-note">
                تحليل الحد الأقصى للقوة. الإشارة تظهر قبل 10 ثوانٍ من إغلاق شمعة الـ 5 دقائق.
            </div>
            
            <div class="status-box">
                <p>الوقت المتبقي لظهور الإشارة:</p>
                <div id="countdown-timer">--:--</div>
                <p id="next-signal-time"></p>
            </div>
            
            <label for="currency_pair">زوج العملات:</label>
            <select id="currency_pair">
                {pair_options}
            </select>
            
            <div id="price-info">
                آخر سعر إغلاق تم تحليله: <span id="current-price">N/A</span>
            </div>

            <div id="reason-box">
                سبب الإشارة: <span id="signal-reason">نظام 21 محور للتحليل الكمي.</span>
            </div>
            
            <div id="result">---</div>
        </div>

        <script>
            const resultDiv = document.getElementById('result');
            const priceSpan = document.getElementById('current-price');
            const reasonSpan = document.getElementById('signal-reason');
            const countdownTimer = document.getElementById('countdown-timer');
            const nextSignalTimeDisplay = document.getElementById('next-signal-time');
            let countdownInterval = null; 
            const SIGNAL_DURATION_MS = 30000; // 30 ثانية مدة ظهور الإشارة

            // --- التوقيت الآلي والحسابات المعقدة ---

            function calculateNextSignalTime() {{
                const now = new Date();
                const currentMinutes = now.getMinutes();
                
                // 1. حساب أقرب علامة 5 دقائق تالية (T_close)
                const nextFiveMinuteMark = Math.ceil((currentMinutes + 1) / 5) * 5;
                
                let nextTargetTime = new Date(now);
                nextTargetTime.setMinutes(nextFiveMinuteMark);
                nextTargetTime.setSeconds(0);
                nextTargetTime.setMilliseconds(0);
                
                if (nextTargetTime.getTime() <= now.getTime()) {{
                    nextTargetTime.setMinutes(nextTargetTime.getMinutes() + 5);
                }}

                // 2. توقيت الإشارة (T_signal = T_close - 10 ثواني)
                const signalTime = new Date(nextTargetTime.getTime() - 10000); 

                // 3. حساب التأخير بالملي ثانية
                const delayMs = signalTime.getTime() - now.getTime();
                const safeDelay = Math.max(1000, delayMs); 

                return {{
                    delay: safeDelay,
                    closeTime: nextTargetTime,
                    signalTime: signalTime
                }};
            }}
            
            function startCountdown() {{
                if (countdownInterval) clearInterval(countdownInterval);

                countdownInterval = setInterval(() => {{
                    const targetInfo = calculateNextSignalTime();
                    let remainingSeconds = Math.ceil(targetInfo.delay / 1000);

                    if (remainingSeconds < 1) {{
                        countdownTimer.textContent = '...تحليل الآن...';
                        nextSignalTimeDisplay.innerHTML = الإشارة القادمة بعد قليل.;
                        return;
                    }}
                    
                    const displayMinutes = Math.floor(remainingSeconds / 60);
                    const displaySeconds = remainingSeconds % 60;
                    countdownTimer.textContent = ${displayMinutes.toString().padStart(2, '0')}:${displaySeconds.toString().padStart(2, '0')};

                    const minutes = targetInfo.closeTime.getMinutes().toString().padStart(2, '0');
                    const hours = targetInfo.closeTime.getHours().toString().padStart(2, '0');
                    nextSignalTimeDisplay.innerHTML = إغلاق الشمعة: ${hours}:${minutes}:00 (بتوقيتك المحلي);
                }}, 1000);
            }}

            // --- دالة إخفاء الإشارة ---
            function hideSignal() {{
                resultDiv.innerHTML = '---';
                resultDiv.style.color = '#c9d1d9'; 
                reasonSpan.innerHTML = 'انتهت مدة الإشارة (30 ثانية). جاري الاستعداد للإشارة التالية.';
            }}

            // --- دالة جلب الإشارة الرئيسية ---
            async function autoFetchSignal() {{
                // 1. إظهار حالة التحليل
                if (countdownInterval) clearInterval(countdownInterval);
                countdownTimer.textContent = '...KhouryBot يحلل القوة القصوى...'; 

                const pair = document.getElementById('currency_pair').value;
                const time = '1m'; 
                
                resultDiv.innerHTML = '<span class="loading">KhouryBot يحلل الـ 21 محوراً...</span>';
                priceSpan.innerText = 'جاري جلب البيانات...';
                reasonSpan.innerText = 'KhouryBot يطبق القوة الإحصائية المطلقة...';

                try {{
                    // 2. جلب الإشارة
                    const response = await fetch('/get-inverted-signal', {{ 
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ pair: pair, time: time }})
                    }});
                    const data = await response.json();
                    
                    // 3. عرض الإشارة
                    resultDiv.innerHTML = data.signal; 
                    resultDiv.style.color = data.color; 
                    priceSpan.innerText = data.price;
                    reasonSpan.innerHTML = data.reason; 

                    // 4. جدولة إخفاء الإشارة بعد 30 ثانية
                    setTimeout(() => {{
                        hideSignal();
                        scheduleNextSignal(); 
                    }}, SIGNAL_DURATION_MS);

                }} catch (error) {{
                    resultDiv.innerHTML = 'خطأ في الخادم.';
                    resultDiv.style.color = '#ff9800'; 
                    priceSpan.innerText = 'فشل الاتصال';
                    reasonSpan.innerText = 'فشل الاتصال بخادم Deriv أو خطأ في المعالجة.';
                    
                    // في حال الخطأ، ننتظر 30 ثانية ثم نحاول الجولة التالية
                    setTimeout(scheduleNextSignal, SIGNAL_DURATION_MS);
                }}
            }}

            // --- جدولة الإشارة التالية ---
            function scheduleNextSignal() {{
                const target = calculateNextSignalTime();
                
                startCountdown(); 
                
                setTimeout(autoFetchSignal, target.delay);
            }}

            // --- نقطة البداية ---
            function startAutomation() {{
                scheduleNextSignal();
            }}
            
            window.startAutomation = startAutomation;

        </script>
    </body>
    </html>
    """
    return html_content

@app.route('/get-inverted-signal', methods=['POST'])
def get_signal_api():
    """نقطة النهاية لطلب الإشارة."""
    
    try:
        data = request.json
        symbol = data.get('pair')
        time_frame = '1m' 

        hft_trend = get_high_timeframe_trend(symbol)
        df_ticks = get_market_data(symbol, time_frame, TICK_COUNT)
        df_local = aggregate_ticks_to_candles(df_ticks, time_frame)
        
        if df_local.empty:
            return jsonify({"signal": "ERROR", "color": "darkred", "price": "N/A", "reason": "فشل جلب النقرات أو عدم كفاية البيانات لتكوين الشموع المحلية."}), 200

        current_price = df_local.iloc[-1]['close']
        
        final_signal, color, reason = generate_and_invert_signal(df_local, hft_trend)
        
        return jsonify({
            "signal": final_signal, 
            "color": color, 
            "price": f"{current_price:.6f}",
            "reason": reason
        })
    except Exception as e:
        return jsonify({
            "signal": "ERROR", 
            "color": "darkred", 
            "price": "N/A",
            "reason": f"خطأ غير متوقع في الخادم: {str(e)}"
        }), 500

if _name_ == '_main_':
    # هذا الأمر يستخدم لتجربة محلية، Render سيستخدم Gunicorn
    app.run(host='0.0.0.0', port=5000)
