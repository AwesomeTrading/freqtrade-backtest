# --- Do not remove these libs ---
import freqtrade.vendor.qtpylib.indicators as qtpylib
import numpy as np
import talib.abstract as ta
import pandas_ta as pta

from freqtrade.persistence import Trade
from freqtrade.strategy.interface import IStrategy
from pandas import DataFrame, Series, DatetimeIndex, merge
from datetime import datetime, timedelta
from freqtrade.strategy import merge_informative_pair, CategoricalParameter, DecimalParameter, IntParameter, stoploss_from_open
from freqtrade.exchange import timeframe_to_prev_date
from functools import reduce
from technical.indicators import RMI, zema, ichimoku

import json 
import requests

# --------------------------------
def ha_typical_price(bars):
    res = (bars['ha_high'] + bars['ha_low'] + bars['ha_close']) / 3.
    return Series(index=bars.index, data=res)

# Volume Weighted Moving Average
def vwma(dataframe: DataFrame, length: int = 10):
    """Indicator: Volume Weighted Moving Average (VWMA)"""
    # Calculate Result
    pv = dataframe['close'] * dataframe['volume']
    vwma = Series(ta.SMA(pv, timeperiod=length) / ta.SMA(dataframe['volume'], timeperiod=length))
    return vwma

# Modified Elder Ray Index
def moderi(dataframe: DataFrame, len_slow_ma: int = 32) -> Series:
    slow_ma = Series(ta.EMA(vwma(dataframe, length=len_slow_ma), timeperiod=len_slow_ma))
    return slow_ma >= slow_ma.shift(1)  # we just need true & false for ERI trend

def EWO(dataframe, ema_length=5, ema2_length=35):
    df = dataframe.copy()
    ema1 = ta.EMA(df, timeperiod=ema_length)
    ema2 = ta.EMA(df, timeperiod=ema2_length)
    emadif = (ema1 - ema2) / df['low'] * 100
    return emadif

def SROC(dataframe, roclen=21, emalen=13, smooth=21):
    df = dataframe.copy()

    roc = ta.ROC(df, timeperiod=roclen)
    ema = ta.EMA(df, timeperiod=emalen)
    sroc = ta.ROC(ema, timeperiod=smooth)

    return sroc

def range_percent_change(dataframe: DataFrame, method, length: int) -> float:
        """
        Rolling Percentage Change Maximum across interval.

        :param dataframe: DataFrame The original OHLC dataframe
        :param method: High to Low / Open to Close
        :param length: int The length to look back
        """
        if method == 'HL':
            return (dataframe['high'].rolling(length).max() - dataframe['low'].rolling(length).min()) / dataframe['low'].rolling(length).min()
        elif method == 'OC':
            return (dataframe['open'].rolling(length).max() - dataframe['close'].rolling(length).min()) / dataframe['close'].rolling(length).min()
        else:
            raise ValueError(f"Method {method} not defined!")

# Williams %R
def williams_r(dataframe: DataFrame, period: int = 14) -> Series:
    """Williams %R, or just %R, is a technical analysis oscillator showing the current closing price in relation to the high and low
        of the past N days (for a given N). It was developed by a publisher and promoter of trading materials, Larry Williams.
        Its purpose is to tell whether a stock or commodity market is trading near the high or the low, or somewhere in between,
        of its recent trading range.
        The oscillator is on a negative scale, from −100 (lowest) up to 0 (highest).
    """

    highest_high = dataframe["high"].rolling(center=False, window=period).max()
    lowest_low = dataframe["low"].rolling(center=False, window=period).min()

    WR = Series(
        (highest_high - dataframe["close"]) / (highest_high - lowest_low),
        name=f"{period} Williams %R",
        )

    return WR * -100

# Chaikin Money Flow
def chaikin_money_flow(dataframe, n=20, fillna=False) -> Series:
    """Chaikin Money Flow (CMF)
    It measures the amount of Money Flow Volume over a specific period.
    http://stockcharts.com/school/doku.php?id=chart_school:technical_indicators:chaikin_money_flow_cmf
    Args:
        dataframe(pandas.Dataframe): dataframe containing ohlcv
        n(int): n period.
        fillna(bool): if fill nan values.
    Returns:
        pandas.Series: New feature generated.
    """
    mfv = ((dataframe['close'] - dataframe['low']) - (dataframe['high'] - dataframe['close'])) / (dataframe['high'] - dataframe['low'])
    mfv = mfv.fillna(0.0)  # float division by zero
    mfv *= dataframe['volume']
    cmf = (mfv.rolling(n, min_periods=0).sum()
           / dataframe['volume'].rolling(n, min_periods=0).sum())
    if fillna:
        cmf = cmf.replace([np.inf, -np.inf], np.nan).fillna(0)
    return Series(cmf, name='cmf')

