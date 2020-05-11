import time

import ccxt
import pandas as pd
from stockstats import StockDataFrame


# TRADING SETTINGS
# Cryptocurrency to trade
CRYPTO_SYMBOL = 'BTC-PERP'
# Time period to consolidate trading information by
DATA_TIMEFRAME = '1d'
# Time period for moving average in days
MA_LOOKBACK = 18
# Time period for exponential moving average in days
EMA_LOOKBACK = 8
# Time period for average true range in days
ATR_LOOKBACK = 14
# Highest allowable portion of equity for position
EQUITY_AMOUNT = 0.75
# Leverage for sizing position
ACCOUNT_LEVERAGE = 1
# Risk for sizing position
RISK_MULTIPLIER = 0.05
# Currency of subaccount balance
EQUITY_CURRENCY = 'USD'
# Key for exchange API
API_KEY = ''
# Secret for exchange API
API_SECRET = ''
# Name of exchange subaccount
SUBACCOUNT_NAME = ''
# Prohibits immediate repositioning if stop loss is exceeded
WAIT_IF_STOP_LOSS = True


# Access FTX cryptocurrency exchange
exchange = ccxt.ftx({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'headers': {
        'FTX-SUBACCOUNT': SUBACCOUNT_NAME,
    },
})
# Verify login was successful
exchange.check_required_credentials()


def retrieve_trading_data() -> pd.DataFrame:
    longest_lookback = max([MA_LOOKBACK, EMA_LOOKBACK, ATR_LOOKBACK + 1])
    # First day of lookback
    start_point = int(time.time() - longest_lookback * 86400) * 1000
    trading_data = exchange.fetch_ohlcv(symbol=CRYPTO_SYMBOL, timeframe=DATA_TIMEFRAME, since=start_point)
    df = pd.DataFrame(trading_data)
    df.columns = ['DateTime', 'Open', 'High', 'Low', 'Close', 'Volume']
    return df


def get_indicator(trading_sdf: StockDataFrame, key: str) -> float:
    # Compute most recent indicator through stock data frame
    return trading_sdf[key].loc[len(trading_sdf) - 1]


def search_current_position() -> dict:
    # Method for fetching current positions, unique to FTX
    current_positions = exchange.private_get_positions()
    if current_positions['success']:
        if len(current_positions['result']) > 0:
            return current_positions['result'][0]
        return {}
    raise Exception('request failed to retrieve account positions')


def fetch_account_balance() -> float:
    balance_info = exchange.fetch_balance()
    if balance_info['info']['success']:
        if EQUITY_CURRENCY in balance_info['total']:
            return balance_info['total'][EQUITY_CURRENCY]
        raise Exception('account balance does not have a total in {}'.format(EQUITY_CURRENCY))
    raise Exception('request failed to retrieve account balance')


def recalibrate_position(ma: float, ema: float, atr: float, price):
    # Flag to signal when repositioning should occur
    should_reposition = False
    # Determine if position needs to be reversed
    current = search_current_position()
    if current:
        # Reverse entire position amount
        order_amount = current['size']
        # Current position is long
        if current['side'] == 'buy':
            # MA > EMA or stop loss exceeded
            if ma > ema or current['entryPrice'] - current['estimatedLiquidationPrice'] >= atr:
                exchange.create_order(symbol=CRYPTO_SYMBOL, type='market', side='sell', amount=order_amount)
                # Decide not to reposition if stop loss exceeded
                if current['entryPrice'] - current['estimatedLiquidationPrice'] >= atr and WAIT_IF_STOP_LOSS:
                    return
                should_reposition = True
        # Current position is short
        elif current['side'] == 'sell':
            # EMA > MA or stop loss exceeded
            if ema > ma or current['estimatedLiquidationPrice'] - current['entryPrice'] >= atr:
                exchange.create_order(symbol=CRYPTO_SYMBOL, type='market', side='buy', amount=order_amount)
                # Decide not to reposition if stop loss exceeded
                if current['estimatedLiquidationPrice'] - current['entryPrice'] >= atr and WAIT_IF_STOP_LOSS:
                    return
                should_reposition = True
    # Position is being reversed or no position exists
    if not current or should_reposition:
        # Size new position
        account_balance = fetch_account_balance()
        max_amount = (EQUITY_AMOUNT * ACCOUNT_LEVERAGE * account_balance) / price
        risk_adjusted_amount = (account_balance * RISK_MULTIPLIER) / (atr * 2)
        new_amount = risk_adjusted_amount if risk_adjusted_amount <= max_amount else max_amount
        # Short position when MA > EMA
        if ma > ema:
            exchange.create_order(symbol=CRYPTO_SYMBOL, type='market', side='sell', amount=new_amount)
        # Long position when EMA > MA
        elif ema > ma:
            exchange.create_order(symbol=CRYPTO_SYMBOL, type='market', side='buy', amount=new_amount)


def main():
    trading_df = retrieve_trading_data()
    trading_sdf = StockDataFrame.retype(trading_df)
    ma_key = 'close_' + str(MA_LOOKBACK) + '_sma'
    ma = get_indicator(trading_sdf, ma_key)
    ema_key = 'close_' + str(EMA_LOOKBACK) + '_ema'
    ema = get_indicator(trading_sdf, ema_key)
    atr_key = 'atr_' + str(ATR_LOOKBACK)
    atr = get_indicator(trading_sdf, atr_key)
    price = trading_df['Close'].loc[len(trading_df) - 1]
    recalibrate_position(ma, ema, atr, price)


if __name__ == '__main__':
    main()
