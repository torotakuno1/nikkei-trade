"""
NIKKEI 案C リアルタイム トレーディングエンジン v1
案C: VM点灯 OR NTクロス ハイブリッド + VIXターム構造フィルター

v6との違い:
  - エントリー: (VM新規点灯 AND NTスコア同方向) OR (NTフルシグナル AND VM背景同方向)
  - エグジット: VM消灯 OR NT反転（早い方）
  - フィルター: D20MA方向 + LDN16 + VIX VR>1.00（SQ週なし、DMA傾きなし）
  - ADX Smoothing: 14（v6は20）

使い方:
  python caseC_realtime_engine.py          # ペーパー(4002)
  python caseC_realtime_engine.py --live   # 本番(4001)
"""
import sys
import os
import json
import time
import asyncio
import logging
from datetime import datetime, timedelta
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
    'client_id': 20,  # v6は10、案Cは20（同時起動用）

    # 先物
    'symbol': 'N225M',
    'last_trade_date': '20260611',
    'exchange': 'OSE.JPN',
    'currency': 'JPY',

    # 案C パラメータ（PKG A）
    'min_score': 2,
    'adx_thresh': 20,
    'adx_smoothing': 14,  # ★ v6は20、案Cは14
    'stop_loss': 300,
    'dma_len': 20,

    # VIXターム構造
    'vix_ratio_thresh': 1.00,

    # 運用
    'qty': 1,

    # ファイル
    'state_file': 'caseC_rt_state.json',
    'log_file': 'caseC_rt_engine.log',
}

# ============================================================
# ロギング
# ============================================================

def setup_logging(log_file):
    logger = logging.getLogger('caseC_rt')
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    fh = logging.FileHandler(log_file, encoding='utf-8')
    ch = logging.StreamHandler()
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
            if started: result[i] = result[i-1]
            continue
        if not started:
            result[i] = vals[i]; started = True
        else:
            result[i] = alpha * vals[i] + (1 - alpha) * result[i-1]
    return result

def pine_rma(series, length):
    alpha = 1.0 / length
    vals = series.values if hasattr(series, 'values') else np.array(series, dtype=float)
    result = np.full(len(vals), np.nan)
    non_nan = np.where(~np.isnan(vals))[0]
    if len(non_nan) < length: return result
    start = non_nan[0]
    if start + length > len(vals): return result
    result[start + length - 1] = np.mean(vals[start:start+length])
    for i in range(start + length, len(vals)):
        if np.isnan(vals[i]): result[i] = result[i-1]
        else: result[i] = alpha * vals[i] + (1 - alpha) * result[i-1]
    return result

# ============================================================
# 案Cインジケーター
# ============================================================

