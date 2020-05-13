from unittest import mock

import pandas as pd
from pyalgotrade import plotter, strategy
from pyalgotrade.bar import Bars, Frequency
from pyalgotrade.barfeed import csvfeed
from pyalgotrade.stratanalyzer import returns, trades
from pyalgotrade.technical import ma
from stockstats import StockDataFrame

from trading_client import ATR_LOOKBACK, EMA_LOOKBACK, MA_LOOKBACK, get_indicator, recalibrate_position


# CSV file to load data for backtesting
BACKTESTING_DATA = 'backtesting_data/bitstamp_btcusd_from_2014.csv'
# Current cryptocurrency instrument to backtest on
INSTRUMENT = 'BTC-USD'


class CryptoMomentumStrategy(strategy.BacktestingStrategy):
    def __init__(self, feed: csvfeed.GenericBarFeed, instrument: str, ma_look: int, ema_look: int, atr_look: int):
        super().__init__(feed)
        self.long = None
        self.short = None
        self.instrument = instrument
        self.bar = None
        self.bar_count = 0
        self.market_price = 0
        self.current_position = {}
        self.was_long = None
        self.plot_ma = ma.SMA(feed[self.instrument].getPriceDataSeries(), ma_look)
        self.plot_ema = ma.EMA(feed[self.instrument].getPriceDataSeries(), ema_look)
        self.longest_look = 4 * max([ma_look, ema_look, atr_look + 1])

    def onEnterOk(self, position: strategy.position.Position):
        # Long position entered
        if self.long == position:
            self.info('ENTER LONG at ${:.2f} ({} shares)'.format(self.market_price, position.getShares()))
        # Short position entered
        elif self.short == position:
            self.info('ENTER SHORT at ${:.2f} ({} shares)'.format(self.market_price, position.getShares()))
        else:
            self.position_error()

    def onEnterCanceled(self, position: strategy.position.Position):
        # Abort entry on failure
        if self.long == position:
            self.long = None
        elif self.short == position:
            self.short = None
        else:
            self.position_error()

    def onExitOk(self, position: strategy.position.Position):
        if self.was_long is None:
            self.position_error()
        # Determine profit or loss
        profit_or_loss = position.getReturn()
        profit_tag = 'profit' if profit_or_loss >= 0 else 'loss'
        # Long position reversed
        if self.was_long:
            self.info('EXIT LONG at ${:.2f} ({} of {:.2%})'.format(self.market_price, profit_tag, profit_or_loss))
        # Short position reversed
        else:
            self.info('EXIT SHORT at ${:.2f} ({} of {:.2%})'.format(self.market_price, profit_tag, profit_or_loss))

    def onExitCanceled(self, position: strategy.position.Position):
        # Resubmit exit on failure
        position.exitMarket()

    @mock.patch('trading_client.stop_was_triggered')
    @mock.patch('trading_client.search_current_position')
    @mock.patch('trading_client.fetch_account_balance')
    @mock.patch('ccxt.ftx.create_order')
    def onBars(self, bars: Bars, order_mock, balance_mock, position_mock, trigger_mock):
        self.bar = bars[self.instrument]
        self.market_price = self.bar.getPrice()
        # Wait for enough bars to be available for calculating MA, EMA, ATR
        self.bar_count += 1
        if self.bar_count < self.longest_look:
            return
        # Mock execution of market orders and information on balance and positions
        order_mock.side_effect = self.simulate_market_order
        balance_mock.return_value = self.getBroker().getEquity()
        position_mock.return_value = self.current_position
        trigger_mock.return_value = not (bool(self.current_position) or self.bar_count == self.longest_look)
        # Retrieve bars in lookback period and compute MA, EMA, ATR
        data_series = self.getFeed().getDataSeries(self.instrument)[-self.longest_look:]
        ma_for_look = self.ma_from_bars(data_series)
        ema_for_look = self.ema_from_bars(data_series)
        atr_for_look = self.atr_from_bars(data_series)
        # Determine if position has been reversed
        long_before = bool(self.long)
        short_before = bool(self.short)
        recalibrate_position(ma_for_look, ema_for_look, atr_for_look, self.market_price)
        # Update entry price, size, and side if position is new
        if long_before != bool(self.long) or short_before != bool(self.short):
            self.current_position = {}
            self.update_position()

    def get_plot_ma(self):
        return self.plot_ma

    def get_plot_ema(self):
        return self.plot_ema

    def simulate_market_order(self, *args, params=None):
        # No bars have been processed
        if not self.bar:
            return
        # Order is to reverse a long position
        if self.long:
            self.was_long = True
            self.long.exitMarket()
            self.long = None
            return
        # Order is to reverse a short position
        if self.short:
            self.was_long = False
            self.short.exitMarket()
            self.short = None
            return
        # Order is to enter a long position
        if args[2] == 'buy':
            self.long = self.enterLongStop(self.instrument, params['triggerPrice'], args[3], goodTillCanceled=True)
        # Order is to enter a short position
        elif args[2] == 'sell':
            self.short = self.enterShortStop(self.instrument, params['triggerPrice'], args[3], goodTillCanceled=True)
        else:
            self.position_error()

    def update_position(self):
        self.current_position['entryPrice'] = self.market_price
        # Long position has been entered
        if self.long:
            self.current_position['size'] = self.long.getShares()
            self.current_position['side'] = 'buy'
        # Short position has been entered
        elif self.short:
            self.current_position['size'] = abs(self.short.getShares())
            self.current_position['side'] = 'sell'
        # Stop was triggered
        else:
            self.current_position = {}

    @staticmethod
    def ma_from_bars(bars: Bars) -> float:
        # Convert array of bars to data frame with 'Close' column
        df = pd.concat([pd.DataFrame([bar.getClose()], columns=['Close']) for bar in bars], ignore_index=True)
        # Convert data frame to stock data frame
        sdf = StockDataFrame.retype(df)
        ma_key = 'close_' + str(MA_LOOKBACK) + '_sma'
        return get_indicator(sdf, ma_key)

    @staticmethod
    def ema_from_bars(bars: Bars) -> float:
        # Convert array of bars to data frame with 'Close' column
        df = pd.concat([pd.DataFrame([bar.getClose()], columns=['Close']) for bar in bars], ignore_index=True)
        # Convert data frame to stock data frame
        sdf = StockDataFrame.retype(df)
        ema_key = 'close_' + str(EMA_LOOKBACK) + '_ema'
        return get_indicator(sdf, ema_key)

    @staticmethod
    def atr_from_bars(bars: Bars) -> float:
        # Convert array of bars to data frame with 'High', 'Low', and 'Close' columns
        df = pd.concat(
            [pd.DataFrame(
                [[bar.getHigh(), bar.getLow(), bar.getClose()]],
                columns=['High', 'Low', 'Close'],
            ) for bar in bars],
            ignore_index=True,
        )
        # Convert data frame to stock data frame
        sdf = StockDataFrame.retype(df)
        atr_key = 'atr_' + str(ATR_LOOKBACK)
        return get_indicator(sdf, atr_key)

    @staticmethod
    def position_error():
        raise Exception('position must be short or long')


