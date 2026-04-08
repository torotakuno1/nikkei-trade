"""
NIKKEI v6 リアルタイム トレーディングエンジン v2
修正: 限月指定、disconnectedEventバインド

使い方:
  python v6_realtime_engine.py          # ペーパー(4002)
  python v6_realtime_engine.py --live   # 本番(4001) ※確認プロンプト付き
"""
import sys
import os
import json
import time
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
from ib_insync import *

try:
    from telegram_notify import TelegramNotifier
except ImportError:
    class TelegramNotifier:
        enabled = False
        def send(self,*a):pass
        def trade(self,*a):pass
        def exit(self,*a):pass
        def warn(self,*a):pass
        def error(self,*a):pass
        def startup(self,*a):pass
        def status(self,*a):pass

# ============================================================
# 設定
# ============================================================

CONFIG = {
    'paper_port': 4002,
    'live_port': 4001,
    'host': '127.0.0.1',
    'client_id': 10,

    # 先物 — 直近限月を手動で更新する（SQ前にロールオーバー）
    # 2026/6限月: 20260611, 次は2026/9限月: 20260910
    'symbol': 'N225M',
    'last_trade_date': '20260611',
    'exchange': 'OSE.JPN',
    'currency': 'JPY',

    # v6 パラメータ
    'min_score': 2,
    'adx_thresh': 20,
    'stop_loss': 300,
    'slope_thresh': 0.05,
    'dma_len': 20,

    # 運用
    'warmup_bars': 500,
    'qty': 1,

    # ファイル
    'state_file': 'v6_rt_state.json',
    'log_file': 'v6_rt_engine.log',
}

# ============================================================
# ロギング
# ============================================================

def setup_logging(log_file):
    logger = logging.getLogger('v6_rt')
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setLevel(logging.INFO)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger

# ============================================================
# Pine Script再帰関数
# ============================================================

def pine_ema(series, length):
    alpha = 2.0 / (length + 1)
    vals = series.values if hasattr(series, 'values') else np.array(series, dtype=float)
    result = np.full(len(vals), np.nan)
    started = False
    for i in range(len(vals)):
        if np.isnan(vals[i]):
            if started:
                result[i] = result[i-1]
            continue
        if not started:
            result[i] = vals[i]
            started = True
        else:
            result[i] = alpha * vals[i] + (1 - alpha) * result[i-1]
    return result


def pine_rma(series, length):
    alpha = 1.0 / length
    vals = series.values if hasattr(series, 'values') else np.array(series, dtype=float)
    result = np.full(len(vals), np.nan)
    non_nan = np.where(~np.isnan(vals))[0]
    if len(non_nan) < length:
        return result
    start = non_nan[0]
    if start + length > len(vals):
        return result
    seed = np.mean(vals[start:start+length])
    result[start + length - 1] = seed
    for i in range(start + length, len(vals)):
        if np.isnan(vals[i]):
            result[i] = result[i-1]
        else:
            result[i] = alpha * vals[i] + (1 - alpha) * result[i-1]
    return result

# ============================================================
# インジケーター管理
# ============================================================