class CaseCIndicators:

    def __init__(self, adx_smoothing=14):
        self.bars = []
        self.adx_smoothing = adx_smoothing
        self._computed = False
        # 結果
        self.macd_hist = None
        self.rsi = None
        self.di_plus = None
        self.di_minus = None
        self.adx = None
        self.kvo = None
        self.ha_bullish = None
        self.ha_bearish = None
        self.vm_dir = None
        self.score_long = None
        self.score_short = None

    def add_bar(self, bar_dict):
        self.bars.append(bar_dict)
        self._computed = False

    @property
    def n(self):
        return len(self.bars)

    def compute_all(self):
        if self.n < 50: return
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
            self.rsi = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

        # DMI / ADX (adx_smoothing=14 for Case C)
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
        self.adx = pine_rma(pd.Series(dx), self.adx_smoothing)

        # KVO
        hlc3 = (h + l + c) / 3.0
        hlc3_diff = np.diff(hlc3, prepend=0)
        sv = np.where(hlc3_diff > 0, v, -v).astype(float); sv[0] = 0.0
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
        if np.isnan(a[idx]) or np.isnan(b[idx]) or np.isnan(a[idx-1]) or np.isnan(b[idx-1]): return False
        return (a[idx] > b[idx]) and (a[idx-1] <= b[idx-1])

    def _crossunder(self, a, b, idx):
        return self._crossover(b, a, idx)

    def evaluate_caseC(self, idx=-1):
        """
        案Cのシグナル評価
        返値: (entry_signal, exit_signal, state_dict)
          entry_signal: 'Long', 'Short', or None
          exit_signal: 'ExitLong', 'ExitShort', or None
        """
        if not self._computed:
            self.compute_all()
        if not self._computed:
            return None, None, {}
        if idx < 0:
            idx = self.n + idx

        zero = np.zeros(self.n)
        fifty = np.full(self.n, 50.0)

        # --- NTフルシグナル（クロス+スコア+平均足+ADX）---
        cross_up = (self._crossover(self.macd_hist, zero, idx) or
                    self._crossover(self.rsi, fifty, idx) or
                    self._crossover(self.di_plus, self.di_minus, idx) or
                    self._crossover(self.kvo, zero, idx))
        cross_dn = (self._crossunder(self.macd_hist, zero, idx) or
                    self._crossunder(self.rsi, fifty, idx) or
                    self._crossunder(self.di_plus, self.di_minus, idx) or
                    self._crossunder(self.kvo, zero, idx))

        adx_ok = self.adx[idx] > CONFIG['adx_thresh']

        ntFullLong = (self.score_long[idx] >= CONFIG['min_score'] and
                      cross_up and self.ha_bullish[idx] and adx_ok)
        ntFullShort = (self.score_short[idx] >= CONFIG['min_score'] and
                       cross_dn and self.ha_bearish[idx] and adx_ok)

        # --- NTスコアのみ（クロス不要）---
        ntScoreLong = self.score_long[idx] >= CONFIG['min_score'] and self.ha_bullish[idx]
        ntScoreShort = self.score_short[idx] >= CONFIG['min_score'] and self.ha_bearish[idx]

        # --- VM状態 ---
        vm_now = int(self.vm_dir[idx])
        vm_prev = int(self.vm_dir[idx-1]) if idx > 0 else 0

        vmBull = vm_now == 1
        vmBear = vm_now == -1
        vmEntryLong = vm_now == 1 and vm_prev != 1   # VM新規点灯
        vmEntryShort = vm_now == -1 and vm_prev != -1
        vmExitLong = vm_now == 0 and vm_prev == 1     # VM消灯
        vmExitShort = vm_now == 0 and vm_prev == -1

        # --- 案C エントリー: OR条件 ---
        vmTrigL = vmEntryLong and ntScoreLong   # VM新規点灯 AND NTスコア同方向
        vmTrigS = vmEntryShort and ntScoreShort
        ntTrigL = ntFullLong and vmBull          # NTフルシグナル AND VM背景同方向
        ntTrigS = ntFullShort and vmBear

        entry_long = vmTrigL or ntTrigL
        entry_short = vmTrigS or ntTrigS

        # --- 案C エグジット: VM消灯 OR NT反転 ---
        exit_long = vmExitLong or ntFullShort
        exit_short = vmExitShort or ntFullLong

        state = {
            'ntFullLong': bool(ntFullLong), 'ntFullShort': bool(ntFullShort),
            'ntScoreLong': bool(ntScoreLong), 'ntScoreShort': bool(ntScoreShort),
            'vm_dir': vm_now, 'vm_prev': vm_prev,
            'vmEntryLong': bool(vmEntryLong), 'vmEntryShort': bool(vmEntryShort),
            'vmExitLong': bool(vmExitLong), 'vmExitShort': bool(vmExitShort),
            'vmTrigL': bool(vmTrigL), 'vmTrigS': bool(vmTrigS),
            'ntTrigL': bool(ntTrigL), 'ntTrigS': bool(ntTrigS),
            'adx': round(float(self.adx[idx]), 1),
            'rsi': round(float(self.rsi[idx]), 1),
            'score_long': int(self.score_long[idx]),
            'score_short': int(self.score_short[idx]),
        }

        entry_signal = None
        if entry_long: entry_signal = 'Long'
        elif entry_short: entry_signal = 'Short'

        exit_signal = None
        if exit_long: exit_signal = 'ExitLong'
        if exit_short: exit_signal = 'ExitShort'

        return entry_signal, exit_signal, state

# ============================================================
# 日足フィルター（案C: 方向のみ、傾きなし）
# ============================================================

