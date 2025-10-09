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
# الإعدادات والثوابت (FOCUS: HIGH FREQUENCY & REVERSION)
# =======================================================

app = Flask(__name__)

# 📌 معلومات Deriv/Binary WebSocket API
DERIV_WSS = "wss://blue.derivws.com/websockets/v3?app_id=16929"
MAX_RETRIES = 3 

# 📊 أزواج الفوركس فقط
PAIRS = {
    "frxEURUSD": "EUR/USD", "frxGBPUSD": "GBP/USD", "frxUSDJPY": "USD/JPY",
    "frxAUDUSD": "AUD/USD", "frxNZDUSD": "NZD/USD", "frxUSDCAD": "USD/CAD",
    "frxUSDCHF": "USD/CHF", "frxEURGBP": "EUR/GBP", "frxEURJPY": "EUR/JPY",
    "frxGBPJPY": "GBR/JPY", "frxEURCAD": "EUR/CAD", "frxEURCHF": "EUR/CHF",
    "frxAUDJPY": "AUD/JPY", "frxCHFJPY": "CHF/JPY", "frxCADJPY": "CAD/JPY"
}

# 🟢 إعدادات الشموع المعتمدة على النقرات (لحظية جداً)
TICKS_PER_CANDLE = 10 # 💥 10 نقرات لكل شمعة لصفقات الدقيقة
TICK_COUNT = 3000     # عدد النقرات الإجمالي المطلوب 

# متغيرات الاستراتيجية المدمجة (انعكاس قوي وزخم لحظي)
EMA_SHORT = 5
EMA_MED = 12
EMA_LONG = 26
RSI_PERIOD = 7
SD_PERIOD = 14 
BB_WINDOW = 14
BB_DEV = 2.5    # انحراف معياري أعلى للاختراق القوي
ADX_PERIOD = 7
Z_SCORE_THRESHOLD_STRICT = 2.5 # صرامة قصوى للانحراف
ATR_PERIOD = 5
CANDLE_STRENGTH_RATIO = 0.9 # شمعة قوية جداً
STOCH_RSI_WINDOW = 5
STOCH_OVERSOLD_STRICT = 5   # تشبع قصوى
STOCH_OVERBOUGHT_STRICT = 95 # تشبع قصوى
SNR_WINDOW = 30 
REQUIRED_CANDLES = 100 
CCI_PERIOD = 10
VW_MACD_FAST = 5
VW_MACD_SLOW = 10
VW_MACD_SIGNAL = 3

# =======================================================
# دوال المؤشرات المساعدة
# =======================================================

def create_ssl_context():
    """إنشاء سياق SSL موثوق به."""
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context

def calculate_ema(series, window):
    """حساب المتوسط المتحرك الأسي (EMA)."""
    return series.ewm(span=window, adjust=False).mean()