class TA_Collector_v1(IStrategy):
    # (1) sell rework

    ##########################################################################

    # Hyperopt result area

    # buy space
    buy_params = {
        "max_slip": 0.983,
        ##
        "buy_bb_width_1h": 0.954,
        "buy_roc_1h": 86,
        ##
        "buy_threshold": 0.003,
        "buy_bb_factor": 0.999,
        #
        "buy_bb_delta": 0.025,
        "buy_bb_width": 0.095,
        ##
        "buy_cci": -116,
        "buy_cci_length": 25,
        "buy_rmi": 49,
        "buy_rmi_length": 17,
        "buy_srsi_fk": 32,
        ##
        "buy_closedelta": 17.922,
        "buy_ema_diff": 0.026,
        ##
        "buy_ema_high": 0.968,
        "buy_ema_low": 0.935,
        "buy_ewo": -5.001,
        "buy_rsi": 23,
        "buy_rsi_fast": 44,
        ##
        "buy_ema_high_2": 1.087,
        "buy_ema_low_2": 0.970,
        "buy_ewo_high_2": 4.179,
        "buy_rsi_ewo_2": 35,
        "buy_rsi_fast_ewo_2": 45,
        ##
        "buy_closedelta_local_dip": 12.044,
        "buy_ema_diff_local_dip": 0.024,
        "buy_ema_high_local_dip": 1.014,
        "buy_rsi_local_dip": 21,
        ##
        "buy_r_deadfish_bb_factor": 1.014,
        "buy_r_deadfish_bb_width": 0.299,
        "buy_r_deadfish_ema": 1.054,
        "buy_r_deadfish_volume_factor": 1.59,
        "buy_r_deadfish_cti": -0.115,
        "buy_r_deadfish_r14": -44.34,
        ##
        "buy_clucha_bbdelta_close": 0.049,
        "buy_clucha_bbdelta_tail": 1.146,
        "buy_clucha_close_bblower": 0.018,
        "buy_clucha_closedelta_close": 0.017,
        "buy_clucha_rocr_1h": 0.526,
        ##
        "buy_adx": 13,
        "buy_cofi_r14": -85.016,
        "buy_cofi_cti": -0.892,
        "buy_ema_cofi": 1.147,
        "buy_ewo_high": 8.594,
        "buy_fastd": 28,
        "buy_fastk": 39,
        ##
        "buy_gumbo_ema": 1.121,
        "buy_gumbo_ewo_low": -9.442,
        "buy_gumbo_cti": -0.374,
        "buy_gumbo_r14": -51.971,
        ##
        "buy_sqzmom_ema": 0.981,
        "buy_sqzmom_ewo": -3.966,
        "buy_sqzmom_r14": -45.068,
        ##
        "buy_nfix_39_ema": 0.912,
        ##
        "buy_nfix_49_cti": -0.105,
        "buy_nfix_49_r14": -81.827,
    }

    # sell space
    sell_params = {
        ##
        "sell_cmf": -0.046,
        "sell_ema": 0.988,
        "sell_ema_close_delta": 0.022,
        ##
        "sell_deadfish_profit": -0.063,
        "sell_deadfish_bb_factor": 0.954,
        "sell_deadfish_bb_width": 0.043,
        "sell_deadfish_volume_factor": 2.37,
        ##
        "sell_cti_r_cti": 0.844,
        "sell_cti_r_r": -19.99,
    }

    minimal_roi = {
        "0": 0.205,
        "81": 0.038,
        "292": 0.005,
    }

    # Optimal timeframe for the strategy
    timeframe = '1m'
    inf_5m = '5m'
    inf_1h = '1h'

    # Run "populate_indicators()" only for new candle.
    process_only_new_candles = True

    # Disabled
    stoploss = -0.99

    # Custom stoploss
    use_custom_stoploss = True
    use_sell_signal = True

    ############################################################################

    ## Buy params

    is_optimize_dip = False
    buy_rmi = IntParameter(30, 50, default=35, optimize= is_optimize_dip)
    buy_cci = IntParameter(-135, -90, default=-133, optimize= is_optimize_dip)
    buy_srsi_fk = IntParameter(30, 50, default=25, optimize= is_optimize_dip)
    buy_cci_length = IntParameter(25, 45, default=25, optimize = is_optimize_dip)
    buy_rmi_length = IntParameter(8, 20, default=8, optimize = is_optimize_dip)

    is_optimize_break = False
    buy_bb_width = DecimalParameter(0.065, 0.135, default=0.095, optimize = is_optimize_break)
    buy_bb_delta = DecimalParameter(0.018, 0.035, default=0.025, optimize = is_optimize_break)

    is_optimize_local_uptrend = False
    buy_ema_diff = DecimalParameter(0.022, 0.027, default=0.025, optimize = is_optimize_local_uptrend)
    buy_bb_factor = DecimalParameter(0.990, 0.999, default=0.995, optimize = False)
    buy_closedelta = DecimalParameter(12.0, 18.0, default=15.0, optimize = is_optimize_local_uptrend)

    is_optimize_local_dip = False
    buy_ema_diff_local_dip = DecimalParameter(0.022, 0.027, default=0.025, optimize = is_optimize_local_dip)
    buy_ema_high_local_dip = DecimalParameter(0.90, 1.2, default=0.942 , optimize = is_optimize_local_dip)
    buy_closedelta_local_dip = DecimalParameter(12.0, 18.0, default=15.0, optimize = is_optimize_local_dip)
    buy_rsi_local_dip = IntParameter(15, 45, default=28, optimize = is_optimize_local_dip)
    buy_crsi_local_dip = IntParameter(10, 18, default=10, optimize = False)

    is_optimize_ewo = False
    buy_rsi_fast = IntParameter(35, 50, default=45, optimize = is_optimize_ewo)
    buy_rsi = IntParameter(15, 35, default=35, optimize = is_optimize_ewo)
    buy_ewo = DecimalParameter(-6.0, 5, default=-5.585, optimize = is_optimize_ewo)
    buy_ema_low = DecimalParameter(0.9, 0.99, default=0.942 , optimize = is_optimize_ewo)
    buy_ema_high = DecimalParameter(0.95, 1.2, default=1.084 , optimize = is_optimize_ewo)

    is_optimize_ewo_2 = False
    buy_rsi_fast_ewo_2 = IntParameter(15, 50, default=45, optimize = is_optimize_ewo_2)
    buy_rsi_ewo_2 = IntParameter(15, 50, default=35, optimize = is_optimize_ewo_2)
    buy_ema_low_2 = DecimalParameter(0.90, 1.2, default=0.970 , optimize = is_optimize_ewo_2)
    buy_ema_high_2 = DecimalParameter(0.90, 1.2, default=1.087 , optimize = is_optimize_ewo_2)
    buy_ewo_high_2 = DecimalParameter(2, 12, default=4.179, optimize = is_optimize_ewo_2)

    is_optimize_r_deadfish = False
    buy_r_deadfish_ema = DecimalParameter(0.90, 1.2, default=1.087 , optimize = is_optimize_r_deadfish)
    buy_r_deadfish_bb_width = DecimalParameter(0.03, 0.75, default=0.05 , optimize = is_optimize_r_deadfish)
    buy_r_deadfish_bb_factor = DecimalParameter(0.90, 1.2, default=1.0 , optimize = is_optimize_r_deadfish)
    buy_r_deadfish_volume_factor = DecimalParameter(1, 2.5, default=1.0 , optimize = is_optimize_r_deadfish)

    is_optimize_r_deadfish_protection = False
    buy_r_deadfish_cti = DecimalParameter(-0.6, -0.0, default=-0.5 , optimize = is_optimize_r_deadfish_protection)
    buy_r_deadfish_r14 = DecimalParameter(-60, -44, default=-60 , optimize = is_optimize_r_deadfish_protection)

    is_optimize_clucha = False
    buy_clucha_bbdelta_close = DecimalParameter(0.01,0.05, default=0.02206, optimize = is_optimize_clucha)
    buy_clucha_bbdelta_tail = DecimalParameter(0.7, 1.2, default=1.02515, optimize = is_optimize_clucha)
    buy_clucha_closedelta_close = DecimalParameter(0.001, 0.05, default=0.04401, optimize = is_optimize_clucha)
    buy_clucha_rocr_1h = DecimalParameter(0.1, 1.0, default=0.47782, optimize = is_optimize_clucha)

    is_optimize_cofi = False
    buy_ema_cofi = DecimalParameter(0.94, 1.2, default=0.97 , optimize = is_optimize_cofi)
    buy_fastk = IntParameter(0, 40, default=20, optimize = is_optimize_cofi)
    buy_fastd = IntParameter(0, 40, default=20, optimize = is_optimize_cofi)
    buy_adx = IntParameter(0, 30, default=30, optimize = is_optimize_cofi)
    buy_ewo_high = DecimalParameter(2, 12, default=3.553, optimize = is_optimize_cofi)

    is_optimize_cofi_protection = False
    buy_cofi_cti = DecimalParameter(-0.9, -0.0, default=-0.5 , optimize = is_optimize_cofi_protection)
    buy_cofi_r14 = DecimalParameter(-100, -44, default=-60 , optimize = is_optimize_cofi_protection)

    is_optimize_gumbo = False
    buy_gumbo_ema = DecimalParameter(0.9, 1.2, default=0.97 , optimize = is_optimize_gumbo)
    buy_gumbo_ewo_low = DecimalParameter(-12.0, 5, default=-5.585, optimize = is_optimize_gumbo)

    is_optimize_gumbo_protection = False
    buy_gumbo_cti = DecimalParameter(-0.9, -0.0, default=-0.5 , optimize = is_optimize_gumbo_protection)
    buy_gumbo_r14 = DecimalParameter(-100, -44, default=-60 , optimize = is_optimize_gumbo_protection)

    is_optimize_sqzmom_protection = False
    buy_sqzmom_ema = DecimalParameter(0.9, 1.2, default=0.97 , optimize = is_optimize_sqzmom_protection)
    buy_sqzmom_ewo = DecimalParameter(-12 , 12, default= 0 , optimize = is_optimize_sqzmom_protection)
    buy_sqzmom_r14 = DecimalParameter(-100, -22, default=-50 , optimize = is_optimize_sqzmom_protection)

    is_optimize_nfix_39 = True
    buy_nfix_39_ema = DecimalParameter(0.9, 1.2, default=0.97 , optimize = is_optimize_nfix_39)

    is_optimize_nfix_49_protection = False
    buy_nfix_49_cti = DecimalParameter(-0.9, -0.0, default=-0.5 , optimize = is_optimize_nfix_49_protection)
    buy_nfix_49_r14 = DecimalParameter(-100, -44, default=-60 , optimize = is_optimize_nfix_49_protection)

    is_optimize_btc_safe = False
    buy_btc_safe = IntParameter(-300, 50, default=-200, optimize = is_optimize_btc_safe)
    buy_btc_safe_1d = DecimalParameter(-0.075, -0.025, default=-0.05, optimize = is_optimize_btc_safe)
    buy_threshold = DecimalParameter(0.003, 0.012, default=0.008, optimize = is_optimize_btc_safe)

    is_optimize_check = False
    buy_roc_1h = IntParameter(-25, 200, default=10, optimize = is_optimize_check)
    buy_bb_width_1h = DecimalParameter(0.3, 2.0, default=0.3, optimize = is_optimize_check)

    ## Slippage params

    is_optimize_slip = False
    max_slip = DecimalParameter(0.33, 1.00, default=0.33, decimals=3, optimize=is_optimize_slip , space='buy', load=True)

    ## Sell params

    sell_btc_safe = IntParameter(-400, -300, default=-365, optimize = False)

    is_optimize_sell_stoploss = False
    sell_cmf = DecimalParameter(-0.4, 0.0, default=0.0, optimize = is_optimize_sell_stoploss)
    sell_ema_close_delta = DecimalParameter(0.022, 0.027, default= 0.024, optimize = is_optimize_sell_stoploss)
    sell_ema = DecimalParameter(0.97, 0.99, default=0.987 , optimize = is_optimize_sell_stoploss)

    is_optimize_deadfish = False
    sell_deadfish_bb_width = DecimalParameter(0.03, 0.75, default=0.05 , optimize = is_optimize_deadfish)
    sell_deadfish_profit = DecimalParameter(-0.15, -0.05, default=-0.05 , optimize = is_optimize_deadfish)
    sell_deadfish_bb_factor = DecimalParameter(0.90, 1.20, default=1.0 , optimize = is_optimize_deadfish)
    sell_deadfish_volume_factor = DecimalParameter(1, 2.5, default=1.0 , optimize = is_optimize_deadfish)

    is_optimize_bleeding = False
    sell_bleeding_cti = DecimalParameter(-0.9, -0.0, default=-0.5 , optimize = is_optimize_bleeding)
    sell_bleeding_r14 = DecimalParameter(-100, -44, default=-60 , optimize = is_optimize_bleeding)
    sell_bleeding_volume_factor = DecimalParameter(1, 2.5, default=1.0 , optimize = is_optimize_bleeding)

    is_optimize_cti_r = False
    sell_cti_r_cti = DecimalParameter(0.55, 1, default=0.5 , optimize = is_optimize_cti_r)
    sell_cti_r_r = DecimalParameter(-15, 0, default=-20 , optimize = is_optimize_cti_r)

    ############################################################################

    def informative_pairs(self):

        pairs = self.dp.current_whitelist()
        informative_pairs = [(pair, self.inf_1h) for pair in pairs]
        informative_pairs.extend([(pair, self.inf_5m) for pair in pairs])

        return informative_pairs

    def informative_1h_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        assert self.dp, "DataProvider is required for multiple timeframes."
        # Get the informative pair
        informative_1h = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe=self.inf_1h)

        # Bollinger bands
        bollinger2 = qtpylib.bollinger_bands(qtpylib.typical_price(informative_1h), window=20, stds=2)
        informative_1h['bb_lowerband2'] = bollinger2['lower']
        informative_1h['bb_middleband2'] = bollinger2['mid']
        informative_1h['bb_upperband2'] = bollinger2['upper']
        
        bollinger3 = qtpylib.bollinger_bands(qtpylib.typical_price(informative_1h), window=20, stds=3)
        informative_1h['bb_lowerband3'] = bollinger3['lower']
        informative_1h['bb_middleband3'] = bollinger3['mid']
        informative_1h['bb_upperband3'] = bollinger3['upper']

        # CMF
        informative_1h['cmf'] = chaikin_money_flow(informative_1h, 20)

        # RSI & MRSI & MSI OSCILLATOR
        informative_1h['rsi_14'] = ta.RSI(informative_1h, timeperiod=14)
        informative_1h['mrsi_7'] = ta.SMA(informative_1h['rsi_14'], timeperiod=7)
        informative_1h['rsi_osc'] = (informative_1h['rsi_14'] - informative_1h['mrsi_7']) / informative_1h['mrsi_7'] * 100

        # Heikin Ashi
        inf_heikinashi = qtpylib.heikinashi(informative_1h)
        informative_1h['ha_open'] = inf_heikinashi['open']
        informative_1h['ha_high'] = inf_heikinashi['high']
        informative_1h['ha_low'] = inf_heikinashi['low']
        informative_1h['ha_close'] = inf_heikinashi['close']

        # VWAP
        informative_1h['vwap'] = qtpylib.rolling_vwap(informative_1h)

        # VWAP OSCILLATOR
        informative_1h['vwap_osc'] = (informative_1h['close'] - informative_1h['vwap']) / informative_1h['vwap'] * 100

        # OBV
        informative_1h['obv'] = ta.OBV(informative_1h['close'], informative_1h['volume'])

        # OBV HA
        informative_1h['ha_obv'] = ta.OBV(informative_1h['ha_close'], informative_1h['volume'])

        # OBV MA
        informative_1h['mobv_3'] = ta.SMA(informative_1h['ha_obv'], timeperiod=3)

        # OBV OSCILLATOR
        informative_1h['obv_osc'] = (informative_1h['ha_obv'] - informative_1h['mobv_3']) / informative_1h['mobv_3'] * 100

        # Volume Oscilator
        informative_1h['vma_20'] = ta.SMA(informative_1h['volume'], timeperiod=20)
        informative_1h['vol_osc'] = (informative_1h['volume'] - informative_1h['vma_20']) / informative_1h['vma_20'] * 100

        # MACD
        macd = ta.MACD(informative_1h, 20, 5)
        informative_1h['macd'] = macd['macd']
        informative_1h['macd_signal'] = macd['macdsignal']
        informative_1h['macd_histogram'] = macd['macdhist']
        informative_1h['macd_osc'] = informative_1h['macd_histogram'] / informative_1h['close'] * 100

        # SMA
        informative_1h['sma_6'] = ta.SMA(informative_1h, timeperiod=6)
        informative_1h['sma_9'] = ta.SMA(informative_1h, timeperiod=9)
        informative_1h['sma_30'] = ta.SMA(informative_1h, timeperiod=30)
        informative_1h['sma_60'] = ta.SMA(informative_1h, timeperiod=60)
        informative_1h['sma_100'] = ta.SMA(informative_1h, timeperiod=100)

        # SMA OSCILLATOR
        informative_1h['sma_6_osc'] = (informative_1h['close'] - informative_1h['sma_6']) / informative_1h['sma_6'] * 100
        informative_1h['sma_9_osc'] = (informative_1h['close'] - informative_1h['sma_9']) / informative_1h['sma_9'] * 100
        informative_1h['sma_30_osc'] = (informative_1h['close'] - informative_1h['sma_30']) / informative_1h['sma_30'] * 100
        informative_1h['sma_60_osc'] = (informative_1h['close'] - informative_1h['sma_60']) / informative_1h['sma_60'] * 100
        informative_1h['sma_100_osc'] = (informative_1h['close'] - informative_1h['sma_100']) / informative_1h['sma_100'] * 100

        # Stochastic Slow
        stoch = ta.STOCH(informative_1h)
        informative_1h['stoch_slowd'] = stoch['slowd']
        informative_1h['stoch_slowk'] = stoch['slowk']
        informative_1h['stoch_slow_osc'] = (informative_1h['stoch_slowk'] - informative_1h['stoch_slowd']) / informative_1h['stoch_slowd'] * 100

        # Stochastic Fast
        stoch_fast = ta.STOCHF(informative_1h)
        informative_1h["stoch_fastd"] = stoch_fast["fastd"]
        informative_1h["stoch_fastk"] = stoch_fast["fastk"]

        return informative_1h
        
    def informative_5m_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        assert self.dp, "DataProvider is required for multiple timeframes."
        # Get the informative pair
        informative_5m = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe=self.inf_5m)

        # Bollinger bands
        bollinger2 = qtpylib.bollinger_bands(qtpylib.typical_price(informative_5m), window=20, stds=2)
        informative_5m['bb_lowerband2'] = bollinger2['lower']
        informative_5m['bb_middleband2'] = bollinger2['mid']
        informative_5m['bb_upperband2'] = bollinger2['upper']
        
        bollinger3 = qtpylib.bollinger_bands(qtpylib.typical_price(informative_5m), window=20, stds=3)
        informative_5m['bb_lowerband3'] = bollinger3['lower']
        informative_5m['bb_middleband3'] = bollinger3['mid']
        informative_5m['bb_upperband3'] = bollinger3['upper']

        # CMF
        informative_5m['cmf'] = chaikin_money_flow(informative_5m, 20)

        # RSI & MRSI & RSI OSCILLATOR
        informative_5m['rsi_14'] = ta.RSI(informative_5m, timeperiod=14)
        informative_5m['mrsi_7'] = ta.SMA(informative_5m['rsi_14'], timeperiod=7)
        informative_5m['rsi_osc'] = (informative_5m['rsi_14'] - informative_5m['mrsi_7']) / informative_5m['mrsi_7'] * 100

        # Heikin Ashi
        inf_heikinashi = qtpylib.heikinashi(informative_5m)
        informative_5m['ha_open'] = inf_heikinashi['open']
        informative_5m['ha_high'] = inf_heikinashi['high']
        informative_5m['ha_low'] = inf_heikinashi['low']
        informative_5m['ha_close'] = inf_heikinashi['close']

        # VWAP
        informative_5m['vwap'] = qtpylib.rolling_vwap(informative_5m)

        # VWAP OSCILLATOR
        informative_5m['vwap_osc'] = (informative_5m['close'] - informative_5m['vwap']) / informative_5m['vwap'] * 100

        # OBV
        informative_5m['obv'] = ta.OBV(informative_5m['close'], informative_5m['volume'])

        # OBV HA
        informative_5m['ha_obv'] = ta.OBV(informative_5m['ha_close'], informative_5m['volume'])

        # OBV MA
        informative_5m['mobv_3'] = ta.SMA(informative_5m['ha_obv'], timeperiod=3)

        # OBV OSCILLATOR
        informative_5m['obv_osc'] = (informative_5m['ha_obv'] - informative_5m['mobv_3']) / informative_5m['mobv_3'] * 100

        # Volume Oscilator
        informative_5m['vma_20'] = ta.SMA(informative_5m['volume'], timeperiod=20)
        informative_5m['vol_osc'] = (informative_5m['volume'] - informative_5m['vma_20']) / informative_5m['vma_20'] * 100

        # MACD
        macd = ta.MACD(informative_5m, 20, 5)
        informative_5m['macd'] = macd['macd']
        informative_5m['macd_signal'] = macd['macdsignal']
        informative_5m['macd_histogram'] = macd['macdhist']
        informative_5m['macd_osc'] = informative_5m['macd_histogram'] / informative_5m['close'] * 100

        # SMA
        informative_5m['sma_6'] = ta.SMA(informative_5m, timeperiod=6)
        informative_5m['sma_9'] = ta.SMA(informative_5m, timeperiod=9)
        informative_5m['sma_30'] = ta.SMA(informative_5m, timeperiod=30)
        informative_5m['sma_60'] = ta.SMA(informative_5m, timeperiod=60)
        informative_5m['sma_100'] = ta.SMA(informative_5m, timeperiod=100)

        # SMA OSCILLATOR
        informative_5m['sma_6_osc'] = (informative_5m['close'] - informative_5m['sma_6']) / informative_5m['sma_6'] * 100
        informative_5m['sma_9_osc'] = (informative_5m['close'] - informative_5m['sma_9']) / informative_5m['sma_9'] * 100
        informative_5m['sma_30_osc'] = (informative_5m['close'] - informative_5m['sma_30']) / informative_5m['sma_30'] * 100
        informative_5m['sma_60_osc'] = (informative_5m['close'] - informative_5m['sma_60']) / informative_5m['sma_60'] * 100
        informative_5m['sma_100_osc'] = (informative_5m['close'] - informative_5m['sma_100']) / informative_5m['sma_100'] * 100

        # Stochastic Slow
        stoch = ta.STOCH(informative_5m)
        informative_5m['stoch_slowd'] = stoch['slowd']
        informative_5m['stoch_slowk'] = stoch['slowk']
        informative_5m['stoch_slow_osc'] = (informative_5m['stoch_slowk'] - informative_5m['stoch_slowd']) / informative_5m['stoch_slowd'] * 100

        # Stochastic Fast
        stoch_fast = ta.STOCHF(informative_5m)
        informative_5m["stoch_fastd"] = stoch_fast["fastd"]
        informative_5m["stoch_fastk"] = stoch_fast["fastk"]

        return informative_5m

    ############################################################################

    ### Custom functions

    def custom_stoploss(self, pair: str, trade: 'Trade', current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        return 1
        # sl_new = 1

        # if (current_profit > 0.2):
        #     sl_new = 0.05
        # elif (current_profit > 0.1):
        #     sl_new = 0.03
        # elif (current_profit > 0.06):
        #     sl_new = 0.02
        # elif (current_profit > 0.03):
        #     sl_new = 0.015

        # return sl_new

    # From NFIX
    def custom_sell(self, pair: str, trade: 'Trade', current_time: 'datetime', current_rate: float,
                    current_profit: float, **kwargs):

        return None
        # dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)

        # last_candle = dataframe.iloc[-1]
        # previous_candle_1 = dataframe.iloc[-2]
        # previous_candle_2 = dataframe.iloc[-3]

        # max_profit = ((trade.max_rate - trade.open_rate) / trade.open_rate)
        # max_loss = ((trade.open_rate - trade.min_rate) / trade.min_rate)

        # buy_tag = 'empty'
        # if hasattr(trade, 'buy_tag') and trade.buy_tag is not None:
        #     buy_tag = trade.buy_tag
        # buy_tags = buy_tag.split()

        # # sell trail
        # if 0.012 > current_profit >= 0.0:
        #     if (max_profit > (current_profit + 0.045)) and (last_candle['rsi'] < 46.0):
        #         return f"sell_profit_t_0_1( {buy_tag})"
        #     elif (max_profit > (current_profit + 0.025)) and (last_candle['rsi'] < 32.0):
        #         return f"sell_profit_t_0_2( {buy_tag})"
        #     elif (max_profit > (current_profit + 0.05)) and (last_candle['rsi'] < 48.0):
        #         return f"sell_profit_t_0_3( {buy_tag})"
        # elif 0.02 > current_profit >= 0.012:
        #     if (max_profit > (current_profit + 0.01)) and (last_candle['rsi'] < 39.0):
        #         return f"sell_profit_t_1_1( {buy_tag})"
        #     elif (max_profit > (current_profit + 0.035)) and (last_candle['rsi'] < 45.0) and (last_candle['cmf'] < -0.0) and (last_candle['cmf_1h'] < -0.0):
        #         return f"sell_profit_t_1_2( {buy_tag})"
        #     elif (max_profit > (current_profit + 0.02)) and (last_candle['rsi'] < 40.0) and (last_candle['cmf'] < -0.0) and (last_candle['cti_1h'] > 0.8):
        #         return f"sell_profit_t_1_4( {buy_tag})"
        #     elif (max_profit > (current_profit + 0.04)) and (last_candle['rsi'] < 49.0) and (last_candle['cmf_1h'] < -0.0):
        #         return f"sell_profit_t_1_5( {buy_tag})"
        #     elif (max_profit > (current_profit + 0.06)) and (last_candle['rsi'] < 43.0) and (last_candle['cmf'] < -0.0):
        #         return f"sell_profit_t_1_7( {buy_tag})"
        #     elif (max_profit > (current_profit + 0.025)) and (last_candle['rsi'] < 40.0) and (last_candle['cmf'] < -0.1) and (last_candle['rsi_1h'] < 50.0):
        #         return f"sell_profit_t_1_9( {buy_tag})"
        #     elif (max_profit > (current_profit + 0.025)) and (last_candle['rsi'] < 46.0) and (last_candle['cmf'] < -0.0) and (last_candle['r_480_1h'] > -20.0):
        #         return f"sell_profit_t_1_10( {buy_tag})"
        #     elif (max_profit > (current_profit + 0.025)) and (last_candle['rsi'] < 42.0):
        #         return f"sell_profit_t_1_11( {buy_tag})"
        #     elif (max_profit > (current_profit + 0.01)) and (last_candle['rsi'] < 44.0) and (last_candle['cmf'] < -0.25):
        #         return f"sell_profit_t_1_12( {buy_tag})"

        # # sell cti_r
        # if 0.012 > current_profit >= 0.0 :
        #     if (last_candle['cti'] > self.sell_cti_r_cti.value) and (last_candle['r_14'] > self.sell_cti_r_r.value):
        #         return f"sell_profit_t_cti_r_0_1( {buy_tag})"

        # # main sell
        # if current_profit > 0.02:
        #     if (last_candle['momdiv_sell_1h'] == True):
        #         return f"signal_profit_q_momdiv_1h( {buy_tag})"
        #     if (last_candle['momdiv_sell'] == True):
        #         return f"signal_profit_q_momdiv( {buy_tag})"
        #     if (last_candle['momdiv_coh'] == True):
        #         return f"signal_profit_q_momdiv_coh( {buy_tag})"

        # # sell bear
        # if last_candle['close'] < last_candle['ema_200']:
        #     if 0.02 > current_profit >= 0.01:
        #         if (last_candle['rsi'] < 34.0) and (last_candle['cmf'] < 0.0):
        #             return f"sell_profit_u_bear_1_1( {buy_tag})"
        #         elif (last_candle['rsi'] < 44.0) and (last_candle['cmf'] < -0.4):
        #             return f"sell_profit_u_bear_1_2( {buy_tag})"

        # # sell quick
        # if (0.06 > current_profit > 0.02) and (last_candle['rsi'] > 80.0):
        #     return f"signal_profit_q_1( {buy_tag})"

        # if (0.06 > current_profit > 0.02) and (last_candle['cti'] > 0.95):
        #     return f"signal_profit_q_2( {buy_tag})"

        # if (0.06 > current_profit > 0.02) and (last_candle['pm'] <= last_candle['pmax_thresh']) and (last_candle['close'] > last_candle['sma_21'] * 1.1):
        #     return f"signal_profit_q_pmax_bull( {buy_tag})"
        # if (0.06 > current_profit > 0.02) and (last_candle['pm'] > last_candle['pmax_thresh']) and (last_candle['close'] > last_candle['sma_21'] * 1.016):
        #     return f"signal_profit_q_pmax_bear( {buy_tag})"

        # # sell scalp
        # if (current_profit > 0 and buy_tag in [ 'nfix_39 ']):
        #     if (
        #             (current_profit > 0)
        #             and (last_candle['fisher'] > 0.39075)
        #             and (last_candle['ha_high'] <= previous_candle_1['ha_high'])
        #             and (previous_candle_1['ha_high'] <= previous_candle_2['ha_high'])
        #             and (last_candle['ha_close'] <= previous_candle_1['ha_close'])
        #             and (last_candle['ema_4'] > last_candle['ha_close'])
        #             and (last_candle['ha_close'] * 0.99754 > last_candle['bb_middleband2'])
        #         ):
        #         return f"sell_scalp( {buy_tag})"

        # if (
        #         (current_profit < -0.05)
        #         and (last_candle['close'] < last_candle['ema_200'] * 0.988)
        #         and (last_candle['cmf'] < -0.046)
        #         and (((last_candle['ema_200'] - last_candle['close']) / last_candle['close']) < 0.022)
        #         and last_candle['rsi'] > previous_candle_1['rsi']
        #         and (last_candle['rsi'] > (last_candle['rsi_1h'] + 10.0))
        #     ):
        #     return f"sell_stoploss_u_e_1( {buy_tag})"

        # # stoploss - deadfish
        # if (    (current_profit < self.sell_deadfish_profit.value)
        #         and (last_candle['close'] < last_candle['ema_200'])
        #         and (last_candle['bb_width'] < self.sell_deadfish_bb_width.value)
        #         and (last_candle['close'] > last_candle['bb_middleband2'] * self.sell_deadfish_bb_factor.value)
        #         and (last_candle['volume_mean_12'] < last_candle['volume_mean_24'] * self.sell_deadfish_volume_factor.value)
        #     ):
        #     return f"sell_stoploss_deadfish( {buy_tag})"

        # # stoploss - bleeding
        # #if (    (current_profit < -0.05)
        #         #and (last_candle['close'] < last_candle['ema_200'])
        #         #and (last_candle['cti_mean_24'] < self.sell_bleeding_cti.value)
        #         #and (last_candle['r_14_mean_24'] < self.sell_bleeding_r14.value)
        #         #and (last_candle['volume_mean_12'] < last_candle['volume_mean_24'] * self.sell_bleeding_volume_factor.value)
        #     #):
        #     #return f"sell_stoploss_bleeding( {buy_tag})"

        # return None

    ## Confirm Entry
    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float, time_in_force: str, **kwargs) -> bool:

        # dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)

        # max_slip = self.max_slip.value

        # if(len(dataframe) < 1):
        #     return False

        # dataframe = dataframe.iloc[-1].squeeze()
        # if ((rate > dataframe['close'])) :

        #     slippage = ( (rate / dataframe['close']) - 1 ) * 100

        #     if slippage < max_slip:
        #         return True
        #     else:
        #         return False

        return False

    ############################################################################

    def normal_tf_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        # Basic OHLCV
        dataframe['open_1m'] = dataframe['open']
        dataframe['high_1m'] = dataframe['high']
        dataframe['low_1m'] = dataframe['low']
        dataframe['close_1m'] = dataframe['close']
        dataframe['volume_1m'] = dataframe['volume']
        dataframe['date_1m'] = dataframe['date']

        # Bollinger bands
        bollinger2 = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2)
        dataframe['bb_lowerband2_1m'] = bollinger2['lower']
        dataframe['bb_middleband2_1m'] = bollinger2['mid']
        dataframe['bb_upperband2_1m'] = bollinger2['upper']

        bollinger3 = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=3)
        dataframe['bb_lowerband3_1m'] = bollinger3['lower']
        dataframe['bb_middleband3_1m'] = bollinger3['mid']
        dataframe['bb_upperband3_1m'] = bollinger3['upper']

        # CMF
        dataframe['cmf_1m'] = chaikin_money_flow(dataframe, 20)

        # RSI & MA RSI & MSI OSCILLATOR
        dataframe['rsi_14_1m'] = ta.RSI(dataframe, timeperiod=14)
        dataframe['mrsi_7_1m'] = ta.SMA(dataframe['rsi_14_1m'], timeperiod=7)
        dataframe['rsi_osc_1m'] = (dataframe['rsi_14_1m'] - dataframe['mrsi_7_1m']) / dataframe['mrsi_7_1m'] * 100

        # Heiken Ashi
        heikinashi = qtpylib.heikinashi(dataframe)
        dataframe['ha_open_1m'] = heikinashi['open']
        dataframe['ha_close_1m'] = heikinashi['close']
        dataframe['ha_high_1m'] = heikinashi['high']
        dataframe['ha_low_1m'] = heikinashi['low']

        # VWAP
        dataframe['vwap_1m'] = qtpylib.rolling_vwap(dataframe)

        # VWAP OSCILLATOR
        dataframe['vwap_osc_1m'] = (dataframe['close'] - dataframe['vwap_1m']) / dataframe['vwap_1m'] * 100

        # OBV
        dataframe['obv_1m'] = ta.OBV(dataframe['close'], dataframe['volume'])

        # OBV HA
        dataframe['ha_obv_1m'] = ta.OBV(dataframe['ha_close_1m'], dataframe['volume'])

        # OBV MA
        dataframe['mobv_3_1m'] = ta.SMA(dataframe['ha_obv_1m'], timeperiod=3)

        # OBV OSCILLATOR
        dataframe['obv_osc_1m'] = (dataframe['ha_obv_1m'] - dataframe['mobv_3_1m']) / dataframe['mobv_3_1m'] * 100

        # Volume Oscilator
        dataframe['vma_20_1m'] = ta.SMA(dataframe['volume'], timeperiod=20)
        dataframe['vol_osc_1m'] = (dataframe['volume'] - dataframe['vma_20_1m']) / dataframe['vma_20_1m'] * 100

        # MACD
        macd = ta.MACD(dataframe, 20, 5)
        dataframe['macd_1m'] = macd['macd']
        dataframe['macd_signal_1m'] = macd['macdsignal']
        dataframe['macd_histogram_1m'] = macd['macdhist']
        dataframe['macd_osc_1m'] = dataframe['macd_histogram_1m'] / dataframe['close_1m'] * 100

        # SMA
        dataframe['sma_6_1m'] = ta.SMA(dataframe, timeperiod=6)
        dataframe['sma_9_1m'] = ta.SMA(dataframe, timeperiod=9)
        dataframe['sma_30_1m'] = ta.SMA(dataframe, timeperiod=30)
        dataframe['sma_60_1m'] = ta.SMA(dataframe, timeperiod=30)
        dataframe['sma_100_1m'] = ta.SMA(dataframe, timeperiod=100)

        # SMA OSCILLATOR
        dataframe['sma_6_osc_1m'] = (dataframe['close'] - dataframe['sma_6_1m']) / dataframe['sma_6_1m'] * 100
        dataframe['sma_9_osc_1m'] = (dataframe['close'] - dataframe['sma_9_1m']) / dataframe['sma_9_1m'] * 100
        dataframe['sma_30_osc_1m'] = (dataframe['close'] - dataframe['sma_30_1m']) / dataframe['sma_30_1m'] * 100
        dataframe['sma_60_osc_1m'] = (dataframe['close'] - dataframe['sma_60_1m']) / dataframe['sma_60_1m'] * 100
        dataframe['sma_100_osc_1m'] = (dataframe['close'] - dataframe['sma_100_1m']) / dataframe['sma_100_1m'] * 100

        # Stochastic Slow
        stoch = ta.STOCH(dataframe)
        dataframe['stoch_slowd_1m'] = stoch['slowd']
        dataframe['stoch_slowk_1m'] = stoch['slowk']
        dataframe['stoch_slow_osc_1m'] = (dataframe['stoch_slowk_1m'] - dataframe['stoch_slowd_1m']) / dataframe['stoch_slowd_1m'] * 100

        # Stochastic Fast
        stoch_fast = ta.STOCHF(dataframe)
        dataframe["stoch_fastd_1m"] = stoch_fast["fastd"]
        dataframe["stoch_fastk_1m"] = stoch_fast["fastk"]

        # Pattern Recognition - Bullish candlestick patterns
        # ------------------------------------
        # # Hammer: values [0, 100]
        # dataframe['CDLHAMMER'] = ta.CDLHAMMER(dataframe)
        # # Inverted Hammer: values [0, 100]
        # dataframe['CDLINVERTEDHAMMER'] = ta.CDLINVERTEDHAMMER(dataframe)
        # # Dragonfly Doji: values [0, 100]
        # dataframe['CDLDRAGONFLYDOJI'] = ta.CDLDRAGONFLYDOJI(dataframe)
        # # Piercing Line: values [0, 100]
        # dataframe['CDLPIERCING'] = ta.CDLPIERCING(dataframe) # values [0, 100]
        # # Morningstar: values [0, 100]
        # dataframe['CDLMORNINGSTAR'] = ta.CDLMORNINGSTAR(dataframe) # values [0, 100]
        # # Three White Soldiers: values [0, 100]
        # dataframe['CDL3WHITESOLDIERS'] = ta.CDL3WHITESOLDIERS(dataframe) # values [0, 100]

        # Pattern Recognition - Bearish candlestick patterns
        # ------------------------------------
        # # Hanging Man: values [0, 100]
        # dataframe['CDLHANGINGMAN'] = ta.CDLHANGINGMAN(dataframe)
        # # Shooting Star: values [0, 100]
        # dataframe['CDLSHOOTINGSTAR'] = ta.CDLSHOOTINGSTAR(dataframe)
        # # Gravestone Doji: values [0, 100]
        # dataframe['CDLGRAVESTONEDOJI'] = ta.CDLGRAVESTONEDOJI(dataframe)
        # # Dark Cloud Cover: values [0, 100]
        # dataframe['CDLDARKCLOUDCOVER'] = ta.CDLDARKCLOUDCOVER(dataframe)
        # # Evening Doji Star: values [0, 100]
        # dataframe['CDLEVENINGDOJISTAR'] = ta.CDLEVENINGDOJISTAR(dataframe)
        # # Evening Star: values [0, 100]
        # dataframe['CDLEVENINGSTAR'] = ta.CDLEVENINGSTAR(dataframe)

        # Pattern Recognition - Bullish/Bearish candlestick patterns
        # ------------------------------------
        # # Three Line Strike: values [0, -100, 100]
        # dataframe['CDL3LINESTRIKE'] = ta.CDL3LINESTRIKE(dataframe)
        # # Spinning Top: values [0, -100, 100]
        # dataframe['CDLSPINNINGTOP'] = ta.CDLSPINNINGTOP(dataframe) # values [0, -100, 100]
        # # Engulfing: values [0, -100, 100]
        # dataframe['CDLENGULFING'] = ta.CDLENGULFING(dataframe) # values [0, -100, 100]
        # # Harami: values [0, -100, 100]
        # dataframe['CDLHARAMI'] = ta.CDLHARAMI(dataframe) # values [0, -100, 100]
        # # Three Outside Up/Down: values [0, -100, 100]
        # dataframe['CDL3OUTSIDE'] = ta.CDL3OUTSIDE(dataframe) # values [0, -100, 100]
        # # Three Inside Up/Down: values [0, -100, 100]
        # dataframe['CDL3INSIDE'] = ta.CDL3INSIDE(dataframe) # values [0, -100, 100]

        return dataframe

    ############################################################################

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        # The indicators for the 1h informative timeframe
        informative_1h = self.informative_1h_indicators(dataframe, metadata)
        dataframe = merge_informative_pair(dataframe, informative_1h, self.timeframe, self.inf_1h, ffill=True)

        # The indicators for the 5m informative timeframe
        informative_5m = self.informative_5m_indicators(dataframe, metadata)
        dataframe = merge_informative_pair(dataframe, informative_5m, self.timeframe, self.inf_5m, ffill=True)

        # The indicators for the normal (5m) timeframe
        dataframe = self.normal_tf_indicators(dataframe, metadata)
        
        # send data to the destination server
        postData = {
            "pair": metadata['pair'],
            "strategy": self.config['strategy'],
            "data": dataframe.iloc[-1:].to_json(orient = "records")
        }
        requests.post('http://192.168.1.7:9000/data-gateway/submit-data', json=postData)
        # requests.post('http://10.25.96.7:8999/data-gateway/submit-data', json=postData)
        
        return dataframe

    def populate_buy_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # disable all buy conditions
        # if 1==1:
        dataframe.loc[:, 'buy'] = 0
        return dataframe

        # conditions = []
        # dataframe.loc[:, 'buy_tag'] = ''

        # is_dip = (
        #         (dataframe[f'rmi_length_{self.buy_rmi_length.value}'] < self.buy_rmi.value) &
        #         (dataframe[f'cci_length_{self.buy_cci_length.value}'] <= self.buy_cci.value) &
        #         (dataframe['srsi_fk'] < self.buy_srsi_fk.value)
        #     )

        # is_sqzOff = (
        #         (dataframe['bb_lowerband2'] < dataframe['kc_lowerband_28_1']) &
        #         (dataframe['bb_upperband2'] > dataframe['kc_upperband_28_1'])
        #     )

        # is_break = (

        #         (dataframe['bb_delta'] > self.buy_bb_delta.value) &
        #         (dataframe['bb_width'] > self.buy_bb_width.value) &
        #         (dataframe['closedelta'] > dataframe['close'] * self.buy_closedelta.value / 1000 ) &    # from BinH
        #         (dataframe['close'] < dataframe['bb_lowerband3'] * self.buy_bb_factor.value)
        #     )

        # is_local_uptrend = (                                                                            # from NFI next gen, credit goes to @iterativ
        #         (dataframe['ema_26'] > dataframe['ema_12']) &
        #         (dataframe['ema_26'] - dataframe['ema_12'] > dataframe['open'] * self.buy_ema_diff.value) &
        #         (dataframe['ema_26'].shift() - dataframe['ema_12'].shift() > dataframe['open'] / 100) &
        #         (dataframe['close'] < dataframe['bb_lowerband2'] * self.buy_bb_factor.value) &
        #         (dataframe['closedelta'] > dataframe['close'] * self.buy_closedelta.value / 1000 )
        #     )

        # is_local_dip = (
        #         (dataframe['ema_26'] > dataframe['ema_12']) &
        #         (dataframe['ema_26'] - dataframe['ema_12'] > dataframe['open'] * self.buy_ema_diff_local_dip.value) &
        #         (dataframe['ema_26'].shift() - dataframe['ema_12'].shift() > dataframe['open'] / 100) &
        #         (dataframe['close'] < dataframe['ema_20'] * self.buy_ema_high_local_dip.value) &
        #         (dataframe['rsi'] < self.buy_rsi_local_dip.value) &
        #         (dataframe['crsi'] > self.buy_crsi_local_dip.value) &
        #         (dataframe['closedelta'] > dataframe['close'] * self.buy_closedelta_local_dip.value / 1000 )
        #     )

        # is_ewo = (                                                                                      # from SMA offset
        #         (dataframe['rsi_fast'] < self.buy_rsi_fast.value) &
        #         (dataframe['close'] < dataframe['ema_8'] * self.buy_ema_low.value) &
        #         (dataframe['EWO'] > self.buy_ewo.value) &
        #         (dataframe['close'] < dataframe['ema_16'] * self.buy_ema_high.value) &
        #         (dataframe['rsi'] < self.buy_rsi.value)
        #     )

        # is_ewo_2 = (
        #         (dataframe['ema_200_1h'] > dataframe['ema_200_1h'].shift(12)) &
        #         (dataframe['ema_200_1h'].shift(12) > dataframe['ema_200_1h'].shift(24)) &
        #         (dataframe['rsi_fast'] < self.buy_rsi_fast_ewo_2.value) &
        #         (dataframe['close'] < dataframe['ema_8'] * self.buy_ema_low_2.value) &
        #         (dataframe['EWO'] > self.buy_ewo_high_2.value) &
        #         (dataframe['close'] < dataframe['ema_16'] * self.buy_ema_high_2.value) &
        #         (dataframe['rsi'] < self.buy_rsi_ewo_2.value)
        #     )

        # is_r_deadfish = (                                                                               # reverse deadfish
        #         (dataframe['ema_100'] < dataframe['ema_200'] * self.buy_r_deadfish_ema.value) &
        #         (dataframe['bb_width'] > self.buy_r_deadfish_bb_width.value) &
        #         (dataframe['close'] < dataframe['bb_middleband2'] * self.buy_r_deadfish_bb_factor.value) &
        #         (dataframe['volume_mean_12'] > dataframe['volume_mean_24'] * self.buy_r_deadfish_volume_factor.value) &
        #         (dataframe['cti'] < self.buy_r_deadfish_cti.value) &
        #         (dataframe['r_14'] < self.buy_r_deadfish_r14.value)
        #     )

        # is_clucHA = (
        #         (dataframe['rocr_1h'] > self.buy_clucha_rocr_1h.value ) &
        #         (
        #                 (dataframe['bb_lowerband2_40'].shift() > 0) &
        #                 (dataframe['bb_delta_cluc'] > dataframe['ha_close'] * self.buy_clucha_bbdelta_close.value) &
        #                 (dataframe['ha_closedelta'] > dataframe['ha_close'] * self.buy_clucha_closedelta_close.value) &
        #                 (dataframe['tail'] < dataframe['bb_delta_cluc'] * self.buy_clucha_bbdelta_tail.value) &
        #                 (dataframe['ha_close'] < dataframe['bb_lowerband2_40'].shift()) &
        #                 (dataframe['ha_close'] < dataframe['ha_close'].shift())
        #         )
        #     )

        # is_cofi = (                                                                         # Modified from cofi, credit goes to original author "slack user CofiBit"
        #         (dataframe['open'] < dataframe['ema_8'] * self.buy_ema_cofi.value) &
        #         (qtpylib.crossed_above(dataframe['fastk'], dataframe['fastd'])) &
        #         (dataframe['fastk'] < self.buy_fastk.value) &
        #         (dataframe['fastd'] < self.buy_fastd.value) &
        #         (dataframe['adx'] > self.buy_adx.value) &
        #         (dataframe['EWO'] > self.buy_ewo_high.value) &
        #         (dataframe['cti'] < self.buy_cofi_cti.value) &
        #         (dataframe['r_14'] < self.buy_cofi_r14.value)
        #     )

        # is_gumbo = (                                                                        # Modified from gumbo1, creadit goes to original author @raph92
        #         (dataframe['EWO'] < self.buy_gumbo_ewo_low.value) &
        #         (dataframe['bb_middleband2_1h'] >= dataframe['T3_1h']) &
        #         (dataframe['T3'] <= dataframe['ema_8'] * self.buy_gumbo_ema.value) &
        #         (dataframe['cti'] < self.buy_gumbo_cti.value) &
        #         (dataframe['r_14'] < self.buy_gumbo_r14.value)
        #     )

        # is_sqzmom = (                                                                       # Modified from squeezeMomentum, credit goes to original author @LazyBear of TradingView
        #         (is_sqzOff) &
        #         (dataframe['linreg_val_20'].shift(2) > dataframe['linreg_val_20'].shift(1)) &
        #         (dataframe['linreg_val_20'].shift(1) < dataframe['linreg_val_20']) &
        #         (dataframe['linreg_val_20'] < 0) &
        #         (dataframe['close'] < dataframe['ema_13'] * self.buy_sqzmom_ema.value) &
        #         (dataframe['EWO'] < self.buy_sqzmom_ewo.value) &
        #         (dataframe['r_14'] < self.buy_sqzmom_r14.value)
        #     )

        # # NFI quick mode, credit goes to @iterativ
        # is_nfi_13 = (
        #         (dataframe['ema_50_1h'] > dataframe['ema_100_1h']) &
        #         (dataframe['close'] < dataframe['sma_30'] * 0.99) &
        #         (dataframe['cti'] < -0.92) &
        #         (dataframe['EWO'] < -5.585) &
        #         (dataframe['cti_1h'] < -0.88) &
        #         (dataframe['crsi_1h'] > 10.0)
        #     )

        # is_nfi_32 = (  # NFIX 26
        #         (dataframe['rsi_slow'] < dataframe['rsi_slow'].shift(1)) &
        #         (dataframe['rsi_fast'] < 46) &
        #         (dataframe['rsi'] > 25.0) &
        #         (dataframe['close'] < dataframe['sma_15'] * 0.93) &
        #         (dataframe['cti'] < -0.9)
        #     )

        # is_nfi_33 = (
        #         (dataframe['close'] < (dataframe['ema_13'] * 0.978)) &
        #         (dataframe['EWO'] > 8) &
        #         (dataframe['cti'] < -0.88) &
        #         (dataframe['rsi'] < 32) &
        #         (dataframe['r_14'] < -98.0) &
        #         (dataframe['volume'] < (dataframe['volume_mean_4'] * 2.5))
        #     )

        # is_nfi_38 = (
        #         (dataframe['pm'] > dataframe['pmax_thresh']) &
        #         (dataframe['close'] < dataframe['sma_75'] * 0.98) &
        #         (dataframe['EWO'] < -4.4) &
        #         (dataframe['cti'] < -0.95) &
        #         (dataframe['r_14'] < -97) &
        #         (dataframe['crsi_1h'] > 0.5)
        #     )

        # is_nfix_5 = (
        #         (dataframe['ema_200_1h'] > dataframe['ema_200_1h'].shift(12)) &
        #         (dataframe['ema_200_1h'].shift(12) > dataframe['ema_200_1h'].shift(24)) &
        #         (dataframe['close'] < dataframe['sma_75'] * 0.932) &
        #         (dataframe['EWO'] > 3.6) &
        #         (dataframe['cti'] < -0.9) &
        #         (dataframe['r_14'] < -97.0)
        #     )

        # is_nfix_39 = (
        #         (dataframe['ema_200_1h'] > dataframe['ema_200_1h'].shift(12)) &
        #         (dataframe['ema_200_1h'].shift(12) > dataframe['ema_200_1h'].shift(24)) &
        #         (dataframe['bb_lowerband2_40'].shift().gt(0)) &
        #         (dataframe['bb_delta_cluc'].gt(dataframe['close'] * 0.056)) &
        #         (dataframe['closedelta'].gt(dataframe['close'] * 0.01)) &
        #         (dataframe['tail'].lt(dataframe['bb_delta_cluc'] * 0.5)) &
        #         (dataframe['close'].lt(dataframe['bb_lowerband2_40'].shift())) &
        #         (dataframe['close'].le(dataframe['close'].shift())) &
        #         (dataframe['close'] > dataframe['ema_13'] * self.buy_nfix_39_ema.value)
        #     )

        # is_nfix_49 = (
        #         (dataframe['ema_26'].shift(3) > dataframe['ema_12'].shift(3)) &
        #         (dataframe['ema_26'].shift(3) - dataframe['ema_12'].shift(3) > dataframe['open'].shift(3) * 0.032) &
        #         (dataframe['ema_26'].shift(9) - dataframe['ema_12'].shift(9) > dataframe['open'].shift(3) / 100) &
        #         (dataframe['close'].shift(3) < dataframe['ema_20'].shift(3) * 0.916) &
        #         (dataframe['rsi'].shift(3) < 32.5) &
        #         (dataframe['crsi'].shift(3) > 18.0) &
        #         (dataframe['cti'] < self.buy_nfix_49_cti.value) &
        #         (dataframe['r_14'] < self.buy_nfix_49_r14.value)
        #     )

        # is_nfi7_33 = (
        #         (dataframe['moderi_96']) &
        #         (dataframe['cti'] < -0.88) &
        #         (dataframe['close'] < (dataframe['ema_13'] * 0.988)) &
        #         (dataframe['EWO'] > 6.4) &
        #         (dataframe['rsi'] < 32.0) &
        #         (dataframe['volume'] < (dataframe['volume_mean_4'] * 2.0))
        #     )

        # is_nfi7_37 = (
        #         (dataframe['pm'] > dataframe['pmax_thresh']) &
        #         (dataframe['close'] < dataframe['sma_75'] * 0.98) &
        #         (dataframe['EWO'] > 9.8) &
        #         (dataframe['rsi'] < 56.0) &
        #         (dataframe['cti'] < -0.7) &
        #         (dataframe['safe_dump_50_1h'])
        #     )

        # is_additional_check = (
        #         (dataframe['roc_1h'] < self.buy_roc_1h.value) &
        #         (dataframe['bb_width_1h'] < self.buy_bb_width_1h.value)
        #     )

        # ## Additional Check
        # is_BB_checked = is_dip & is_break

        # ## Condition Append
        # conditions.append(is_BB_checked)                                           # ~2.32 / 91.1% / 46.27%      D
        # dataframe.loc[is_BB_checked, 'buy_tag'] += 'bb '

        # conditions.append(is_local_uptrend)                                        # ~3.28 / 92.4% / 69.72%
        # dataframe.loc[is_local_uptrend, 'buy_tag'] += 'local_uptrend '

        # conditions.append(is_local_dip)                                            # ~0.76 / 91.1% / 15.54%
        # dataframe.loc[is_local_dip, 'buy_tag'] += 'local_dip '

        # conditions.append(is_ewo)                                                  # ~0.92 / 92.0% / 43.74%      D
        # dataframe.loc[is_ewo, 'buy_tag'] += 'ewo '

        # conditions.append(is_ewo_2)                                                 # ~2.86 / 91.5% / 33.31%     D
        # dataframe.loc[is_ewo_2, 'buy_tag'] += 'ewo2 '

        # conditions.append(is_r_deadfish)                                           # ~0.99 / 86.9% / 21.93%      D
        # dataframe.loc[is_r_deadfish, 'buy_tag'] += 'r_deadfish '

        # conditions.append(is_clucHA)                                               # ~7.2 / 92.5% / 97.98%       D
        # dataframe.loc[is_clucHA, 'buy_tag'] += 'clucHA '

        # conditions.append(is_cofi)                                                 # ~0.4 / 94.4% / 9.59%        D
        # dataframe.loc[is_cofi, 'buy_tag'] += 'cofi '

        # conditions.append(is_gumbo)                                                # ~2.63 / 90.6% / 41.49%      D
        # dataframe.loc[is_gumbo, 'buy_tag'] += 'gumbo '

        # conditions.append(is_sqzmom)                                               # ~3.14 / 92.4% / 64.14%      D
        # dataframe.loc[is_sqzmom, 'buy_tag'] += 'sqzmom '

        # conditions.append(is_nfi_13)                                               # ~0.4 / 100%                 D
        # dataframe.loc[is_nfi_13, 'buy_tag'] += 'nfi_13 '

        # conditions.append(is_nfi_32)                                               # ~0.78 / 92.0 % / 37.41%     D
        # dataframe.loc[is_nfi_32, 'buy_tag'] += 'nfi_32 '

        # conditions.append(is_nfi_33)                                               # ~0.11 / 100%                D
        # dataframe.loc[is_nfi_33, 'buy_tag'] += 'nfi_33 '

        # conditions.append(is_nfi_38)                                               # ~1.13 / 88.5% / 31.34%      D
        # dataframe.loc[is_nfi_38, 'buy_tag'] += 'nfi_38 '

        # conditions.append(is_nfix_5)                                               # ~0.25 / 97.7% / 6.53%       D
        # dataframe.loc[is_nfix_5, 'buy_tag'] += 'nfix_5 '

        # conditions.append(is_nfix_39)                                              # ~5.33 / 91.8% / 58.57%      D
        # dataframe.loc[is_nfix_39, 'buy_tag'] += 'nfix_39 '

        # conditions.append(is_nfix_49)                                              # ~0.33 / 100% / 0%           D
        # dataframe.loc[is_nfix_49, 'buy_tag'] += 'nfix_49 '

        # conditions.append(is_nfi7_33)                                              # ~0.71 / 91.3% / 28.94%      D
        # dataframe.loc[is_nfi7_33, 'buy_tag'] += 'nfi7_33 '

        # conditions.append(is_nfi7_37)                                              # ~0.46 / 92.6% / 17.05%      D
        # dataframe.loc[is_nfi7_37, 'buy_tag'] += 'nfi7_37 '

        # if conditions:
        #     dataframe.loc[
        #                     is_additional_check
        #                     &
        #                     reduce(lambda x, y: x | y, conditions)

        #                 , 'buy' ] = 1

        # return dataframe

    def populate_sell_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # disable sell
        dataframe.loc[:, 'sell'] = 0

        return dataframe


