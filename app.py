import os
import json
import time
import pandas as pd
import numpy as np
import ssl
from datetime import datetime, timedelta
from websocket import create_connection, WebSocketTimeoutException
from flask import Flask, request, jsonify, render_template_string

# =======================================================
# الإعدادات والثوابت
# =======================================================

app = Flask(__name__)

# 📌 معلومات Deriv/Binary WebSocket API
DERIV_WSS = "wss://blue.derivws.com/websockets/v3?app_id=16929"
MAX_RETRIES = 3 

# 📊 أزواج الفوركس فقط (مجموعة واسعة)
PAIRS = {
    "frxEURUSD": "EUR/USD", "frxGBPUSD": "GBP/USD", "frxUSDJPY": "USD/JPY",
    "frxAUDUSD": "AUD/USD", "frxNZDUSD": "NZD/USD", "frxUSDCAD": "USD/CAD",
    "frxUSDCHF": "USD/CHF", "frxEURGBP": "EUR/GBP", "frxEURJPY": "EUR/JPY",
    "frxGBPJPY": "GBR/JPY", "frxEURCAD": "EUR/CAD", "frxEURCHF": "EUR/CHF",
    "frxAUDJPY": "AUD/JPY", "frxCHFJPY": "CHF/JPY", "frxCADJPY": "CAD/JPY"
}

# 🟢 إعدادات الشموع المعتمدة على النقرات
TICKS_PER_CANDLE = 30 
TICK_COUNT = 5000 # عدد النقرات الإجمالي المطلوب (آمن)

# متغيرات الاستراتيجية المدمجة (القوة الواحد والعشرون + 2 دعم ومقاومة = 23 محور)
EMA_SHORT = 10
EMA_MED = 30
EMA_LONG = 50
ADX_PERIOD = 14
RSI_PERIOD = 14
SD_PERIOD = 20 
BB_WINDOW = 20
BB_DEV = 2.0
ADX_STRENGTH_THRESHOLD = 25
Z_SCORE_THRESHOLD_STRICT = 2.0 
ATR_PERIOD = 14
CANDLE_STRENGTH_RATIO = 0.8
STOCH_RSI_WINDOW = 14
STOCH_RSI_SIGNAL_PERIOD = 3
STOCH_OVERSOLD_STRICT = 10
STOCH_OVERBOUGHT_STRICT = 90
FIB_LEVEL_THRESHOLD = 0.618
SHARPE_PERIOD = 10
VW_MACD_FAST = 12
VW_MACD_SLOW = 26
VW_MACD_SIGNAL = 9
VW_MACD_THRESHOLD = 0.0
REQUIRED_CANDLES = 120 
SNR_WINDOW = 50 # الشموع المستخدمة لتحديد الدعم والمقاومة

# =======================================================
# دوال المؤشرات المساعدة
# =======================================================

def create_ssl_context():
    """إنشاء سياق SSL موثوق به لاستخدامه في WebSocket."""
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context

def calculate_ema(series, window):
    """حساب المتوسط المتحرك الأسي (EMA)."""
    return series.ewm(span=window, adjust=False).mean()

