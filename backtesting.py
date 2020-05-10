from unittest import mock

import pandas as pd
from pyalgotrade import plotter, strategy
from pyalgotrade.bar import Bars, Frequency
from pyalgotrade.barfeed import csvfeed
from pyalgotrade.stratanalyzer import returns, trades

from trading_client import ATR_LOOKBACK, EMA_LOOKBACK, MA_LOOKBACK, get_atr, get_ema, get_ma, recalibrate_position


# CSV file to load data for backtesting
BACKTESTING_DATA = 'backtesting_data/btcusd_from_2015.csv'
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
        self.ma_look = ma_look
        self.ema_look = ema_look
        self.atr_look = atr_look
        self.longest_look = max([self.ma_look, self.ema_look, self.atr_look])

    def onEnterOk(self, position: strategy.position.Position):
        # Long position entered
        if self.long == position:
            self.info('ENTER LONG at ${:.2f}'.format(self.market_price))
        # Short position entered
        elif self.short == position:
            self.info('ENTER SHORT at ${:.2f}'.format(self.market_price))
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
        # Long position reversed
        elif self.was_long:
            self.info('EXIT LONG at ${:.2f}'.format(self.market_price))
        # Short position reversed
        else:
            self.info('EXIT SHORT at ${:.2f}'.format(self.market_price))

    def onExitCanceled(self, position: strategy.position.Position):
        # Resubmit exit on failure
        position.exitMarket()

    @mock.patch('trading_client.search_current_position')
    @mock.patch('trading_client.fetch_account_balance')
    @mock.patch('ccxt.ftx.create_order')
    def onBars(self, bars: Bars, order_mock, balance_mock, position_mock):
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
        # Retrieve bars in lookback period and compute MA, EMA, ATR
        data_series = self.getFeed().getDataSeries(self.instrument)
        ma = self.ma_from_bars(data_series[-self.ma_look:])
        ema = self.ema_from_bars(data_series[-self.ema_look:])
        atr = self.atr_from_bars(data_series[-self.atr_look:])
        # Determine if position has been reversed
        long_before = bool(self.long)
        short_before = bool(self.short)
        recalibrate_position(ma, ema, atr)
        if long_before != bool(self.long) or short_before != bool(self.short):
            self.current_position = {}
        self.update_position()

    def simulate_market_order(self, **kwargs):
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
        # Convert currency amount to number of shares
        shares = int(kwargs['amount'] / self.bar.getPrice())
        # Order is to enter a long position
        if kwargs['side'] == 'buy':
            self.long = self.enterLong(self.instrument, shares, goodTillCanceled=True)
        # Order is to enter a short position
        elif kwargs['side'] == 'sell':
            self.short = self.enterShort(self.instrument, shares, goodTillCanceled=True)
        else:
            self.position_error()

    def update_position(self):
        has_previous_position = bool(self.current_position)
        # Update current market price
        self.current_position['estimatedLiquidationPrice'] = self.bar.getPrice()
        # Update entry price, size, and side if position is new
        if not has_previous_position:
            self.current_position['entryPrice'] = self.bar.getPrice()
            # Long position has been entered
            if self.long:
                self.current_position['size'] = self.long.getShares()
                self.current_position['side'] = 'buy'
            # Short position has been entered
            elif self.short:
                self.current_position['size'] = self.short.getShares()
                self.current_position['side'] = 'sell'
            # Stop loss was triggered
            else:
                self.current_position = {}

    @staticmethod
    def ma_from_bars(bars: Bars) -> float:
        # Convert array of bars to data frame with 'Close' column
        df = pd.concat([pd.DataFrame([bar.getClose()], columns=['Close']) for bar in bars], ignore_index=True)
        return get_ma(df)

    @staticmethod
    def ema_from_bars(bars: Bars) -> float:
        # Convert array of bars to data frame with 'Close' column
        df = pd.concat([pd.DataFrame([bar.getClose()], columns=['Close']) for bar in bars], ignore_index=True)
        return get_ema(df)

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
        return get_atr(df)

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
    plt.getOrCreateSubplot('returns').addDataSeries('Simple Returns', returns_analyzer.getReturns())
    # Run strategy
    momentum_strategy.run()
    momentum_strategy.info('Final portfolio value: {}'.format(momentum_strategy.getResult()))
    # Plot strategy
    plt.plot()


if __name__ == '__main__':
    main()