# PMAX
def pmax(df, period, multiplier, length, MAtype, src):

    period = int(period)
    multiplier = int(multiplier)
    length = int(length)
    MAtype = int(MAtype)
    src = int(src)

    mavalue = f'MA_{MAtype}_{length}'
    atr = f'ATR_{period}'
    pm = f'pm_{period}_{multiplier}_{length}_{MAtype}'
    pmx = f'pmX_{period}_{multiplier}_{length}_{MAtype}'

    # MAtype==1 --> EMA
    # MAtype==2 --> DEMA
    # MAtype==3 --> T3
    # MAtype==4 --> SMA
    # MAtype==5 --> VIDYA
    # MAtype==6 --> TEMA
    # MAtype==7 --> WMA
    # MAtype==8 --> VWMA
    # MAtype==9 --> zema
    if src == 1:
        masrc = df["close"]
    elif src == 2:
        masrc = (df["high"] + df["low"]) / 2
    elif src == 3:
        masrc = (df["high"] + df["low"] + df["close"] + df["open"]) / 4

    if MAtype == 1:
        mavalue = ta.EMA(masrc, timeperiod=length)
    elif MAtype == 2:
        mavalue = ta.DEMA(masrc, timeperiod=length)
    elif MAtype == 3:
        mavalue = ta.T3(masrc, timeperiod=length)
    elif MAtype == 4:
        mavalue = ta.SMA(masrc, timeperiod=length)
    elif MAtype == 5:
        mavalue = VIDYA(df, length=length)
    elif MAtype == 6:
        mavalue = ta.TEMA(masrc, timeperiod=length)
    elif MAtype == 7:
        mavalue = ta.WMA(df, timeperiod=length)
    elif MAtype == 8:
        mavalue = vwma(df, length)
    elif MAtype == 9:
        mavalue = zema(df, period=length)

    df[atr] = ta.ATR(df, timeperiod=period)
    df['basic_ub'] = mavalue + ((multiplier/10) * df[atr])
    df['basic_lb'] = mavalue - ((multiplier/10) * df[atr])


    basic_ub = df['basic_ub'].values
    final_ub = np.full(len(df), 0.00)
    basic_lb = df['basic_lb'].values
    final_lb = np.full(len(df), 0.00)

    for i in range(period, len(df)):
        final_ub[i] = basic_ub[i] if (
            basic_ub[i] < final_ub[i - 1]
            or mavalue[i - 1] > final_ub[i - 1]) else final_ub[i - 1]
        final_lb[i] = basic_lb[i] if (
            basic_lb[i] > final_lb[i - 1]
            or mavalue[i - 1] < final_lb[i - 1]) else final_lb[i - 1]

    df['final_ub'] = final_ub
    df['final_lb'] = final_lb

    pm_arr = np.full(len(df), 0.00)
    for i in range(period, len(df)):
        pm_arr[i] = (
            final_ub[i] if (pm_arr[i - 1] == final_ub[i - 1]
                                    and mavalue[i] <= final_ub[i])
        else final_lb[i] if (
            pm_arr[i - 1] == final_ub[i - 1]
            and mavalue[i] > final_ub[i]) else final_lb[i]
        if (pm_arr[i - 1] == final_lb[i - 1]
            and mavalue[i] >= final_lb[i]) else final_ub[i]
        if (pm_arr[i - 1] == final_lb[i - 1]
            and mavalue[i] < final_lb[i]) else 0.00)

    pm = Series(pm_arr)

    # Mark the trend direction up/down
    pmx = np.where((pm_arr > 0.00), np.where((mavalue < pm_arr), 'down',  'up'), np.NaN)

    return pm, pmx