def calculate_rsi(series, window):
    """حساب مؤشر القوة النسبية (RSI)."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(com=window - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=window - 1, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(0)

def calculate_atr(df, window):
    """حساب متوسط المدى الحقيقي (ATR)."""
    df['H-L'] = df['high'] - df['low']
    df['H-PC'] = np.abs(df['high'] - df['close'].shift(1))
    df['L-PC'] = np.abs(df['low'] - df['close'].shift(1))
    tr = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
    return tr.ewm(span=window, adjust=False).mean()

def calculate_cci(df, window):
    """حساب مؤشر قناة السلع (CCI)."""
    df['TP'] = (df['high'] + df['low'] + df['close']) / 3
    df['SMA_TP'] = df['TP'].rolling(window=window).mean()
    df['MAD'] = df['TP'].rolling(window=window).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    denom = df['MAD'] + 1e-9
    cci = (df['TP'] - df['SMA_TP']) / (0.015 * denom)
    return cci.fillna(0)

def calculate_adx_ndi_pdi(df, window):
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

def calculate_stochrsi(series, window, smooth_k=3, smooth_d=3):
    """حساب مؤشر Stochastic RSI."""
    rsi = calculate_rsi(series, window=window)
    min_rsi = rsi.rolling(window=window).min()
    max_rsi = rsi.rolling(window=window).max()
    stochrsi_k = (rsi - min_rsi) / ((max_rsi - min_rsi) + 1e-9)
    stochrsi_k = stochrsi_k.fillna(0).clip(0, 1) * 100
    stochrsi_k_smooth = calculate_ema(stochrsi_k, smooth_k)
    stochrsi_d = calculate_ema(stochrsi_k_smooth, smooth_d)
    return stochrsi_k_smooth.fillna(0), stochrsi_d.fillna(0)

def calculate_bollinger_bands(series, window, dev):
    """حساب Bollinger Bands (%B)."""
    sma = series.rolling(window=window).mean()
    std = series.rolling(window=window).std()
    upper = sma + (std * dev)
    lower = sma - (std * dev)
    bbp = (series - lower) / ((upper - lower) + 1e-9)
    return bbp.fillna(0)

def calculate_uo(df, s=5, m=10, l=20):
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

def calculate_macd_diff(series, fast, slow, sign):
    """حساب MACD Histogram (Diff)."""
    ema_fast = calculate_ema(series, fast)
    ema_slow = calculate_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, sign)
    macd_diff = macd_line - signal_line
    return macd_diff.fillna(0)

def find_snr_levels(df: pd.DataFrame, window: int) -> tuple:
    """تحدد مستويات الدعم والمقاومة القريبة."""
    recent_data = df.iloc[-window:]
    if recent_data.empty: return None, None
    resistance = recent_data['high'].max()
    support = recent_data['low'].min()
    resistance_close = recent_data['close'].nlargest(3).mean()
    support_close = recent_data['close'].nsmallest(3).mean()
    final_resistance = max(resistance, resistance_close) 
    final_support = min(support, support_close)
    if final_resistance == final_support: return None, None
    return final_support, final_resistance

def check_snr_reaction(close_price: float, support: float, resistance: float, prev_close: float) -> str:
    """تحدد إذا كان هناك اختراق (Breakout) أو ارتداد (Rejection) من الدعم/المقاومة."""
    if support is None or resistance is None: return "NONE"
    tolerance = 0.0001 
    if close_price > resistance + tolerance and prev_close < resistance - tolerance: return "BREAKOUT_UP"
    if close_price < support - tolerance and prev_close > support + tolerance: return "BREAKOUT_DOWN"
    # الارتداد هو المهم لصفقة الدقيقة
    if close_price < resistance and resistance - close_price < tolerance * 5 and close_price < prev_close: return "REJECTION_DOWN"
    if close_price > support and close_price - support < tolerance * 5 and close_price > prev_close: return "REJECTION_UP"
    return "NONE"

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
            if 'error' in data: continue 
            if 'history' in data and 'prices' in data['history']:
                df_ticks = pd.DataFrame({'epoch': data['history']['times'], 'quote': data['history']['prices']})
                df_ticks['quote'] = pd.to_numeric(df_ticks['quote'], errors='coerce')
                df_ticks.dropna(inplace=True)
                return df_ticks
        except WebSocketTimeoutException: pass
        except Exception as e: pass
        finally:
            if ws:
                try: ws.close() 
                except: pass 
        if attempt < MAX_RETRIES - 1:
            wait_time = 2 ** attempt
            time.sleep(wait_time)
    return pd.DataFrame()

def aggregate_ticks_to_candles(df_ticks: pd.DataFrame) -> pd.DataFrame:
    """تحويل النقرات إلى شموع OHLCV بناءً على 10 نقرات."""
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
    """يحدد ما إذا كانت الشمعة الأخيرة شمعة قوية (90% من الجسم)."""
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

def calculate_advanced_indicators(df: pd.DataFrame):
    """حساب جميع المؤشرات الـ 20 (لحظية)."""
    
    df['EMA_SHORT'] = calculate_ema(df['close'], window=EMA_SHORT)
    df['EMA_MED'] = calculate_ema(df['close'], window=EMA_MED)
    df['EMA_LONG'] = calculate_ema(df['close'], window=EMA_LONG)

    df['BBP'] = calculate_bollinger_bands(df['close'], window=BB_WINDOW, dev=BB_DEV)
    
    df['ADX'], df['PDI'], df['NDI'] = calculate_adx_ndi_pdi(df.copy(), window=ADX_PERIOD)
    
    df['OBV'] = (np.sign(df['close'].diff()) * df['volume']).fillna(0).cumsum()
    
    df['PV'] = (df['high'] + df['low'] + df['close']) / 3 * df['volume']
    df['Cum_PV'] = df['PV'].cumsum()
    df['Cum_Volume'] = df['volume'].cumsum()
    df['VWAP'] = df['Cum_PV'] / (df['Cum_Volume'] + 1e-9)

    df['SD'] = df['close'].rolling(window=SD_PERIOD).std().fillna(0)
    
    df['StochRSI_K'], df['StochRSI_D'] = calculate_stochrsi(df['close'], window=STOCH_RSI_WINDOW)

    df['Z_SCORE'] = (df['close'] - df['EMA_LONG']) / (df['SD'] + 1e-9) 
    
    df['ATR'] = calculate_atr(df.copy(), window=ATR_PERIOD)
    df['ATR_AVG'] = df['ATR'].rolling(window=ATR_PERIOD * 2).mean()
    
    df['UO'] = calculate_uo(df.copy())
    
    df['RSI'] = calculate_rsi(df['close'], window=RSI_PERIOD) 

    df['VW_MACD'] = calculate_macd_diff(
        series=df['close'] * df['volume'], 
        fast=VW_MACD_FAST, slow=VW_MACD_SLOW, sign=VW_MACD_SIGNAL
    )
    
    df['CCI'] = calculate_cci(df.copy(), window=CCI_PERIOD)

    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    df.fillna(0, inplace=True)

    return df

def generate_and_confirm_signal(df: pd.DataFrame): 
    """تطبيق نظام التصويت بالأغلبية الموزونة (20 محوراً للانعكاس السريع)."""
    
    if df.empty or len(df) < REQUIRED_CANDLES: 
        return "ERROR", "darkred", "N/A", f"فشل في إنشاء عدد كافٍ من الشموع ({len(df)})."

    df = calculate_advanced_indicators(df)
    
    support_level, resistance_level = find_snr_levels(df, SNR_WINDOW)
    snr_reaction = check_snr_reaction(df.iloc[-1]['close'], support_level, resistance_level, df.iloc[-2]['close'])

    last_candle = df.iloc[-1]
    prev_candle = df.iloc[-2]
    last_close = last_candle['close']
    
    signal_score = []
    
    # **إجمالي المحاور = 20 محوراً موزوناً** (بعضها قيمته 2)

    # 1. نقاط المتوسطات والترند اللحظي (3)
    signal_score.append(1 if last_close > last_candle['EMA_SHORT'] else -1)
    signal_score.append(1 if last_close > last_candle['EMA_MED'] else -1)
    signal_score.append(1 if last_candle['EMA_SHORT'] > last_candle['EMA_LONG'] else -1) 
    
    # 2. نقاط الانعكاس والتشبع القصوى (4) - أصوات مزدوجة
    # 4. StochRSI (تشبع قصوى)
    stoch_buy = (last_candle['StochRSI_K'] < STOCH_OVERSOLD_STRICT) and (last_candle['StochRSI_K'] > last_candle['StochRSI_D'])
    stoch_sell = (last_candle['StochRSI_K'] > STOCH_OVERBOUGHT_STRICT) and (last_candle['StochRSI_K'] < last_candle['StochRSI_D'])
    if stoch_buy: signal_score.append(2)
    elif stoch_sell: signal_score.append(-2)
    else: signal_score.append(0)
    
    # 5. Z-Score (انحراف قصوى)
    z_buy = last_candle['Z_SCORE'] < -Z_SCORE_THRESHOLD_STRICT
    z_sell = last_candle['Z_SCORE'] > Z_SCORE_THRESHOLD_STRICT
    if z_buy: signal_score.append(2)
    elif z_sell: signal_score.append(-2)
    else: signal_score.append(0)
    
    # 6. Bollinger Band (%B) - اختراق النطاق
    bb_buy = last_candle['BBP'] < 0.05
    bb_sell = last_candle['BBP'] > 0.95
    if bb_buy: signal_score.append(1)
    elif bb_sell: signal_score.append(-1)
    else: signal_score.append(0)

    # 7. RSI (تشبع سريع)
    rsi_buy = last_candle['RSI'] < 25
    rsi_sell = last_candle['RSI'] > 75
    if rsi_buy: signal_score.append(1)
    elif rsi_sell: signal_score.append(-1)
    else: signal_score.append(0)

    # 3. نقاط الزخم والسيولة (7)
    # 8. VWAP (سيولة قوية)
    signal_score.append(1 if last_close > last_candle['VWAP'] else -1)
    # 9. VW-MACD (زخم السيولة)
    signal_score.append(1 if last_candle['VW_MACD'] > 0 and last_candle['VW_MACD'] > prev_candle['VW_MACD'] else -1)
    # 10. OBV (زخم الحجم)
    signal_score.append(1 if last_candle['OBV'] > prev_candle['OBV'] else -1)
    # 11. CCI (زخم) 
    cci_buy = last_candle['CCI'] < -100
    cci_sell = last_candle['CCI'] > 100
    if cci_buy: signal_score.append(1)
    elif cci_sell: signal_score.append(-1)
    else: signal_score.append(0)
    # 12. UO (زخم قصير)
    signal_score.append(1 if last_candle['UO'] > 50 else -1)
    # 13. Strong Candle
    if is_strong_candle(last_candle, "BUY"): signal_score.append(1)
    elif is_strong_candle(last_candle, "SELL"): signal_score.append(-1)
    else: signal_score.append(0)
    # 14. ATR (تقلب مناسب)
    signal_score.append(1 if last_candle['ATR'] < last_candle['ATR_AVG'] else -1) 

    # 4. نقاط الدعم والمقاومة والترند (6)
    # 15. Reaction to SNR (ارتداد فقط)
    if snr_reaction in ["REJECTION_UP"]: signal_score.append(2)
    elif snr_reaction in ["REJECTION_DOWN"]: signal_score.append(-2)
    else: signal_score.append(0)
    
    # 16. Price Location relative to Support/Resistance (القرب من الدعم/المقاومة)
    if resistance_level and last_close < resistance_level and last_close > support_level:
        distance_to_support = abs(last_close - support_level)
        distance_to_resistance = abs(last_close - resistance_level)
        signal_score.append(1 if distance_to_support < distance_to_resistance else -1)
    else: signal_score.append(0)
    
    # 17. PDI (لحظي)
    signal_score.append(1 if last_candle['PDI'] > last_candle['NDI'] else -1)
    # 18. NDI (لحظي)
    signal_score.append(1 if last_candle['NDI'] < last_candle['PDI'] else -1)
    
    # 19. ADX (قوة الترند)
    signal_score.append(1 if last_candle['ADX'] > 20 and last_candle['PDI'] > last_candle['NDI'] else -1 if last_candle['ADX'] > 20 and last_candle['PDI'] < last_candle['NDI'] else 0)
    
    # 20. MACD Histogram (إيجابي/سلبي)
    signal_score.append(1 if last_candle['VW_MACD'] > 0 else -1)


    # 5. القرار النهائي وحساب نسبة الربح (Profit Ratio)

    total_score = sum(signal_score)
    buy_votes = sum(s for s in signal_score if s > 0)
    sell_votes = sum(s for s in signal_score if s < 0) * -1
    
    max_possible_score = sum(abs(s) for s in signal_score) 
    
    if total_score > 0:
        strength_ratio = (buy_votes / max_possible_score) * 100
        final_signal = "BUY (CALL)"
        color = "lime"
    elif total_score < 0:
        strength_ratio = (sell_votes / max_possible_score) * 100
        final_signal = "SELL (PUT)"
        color = "red"
    else:
        strength_ratio = 0
        final_signal = "WAIT (Neutral)"
        color = "yellow"
    
    # **الفلتر الإضافي لضمان قوة الإشارة (70% حد أدنى)**
    if strength_ratio < 70 and final_signal != "WAIT (Neutral)":
         final_signal = "WAIT (Weak Signal)"
         color = "orange"
         strength_ratio = 0
         
    profit_ratio_text = f"الربح المتوقع: **{strength_ratio:.1f}%**"
    reason = f"محاور التشبع والانعكاس: {buy_votes} مقابل {sell_votes}. النتيجة الصافية: {total_score}."
    
    return final_signal, color, profit_ratio_text, reason

# --- مسارات Flask ---

@app.route('/', methods=['GET'])
def index():
    """ينشئ الواجهة الأمامية النظيفة التي طلبها المستخدم مع جدولة الدقيقة الواحدة."""
    
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
                <p>الوقت المتبقي للتحليل التالي (الثانية 50):</p>
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
            
            // 🛑 مدة عرض الإشارة في الواجهة (15 ثانية)
            const SIGNAL_DURATION_MS = 15000; 
            
            function calculateNextSignalTime() {{
                const now = new Date();
                
                // 1. تحديد بداية الدقيقة التالية
                let nextTargetTime = new Date(now);
                nextTargetTime.setMinutes(now.getMinutes() + 1);
                nextTargetTime.setSeconds(0);
                nextTargetTime.setMilliseconds(0);
                
                // 2. توقيت الإشارة: قبل 10 ثوانٍ من الدقيقة التالية (الثانية 50)
                const signalTime = new Date(nextTargetTime.getTime() - 10000); 

                // 3. حساب التأخير بالملي ثانية
                const delayMs = signalTime.getTime() - now.getTime();
                const safeDelay = Math.max(100, delayMs); // حد أدنى 100 ملي ثانية

                return {{ delay: safeDelay }};
            }}
            
            function startCountdown() {{
                if (countdownInterval) clearInterval(countdownInterval);

                countdownInterval = setInterval(() => {{
                    const targetInfo = calculateNextSignalTime();
                    let remainingSeconds = Math.ceil(targetInfo.delay / 1000);

                    if (remainingSeconds < 1 || targetInfo.delay <= 0) {{
                        countdownTimer.textContent = '...تحليل الآن...';
                        return;
                    }}
                    
                    const displaySeconds = remainingSeconds % 60;
                    
                    // عرض الثواني فقط
                    countdownTimer.textContent = '00:' + displaySeconds.toString().padStart(2, '0');

                }, 1000);
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
                    
                    // في حالة الخطأ، حاول مرة أخرى بعد 5 ثوانٍ
                    setTimeout(scheduleNextSignal, 5000);
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
            "reason": reason 
        })
    except Exception as e:
        return jsonify({
            "signal": "ERROR", 
            "color": "darkred", 
            "ratio_text": "N/A",
            "reason": f"خطأ غير متوقع في الخادم: {str(e)}"
        }), 500

if __name__ == '__main__':
    # لتشغيل البوت محليًا، استخدم:
    # app.run(host='0.0.0.0', port=5000, debug=True)
    # ملاحظة: يجب إزالة debug=True عند النشر على خادم إنتاج
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