class V6Indicators:

    def __init__(self):
        self.bars = []
        self._computed = False
        self.macd_hist = None
        self.rsi = None
        self.di_plus = None
        self.di_minus = None
        self.adx = None
        self.kvo = None
        self.ha_bullish = None
        self.ha_bearish = None
        self.vm_dir = None
        self.mtf_bullish = None
        self.mtf_bearish = None
        self.score_long = None
        self.score_short = None

    def add_bar(self, bar_dict):
        self.bars.append(bar_dict)
        self._computed = False

    def add_bars(self, bar_list):
        self.bars.extend(bar_list)
        self._computed = False

    @property
    def n(self):
        return len(self.bars)

    def compute_all(self):
        if self.n < 50:
            return
        o = np.array([b['open'] for b in self.bars], dtype=float)
        h = np.array([b['high'] for b in self.bars], dtype=float)
        l = np.array([b['low'] for b in self.bars], dtype=float)
        c = np.array([b['close'] for b in self.bars], dtype=float)
        v = np.array([b.get('volume', 0) for b in self.bars], dtype=float)

        # MACD
        ema_fast = pine_ema(pd.Series(c), 12)
        ema_slow = pine_ema(pd.Series(c), 26)
        macd_line = ema_fast - ema_slow
        signal_line = pine_ema(pd.Series(macd_line), 9)
        self.macd_hist = macd_line - signal_line

        # RSI
        delta = np.diff(c, prepend=np.nan)
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)
        avg_gain = pine_rma(pd.Series(gain), 14)
        avg_loss = pine_rma(pd.Series(loss), 14)
        with np.errstate(divide='ignore', invalid='ignore'):
            rs = avg_gain / avg_loss
        self.rsi = 100.0 - (100.0 / (1.0 + rs))

        # DMI / ADX
        up = np.diff(h, prepend=np.nan)
        down = -np.diff(l, prepend=np.nan)
        plus_dm = np.where((up > down) & (up > 0), up, 0.0)
        minus_dm = np.where((down > up) & (down > 0), down, 0.0)
        tr1 = h - l
        tr2 = np.abs(h - np.roll(c, 1)); tr2[0] = 0
        tr3 = np.abs(l - np.roll(c, 1)); tr3[0] = 0
        tr = np.maximum(np.maximum(tr1, tr2), tr3)
        atr14 = pine_rma(pd.Series(tr), 14)
        with np.errstate(divide='ignore', invalid='ignore'):
            self.di_plus = 100.0 * pine_rma(pd.Series(plus_dm), 14) / atr14
            self.di_minus = 100.0 * pine_rma(pd.Series(minus_dm), 14) / atr14
            dx = 100.0 * np.abs(self.di_plus - self.di_minus) / (self.di_plus + self.di_minus)
        self.adx = pine_rma(pd.Series(dx), 20)

        # KVO
        hlc3 = (h + l + c) / 3.0
        hlc3_diff = np.diff(hlc3, prepend=0)
        sv = np.where(hlc3_diff > 0, v, -v).astype(float)
        sv[0] = 0.0
        self.kvo = pine_ema(pd.Series(sv), 34) - pine_ema(pd.Series(sv), 55)

        # 平均足
        ha_close = (o + h + l + c) / 4.0
        ha_open = np.full(len(c), np.nan)
        ha_open[0] = (o[0] + c[0]) / 2.0
        for i in range(1, len(c)):
            ha_open[i] = (ha_open[i-1] + ha_close[i-1]) / 2.0
        self.ha_bullish = ha_close > ha_open
        self.ha_bearish = ha_close < ha_open

        # VM
        std_dev = pd.Series(c).rolling(26).std(ddof=0).values
        std_rising = np.zeros(len(c), dtype=bool)
        std_rising[1:] = std_dev[1:] > std_dev[:-1]
        adx_rising = np.zeros(len(c), dtype=bool)
        adx_rising[1:] = self.adx[1:] > self.adx[:-1]
        trend_active = std_rising & adx_rising
        bb_basis = pd.Series(c).rolling(21).mean().values
        bb_dev = 0.6 * pd.Series(c).rolling(21).std(ddof=0).values
        bb_upper = bb_basis + bb_dev
        bb_lower = bb_basis - bb_dev
        self.vm_dir = np.zeros(len(c), dtype=int)
        for i in range(1, len(c)):
            d = self.vm_dir[i-1]
            if np.isnan(bb_upper[i]) or np.isnan(bb_lower[i]):
                self.vm_dir[i] = d; continue
            if trend_active[i] and c[i] > bb_upper[i] and d != 1: d = 1
            if trend_active[i] and c[i] < bb_lower[i] and d != -1: d = -1
            if d == 1 and c[i] < bb_upper[i]: d = 0
            if d == -1 and c[i] > bb_lower[i]: d = 0
            self.vm_dir[i] = d

        # MTF (同一足)
        mtf_ema20 = pine_ema(pd.Series(c), 20)
        self.mtf_bullish = (c > mtf_ema20) & (self.macd_hist > 0)
        self.mtf_bearish = (c < mtf_ema20) & (self.macd_hist < 0)

        # NTスコア
        sig_macd = np.where(self.macd_hist > 0, 1, np.where(self.macd_hist < 0, -1, 0))
        sig_rsi = np.where(self.rsi > 50, 1, np.where(self.rsi < 50, -1, 0))
        sig_di = np.where(self.di_plus > self.di_minus, 1, np.where(self.di_plus < self.di_minus, -1, 0))
        sig_kvo = np.where(self.kvo > 0, 1, np.where(self.kvo < 0, -1, 0))
        self.score_long = (sig_macd==1).astype(int)+(sig_rsi==1).astype(int)+(sig_di==1).astype(int)+(sig_kvo==1).astype(int)
        self.score_short = (sig_macd==-1).astype(int)+(sig_rsi==-1).astype(int)+(sig_di==-1).astype(int)+(sig_kvo==-1).astype(int)

        self._computed = True

    def _crossover(self, a, b, idx):
        if idx < 1: return False
        if np.isnan(a[idx]) or np.isnan(b[idx]) or np.isnan(a[idx-1]) or np.isnan(b[idx-1]):
            return False
        return (a[idx] > b[idx]) and (a[idx-1] <= b[idx-1])

    def _crossunder(self, a, b, idx):
        return self._crossover(b, a, idx)

    def evaluate_signal(self, idx=-1):
        if not self._computed:
            self.compute_all()
        if not self._computed:
            return None, {}
        if idx < 0:
            idx = self.n + idx

        zero = np.zeros(self.n)
        fifty = np.full(self.n, 50.0)

        cross_up = (self._crossover(self.macd_hist, zero, idx) or
                    self._crossover(self.rsi, fifty, idx) or
                    self._crossover(self.di_plus, self.di_minus, idx))
        cross_dn = (self._crossunder(self.macd_hist, zero, idx) or
                    self._crossunder(self.rsi, fifty, idx) or
                    self._crossunder(self.di_plus, self.di_minus, idx))

        adx_ok = self.adx[idx] > CONFIG['adx_thresh']

        nt_long = (self.score_long[idx] >= CONFIG['min_score'] and
                   cross_up and self.ha_bullish[idx] and adx_ok)
        nt_short = (self.score_short[idx] >= CONFIG['min_score'] and
                    cross_dn and self.ha_bearish[idx] and adx_ok)

        state = {
            'nt_long': bool(nt_long), 'nt_short': bool(nt_short),
            'vm_dir': int(self.vm_dir[idx]),
            'mtf_bull': bool(self.mtf_bullish[idx]),
            'mtf_bear': bool(self.mtf_bearish[idx]),
            'score_long': int(self.score_long[idx]),
            'score_short': int(self.score_short[idx]),
            'adx': round(float(self.adx[idx]), 1),
            'rsi': round(float(self.rsi[idx]), 1),
            'macd_hist': round(float(self.macd_hist[idx]), 2),
        }

        long_sig = nt_long and self.vm_dir[idx] == 1 and self.mtf_bullish[idx]
        short_sig = nt_short and self.vm_dir[idx] == -1 and self.mtf_bearish[idx]

        if long_sig:
            return 'Long', state
        elif short_sig:
            return 'Short', state
        return None, state