# Mom DIV
def momdiv(dataframe: DataFrame, mom_length: int = 10, bb_length: int = 20, bb_dev: float = 2.0, lookback: int = 30) -> DataFrame:
    mom: Series = ta.MOM(dataframe, timeperiod=mom_length)
    upperband, middleband, lowerband = ta.BBANDS(mom, timeperiod=bb_length, nbdevup=bb_dev, nbdevdn=bb_dev, matype=0)
    buy = qtpylib.crossed_below(mom, lowerband)
    sell = qtpylib.crossed_above(mom, upperband)
    hh = dataframe['high'].rolling(lookback).max()
    ll = dataframe['low'].rolling(lookback).min()
    coh = dataframe['high'] >= hh
    col = dataframe['low'] <= ll
    df = DataFrame({
            "momdiv_mom": mom,
            "momdiv_upperb": upperband,
            "momdiv_lowerb": lowerband,
            "momdiv_buy": buy,
            "momdiv_sell": sell,
            "momdiv_coh": coh,
            "momdiv_col": col,
        }, index=dataframe['close'].index)
    return df

def T3(dataframe, length=5):
    """
    T3 Average by HPotter on Tradingview
    https://www.tradingview.com/script/qzoC9H1I-T3-Average/
    """
    df = dataframe.copy()

    df['xe1'] = ta.EMA(df['close'], timeperiod=length)
    df['xe2'] = ta.EMA(df['xe1'], timeperiod=length)
    df['xe3'] = ta.EMA(df['xe2'], timeperiod=length)
    df['xe4'] = ta.EMA(df['xe3'], timeperiod=length)
    df['xe5'] = ta.EMA(df['xe4'], timeperiod=length)
    df['xe6'] = ta.EMA(df['xe5'], timeperiod=length)
    b = 0.7
    c1 = -b * b * b
    c2 = 3 * b * b + 3 * b * b * b
    c3 = -6 * b * b - 3 * b - 3 * b * b * b
    c4 = 1 + 3 * b + b * b * b + 3 * b * b
    df['T3Average'] = c1 * df['xe6'] + c2 * df['xe5'] + c3 * df['xe4'] + c4 * df['xe3']

    return df['T3Average']
