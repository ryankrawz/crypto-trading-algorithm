import time

import ccxt
import pandas as pd


# TRADING SETTINGS
# Cryptocurrency to trade
CRYPTO_SYMBOL = 'BTC/USD'
# Time period to consolidate trading information by
DATA_TIMEFRAME = '1d'
# Time period for moving average in days
MA_LOOKBACK = 18
# Time period for exponential moving average in days
EMA_LOOKBACK = 8
# Time period for average true range in days
ATR_LOOKBACK = 14
# Minimum millisecond delay between two requests
RATE_LIMIT = 5000
# Enables the built-in rate limiter
ENABLE_RATE_LIMIT = True
# Key for the exchange API
API_KEY = ''
# Secret for the exchange API
API_SECRET = ''
# Name of exchange subaccount
SUBACCOUNT_NAME = ''
# Enables waiting period if stop loss is exceeded
WAIT_IF_STOP_LOSS = True
# Waiting period in hours
WAIT_TIME = 24
# Flag to activate waiting period
CURRENTLY_WAITING = False


# Access FTX cryptocurrency exchange
exchange = ccxt.ftx({
    'rateLimit': RATE_LIMIT,
    'enableRateLimit': ENABLE_RATE_LIMIT,
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'options': {
        'FTX-SUBACCOUNT': SUBACCOUNT_NAME,
    },
})
# Manually preload markets
markets = exchange.load_markets()


def retrieve_trading_data() -> pd.DataFrame:
    longest_lookback = max([MA_LOOKBACK, EMA_LOOKBACK, ATR_LOOKBACK])
    # First day of lookback
    start_point = int(time.time() - longest_lookback * 86400) * 1000
    trading_data = exchange.fetch_ohlcv(CRYPTO_SYMBOL, timeframe=DATA_TIMEFRAME, since=start_point)
    df = pd.DataFrame(trading_data)
    df.columns = ['DateTime', 'Open', 'High', 'Low', 'Close', 'Volume']
    return df


def get_ma(trading_df: pd.DataFrame) -> float:
    return trading_df['Close'][-MA_LOOKBACK:].mean()


def get_ema(trading_df: pd.DataFrame) -> float:
    base = len(trading_df) - EMA_LOOKBACK
    ema = trading_df['Close'].loc[base]
    i = base + 1
    while i < len(trading_df):
        ema = (trading_df['Close'].loc[i] - ema) * (2 / (i - base + 1)) + ema
        i += 1
    return ema


def get_atr(trading_df: pd.DataFrame) -> float:
    total_true_range = 0
    i = len(trading_df) - ATR_LOOKBACK
    while i < len(trading_df):
        high_low = trading_df['High'].loc[i] - trading_df['Low'].loc[i]
        high_close = abs(trading_df['High'].loc[i] - trading_df['Close'].loc[i])
        low_close = abs(trading_df['Low'].loc[i] - trading_df['Close'].loc[i])
        total_true_range += max([high_low, high_close, low_close])
        i += 1
    return total_true_range / ATR_LOOKBACK


def calculate_position(ma: float, ema: float, atr: float) -> int:
    pass  # TODO: determine short/long position and size of position


def main():
    trading_df = retrieve_trading_data()
    ma = get_ma(trading_df)
    ema = get_ema(trading_df)
    atr = get_atr(trading_df)
    x = calculate_position(ma, ema, atr)
