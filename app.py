import os
import json
import time
import pandas as pd
import numpy as np
import ssl
from datetime import datetime, timedelta
from websocket import create_connection, WebSocketTimeoutException
from flask import Flask, request, jsonify, render_template_string
# تم إزالة: import ta 

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
    "frxGBPJPY": "GBP/JPY", "frxEURCAD": "EUR/CAD", "frxEURCHF": "EUR/CHF",
    "frxAUDJPY": "AUD/JPY", "frxCHFJPY": "CHF/JPY", "frxCADJPY": "CAD/JPY"
}

# 🟢 إعدادات الشموع المعتمدة على النقرات
TICKS_PER_CANDLE = 30 
TICK_COUNT = 5000 # عدد النقرات الإجمالي المطلوب (آمن)

# متغيرات الاستراتيجية المدمجة (القوة الواحد والعشرون)
EMA_SHORT = 20
EMA_MED = 50
EMA_LONG = 100 
ADX_PERIOD = 14
RSI_PERIOD = 14
SD_PERIOD = 20 # فترة الانحراف المعياري
BB_WINDOW = 20
BB_DEV = 2.0
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
VW_MACD_FAST = 12
VW_MACD_SLOW = 26
VW_MACD_SIGNAL = 9
VW_MACD_THRESHOLD = 0.0
REQUIRED_CANDLES = 120 

# =======================================================
# دوال المؤشرات اليدوية (بديل ta)
# =======================================================

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
    return rsi

def calculate_atr(df, window=14):
    """حساب متوسط المدى الحقيقي (ATR)."""
    df['H-L'] = df['high'] - df['low']
    df['H-PC'] = np.abs(df['high'] - df['close'].shift(1))
    df['L-PC'] = np.abs(df['low'] - df['close'].shift(1))
    tr = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
    atr = tr.ewm(span=window, adjust=False).mean()
    return atr

def calculate_adx_ndi_pdi(df, window=14):
    """حساب ADX, NDI, PDI."""
    df['UpMove'] = df['high'] - df['high'].shift(1)
    df['DownMove'] = df['low'].shift(1) - df['low']
    
    # +DM / -DM
    df['+DM'] = np.where((df['UpMove'] > df['DownMove']) & (df['UpMove'] > 0), df['UpMove'], 0)
    df['-DM'] = np.where((df['DownMove'] > df['UpMove']) & (df['DownMove'] > 0), df['DownMove'], 0)
    
    # ATR (للتطبيع)
    df['ATR_ADX'] = calculate_atr(df.copy(), window=window)
    
    # +DI / -DI
    df['+DI'] = (df['+DM'].rolling(window=window).sum() / df['ATR_ADX']) * 100
    df['-DI'] = (df['-DM'].rolling(window=window).sum() / df['ATR_ADX']) * 100
    
    # DX
    df['DX'] = np.abs(df['+DI'] - df['-DI']) / (df['+DI'] + df['-DI']) * 100
    
    # ADX (EMA of DX)
    df['ADX'] = df['DX'].ewm(span=window, adjust=False).mean()
    
    return df['ADX'], df['+DI'], df['-DI']

def calculate_stochrsi(series, window=14, smooth_k=3, smooth_d=3):
    """حساب مؤشر Stochastic RSI."""
    rsi = calculate_rsi(series, window=window)
    
    # %K StochRSI
    min_rsi = rsi.rolling(window=window).min()
    max_rsi = rsi.rolling(window=window).max()
    stochrsi_k = (rsi - min_rsi) / (max_rsi - min_rsi)
    stochrsi_k = stochrsi_k.fillna(0) * 100
    
    # Smoothing K and D
    stochrsi_k_smooth = calculate_ema(stochrsi_k, smooth_k)
    stochrsi_d = calculate_ema(stochrsi_k_smooth, smooth_d)
    
    return stochrsi_k_smooth, stochrsi_d

def calculate_bollinger_bands(series, window=20, dev=2.0):
    """حساب Bollinger Bands (%B)."""
    sma = series.rolling(window=window).mean()
    std = series.rolling(window=window).std()
    upper = sma + (std * dev)
    lower = sma - (std * dev)
    
    # %B (Percent Bandwidth)
    bbp = (series - lower) / (upper - lower)
    return bbp