class DailyFilterCaseC:
    def __init__(self, dma_len=20):
        self.dma_len = dma_len
        self.dma_rising = False
        self.dma_falling = False

    def update(self, bars):
        daily = {}
        for b in bars:
            dt = b['time']
            if isinstance(dt, str): dt = pd.Timestamp(dt)
            if hasattr(dt, 'date') and callable(dt.date): d = dt.date()
            elif hasattr(dt, 'date'): d = dt.date
            else: d = dt
            daily[d] = b['close']
        dates = sorted(daily.keys())
        closes = [daily[d] for d in dates]
        if len(closes) < self.dma_len + 1: return
        dma_today = np.mean(closes[-self.dma_len:])
        dma_yesterday = np.mean(closes[-(self.dma_len+1):-1])
        self.dma_rising = dma_today > dma_yesterday
        self.dma_falling = dma_today < dma_yesterday

# ============================================================
# VIXターム構造フィルター
# ============================================================

class VIXFilter:
    """VIX3M/VIX比率でフィルター。IBKRからVIX/VIX3Mのスナップショットを取得。"""

    def __init__(self, ib, threshold=1.00):
        self.ib = ib
        self.threshold = threshold
        self.vix_ratio = None
        self.last_update = None

    def update(self):
        """VIXとVIX3Mの最新値を取得"""
        try:
            vix_contract = Index('VIX', 'CBOE', 'USD')
            self.ib.qualifyContracts(vix_contract)
            vix_data = self.ib.reqMktData(vix_contract, '', True, False)
            self.ib.sleep(2)

            vix3m_contract = Index('VIX3M', 'CBOE', 'USD')
            self.ib.qualifyContracts(vix3m_contract)
            vix3m_data = self.ib.reqMktData(vix3m_contract, '', True, False)
            self.ib.sleep(2)

            vix_val = vix_data.last if vix_data.last > 0 else vix_data.close
            vix3m_val = vix3m_data.last if vix3m_data.last > 0 else vix3m_data.close

            if vix_val > 0 and vix3m_val > 0:
                self.vix_ratio = vix3m_val / vix_val
                self.last_update = datetime.now()
                return True

            # snapshot might not be available, try close
            if vix_val > 0 and vix3m_val > 0:
                self.vix_ratio = vix3m_val / vix_val
                self.last_update = datetime.now()
                return True

        except Exception as e:
            logging.getLogger('caseC_rt').warning(f"VIXデータ取得失敗: {e}")

        return False

    def is_ok(self):
        if self.vix_ratio is None:
            return True  # データ取得失敗時はフィルターをパス（保守的）
        return self.vix_ratio > self.threshold

# ============================================================
# LDN16フィルター
# ============================================================

def is_london_16(dt):
    if isinstance(dt, str): dt = pd.Timestamp(dt)
    if hasattr(dt, 'hour'):
        utc_hour = dt.hour - 9
        if utc_hour < 0: utc_hour += 24
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