# ============================================================
# 日足フィルター
# ============================================================

class DailyFilter:
    def __init__(self, dma_len=20, slope_thresh=0.05):
        self.dma_len = dma_len
        self.slope_thresh = slope_thresh
        self.dma_rising = False
        self.dma_falling = False
        self.slope_ok = False

    def update(self, bars):
        daily = {}
        for b in bars:
            dt = b['time']
            if isinstance(dt, str):
                dt = pd.Timestamp(dt)
            if hasattr(dt, 'date') and callable(dt.date):
                d = dt.date()
            elif hasattr(dt, 'date'):
                d = dt.date
            else:
                d = dt
            daily[d] = b['close']
        dates = sorted(daily.keys())
        closes = [daily[d] for d in dates]
        if len(closes) < self.dma_len + 1:
            return
        dma_today = np.mean(closes[-self.dma_len:])
        dma_yesterday = np.mean(closes[-(self.dma_len+1):-1])
        self.dma_rising = dma_today > dma_yesterday
        self.dma_falling = dma_today < dma_yesterday
        slope = (dma_today - dma_yesterday) / dma_yesterday * 100
        self.slope_ok = abs(slope) > self.slope_thresh

    def check_long(self):
        slope_long_ok = self.slope_ok or (not self.dma_rising)
        return self.dma_rising and slope_long_ok

    def check_short(self):
        slope_short_ok = self.slope_ok or (not self.dma_falling)
        return self.dma_falling and slope_short_ok

# ============================================================
# SQ週・LDN16フィルター
# ============================================================