def calculate_uo(df, s=7, m=14, l=28):
    """حساب Ultimate Oscillator (UO)."""
    df['TR'] = calculate_atr(df.copy(), window=1) # True Range (for UO calculation)
    df['BP'] = df['close'] - df[['low', 'close']].min(axis=1).shift(1) # Buying Pressure
    
    avg7 = (df['BP'].rolling(s).sum() / df['TR'].rolling(s).sum()).fillna(0)
    avg14 = (df['BP'].rolling(m).sum() / df['TR'].rolling(m).sum()).fillna(0)
    avg28 = (df['BP'].rolling(l).sum() / df['TR'].rolling(l).sum()).fillna(0)
    
    uo = 100 * ((4 * avg7) + (2 * avg14) + avg28) / 7
    return uo

def calculate_macd_diff(series, fast=12, slow=26, sign=9):
    """حساب MACD Histogram (Diff)."""
    ema_fast = calculate_ema(series, fast)
    ema_slow = calculate_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, sign)
    macd_diff = macd_line - signal_line
    return macd_diff


# =======================================================
# دوال المساعدة للاتصال والأمان
# =======================================================

def create_ssl_context():
    """إنشاء سياق SSL موثوق به لاستخدامه في WebSocket."""
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


# -------------------- الدوال المساعدة --------------------

def get_market_data(symbol) -> pd.DataFrame:
    """جلب النقرات التاريخية من Deriv WSS."""
    ssl_context = create_ssl_context()
    
    for attempt in range(MAX_RETRIES):
        ws = None
        try:
            ws = create_connection(DERIV_WSS, ssl_context=ssl_context)
            ws.settimeout(20) 
            
            request_data = json.dumps({
                "ticks_history": symbol, "end": "latest", "start": 1, 
                "style": "ticks", "count": TICK_COUNT
            })
            
            ws.send(request_data)
            response = ws.recv()
            data = json.loads(response)
            
            if 'error' in data:
                error_msg = data['error'].get('message', 'Unknown API Error')
                print(f"ATTEMPT {attempt + 1}: Deriv API returned an error for symbol {symbol}: {error_msg}")
                continue 
            
            if 'history' in data and 'prices' in data['history']:
                df_ticks = pd.DataFrame({'epoch': data['history']['times'], 'quote': data['history']['prices']})
                df_ticks['quote'] = pd.to_numeric(df_ticks['quote'], errors='coerce')
                df_ticks.dropna(inplace=True)
                return df_ticks
            
            print(f"ATTEMPT {attempt + 1}: Received unexpected successful format from Deriv (missing 'history' field or data).")
        
        except WebSocketTimeoutException:
            print(f"ATTEMPT {attempt + 1}: WebSocket Timeout.")
        except Exception as e:
            print(f"ATTEMPT {attempt + 1}: General connection error: {e}")
        finally:
            if ws:
                try: ws.close() 
                except: pass 

        if attempt < MAX_RETRIES - 1:
            wait_time = 2 ** attempt
            print(f"Waiting {wait_time} seconds before retrying...")
            time.sleep(wait_time)

    return pd.DataFrame()