class CaseCRealtimeEngine:

    def __init__(self, is_live=False):
        self.is_live = is_live
        self.port = CONFIG['live_port'] if is_live else CONFIG['paper_port']
        self.ib = IB()
        self.contract = None
        self.indicators = CaseCIndicators(adx_smoothing=CONFIG['adx_smoothing'])
        self.daily_filter = DailyFilterCaseC(CONFIG['dma_len'])
        self.vix_filter = None  # connect後に初期化
        self.state = load_state(CONFIG['state_file'])
        self.log = setup_logging(CONFIG['log_file'])
        self.streaming_bars = None
        self._needs_reconnect = False
        self.tg = TelegramNotifier()

        self.log.info("=== 案C リアルタイムエンジン起動 ===")
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
        self.log.info(f"コントラクト: {self.contract.localSymbol} (conId={self.contract.conId})")

        # VIXフィルター初期化
        self.vix_filter = VIXFilter(self.ib, CONFIG['vix_ratio_thresh'])
        if self.vix_filter.update():
            self.log.info(f"VIX比率: {self.vix_filter.vix_ratio:.3f} (閾値>{CONFIG['vix_ratio_thresh']})")
        else:
            self.log.warning("VIXデータ取得失敗。フィルター無効化（パス扱い）")

        positions = self.ib.positions()
        for p in positions:
            if p.contract.symbol == CONFIG['symbol']:
                self.log.info(f"既存ポジション: {p.position}枚 @ {p.avgCost}")
                self.state['position'] = int(p.position)

        self.ib.disconnectedEvent += lambda: self._on_disconnect()

    def _on_disconnect(self):
        self.log.warning("=" * 50)
        self.log.warning("IB Gateway 切断検出!")
        self.tg.warn("案C N225", "IB Gateway切断")
        self.tg.warn("案C N225", "IB Gateway切断")
        self.log.warning("=" * 50)
        self._needs_reconnect = True

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
                self.ib = IB()
                self.ib.connect(CONFIG['host'], self.port, clientId=CONFIG['client_id'])
                self.log.info(f"再接続成功! (試行#{attempt})")
                self.tg.send("案C N225 再接続OK")

                self.ib.disconnectedEvent += lambda: self._on_disconnect()

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

                # VIXフィルター再初期化
                self.vix_filter = VIXFilter(self.ib, CONFIG['vix_ratio_thresh'])
                if self.vix_filter.update():
                    self.log.info(f"VIX比率再取得: {self.vix_filter.vix_ratio:.3f}")
                else:
                    self.log.warning("VIXデータ再取得失敗（パス扱い）")

                # 歴史データ再読み込み
                self.log.info("歴史データ再読み込み中...")
                self.indicators = CaseCIndicators(adx_smoothing=CONFIG['adx_smoothing'])
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

                # バー購読再開
                self._subscribe_bars()
                self.log.info("再接続完了 — 通常運用に復帰")
                return

            except Exception as e:
                self.log.error(f"再接続試行 #{attempt} 失敗: {e}")
                wait = min(wait * 2, max_wait)

    def load_history(self):
        self.log.info("歴史データ取得中...")
        bars = self.ib.reqHistoricalData(
            self.contract, endDateTime='', durationStr='60 D',
            barSizeSetting='1 hour', whatToShow='TRADES',
            useRTH=False, formatDate=1, keepUpToDate=False
        )
        self.log.info(f"歴史データ: {len(bars)}バー")
        if not bars:
            self.log.error("歴史データ取得失敗")
            return False

        for bar in bars:
            self.indicators.add_bar({
                'time': bar.date, 'open': bar.open, 'high': bar.high,
                'low': bar.low, 'close': bar.close, 'volume': bar.volume
            })
        self.indicators.compute_all()
        self.daily_filter.update(self.indicators.bars)

        last = self.indicators.bars[-1]
        self.log.info(f"初期化完了（{self.indicators.n}バー）")
        self.log.info(f"最終バー: {last['time']} C={last['close']}")
        self.log.info(f"日足: rising={self.daily_filter.dma_rising} falling={self.daily_filter.dma_falling}")

        _, _, state = self.indicators.evaluate_caseC(-1)
        self.log.info(f"最新状態: VM={state.get('vm_dir')} ADX={state.get('adx')}")
        return True

    def _subscribe_bars(self):
        self.log.info("1H足ストリーミング開始...")
        self.streaming_bars = self.ib.reqHistoricalData(
            self.contract, endDateTime='', durationStr='2 D',
            barSizeSetting='1 hour', whatToShow='TRADES',
            useRTH=False, formatDate=1, keepUpToDate=True
        )
        self.streaming_bars.updateEvent += self._on_bar_update
        self.log.info("ストリーミング開始")

    def _on_bar_update(self, bars, hasNewBar):
        if not hasNewBar: return
        bar = bars[-2]
        bar_dict = {
            'time': bar.date, 'open': bar.open, 'high': bar.high,
            'low': bar.low, 'close': bar.close, 'volume': bar.volume
        }
        self.log.info(f"バー確定: {bar.date} O={bar.open} H={bar.high} L={bar.low} C={bar.close}")
        self.indicators.add_bar(bar_dict)
        self.indicators.compute_all()
        self.daily_filter.update(self.indicators.bars)

        # VIXは1時間ごとに更新
        self.vix_filter.update()

        self._evaluate_and_trade(bar_dict)

    def _evaluate_and_trade(self, bar):
        dt = bar['time']
        if isinstance(dt, str): dt = pd.Timestamp(dt)

        if is_london_16(dt):
            self.log.info("  → LDN16除外")
            return

        entry_signal, exit_signal, state = self.indicators.evaluate_caseC(-1)

        self.log.info(f"  vmTrigL={state.get('vmTrigL')} ntTrigL={state.get('ntTrigL')} "
                      f"vmTrigS={state.get('vmTrigS')} ntTrigS={state.get('ntTrigS')} "
                      f"VM={state['vm_dir']} ADX={state['adx']}")

        pos = self.state['position']

        # --- エグジット ---
        if pos > 0 and exit_signal == 'ExitLong':
            reason = 'VM消灯' if state['vmExitLong'] else 'NT反転'
            self.log.info(f"  ★ エグジット: ロング決済 ({reason})")
            self.tg.exit("案C N225", "Long決済")
            self._close_position('Long')
            pos = 0
        elif pos < 0 and exit_signal == 'ExitShort':
            reason = 'VM消灯' if state['vmExitShort'] else 'NT反転'
            self.log.info(f"  ★ エグジット: ショート決済 ({reason})")
            self.tg.exit("案C N225", "Short決済")
            self._close_position('Short')
            pos = 0

        # --- フィルター ---
        if entry_signal == 'Long' and not self.daily_filter.dma_rising:
            self.log.info("  → 日足: ロング不可")
            entry_signal = None
        elif entry_signal == 'Short' and not self.daily_filter.dma_falling:
            self.log.info("  → 日足: ショート不可")
            entry_signal = None

        if entry_signal and not self.vix_filter.is_ok():
            self.log.info(f"  → VIXフィルター: VR={self.vix_filter.vix_ratio:.3f} < {CONFIG['vix_ratio_thresh']}")
            entry_signal = None

        # --- エントリー ---
        if entry_signal == 'Long' and pos <= 0:
            if pos < 0: self._close_position('Short')
            self.log.info("  ★★★ ロングエントリー ★★★")
            self.tg.trade("案C N225", f"LONG @ {bar['close']}")
            self._enter_position('Long', bar['close'])
        elif entry_signal == 'Short' and pos >= 0:
            if pos > 0: self._close_position('Long')
            self.log.info("  ★★★ ショートエントリー ★★★")
            self.tg.trade("案C N225", f"SHORT @ {bar['close']}")
            self._enter_position('Short', bar['close'])

    def _enter_position(self, direction, ref_price):
        action = 'BUY' if direction == 'Long' else 'SELL'
        sl_price = ref_price - CONFIG['stop_loss'] if direction == 'Long' else ref_price + CONFIG['stop_loss']

        bracket = self.ib.bracketOrder(
            action=action, quantity=CONFIG['qty'], limitPrice=ref_price,
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
                self.log.info(f"  発注: STP {order.action} @ {sl_price}")
            elif order.orderType == 'MKT':
                self.log.info(f"  発注: MKT {order.action} {order.totalQuantity}枚")

        self.state['position'] = 1 if direction == 'Long' else -1
        self.state['entry_price'] = ref_price
        self.state['entry_time'] = str(datetime.now())
        save_state(self.state, CONFIG['state_file'])
        self.log.info(f"  ポジション: {direction} @ ~{ref_price} SL={sl_price}")

    def _close_position(self, direction):
        action = 'SELL' if direction == 'Long' else 'BUY'
        for order in self.ib.openOrders():
            if order.parentId != 0:
                self.ib.cancelOrder(order)
                self.log.info(f"  子注文キャンセル: {order.orderId}")
        close_order = MarketOrder(action, CONFIG['qty'])
        close_order.account = self.ib.managedAccounts()[0]
        self.ib.placeOrder(self.contract, close_order)
        self.state['position'] = 0
        self.state['entry_price'] = 0
        self.state['entry_time'] = None
        save_state(self.state, CONFIG['state_file'])
        self.log.info(f"  決済: MKT {action}")

    def run(self):
        self.connect()
        if not self.load_history(): return
        self._subscribe_bars()
        self.log.info("=== 案C リアルタイム監視開始 ===")
        self.tg.startup("案C N225", f"port:{self.port} pos={self.state['position']}")
        self.log.info("Ctrl+C で停止")
        try:
            while True:
                try:
                    self.ib.sleep(1)
                except (ConnectionError, OSError, asyncio.CancelledError):
                    self._needs_reconnect = True
                if self._needs_reconnect:
                    self._reconnect()
        except KeyboardInterrupt:
            self.log.info("停止")
        finally:
            save_state(self.state, CONFIG['state_file'])
            try:
                self.ib.disconnect()
            except Exception:
                pass
            self.log.info("切断完了")

if __name__ == '__main__':
    is_live = '--live' in sys.argv
    if is_live:
        print("⚠️  ライブモードです。(yes/no)")
        if input().strip().lower() != 'yes': sys.exit(0)
    engine = CaseCRealtimeEngine(is_live=is_live)
    engine.run()