def main():
    # Initiate strategy with provided data
    feed = csvfeed.GenericBarFeed(Frequency.DAY)
    feed.addBarsFromCSV(INSTRUMENT, BACKTESTING_DATA)
    momentum_strategy = CryptoMomentumStrategy(feed, INSTRUMENT, MA_LOOKBACK, EMA_LOOKBACK, ATR_LOOKBACK)
    # Attach returns analyzer
    returns_analyzer = returns.Returns()
    momentum_strategy.attachAnalyzer(returns_analyzer)
    # Attach trades analyzer
    trades_analyzer = trades.Trades()
    momentum_strategy.attachAnalyzer(trades_analyzer)
    # Configure plotter
    plt = plotter.StrategyPlotter(momentum_strategy)
    plt.getInstrumentSubplot(INSTRUMENT).addDataSeries('MA', momentum_strategy.get_plot_ma())
    plt.getInstrumentSubplot(INSTRUMENT).addDataSeries('EMA', momentum_strategy.get_plot_ema())
    plt.getOrCreateSubplot('returns').addDataSeries('Simple Returns', returns_analyzer.getReturns())
    # Run strategy
    momentum_strategy.run()
    momentum_strategy.info('Final portfolio value: ${:.2f}'.format(momentum_strategy.getResult()))
    # Plot strategy
    plt.plot()