def aggregate_ticks_to_candles(df_ticks: pd.DataFrame, time_frame: str = None) -> pd.DataFrame:
    """تحويل النقرات (Ticks) إلى شموع OHLCV بناءً على عدد النقرات (30 نقرة)."""
    if df_ticks.empty: return pd.DataFrame()
    
    df_ticks['candle_group'] = np.arange(len(df_ticks)) // TICKS_PER_CANDLE
    
    df_candles = df_ticks.groupby('candle_group').agg(
        open=('quote', 'first'),
        high=('quote', 'max'),
        low=('quote', 'min'),
        close=('quote', 'last'),
        volume=('quote', 'count'), 
        timestamp=('epoch', 'last') 
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
    # يجب أن يكون RSI محسوباً مسبقاً في df['RSI']
    recent_data = df.iloc[-15:].copy()
    
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
    """حساب جميع المؤشرات الـ 21 يدوياً (باستخدام Pandas/NumPy فقط)."""
    
    # 1. المتوسطات المتحركة (1, 2, 3)
    df['EMA_SHORT'] = calculate_ema(df['close'], window=EMA_SHORT)
    df['EMA_MED'] = calculate_ema(df['close'], window=EMA_MED)
    df['EMA_LONG'] = calculate_ema(df['close'], window=EMA_LONG)

    # 2. مؤشرات الزخم والتقلب الأساسية (4-14)
    
    # 4. Bollinger Band %B
    df['BBP'] = calculate_bollinger_bands(df['close'], window=BB_WINDOW, dev=BB_DEV)
    
    # 11, 12, 13. ADX, PDI, NDI
    df['ADX'], df['PDI'], df['NDI'] = calculate_adx_ndi_pdi(df.copy(), window=ADX_PERIOD)
    
    # 13. OBV
    df['OBV'] = (np.sign(df['close'].diff()) * df['volume']).fillna(0).cumsum()
    
    # 5. VWAP (يتم حسابه يدوياً كما اتفقنا سابقاً)
    df['PV'] = (df['high'] + df['low'] + df['close']) / 3 * df['volume']
    df['Cum_PV'] = df['PV'].cumsum()
    df['Cum_Volume'] = df['volume'].cumsum()
    df['VWAP'] = df['Cum_PV'] / df['Cum_Volume']
    df.drop(columns=['PV', 'Cum_PV', 'Cum_Volume'], inplace=True) 

    # 7. PSAR (قيمة وهمية لتعويض المؤشر المحذوف)
    df['PSAR'] = 0.0 
    
    # 10. Standard Deviation (SD) - الانحراف المعياري
    df['SD'] = df['close'].rolling(window=SD_PERIOD).std().fillna(0)
    
    # 9. StochRSI
    df['StochRSI_K'], df['StochRSI_D'] = calculate_stochrsi(df['close'], window=STOCH_RSI_WINDOW, smooth_k=STOCH_RSI_SIGNAL_PERIOD, smooth_d=STOCH_RSI_SIGNAL_PERIOD)

    # 3. المؤشرات الإحصائية المتقدمة (15-18)
    
    # 16. Z-SCORE 
    # يتم إضافة معامل صغير جداً لتجنب القسمة على صفر في حالة التذبذب الصفري
    df['Z_SCORE'] = (df['close'] - df['EMA_LONG']) / (df['SD'] + 1e-9) 
    
    # 17. ATR
    df['ATR'] = calculate_atr(df.copy(), window=ATR_PERIOD)
    df['ATR_AVG'] = df['ATR'].rolling(window=ATR_PERIOD * 2).mean()
    
    # 18. UO
    df['UO'] = calculate_uo(df.copy())
    
    # RSI (يُحسب لأجل التباعد)
    df['RSI'] = calculate_rsi(df['close'], window=RSI_PERIOD) 

    # 4. المؤشرات النهائية الجديدة (19-21)
    
    # 19. VW-MACD 
    df['VW_MACD'] = calculate_macd_diff(
        series=df['close'] * df['volume'], 
        fast=VW_MACD_FAST, slow=VW_MACD_SLOW, sign=VW_MACD_SIGNAL
    )

    # 20. Sharpe Ratio 
    df['Returns'] = df['close'].pct_change() 
    df['Sharpe_Numerator'] = df['Returns'].rolling(window=SHARPE_PERIOD).mean()
    df['Sharpe_Denominator'] = df['Returns'].rolling(window=SHARPE_PERIOD).std()
    df['Sharpe_Ratio'] = df['Sharpe_Numerator'] / df['Sharpe_Denominator']

    # تنظيف قيم Inf و NaN الناتجة عن التقسيم على الصفر
    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    df.fillna(0, inplace=True)

    return df

def generate_and_invert_signal(df: pd.DataFrame): 
    """تطبيق استراتيجية القوة الواحد والعشرون الموحدة (شموع النقرات)."""
    
    if df.empty or len(df) < REQUIRED_CANDLES: 
        return "ERROR", "darkred", f"فشل في إنشاء عدد كافٍ من الشموع ({len(df)}). يتطلب {REQUIRED_CANDLES} شمعة (كل منها {TICKS_PER_CANDLE} نقرة) على الأقل للتحليل."

    hft_trend = "SIDEWAYS"

    df = calculate_advanced_indicators(df)
    fib_levels, _, _ = calculate_fibonacci_ret(df)
    rsi_divergence = check_rsi_divergence(df.iloc[-20:].copy()) 

    last_candle = df.iloc[-1]
    prev_candle = df.iloc[-2]
    last_close = last_candle['close']
    
    # استخراج القيم
    last_ema_short = last_candle['EMA_SHORT']
    last_ema_med = last_candle['EMA_MED']
    last_vwap = last_candle['VWAP']
    vw_macd_hist_rising = last_candle['VW_MACD'] > prev_candle['VW_MACD'] 
    last_pdi = last_candle['PDI']
    last_ndi = last_candle['NDI']
    last_bbp = last_candle['BBP']
    last_adx = last_candle['ADX']
    last_sd = last_candle['SD']
    obv_rising = last_candle['OBV'] > prev_candle['OBV']
    last_z_score = last_candle['Z_SCORE']
    last_atr = last_candle['ATR']
    atr_avg = last_candle['ATR_AVG']
    last_uo = last_candle['UO']
    last_vw_macd = last_candle['VW_MACD']
    last_sharpe_ratio = last_candle['Sharpe_Ratio']
    
    stoch_buy_condition = (last_candle['StochRSI_K'] > last_candle['StochRSI_D'] and 
                           prev_candle['StochRSI_K'] < prev_candle['StochRSI_D'] and 
                           last_candle['StochRSI_K'] < STOCH_OVERSOLD)
    stoch_sell_condition = (last_candle['StochRSI_K'] < last_candle['StochRSI_D'] and 
                            prev_candle['StochRSI_K'] > prev_candle['StochRSI_D'] and 
                            last_candle['StochRSI_K'] > STOCH_OVERBOUGHT)
    
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

    # شروط التوقع الصعودي (BUY - 21 محور)
    if (
        last_close > last_ema_short and last_close > last_ema_med and  # 1, 2: EMA Short/Med Crossover
        (hft_trend == "BULLISH" or hft_trend == "SIDEWAYS") and # 3: HFT Trend Check
        last_close > last_vwap and vw_macd_hist_rising and last_pdi > last_ndi and # 5, 6, 11: VWAP, VW-MACD Rising, PDI
        last_pdi > last_ndi and stoch_buy_condition and last_sd > SD_THRESHOLD and # 7 (PDI>NDI - تعويض PSAR), 9 (StochRSI), 10 (Volatility)
        last_adx > ADX_STRENGTH_THRESHOLD and last_bbp < BB_LOW_EXTREME and obv_rising and # 12 (ADX), 4 (BB), 13 (OBV)
        strong_buy_candle and rsi_divergence == "BULLISH" and # 15 (Strong Candle), 14 (RSI Divergence)
        last_z_score < -Z_SCORE_THRESHOLD and last_atr > atr_avg * ATR_THRESHOLD and last_uo < 30 and # 16 (Z-Score), 17 (ATR), 18 (UO)
        fib_buy_condition and last_sharpe_ratio > 0 and last_vw_macd > VW_MACD_THRESHOLD # 21 (Fibo), 20 (Sharpe), 19 (VW-MACD)
    ):
        original_signal = "BUY"
        reason_detail = f"**قوة قصوى (BUY - 21 محور):** توافق كامل (على {TICKS_PER_CANDLE} نقرة/شمعة). تأكيد شارب وفيبوناتشي وزخم الحجم. **أقصى توقع صعودي لمدة 5 شموع.**"

    # شروط التوقع الهبوطي (SELL - 21 محور)
    elif (
        last_close < last_ema_short and last_close < last_ema_med and # 1, 2: EMA Short/Med Crossover
        (hft_trend == "BEARISH" or hft_trend == "SIDEWAYS") and # 3: HFT Trend Check
        last_close < last_vwap and not vw_macd_hist_rising and last_ndi > last_pdi and # 5, 6, 11: VWAP, VW-MACD Falling, NDI
        last_ndi > last_pdi and stoch_sell_condition and last_sd > SD_THRESHOLD and # 7 (NDI>PDI - تعويض PSAR), 9 (StochRSI), 10 (Volatility)
        last_adx > ADX_STRENGTH_THRESHOLD and last_bbp > BB_HIGH_EXTREME and not obv_rising and # 12 (ADX), 4 (BB), 13 (OBV)
        strong_sell_candle and rsi_divergence == "BEARISH" and # 15 (Strong Candle), 14 (RSI Divergence)
        last_z_score > Z_SCORE_THRESHOLD and last_atr > atr_avg * ATR_THRESHOLD and last_uo > 70 and # 16 (Z-Score), 17 (ATR), 18 (UO)
        fib_sell_condition and last_sharpe_ratio < 0 and last_vw_macd < VW_MACD_THRESHOLD # 21 (Fibo), 20 (Sharpe), 19 (VW-MACD)
    ):
        original_signal = "SELL"
        reason_detail = f"**قوة قصوى (SELL - 21 محور):** توافق كامل (على {TICKS_PER_CANDLE} نقرة/شمعة). تأكيد شارب وفيبوناتشي وزخم الحجم. **أقصى توقع هبوطي لمدة 5 شموع.**"

    # منطق الإشارة الدائم (Fallback - العكسي)
    else:
        # نظام EMA العكسي: إذا كان EMA20 > EMA50 (صاعد)، فالإشارة هي بيع (SELL).
        if last_ema_short > last_ema_med:
            original_signal = "SELL"
            reason_detail = (f"إشارة دائمة (تقاطع EMA العكسي): الترند الصاعد (EMA20>EMA50) يعني إشارة بيع (عكسي).")
        # إذا كان EMA20 < EMA50 (هابط)، فالإشارة هي شراء (BUY).
        else: 
            original_signal = "BUY"
            reason_detail = (f"إشارة دائمة (تقاطع EMA العكسي): الترند الهابط (EMA20<EMA50) يعني إشارة شراء (عكسي).")


    # --- منطق العكس (Inversion Logic) ---
    if original_signal == "BUY":
        inverted_signal = "SELL (PUT) - معكوس"
        color = "red"
        reason = "🛑 **تم عكس إشارة الشراء الأصلية (نظام 21 محور - الحد الأقصى).** " + reason_detail
    elif original_signal == "SELL":
        inverted_signal = "BUY (CALL) - معكوس"
        color = "lime"
        reason = "🟢 **تم عكس إشارة البيع الأصلية (نظام 21 محور - الحد الأقصى).** " + reason_detail
    else:
        inverted_signal, color, reason = "ERROR", "darkred", "لم يتم تحديد إشارة بسبب خطأ في المنطق الداخلي."


    return inverted_signal, color, reason


# --- مسارات Flask (مع العداد التنازلي التلقائي) ---

@app.route('/', methods=['GET'])
def index():
    """ينشئ الواجهة الأمامية الأوتوماتيكية مع العداد التنازلي."""
    
    pair_options = "".join([f'<option value="{code}">{name} ({code})</option>' for code, name in PAIRS.items()])

    html_content = f"""
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>KhouryBot (21 محور - مستقل)</title>
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; text-align: center; margin: 0; background-color: #0d1117; color: #c9d1d9; padding-top: 40px; }}
            .container {{ max-width: 550px; margin: 0 auto; padding: 35px; border-radius: 10px; background-color: #161b22; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5); }}
            h1 {{ color: #FFD700; margin-bottom: 25px; font-size: 1.8em; text-shadow: 0 0 10px rgba(255, 215, 0, 0.5); }}
            .time-note {{ color: #58a6ff; font-weight: bold; margin-bottom: 15px; font-size: 1.1em; }}
            .status-box {{ background-color: #21262d; padding: 15px; border-radius: 6px; margin-bottom: 25px; border-right: 3px solid #FFD700; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3); }}
            #countdown-timer {{ font-size: 2.2em; color: #ff00ff; font-weight: bold; display: block; margin: 5px 0 10px 0; text-shadow: 0 0 8px rgba(255, 0, 255, 0.5); }}
            #next-signal-time {{ color: #8b949e; font-size: 0.9em; }}
            label {{ display: block; text-align: right; margin-bottom: 5px; color: #8b949e; }}
            select {{ padding: 12px; margin: 10px 0; width: 100%; box-sizing: border-box; border: 1px solid #30363d; border-radius: 6px; font-size: 16px; background-color: #21262d; color: #c9d1d9; -webkit-appearance: none; -moz-appearance: none; appearance: none; background-image: url('data:image/svg+xml;charset=US-ASCII,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%22292.4%22%20height%3D%22292.4%22%3E%3Cpath%20fill%3D%22%23c9d1d9%22%20d%3D%22M287%20197.3L159.9%2069.1c-3-3-7.7-3-10.7%200l-127%20128.2c-3%203-3%207.7%200%2010.7l10.7%2010.7c3%203%207.7%203%2010.7%200l113.6-114.6c3-3%207.7-3%2010.7%200l113.6%20114.6c3%203%207.7%203%2010.7%200l10.7-10.7c3.1-3%203.1-7.7%200-10.7z%22%2F%3E%3C%2Fsvg%3E'); background-repeat: no-repeat; background-position: left 0.7em top 50%, 0 0; background-size: 0.65em auto, 100%; }}
            #result {{ font-size: 3.5em; margin-top: 30px; font-weight: 900; min-height: 70px; text-shadow: 0 0 15px rgba(255, 255, 255, 0.7); }}
            #reason-box {{ background-color: #21262d; padding: 15px; border-radius: 6px; margin-top: 20px; font-size: 0.9em; color: #9e9e9e; text-align: right; border-right: 3px solid #FFD700; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3); }}
            .loading {{ color: #58a6ff; font-size: 1.2em; animation: pulse 1.5s infinite alternate; }}
            @keyframes pulse {{ from {{ opacity: 1; }} to {{ opacity: 0.6; }} }}
        </style>
    </head>
    <body onload="startAutomation()">
        <div class="container">
            <h1>KhouryBot (21 محور - مستقل)</h1>
            
            <div class="time-note">
                **التحليل مستقل الآن (لا يعتمد على مكتبة TA).** يتم تحديث الوتيرة كل 5 دقائق.
            </div>
            
            <div class="status-box">
                <p>الوقت المتبقي للتحليل التالي (وتيرة الطلب):</p>
                <div id="countdown-timer">--:--</div>
                <p id="next-signal-time">يتم تحديث التحليل كل 5 دقائق (بتوقيتك المحلي).</p>
            </div>
            
            <label for="currency_pair">زوج العملات:</label>
            <select id="currency_pair">
                {pair_options}
            </select>
            
            <div id="price-info">
                آخر سعر إغلاق تم تحليله: <span id="current-price">N/A</span>
            </div>

            <div id="reason-box">
                سبب الإشارة: <span id="signal-reason">نظام 21 محور للتحليل الكمي (الشموع تعتمد على 30 نقرة).</span>
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
            const SIGNAL_DURATION_MS = 30000; 

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
                        nextSignalTimeDisplay.innerHTML = `يتم تحديث التحليل الآن.`;
                        return;
                    }}
                    
                    const displayMinutes = Math.floor(remainingSeconds / 60);
                    const displaySeconds = remainingSeconds % 60;
                    
                    countdownTimer.textContent = displayMinutes.toString().padStart(2, '0') + ':' + displaySeconds.toString().padStart(2, '0');

                    const minutes = targetInfo.closeTime.getMinutes().toString().padStart(2, '0');
                    const hours = targetInfo.closeTime.getHours().toString().padStart(2, '0');
                    
                    nextSignalTimeDisplay.innerHTML = 'التحليل التالي عند: ' + hours + ':' + minutes + ':00 (بتوقيتك المحلي)';
                }}, 1000);
            }}

            // --- دالة إخفاء الإشارة ---
            function hideSignal() {{
                resultDiv.innerHTML = '---';
                resultDiv.style.color = '#c9d1d9'; 
                reasonSpan.innerHTML = 'انتهت مدة الإشارة (30 ثانية). جاري الاستعداد للتحليل التالي.';
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
                reasonSpan.innerText = 'KhouryBot يطبق القوة الإحصائية المطلقة (على شموع 30 نقرة)...';

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
        
        # 2. جلب التيكات (5000 تيك الآن)
        df_ticks = get_market_data(symbol) 
        
        # 3. تجميع التيكات إلى شموع (30 نقرة لكل شمعة)
        df_local = aggregate_ticks_to_candles(df_ticks) 
        
        # 🛑 التحقق من البيانات هنا
        if df_local.empty:
            return jsonify({"signal": "ERROR", "color": "darkred", "price": "N/A", "reason": f"فشل جلب النقرات أو عدم كفاية البيانات لتكوين {REQUIRED_CANDLES} شمعة (30 نقرة)."}), 200

        current_price = df_local.iloc[-1]['close']
        
        # 4. توليد الإشارة العكسية 21 محور
        final_signal, color, reason = generate_and_invert_signal(df_local)
        
        return jsonify({
            "signal": final_signal, 
            "color": color, 
            "price": f"{current_price:.6f}",
            "reason": reason
        })
    except Exception as e:
        # لطباعة الخطأ في سجلات Render
        print(f"Server Error in get_signal_api: {e}") 
        return jsonify({
            "signal": "ERROR", 
            "color": "darkred", 
            "price": "N/A",
            "reason": f"خطأ غير متوقع في الخادم. قد تكون البيانات غير كافية أو فشل الاتصال. ({str(e)})"
        }), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