def calculate_rsi(series, window=14):
    """حساب مؤشر القوة النسبية (RSI)."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(com=window - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=window - 1, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(0)

def calculate_atr(df, window=14):
    """حساب متوسط المدى الحقيقي (ATR)."""
    df['H-L'] = df['high'] - df['low']
    df['H-PC'] = np.abs(df['high'] - df['close'].shift(1))
    df['L-PC'] = np.abs(df['low'] - df['close'].shift(1))
    tr = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
    atr = tr.ewm(span=window, adjust=False).mean()
    return atr

# دالة لتحديد الدعم والمقاومة (نقاط انعكاس)
def find_snr_levels(df: pd.DataFrame, window: int) -> tuple:
    """
    تحدد مستويات الدعم والمقاومة القريبة بناءً على أعلى وأدنى مستويات الشموع.
    """
    recent_data = df.iloc[-window:]
    if recent_data.empty:
        return None, None
    
    # تحديد المستويات الرئيسية (أعلى وأدنى الشموع)
    resistance = recent_data['high'].max()
    support = recent_data['low'].min()
    
    # تحديد مستويات فرعية (الإغلاقات الرئيسية)
    resistance_close = recent_data['close'].nlargest(3).mean()
    support_close = recent_data['close'].nsmallest(3).mean()

    # استخدام المتوسط بين أعلى/أدنى الشموع وأعلى/أدنى الإغلاقات 
    # لتقليل الاعتماد على مجرد "ظلال" الشموع.
    final_resistance = max(resistance, resistance_close) 
    final_support = min(support, support_close)
    
    # للتأكد من وجود فارق معقول
    if final_resistance == final_support:
        return None, None
    
    return final_support, final_resistance

def check_snr_reaction(close_price: float, support: float, resistance: float, prev_close: float) -> str:
    """
    تحدد إذا كان هناك اختراق (Breakout) أو ارتداد (Rejection) من الدعم/المقاومة.
    التحمل: 0.0001 (نقطة واحدة)
    """
    if support is None or resistance is None:
        return "NONE"

    tolerance = 0.0001 
    
    # **اختراق للأعلى (Breakout UP):** السعر الحالي فوق المقاومة، والسعر السابق كان أسفلها.
    if close_price > resistance + tolerance and prev_close < resistance - tolerance:
        return "BREAKOUT_UP"
    
    # **اختراق للأسفل (Breakout DOWN):** السعر الحالي أسفل الدعم، والسعر السابق كان فوقه.
    if close_price < support - tolerance and prev_close > support + tolerance:
        return "BREAKOUT_DOWN"
        
    # **ارتداد من المقاومة (Rejection DOWN):** السعر الحالي أسفل المقاومة بعد ملامستها.
    if close_price < resistance and resistance - close_price < tolerance * 5 and close_price < prev_close:
        return "REJECTION_DOWN"
        
    # **ارتداد من الدعم (Rejection UP):** السعر الحالي فوق الدعم بعد ملامسته.
    if close_price > support and close_price - support < tolerance * 5 and close_price > prev_close:
        return "REJECTION_UP"

    return "NONE"


# (باقي دوال المؤشرات الأخرى مثل ADX, StochRSI, Bollinger Bands, UO, MACD_diff تبقى كما هي من الكود السابق)

def calculate_adx_ndi_pdi(df, window=14):
    """حساب ADX, NDI, PDI."""
    df['UpMove'] = df['high'] - df['high'].shift(1)
    df['DownMove'] = df['low'].shift(1) - df['low']
    df['+DM'] = np.where((df['UpMove'] > df['DownMove']) & (df['UpMove'] > 0), df['UpMove'], 0)
    df['-DM'] = np.where((df['DownMove'] > df['UpMove']) & (df['DownMove'] > 0), df['DownMove'], 0)
    df['ATR_ADX'] = calculate_atr(df.copy(), window=window)
    plus_dm_sum = df['+DM'].rolling(window=window).sum()
    minus_dm_sum = df['-DM'].rolling(window=window).sum()
    atr_sum = df['ATR_ADX'].rolling(window=window).sum()
    denom = atr_sum.copy()
    denom[denom == 0] = 1e-9 
    df['PDI'] = (plus_dm_sum / denom) * 100
    df['NDI'] = (minus_dm_sum / denom) * 100
    sum_di = df['PDI'] + df['NDI']
    sum_di_safe = sum_di.copy()
    sum_di_safe[sum_di_safe == 0] = 1e-9 
    df['DX'] = np.abs(df['PDI'] - df['NDI']) / sum_di_safe * 100
    df['ADX'] = df['DX'].ewm(span=window, adjust=False).mean()
    df['ADX'] = df['ADX'].fillna(0)
    df['PDI'] = df['PDI'].fillna(0)
    df['NDI'] = df['NDI'].fillna(0)
    return df['ADX'], df['PDI'], df['NDI']

def calculate_stochrsi(series, window=14, smooth_k=3, smooth_d=3):
    """حساب مؤشر Stochastic RSI."""
    rsi = calculate_rsi(series, window=window)
    min_rsi = rsi.rolling(window=window).min()
    max_rsi = rsi.rolling(window=window).max()
    stochrsi_k = (rsi - min_rsi) / ((max_rsi - min_rsi) + 1e-9)
    stochrsi_k = stochrsi_k.fillna(0).clip(0, 1) * 100
    stochrsi_k_smooth = calculate_ema(stochrsi_k, smooth_k)
    stochrsi_d = calculate_ema(stochrsi_k_smooth, smooth_d)
    return stochrsi_k_smooth.fillna(0), stochrsi_d.fillna(0)

def calculate_bollinger_bands(series, window=20, dev=2.0):
    """حساب Bollinger Bands (%B)."""
    sma = series.rolling(window=window).mean()
    std = series.rolling(window=window).std()
    upper = sma + (std * dev)
    lower = sma - (std * dev)
    bbp = (series - lower) / ((upper - lower) + 1e-9)
    return bbp.fillna(0)

def calculate_uo(df, s=7, m=14, l=28):
    """حساب Ultimate Oscillator (UO)."""
    df['TR'] = calculate_atr(df.copy(), window=1) 
    df['BP'] = df['close'] - df[['low', 'close']].min(axis=1).shift(1)
    df['BP'] = df['BP'].clip(lower=0)
    denom7 = df['TR'].rolling(s).sum() + 1e-9
    denom14 = df['TR'].rolling(m).sum() + 1e-9
    denom28 = df['TR'].rolling(l).sum() + 1e-9
    avg7 = (df['BP'].rolling(s).sum() / denom7).fillna(0)
    avg14 = (df['BP'].rolling(m).sum() / denom14).fillna(0)
    avg28 = (df['BP'].rolling(l).sum() / denom28).fillna(0)
    uo = 100 * ((4 * avg7) + (2 * avg14) + avg28) / 7
    return uo.fillna(0)

def calculate_macd_diff(series, fast=12, slow=26, sign=9):
    """حساب MACD Histogram (Diff)."""
    ema_fast = calculate_ema(series, fast)
    ema_slow = calculate_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, sign)
    macd_diff = macd_line - signal_line
    return macd_diff.fillna(0)


def get_market_data(symbol) -> pd.DataFrame:
    """جلب النقرات التاريخية من Deriv WSS."""
    ssl_context = create_ssl_context()
    for attempt in range(MAX_RETRIES):
        ws = None
        try:
            ws = create_connection(DERIV_WSS, ssl_context=ssl_context)
            ws.settimeout(20) 
            request_data = json.dumps({"ticks_history": symbol, "end": "latest", "start": 1, "style": "ticks", "count": TICK_COUNT})
            ws.send(request_data)
            response = ws.recv()
            data = json.loads(response)
            if 'error' in data:
                continue 
            if 'history' in data and 'prices' in data['history']:
                df_ticks = pd.DataFrame({'epoch': data['history']['times'], 'quote': data['history']['prices']})
                df_ticks['quote'] = pd.to_numeric(df_ticks['quote'], errors='coerce')
                df_ticks.dropna(inplace=True)
                return df_ticks
        except WebSocketTimeoutException:
            pass
        except Exception as e:
            pass
        finally:
            if ws:
                try: ws.close() 
                except: pass 
        if attempt < MAX_RETRIES - 1:
            wait_time = 2 ** attempt
            time.sleep(wait_time)
    return pd.DataFrame()

def aggregate_ticks_to_candles(df_ticks: pd.DataFrame, time_frame: str = None) -> pd.DataFrame:
    """تحويل النقرات (Ticks) إلى شموع OHLCV بناءً على عدد النقرات (30 نقرة)."""
    if df_ticks.empty: return pd.DataFrame()
    df_ticks['candle_group'] = np.arange(len(df_ticks)) // TICKS_PER_CANDLE
    df_candles = df_ticks.groupby('candle_group').agg(
        open=('quote', 'first'), high=('quote', 'max'), low=('quote', 'min'),
        close=('quote', 'last'), volume=('quote', 'count'), timestamp=('epoch', 'last') 
    )
    df_candles['timestamp'] = pd.to_datetime(df_candles['timestamp'], unit='s')
    df_candles.set_index('timestamp', inplace=True)
    if len(df_candles) > 0 and df_candles['volume'].iloc[-1] < TICKS_PER_CANDLE:
        df_candles = df_candles.iloc[:-1]
    df_candles.dropna(inplace=True)
    if len(df_candles) < REQUIRED_CANDLES: return pd.DataFrame() 
    return df_candles

def is_strong_candle(candle: pd.Series, direction: str) -> bool:
    """المحور 15: يحدد ما إذا كانت الشمعة الأخيرة شمعة قوية."""
    range_hl = candle['high'] - candle['low']
    if range_hl == 0: return False 
    if direction == "BUY":
        body = candle['close'] - candle['open']
        if body < 0: return False 
        return (body / range_hl) >= CANDLE_STRENGTH_RATIO
    elif direction == "SELL":
        body = candle['open'] - candle['close']
        if body < 0: return False 
        return (body / range_hl) >= CANDLE_STRENGTH_RATIO
    return False

def check_rsi_divergence(df: pd.DataFrame) -> str:
    """المحور 14: يكتشف انحرافات RSI (Divergence)."""
    recent_data = df.iloc[-15:].copy()
    if len(recent_data) < 5: return "NONE"

    if (recent_data['high'].iloc[-1] > recent_data['high'].iloc[-5] and
        recent_data['RSI'].iloc[-1] < recent_data['RSI'].iloc[-5]):
        return "BEARISH"
    if (recent_data['low'].iloc[-1] < recent_data['low'].iloc[-5] and
        recent_data['RSI'].iloc[-1] > recent_data['RSI'].iloc[-5]):
        return "BULLISH"
    return "NONE"

def calculate_fibonacci_ret(df: pd.DataFrame) -> tuple:
    """يحسب مستويات فيبوناتشي التراجعية."""
    recent_data = df.iloc[-50:]
    high = recent_data['high'].max()
    low = recent_data['low'].min()
    if high == low: return None, None, None 
    diff = high - low
    fib_levels = {
        '38.2': high - diff * 0.382,
        '50.0': high - diff * 0.5,
        '61.8': high - diff * 0.618,
    }
    return fib_levels, high, low

def calculate_advanced_indicators(df: pd.DataFrame):
    """حساب جميع المؤشرات الـ 23 (21 أساسي + 2 دعم/مقاومة)."""
    
    # المتوسطات المتحركة (1, 2, 3)
    df['EMA_SHORT'] = calculate_ema(df['close'], window=EMA_SHORT)
    df['EMA_MED'] = calculate_ema(df['close'], window=EMA_MED)
    df['EMA_LONG'] = calculate_ema(df['close'], window=EMA_LONG)

    # 4. Bollinger Band %B
    df['BBP'] = calculate_bollinger_bands(df['close'], window=BB_WINDOW, dev=BB_DEV)
    
    # 11, 12, 13. ADX, PDI, NDI 
    df['ADX'], df['PDI'], df['NDI'] = calculate_adx_ndi_pdi(df.copy(), window=ADX_PERIOD)
    
    # 13. OBV
    df['OBV'] = (np.sign(df['close'].diff()) * df['volume']).fillna(0).cumsum()
    
    # 5. VWAP (سيولة)
    df['PV'] = (df['high'] + df['low'] + df['close']) / 3 * df['volume']
    df['Cum_PV'] = df['PV'].cumsum()
    df['Cum_Volume'] = df['volume'].cumsum()
    df['VWAP'] = df['Cum_PV'] / (df['Cum_Volume'] + 1e-9)

    # 10. Standard Deviation (SD)
    df['SD'] = df['close'].rolling(window=SD_PERIOD).std().fillna(0)
    
    # 9. StochRSI
    df['StochRSI_K'], df['StochRSI_D'] = calculate_stochrsi(df['close'], window=STOCH_RSI_WINDOW, smooth_k=STOCH_RSI_SIGNAL_PERIOD, smooth_d=STOCH_RSI_SIGNAL_PERIOD)

    # 16. Z-SCORE 
    df['Z_SCORE'] = (df['close'] - df['EMA_LONG']) / (df['SD'] + 1e-9) 
    
    # 17. ATR (سيولة/تقلب)
    df['ATR'] = calculate_atr(df.copy(), window=ATR_PERIOD)
    df['ATR_AVG'] = df['ATR'].rolling(window=ATR_PERIOD * 2).mean()
    
    # 18. UO
    df['UO'] = calculate_uo(df.copy())
    
    # RSI (يُحسب لأجل التباعد)
    df['RSI'] = calculate_rsi(df['close'], window=RSI_PERIOD) 

    # 19. VW-MACD (زخم السيولة)
    df['VW_MACD'] = calculate_macd_diff(
        series=df['close'] * df['volume'], 
        fast=VW_MACD_FAST, slow=VW_MACD_SLOW, sign=VW_MACD_SIGNAL
    )

    # 20. Sharpe Ratio 
    df['Returns'] = df['close'].pct_change() 
    df['Sharpe_Numerator'] = df['Returns'].rolling(window=SHARPE_PERIOD).mean()
    df['Sharpe_Denominator'] = df['Returns'].rolling(window=SHARPE_PERIOD).std()
    df['Sharpe_Ratio'] = df['Sharpe_Numerator'] / (df['Sharpe_Denominator'] + 1e-9)

    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    df.fillna(0, inplace=True)

    return df

def generate_and_confirm_signal(df: pd.DataFrame): 
    """تطبيق نظام التصويت بالأغلبية البسيطة مع شروط مشددة وحساب نسبة الربح."""
    
    if df.empty or len(df) < REQUIRED_CANDLES: 
        return "ERROR", "darkred", "N/A", f"فشل في إنشاء عدد كافٍ من الشموع ({len(df)})."

    df = calculate_advanced_indicators(df)
    fib_levels, _, _ = calculate_fibonacci_ret(df)
    rsi_divergence = check_rsi_divergence(df.iloc[-20:].copy()) 
    
    # حساب الدعم والمقاومة
    support_level, resistance_level = find_snr_levels(df, SNR_WINDOW)
    snr_reaction = check_snr_reaction(df.iloc[-1]['close'], support_level, resistance_level, df.iloc[-2]['close'])

    last_candle = df.iloc[-1]
    prev_candle = df.iloc[-2]
    last_close = last_candle['close']
    
    signal_score = []
    
    # ------------------------------------------------
    # 1. نقاط المتوسطات والترند (3) - فترات قصيرة لحساسية النقرات
    # ------------------------------------------------
    signal_score.append(1 if last_close > last_candle['EMA_SHORT'] else -1)
    signal_score.append(1 if last_close > last_candle['EMA_MED'] else -1)
    signal_score.append(1 if last_candle['EMA_SHORT'] > last_candle['EMA_LONG'] else -1) # ترند طويل
    
    # ------------------------------------------------
    # 2. نقاط السيولة والزخم (7)
    # ------------------------------------------------
    # 4. Bollinger Band %B
    signal_score.append(1 if last_candle['BBP'] > 0.5 else -1)
    # 5. VWAP (سيولة قوية)
    signal_score.append(1 if last_close > last_candle['VWAP'] else -1)
    # 6. VW-MACD (زخم السيولة)
    signal_score.append(1 if last_candle['VW_MACD'] > 0 and last_candle['VW_MACD'] > prev_candle['VW_MACD'] else -1)
    # 7. ADX Trend Strength
    if last_candle['ADX'] > ADX_STRENGTH_THRESHOLD:
        signal_score.append(1 if last_candle['PDI'] > last_candle['NDI'] else -1)
    else: signal_score.append(0)
    # 8. OBV 
    signal_score.append(1 if last_candle['OBV'] > prev_candle['OBV'] else -1)
    # 9. StochRSI (تشديد التشبع)
    stoch_buy = (last_candle['StochRSI_K'] < STOCH_OVERSOLD_STRICT) and (last_candle['StochRSI_K'] > last_candle['StochRSI_D'])
    stoch_sell = (last_candle['StochRSI_K'] > STOCH_OVERBOUGHT_STRICT) and (last_candle['StochRSI_K'] < last_candle['StochRSI_D'])
    if stoch_buy: signal_score.append(1)
    elif stoch_sell: signal_score.append(-1)
    else: signal_score.append(0)
    # 10. UO
    signal_score.append(1 if last_candle['UO'] > 50 else -1)

    # ------------------------------------------------
    # 3. نقاط الدعم والمقاومة (2)
    # ------------------------------------------------
    # 11. Reaction to SNR (اختراق/ارتداد)
    if snr_reaction in ["BREAKOUT_UP", "REJECTION_UP"]: signal_score.append(1)
    elif snr_reaction in ["BREAKOUT_DOWN", "REJECTION_DOWN"]: signal_score.append(-1)
    else: signal_score.append(0)
    
    # 12. Price Location relative to Resistance/Support
    if resistance_level and last_close < resistance_level and last_close > support_level:
        # السعر بين الدعم والمقاومة، نفضل الشراء إذا كان أقرب للدعم (نقطة واحدة)
        distance_to_support = abs(last_close - support_level)
        distance_to_resistance = abs(last_close - resistance_level)
        signal_score.append(1 if distance_to_support < distance_to_resistance else -1)
    else: signal_score.append(0)

    # ------------------------------------------------
    # 4. نقاط الإحصاء والتباعد (11) (لإكمال الـ 23 نقطة)
    # ------------------------------------------------
    # 13. PDI
    signal_score.append(1 if last_candle['PDI'] > last_candle['NDI'] else -1)
    # 14. NDI
    signal_score.append(1 if last_candle['NDI'] < last_candle['PDI'] else -1)
    # 15. RSI Divergence
    if rsi_divergence == "BULLISH": signal_score.append(1)
    elif rsi_divergence == "BEARISH": signal_score.append(-1)
    else: signal_score.append(0)
    # 16. Strong Candle
    if is_strong_candle(last_candle, "BUY"): signal_score.append(1)
    elif is_strong_candle(last_candle, "SELL"): signal_score.append(-1)
    else: signal_score.append(0)
    # 17. Z-Score (تشديد العودة للمتوسط)
    signal_score.append(1 if last_candle['Z_SCORE'] < -Z_SCORE_THRESHOLD_STRICT else -1 if last_candle['Z_SCORE'] > Z_SCORE_THRESHOLD_STRICT else 0)
    # 18. ATR (تقلب منخفض)
    signal_score.append(1 if last_candle['ATR'] < last_candle['ATR_AVG'] else -1)
    # 19. Fibonacci
    fib_buy = fib_levels and last_close > fib_levels['61.8']
    fib_sell = fib_levels and last_close < fib_levels['38.2']
    if fib_buy: signal_score.append(1)
    elif fib_sell: signal_score.append(-1)
    else: signal_score.append(0)
    # 20. Sharpe Ratio 
    signal_score.append(1 if last_candle['Sharpe_Ratio'] > 0 else -1)
    # 21. EMA Short/Med Crossover
    signal_score.append(1 if last_candle['EMA_SHORT'] > last_candle['EMA_MED'] else -1)
    # 22. MACD Histogram (إيجابي/سلبي)
    signal_score.append(1 if last_candle['VW_MACD'] > 0 else -1)
    # 23. Z-Score Trend Confirmation (تأكيد الترند)
    signal_score.append(1 if last_candle['Z_SCORE'] < 0.5 and last_candle['Z_SCORE'] > -0.5 else 0)


    # ------------------------------------------------
    # 5. القرار النهائي وحساب نسبة الربح
    # ------------------------------------------------

    total_score = sum(signal_score)
    buy_votes = sum(s == 1 for s in signal_score)
    sell_votes = sum(s == -1 for s in signal_score)
    total_axes = 23 # عدد المحاور الكلي

    # حساب نسبة قوة الصفقة كنسبة مئوية (Profit Ratio)
    if total_score > 0:
        strength_ratio = (buy_votes / total_axes) * 100
        final_signal = "BUY (CALL)"
        color = "lime"
        reason = f"🟢 **توافق (BUY):** {buy_votes} صعود مقابل {sell_votes} هبوط. (قوة الصفقة: {strength_ratio:.1f}%)"
    elif total_score < 0:
        strength_ratio = (sell_votes / total_axes) * 100
        final_signal = "SELL (PUT)"
        color = "red"
        reason = f"🛑 **توافق (SELL):** {sell_votes} هبوط مقابل {buy_votes} صعود. (قوة الصفقة: {strength_ratio:.1f}%)"
    else:
        strength_ratio = 0
        final_signal = "WAIT (Neutral)"
        color = "yellow"
        reason = f"🟡 **تعادل (WAIT):** لا توجد أغلبية. (قوة الصفقة: 0%)"
    
    # إضافة نسبة الربح كنص بسيط لعرضه في الواجهة
    profit_ratio_text = f"الربح المتوقع: **{strength_ratio:.1f}%**"
    
    return final_signal, color, profit_ratio_text, reason


# --- مسارات Flask ---

@app.route('/', methods=['GET'])
def index():
    """ينشئ الواجهة الأمامية النظيفة التي طلبها المستخدم."""
    
    pair_options = "".join([f'<option value="{code}">{name} ({code})</option>' for code, name in PAIRS.items()])

    html_content = f"""
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>KhouryBot</title>
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; text-align: center; margin: 0; background-color: #0d1117; color: #c9d1d9; padding-top: 40px; }}
            .container {{ max-width: 550px; margin: 0 auto; padding: 35px; border-radius: 10px; background-color: #161b22; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5); }}
            h1 {{ color: #FFD700; margin-bottom: 25px; font-size: 1.8em; }}
            .status-box {{ background-color: #21262d; padding: 15px; border-radius: 6px; margin-bottom: 25px; border-right: 3px solid #FFD700; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3); }}
            #countdown-timer {{ font-size: 2.2em; color: #ff00ff; font-weight: bold; display: block; margin: 5px 0 10px 0; text-shadow: 0 0 8px rgba(255, 0, 255, 0.5); }}
            label {{ display: block; text-align: right; margin-bottom: 5px; color: #8b949e; }}
            select {{ padding: 12px; margin: 10px 0; width: 100%; box-sizing: border-box; border: 1px solid #30363d; border-radius: 6px; font-size: 16px; background-color: #21262d; color: #c9d1d9; -webkit-appearance: none; -moz-appearance: none; appearance: none; background-image: url('data:image/svg+xml;charset=US-ASCII,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%22292.4%22%20height%3D%22292.4%22%3E%3Cpath%20fill%3D%22%23c9d1d9%22%20d%3D%22M287%20197.3L159.9%2069.1c-3-3-7.7-3-10.7%200l-127%20128.2c-3%203-3%207.7%200%2010.7l10.7%2010.7c3%203%207.7%203%2010.7%200l113.6-114.6c3-3%207.7-3%2010.7%200l113.6%20114.6c3%203%207.7%203%2010.7%200l10.7-10.7c3.1-3%203.1-7.7%200-10.7z%22%2F%3E%3C%2Fsvg%3E'); background-repeat: no-repeat; background-position: left 0.7em top 50%, 0 0; background-size: 0.65em auto, 100%; }}
            #result {{ font-size: 3.5em; margin-top: 30px; font-weight: 900; min-height: 70px; text-shadow: 0 0 15px rgba(255, 255, 255, 0.7); }}
            #profit-ratio {{ font-size: 1.5em; margin-top: 10px; font-weight: bold; color: #58a6ff; }}
            .loading {{ color: #58a6ff; font-size: 1.2em; animation: pulse 1.5s infinite alternate; }}
            @keyframes pulse {{ from {{ opacity: 1; }} to {{ opacity: 0.6; }} }}
        </style>
    </head>
    <body onload="startAutomation()">
        <div class="container">
            <h1>KhouryBot</h1>
            
            <div class="status-box">
                <p>الوقت المتبقي للتحليل التالي:</p>
                <div id="countdown-timer">--:--</div>
            </div>
            
            <label for="currency_pair">زوج العملات:</label>
            <select id="currency_pair">
                {pair_options}
            </select>
            
            <div id="result">---</div>
            <div id="profit-ratio"></div>
        </div>

        <script>
            const resultDiv = document.getElementById('result');
            const profitRatioDiv = document.getElementById('profit-ratio');
            const countdownTimer = document.getElementById('countdown-timer');
            let countdownInterval = null; 
            const SIGNAL_DURATION_MS = 30000; 
            
            function calculateNextSignalTime() {{
                const now = new Date();
                const currentMinutes = now.getMinutes();
                const nextFiveMinuteMark = Math.ceil((currentMinutes + 1) / 5) * 5;
                let nextTargetTime = new Date(now);
                nextTargetTime.setMinutes(nextFiveMinuteMark);
                nextTargetTime.setSeconds(0);
                nextTargetTime.setMilliseconds(0);
                
                if (nextTargetTime.getTime() <= now.getTime()) {{
                    nextTargetTime.setMinutes(nextTargetTime.getMinutes() + 5);
                }}
                
                const signalTime = new Date(nextTargetTime.getTime() - 10000); 
                const delayMs = signalTime.getTime() - now.getTime();
                const safeDelay = Math.max(1000, delayMs); 

                return {{ delay: safeDelay }};
            }}
            
            function startCountdown() {{
                if (countdownInterval) clearInterval(countdownInterval);

                countdownInterval = setInterval(() => {{
                    const targetInfo = calculateNextSignalTime();
                    let remainingSeconds = Math.ceil(targetInfo.delay / 1000);

                    if (remainingSeconds < 1) {{
                        countdownTimer.textContent = '...تحليل الآن...';
                        return;
                    }}
                    
                    const displayMinutes = Math.floor(remainingSeconds / 60);
                    const displaySeconds = remainingSeconds % 60;
                    
                    countdownTimer.textContent = displayMinutes.toString().padStart(2, '0') + ':' + displaySeconds.toString().padStart(2, '0');
                }}, 1000);
            }}

            function hideSignal() {{
                resultDiv.innerHTML = '---';
                resultDiv.style.color = '#c9d1d9'; 
                profitRatioDiv.innerHTML = '';
            }}

            async function autoFetchSignal() {{
                if (countdownInterval) clearInterval(countdownInterval);
                countdownTimer.textContent = '...تحليل القوة...'; 

                const pair = document.getElementById('currency_pair').value;
                
                resultDiv.innerHTML = '<span class="loading">يتم تحليل السيولة والزخم...</span>';
                profitRatioDiv.innerHTML = '';

                try {{
                    const response = await fetch('/get-inverted-signal', {{ 
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ pair: pair }})
                    }});
                    const data = await response.json();
                    
                    resultDiv.innerHTML = data.signal; 
                    resultDiv.style.color = data.color; 
                    
                    if (data.ratio_text) {{
                        profitRatioDiv.innerHTML = data.ratio_text;
                    }}

                    setTimeout(() => {{
                        hideSignal();
                        scheduleNextSignal(); 
                    }}, SIGNAL_DURATION_MS);

                }} catch (error) {{
                    resultDiv.innerHTML = 'خطأ في الخادم.';
                    resultDiv.style.color = '#ff9800'; 
                    profitRatioDiv.innerHTML = '';
                    
                    setTimeout(scheduleNextSignal, SIGNAL_DURATION_MS);
                }}
            }}

            function scheduleNextSignal() {{
                const target = calculateNextSignalTime();
                startCountdown(); 
                setTimeout(autoFetchSignal, target.delay);
            }}

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
        
        df_ticks = get_market_data(symbol) 
        df_local = aggregate_ticks_to_candles(df_ticks) 
        
        if df_local.empty:
            return jsonify({"signal": "ERROR", "color": "darkred", "ratio_text": "N/A", "reason": f"فشل جلب البيانات."}), 200

        # 4. توليد الإشارة بناءً على الأغلبية وحساب نسبة الربح
        final_signal, color, profit_ratio_text, reason = generate_and_confirm_signal(df_local)
        
        return jsonify({
            "signal": final_signal, 
            "color": color, 
            "ratio_text": profit_ratio_text,
            "reason": reason # هذه تبقى في الخلفية للمطور فقط
        })
    except Exception as e:
        return jsonify({
            "signal": "ERROR", 
            "color": "darkred", 
            "ratio_text": "N/A",
            "reason": f"خطأ غير متوقع في الخادم: {str(e)}"
        }), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