def is_sq_week(dt):
    if isinstance(dt, str):
        dt = pd.Timestamp(dt)
    year, month = dt.year, dt.month
    for day in range(8, 15):
        try:
            d = datetime(year, month, day)
            if d.weekday() == 4:
                monday = d - timedelta(days=4)
                if monday.date() <= dt.date() <= d.date():
                    return True
                break
        except:
            pass
    return False


def is_london_16(dt):
    if isinstance(dt, str):
        dt = pd.Timestamp(dt)
    if hasattr(dt, 'hour'):
        utc_hour = dt.hour - 9
        if utc_hour < 0:
            utc_hour += 24
        month = dt.month
        if 4 <= month <= 9:
            london_hour = (utc_hour + 1) % 24
        else:
            london_hour = utc_hour
        return london_hour == 16
    return False

# ============================================================
# 状態永続化
# ============================================================

def save_state(state, filepath):
    with open(filepath, 'w') as f:
        json.dump(state, f, indent=2, default=str)

def load_state(filepath):
    if os.path.exists(filepath):
        with open(filepath, 'r') as f:
            return json.load(f)
    return {'position': 0, 'entry_price': 0, 'entry_time': None}

# ============================================================
# メインエンジン
# ============================================================

class V6RealtimeEngine:

    def __init__(self, is_live=False):
        self.is_live = is_live
        self.port = CONFIG['live_port'] if is_live else CONFIG['paper_port']
        self.ib = IB()
        self.contract = None
        self.indicators = V6Indicators()
        self.daily_filter = DailyFilter(CONFIG['dma_len'], CONFIG['slope_thresh'])
        self.state = load_state(CONFIG['state_file'])
        self.log = setup_logging(CONFIG['log_file'])
        self.streaming_bars = None
        self._needs_reconnect = False
        self.tg = TelegramNotifier()
        self._last_bar_time = datetime.now()
        self._error_1100_received = False
        self._health_sent_date = None
        self._needs_resubscribe = False

        self.log.info("=== v6 リアルタイムエンジン起動 ===")
        self.log.info(f"モード: {'ライブ' if is_live else 'ペーパー'} (port {self.port})")

    def connect(self):
        self.log.info(f"接続中: {CONFIG['host']}:{self.port}")
        self.ib.connect(CONFIG['host'], self.port, clientId=CONFIG['client_id'])
        accounts = self.ib.managedAccounts()
        self.log.info(f"接続OK: {accounts}")

        self.contract = Future(
            symbol=CONFIG['symbol'],
            lastTradeDateOrContractMonth=CONFIG['last_trade_date'],
            exchange=CONFIG['exchange'],
            currency=CONFIG['currency']
        )
        self.ib.qualifyContracts(self.contract)
        self.log.info(f"コントラクト: {self.contract.localSymbol} "
                      f"(conId={self.contract.conId}, 限月={self.contract.lastTradeDateOrContractMonth})")

        positions = self.ib.positions()
        for p in positions:
            if p.contract.symbol == CONFIG['symbol']:
                self.log.info(f"既存ポジション: {p.position}枚 @ {p.avgCost}")
                self.state['position'] = int(p.position)

        self.ib.disconnectedEvent += lambda: self._on_disconnect()
        self.ib.errorEvent += self._on_error

    def _on_disconnect(self):
        self.log.warning("=" * 50)
        self.log.warning("IB Gateway 切断検出!")
        self.tg.warn("v6 N225", "IB Gateway切断")
        self.log.warning("=" * 50)
        # フラグを立ててメインループに再接続を委譲
        self._needs_reconnect = True

    def _on_error(self, reqId, errorCode, errorString, contract):
        if errorCode == 10182:
            self.log.error(f"Error 10182: {errorString} (ストリーミング死亡)")
            self.tg.error("v6 N225", f"Error 10182: 再購読予約")
            self._needs_resubscribe = True
        elif errorCode == 1100:
            self.log.warning(f"Error 1100: 接続断 - {errorString}")
            self._error_1100_received = True
        elif errorCode == 1102:
            self.log.warning(f"Error 1102: 接続復旧 - {errorString}")
            if self._error_1100_received:
                self._error_1100_received = False
                self.log.info("1102受信: 再購読予約")
                self._needs_resubscribe = True

    def _resubscribe_streaming(self):
        try:
            if self.streaming_bars is not None:
                try:
                    self.ib.cancelHistoricalData(self.streaming_bars)
                    self.log.info("既存ストリーミングをキャンセル")
                except Exception as e:
                    self.log.warning(f"cancelHistoricalData失敗(無視): {e}")
            self._subscribe_bars()
            self._last_bar_time = datetime.now()
            self.log.info("ストリーミング再購読完了")
            self.tg.send("v6 N225 ストリーミング再購読OK")
        except Exception as e:
            self.log.error(f"再購読失敗: {e}")
            self.tg.error("v6 N225", f"再購読失敗: {e}")
            self._needs_reconnect = True

    def _is_trading_hours_n225(self):
        """N225マイクロ: 6:00-8:45 JST は休止"""
        now = datetime.now()
        h, m = now.hour, now.minute
        if h == 6: return False
        if h == 7: return False
        if h == 8 and m < 45: return False
        return True

    def _check_watchdog(self):
        """3時間(1Hバー x 3)バー無し → 取引時間内なら再購読"""
        elapsed = (datetime.now() - self._last_bar_time).total_seconds()
        if elapsed > 3 * 3600 and self._is_trading_hours_n225():
            self.log.warning(f"ウォッチドッグ: {elapsed/3600:.1f}時間バー無し -> 再購読")
            self.tg.warn("v6 N225", f"ウォッチドッグ発動: {elapsed/3600:.1f}h バー無し")
            self._resubscribe_streaming()

    def _reconnect(self):
        """メインループから呼ばれる再接続処理"""
        self._needs_reconnect = False
        wait = 10
        max_wait = 300
        attempt = 0

        while True:
            attempt += 1
            self.log.info(f"再接続試行 #{attempt} ({wait}秒後)...")
            time.sleep(wait)

            try:
                # 新しいIBオブジェクトを作成（event loop衝突回避）
                self.ib = IB()
                self.ib.connect(CONFIG['host'], self.port, clientId=CONFIG['client_id'])
                self.log.info(f"再接続成功! (試行#{attempt})")
                self.tg.send("v6 N225 再接続OK")

                # disconnectedEvent/errorEvent再登録
                self.ib.disconnectedEvent += lambda: self._on_disconnect()
                self.ib.errorEvent += self._on_error

                # コントラクト再取得
                self.contract = Future(
                    symbol=CONFIG['symbol'],
                    lastTradeDateOrContractMonth=CONFIG['last_trade_date'],
                    exchange=CONFIG['exchange'],
                    currency=CONFIG['currency']
                )
                self.ib.qualifyContracts(self.contract)

                # ポジション再同期
                positions = self.ib.positions()
                for p in positions:
                    if p.contract.symbol == CONFIG['symbol']:
                        self.log.info(f"ポジション再同期: {p.position}枚 @ {p.avgCost}")
                        self.state['position'] = int(p.position)
                save_state(self.state, CONFIG['state_file'])

                # 歴史データ再読み込み
                self.log.info("歴史データ再読み込み中...")
                self.indicators = V6Indicators()
                hist_bars = self.ib.reqHistoricalData(
                    self.contract, endDateTime='', durationStr='60 D',
                    barSizeSetting='1 hour', whatToShow='TRADES',
                    useRTH=False, formatDate=1, keepUpToDate=False
                )
                if hist_bars:
                    for bar in hist_bars:
                        self.indicators.add_bar({
                            'time': bar.date, 'open': bar.open, 'high': bar.high,
                            'low': bar.low, 'close': bar.close, 'volume': bar.volume
                        })
                    self.indicators.compute_all()
                    self.daily_filter.update(self.indicators.bars)
                    self.log.info(f"歴史データ復元: {self.indicators.n}バー")

                # バー購読再開（既存をキャンセルしてから）
                if self.streaming_bars is not None:
                    try: self.ib.cancelHistoricalData(self.streaming_bars)
                    except: pass
                self._subscribe_bars()
                self._last_bar_time = datetime.now()
                self._error_1100_received = False
                self._needs_resubscribe = False
                self.log.info("再接続完了 — 通常運用に復帰")
                return  # 成功

            except Exception as e:
                self.log.error(f"再接続試行 #{attempt} 失敗: {e}")
                wait = min(wait * 2, max_wait)

    def load_history(self):
        self.log.info("歴史データ取得中...")
        bars = self.ib.reqHistoricalData(
            self.contract,
            endDateTime='',
            durationStr='60 D',
            barSizeSetting='1 hour',
            whatToShow='TRADES',
            useRTH=False,
            formatDate=1,
            keepUpToDate=False
        )
        self.log.info(f"歴史データ: {len(bars)}バー取得")
        if len(bars) == 0:
            self.log.error("歴史データ取得失敗")
            return False

        for bar in bars:
            self.indicators.add_bar({
                'time': bar.date,
                'open': bar.open, 'high': bar.high,
                'low': bar.low, 'close': bar.close,
                'volume': bar.volume
            })

        self.indicators.compute_all()
        self.daily_filter.update(self.indicators.bars)

        last = self.indicators.bars[-1]
        self.log.info(f"初期化完了（{self.indicators.n}バー）")
        self.log.info(f"最終バー: {last['time']} O={last['open']} H={last['high']} L={last['low']} C={last['close']}")
        self.log.info(f"日足: rising={self.daily_filter.dma_rising} falling={self.daily_filter.dma_falling} slope_ok={self.daily_filter.slope_ok}")

        sig, state = self.indicators.evaluate_signal(-1)
        self.log.info(f"最新シグナル: {sig} | {state}")
        return True

    def _subscribe_bars(self):
        self.log.info("1H足ストリーミング開始...")
        self.streaming_bars = self.ib.reqHistoricalData(
            self.contract,
            endDateTime='',
            durationStr='2 D',
            barSizeSetting='1 hour',
            whatToShow='TRADES',
            useRTH=False,
            formatDate=1,
            keepUpToDate=True
        )
        self.streaming_bars.updateEvent += self._on_bar_update
        self.log.info("ストリーミング開始（keepUpToDate=True）")

    def _on_bar_update(self, bars, hasNewBar):
        if not hasNewBar:
            return
        self._last_bar_time = datetime.now()
        bar = bars[-2]
        bar_dict = {
            'time': bar.date,
            'open': bar.open, 'high': bar.high,
            'low': bar.low, 'close': bar.close,
            'volume': bar.volume
        }
        self.log.info(f"バー確定: {bar.date} O={bar.open} H={bar.high} L={bar.low} C={bar.close} V={bar.volume}")
        self.indicators.add_bar(bar_dict)
        self.indicators.compute_all()
        self.daily_filter.update(self.indicators.bars)
        self._evaluate_and_trade(bar_dict)

    def _evaluate_and_trade(self, bar):
        dt = bar['time']
        if isinstance(dt, str):
            dt = pd.Timestamp(dt)

        if is_london_16(dt):
            self.log.info("  → LDN16除外")
            return
        if is_sq_week(dt):
            self.log.info("  → SQ週除外")
            return

        signal, state = self.indicators.evaluate_signal(-1)
        self.log.info(f"  NT_L={state['nt_long']} NT_S={state['nt_short']} VM={state['vm_dir']} "
                      f"MTF_B={state['mtf_bull']} MTF_S={state['mtf_bear']} "
                      f"ADX={state['adx']} RSI={state['rsi']} MACD={state['macd_hist']}")

        pos = self.state['position']

        if pos > 0 and state['nt_short']:
            self.log.info("  ★ NT反転: ロング決済")
            self.tg.exit("v6 N225", f"Long決済(NT反転) @ {bar['close']}")
            self._close_position('Long')
            pos = 0
        elif pos < 0 and state['nt_long']:
            self.log.info("  ★ NT反転: ショート決済")
            self.tg.exit("v6 N225", f"Short決済(NT反転) @ {bar['close']}")
            self._close_position('Short')
            pos = 0

        if signal == 'Long' and not self.daily_filter.check_long():
            self.log.info("  → 日足フィルター: ロング不可")
            signal = None
        elif signal == 'Short' and not self.daily_filter.check_short():
            self.log.info("  → 日足フィルター: ショート不可")
            signal = None

        if signal == 'Long' and pos <= 0:
            if pos < 0:
                self._close_position('Short')
            self.log.info("  ★★★ ロングエントリー ★★★")
            self.tg.trade("v6 N225", f"LONG @ {bar['close']}")
            self._enter_position('Long', bar['close'])
        elif signal == 'Short' and pos >= 0:
            if pos > 0:
                self._close_position('Long')
            self.log.info("  ★★★ ショートエントリー ★★★")
            self.tg.trade("v6 N225", f"SHORT @ {bar['close']}")
            self._enter_position('Short', bar['close'])

    def _enter_position(self, direction, ref_price):
        action = 'BUY' if direction == 'Long' else 'SELL'
        sl_price = ref_price - CONFIG['stop_loss'] if direction == 'Long' else ref_price + CONFIG['stop_loss']

        bracket = self.ib.bracketOrder(
            action=action,
            quantity=CONFIG['qty'],
            limitPrice=ref_price,
            takeProfitPrice=ref_price + 99999 if direction == 'Long' else max(1, ref_price - 99999),
            stopLossPrice=sl_price
        )
        bracket.parent.orderType = 'MKT'
        bracket.parent.lmtPrice = 0

        account = self.ib.managedAccounts()[0]
        for order in bracket:
            order.account = account
            self.ib.placeOrder(self.contract, order)
            if order.orderType == 'STP':
                self.log.info(f"  発注: STP {order.action} {order.totalQuantity}枚 @ {sl_price}")
            elif order.orderType == 'MKT':
                self.log.info(f"  発注: MKT {order.action} {order.totalQuantity}枚")
            else:
                self.log.info(f"  発注: {order.orderType} {order.action} {order.totalQuantity}枚")

        self.state['position'] = 1 if direction == 'Long' else -1
        self.state['entry_price'] = ref_price
        self.state['entry_time'] = str(datetime.now())
        save_state(self.state, CONFIG['state_file'])
        self.log.info(f"  ポジション: {direction} @ ~{ref_price} SL={sl_price}")

    def _close_position(self, direction):
        action = 'SELL' if direction == 'Long' else 'BUY'
        open_orders = self.ib.openOrders()
        for order in open_orders:
            if order.parentId != 0:
                self.ib.cancelOrder(order)
                self.log.info(f"  子注文キャンセル: orderId={order.orderId} type={order.orderType}")
        close_order = MarketOrder(action, CONFIG['qty'])
        close_order.account = self.ib.managedAccounts()[0]
        self.ib.placeOrder(self.contract, close_order)
        self.state['position'] = 0
        self.state['entry_price'] = 0
        self.state['entry_time'] = None
        save_state(self.state, CONFIG['state_file'])
        self.log.info(f"  決済: MKT {action} {CONFIG['qty']}枚")

    def run(self):
        self.connect()
        if not self.load_history():
            self.log.error("歴史データ取得失敗。終了。")
            return
        self._subscribe_bars()
        self.log.info("=== リアルタイム監視開始 ===")
        self.tg.startup("v6 N225", f"port:{self.port} pos={self.state['position']}")
        self.log.info("Ctrl+C で停止")
        try:
            while True:
                try:
                    self.ib.sleep(1)
                    if self._needs_resubscribe:
                        self._needs_resubscribe = False
                        time.sleep(5)
                        self._resubscribe_streaming()
                    self._check_watchdog()
                    now = datetime.now()
                    if now.hour == 9 and self._health_sent_date != now.date():
                        self._health_sent_date = now.date()
                        last_bar_str = self._last_bar_time.strftime('%H:%M')
                        sig, st = self.indicators.evaluate_signal(-1) if self.indicators._computed else (None, {})
                        fc_str = f"macd={st.get('macd_hist', 'N/A')}"
                        self.tg.send(f"\u2705 v6 N225 稼働中 pos={self.state['position']} {fc_str} 最終バー={last_bar_str}")
                        self.log.info("ヘルスチェック通知送信")
                except (ConnectionError, OSError, asyncio.CancelledError):
                    self._needs_reconnect = True
                if self._needs_reconnect:
                    self._reconnect()
        except KeyboardInterrupt:
            self.log.info("停止シグナル受信")
        finally:
            save_state(self.state, CONFIG['state_file'])
            try:
                self.ib.disconnect()
            except Exception:
                pass
            self.log.info("切断完了")

# ============================================================
# エントリーポイント
# ============================================================

if __name__ == '__main__':
    is_live = '--live' in sys.argv
    if is_live:
        print("⚠️  ライブモードで起動します。本当によろしいですか？ (yes/no)")
        confirm = input().strip().lower()
        if confirm != 'yes':
            print("中止しました。")
            sys.exit(0)
    engine = V6RealtimeEngine(is_live=is_live)
    engine.run()
