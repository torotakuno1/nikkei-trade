#!/usr/bin/env python3
"""
N225ミニ 2H フェード執行エンジン v1.0
========================================
ロジック:
  - 日中セッション（9:00-15:00 JST）の2Hバー確定時にシグナル判定
  - 直近10本の2Hバーの高値/安値を参照
    - close > 直近10本の高値 → ショートエントリー（高値ブレイクをフェード）
    - close < 直近10本の安値 → ロングエントリー（安値ブレイクをフェード）
  - エグジット: 5本後のバー確定時に成行決済
  - 次バーopenでのエントリー（バー確定 → シグナル判定 → 即座に成行発注）
  - ポジションは最大1枚、SLなし

2Hバースロット（JST）:
  9:00-11:00（11:00確定）, 11:00-13:00（13:00確定）, 13:00-15:00（15:00確定）

使い方:
  python fade_2h_engine.py          # ペーパー(4002)
  python fade_2h_engine.py --live   # 本番(4001)

★ 事前確認が必要な設定:
  CONFIG['last_trade_date'] -- 直近限月（例: '202609'）を要更新
  Contract の symbol/exchange -- IB Contractsで N225M / OSE.JPN を確認
"""
import sys, time, json, logging
from datetime import datetime
from pathlib import Path

import pandas as pd
from ib_insync import IB, Future, MarketOrder, util

try:
    from telegram_notify import TelegramNotifier
except ImportError:
    class TelegramNotifier:
        enabled = False
        def send(self, *a): pass
        def trade(self, *a): pass
        def exit(self, *a): pass
        def warn(self, *a): pass
        def error(self, *a): pass
        def startup(self, *a): pass
        def status(self, *a): pass

# ─── ログ設定 ────────────────────────────────────────────────────────────────
LOG_DIR = Path("logs"); LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(
            LOG_DIR / f"fade_2h_{datetime.now().strftime('%Y%m%d')}.log",
            encoding='utf-8'
        ),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger('fade_2h')

# ─── 設定 ────────────────────────────────────────────────────────────────────
CONFIG = {
    'host': '127.0.0.1',
    'paper_port': 4002,
    'live_port': 4001,
    'client_id': 30,                    # v6=10, 案C=20, Gold=3 と重複しない
    # ★ 以下を直近限月に更新すること（YYYYMM または YYYYMMDD）
    'symbol': 'N225M',
    'exchange': 'OSE.JPN',
    'currency': 'JPY',
    'last_trade_date': '202606',        # ★ 要更新: 直近限月
    # データ取得
    'history_duration': '365 D',
    'bar_size': '1 hour',
    # ロジックパラメータ
    'lookback': 10,                     # シグナル計算の参照本数（直近N本の高値/安値）
    'exit_bars': 5,                     # エグジットまでのバー数
    'max_contracts': 1,                 # 最大ポジション（枚数）
    # 状態ファイル
    'state_file': 'fade_2h_state.json',
}

# ─── 日中セッション 2H スロット定義（JST） ────────────────────────────────
# 1H バーの時刻（hour） → 所属する 2H スロットの開始時（hour）
# 例: 9時台・10時台のバーは「スロット9（9:00-11:00）」に属する
SLOT_MAP = {
    9: 9,   # 9:00  bar → スロット 9:00-11:00
    10: 9,  # 10:00 bar → スロット 9:00-11:00
    11: 11, # 11:00 bar → スロット 11:00-13:00 ※同時に前スロット確定トリガー
    12: 11, # 12:00 bar → スロット 11:00-13:00（昼休み後）
    13: 13, # 13:00 bar → スロット 13:00-15:00 ※同時に前スロット確定トリガー
    14: 13, # 14:00 bar → スロット 13:00-15:00
}


# ─── ステート管理 ─────────────────────────────────────────────────────────────
def load_state(fp):
    p = Path(fp)
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return {
        'position': 0,           # 1=ロング, -1=ショート, 0=フラット
        'entry_price': 0.0,
        'bars_since_entry': 0,   # エントリー後の確定2Hバー数
        'last_signal': 'flat',
        'last_bar_time': None,
    }

def save_state(state, fp):
    with open(fp, 'w') as f:
        json.dump(state, f, indent=2, default=str)


# ─── フェードシグナルエンジン ─────────────────────────────────────────────
class FadeEngine:
    """
    1Hバーを受け取り、セッション境界に沿った2Hバーを構築し、
    フェードシグナルを計算するクラス。
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.bars_2h = pd.DataFrame(
            columns=['open', 'high', 'low', 'close', 'volume']
        )
        self._slot_bars = []        # 現スロットに蓄積中の1Hバーリスト
        self._current_slot = None   # 現スロットの開始時（9, 11, 13）または None

    # ── ユーティリティ ────────────────────────────────────────────────────

    def _bar_to_dict(self, b):
        """ib_insync BarData → dict"""
        dt = b.date if isinstance(b.date, datetime) else pd.to_datetime(str(b.date))
        return {
            'datetime': dt,
            'open': b.open, 'high': b.high,
            'low': b.low,   'close': b.close,
            'volume': b.volume or 0,
        }

    def _get_jst_hour(self, b):
        dt = b.date if isinstance(b.date, datetime) else pd.to_datetime(str(b.date))
        return dt.hour

    def _aggregate_slot(self, bars):
        """1Hバーリスト → 2Hバーのdict（datetimeはスロット最終バーの時刻）"""
        rows = [self._bar_to_dict(b) for b in bars]
        return {
            'datetime': rows[-1]['datetime'],
            'open':   rows[0]['open'],
            'high':   max(r['high'] for r in rows),
            'low':    min(r['low']  for r in rows),
            'close':  rows[-1]['close'],
            'volume': sum(r['volume'] for r in rows),
        }

    def _append_2h_bar(self, bar_dict):
        """確定した2Hバーを bars_2h に追記"""
        dt = pd.to_datetime(bar_dict['datetime'])
        row = pd.DataFrame(
            [{k: bar_dict[k] for k in ('open', 'high', 'low', 'close', 'volume')}],
            index=pd.DatetimeIndex([dt])
        )
        self.bars_2h = pd.concat([self.bars_2h, row])
        # 重複タイムスタンプは後者優先で除去
        self.bars_2h = self.bars_2h[~self.bars_2h.index.duplicated(keep='last')]

    # ── ヒストリカルデータから2Hバーを初期構築 ──────────────────────────

    def initialize(self, bars_1h):
        """
        ヒストリカル1Hバーから2Hバーを構築してエンジンを初期化する。
        シグナル計算に必要な最低本数（lookback+1）に満たない場合は False を返す。
        """
        if not bars_1h:
            return False

        # 1Hバーを (日付, スロット開始時) でグルーピング
        slot_groups: dict = {}
        for b in bars_1h:
            dt = b.date if isinstance(b.date, datetime) else pd.to_datetime(str(b.date))
            slot = SLOT_MAP.get(dt.hour)
            if slot is None:
                continue
            key = (dt.date(), slot)
            slot_groups.setdefault(key, []).append(b)

        # スロットごとに2Hバーを生成（日付・スロット順）
        bars_list = []
        for (day, slot_hour) in sorted(slot_groups.keys()):
            rows = [self._bar_to_dict(b) for b in slot_groups[(day, slot_hour)]]
            # 確定タイムスタンプ = スロット開始 + 2H（同日）
            last_dt = rows[-1]['datetime']
            confirm_dt = last_dt.replace(
                hour=slot_hour + 2, minute=0, second=0, microsecond=0
            )
            bars_list.append({
                'datetime': confirm_dt,
                'open':   rows[0]['open'],
                'high':   max(r['high'] for r in rows),
                'low':    min(r['low']  for r in rows),
                'close':  rows[-1]['close'],
                'volume': sum(r['volume'] for r in rows),
            })

        if not bars_list:
            return False

        df = pd.DataFrame(bars_list)
        df['datetime'] = pd.to_datetime(df['datetime'])
        df = df.set_index('datetime').sort_index()
        df = df[~df.index.duplicated(keep='last')]
        self.bars_2h = df
        log.info(f"2Hバー初期構築: {len(self.bars_2h)}本 "
                 f"({self.bars_2h.index[0]} ～ {self.bars_2h.index[-1]})")
        return len(self.bars_2h) >= self.cfg['lookback'] + 1

    # ── リアルタイム: 新しい1Hバーを受け取り2Hバー確定を検出 ────────────

    def on_new_1h_bar(self, b):
        """
        新しい1Hバーを処理する。2Hバーが確定した場合はそのdictを返す。
        確定しない場合（スロット蓄積中 or 日中外）は None を返す。

        確定トリガー:
          - 新しいスロットへの移行（例: 9スロット蓄積中 → 11時台のバー到着）
          - 日中セッション外のバー到着（15時以降 → 13スロット確定）
        """
        hour = self._get_jst_hour(b)
        slot = SLOT_MAP.get(hour)  # None = 日中セッション外

        # ── 日中セッション外のバー到着 → 保留スロット確定 ──────────────
        if slot is None:
            if self._current_slot is not None and self._slot_bars:
                completed = self._aggregate_slot(self._slot_bars)
                self._append_2h_bar(completed)
                log.info(f"  2H確定(セッション終了トリガー): slot={self._current_slot}")
                self._current_slot = None
                self._slot_bars = []
                return completed
            return None

        # ── 初回バー ────────────────────────────────────────────────────
        if self._current_slot is None:
            self._current_slot = slot
            self._slot_bars = [b]
            return None

        # ── 同スロット内のバー ────────────────────────────────────────
        if slot == self._current_slot:
            self._slot_bars.append(b)
            return None

        # ── 新スロットへの移行 → 前スロット確定 ──────────────────────
        completed = self._aggregate_slot(self._slot_bars)
        self._append_2h_bar(completed)

        # 新スロット開始
        self._current_slot = slot
        self._slot_bars = [b]
        return completed

    # ── シグナル計算 ────────────────────────────────────────────────────

    def compute_signal(self):
        """
        最新の確定2Hバーのcloseと、その1本前まで直近N本の高値/安値を比較。
        - close > 直近N本の高値 → 'short'
        - close < 直近N本の安値 → 'long'
        - それ以外             → 'flat'
        """
        n = self.cfg['lookback']
        if len(self.bars_2h) < n + 1:
            log.info(f"  バー不足({len(self.bars_2h)}/{n+1}) → flat")
            return 'flat'

        prev_n = self.bars_2h.iloc[-(n + 1):-1]  # 1本前まで直近N本
        high_n = prev_n['high'].max()
        low_n  = prev_n['low'].min()
        current_close = self.bars_2h['close'].iloc[-1]

        log.info(
            f"  signal計算: close={current_close:.0f} "
            f"high_{n}={high_n:.0f} low_{n}={low_n:.0f}"
        )
        if current_close > high_n:
            return 'short'
        elif current_close < low_n:
            return 'long'
        return 'flat'


# ─── リアルタイム執行エンジン ─────────────────────────────────────────────
class Fade2HRealtimeEngine:

    def __init__(self, is_live=False):
        self.ib = IB()
        self.is_live = is_live
        self.port = CONFIG['live_port'] if is_live else CONFIG['paper_port']
        self.state = load_state(CONFIG['state_file'])
        self.contract = None
        self.engine = FadeEngine(CONFIG)
        self.tg = TelegramNotifier()
        self._needs_reconnect = False
        self.live = None

    # ── 接続・初期化 ────────────────────────────────────────────────────

    def connect(self):
        mode = 'LIVE' if self.is_live else 'Paper'
        log.info("=" * 60)
        log.info(f"=== N225ミニ 2Hフェードエンジン v1.0 === {mode} port:{self.port}")
        log.info("=" * 60)

        self.ib.connect(
            CONFIG['host'], self.port,
            clientId=CONFIG['client_id'], timeout=20
        )
        log.info(f"接続OK: {self.ib.managedAccounts()}")

        self.contract = Future(
            symbol=CONFIG['symbol'],
            lastTradeDateOrContractMonth=CONFIG['last_trade_date'],
            exchange=CONFIG['exchange'],
            currency=CONFIG['currency'],
        )
        self.ib.qualifyContracts(self.contract)
        log.info(f"Contract: {self.contract.localSymbol} conId={self.contract.conId}")

        # ヒストリカルデータ取得 → 2Hバー構築
        log.info("ヒストリカルデータ取得中...")
        bars = self.ib.reqHistoricalData(
            self.contract, endDateTime='',
            durationStr=CONFIG['history_duration'],
            barSizeSetting=CONFIG['bar_size'],
            whatToShow='TRADES', useRTH=False,
            formatDate=1, keepUpToDate=False,
        )
        log.info(f"ヒストリカルデータ: {len(bars)}本(1H)")
        if not bars:
            log.error("ヒストリカルデータなし"); return False
        if not self.engine.initialize(bars):
            log.error("FadeEngine初期化失敗（2Hバー不足）"); return False

        self._sync_pos()

        # ライブバー購読開始
        log.info("ライブバー購読開始...")
        self.live = self.ib.reqHistoricalData(
            self.contract, endDateTime='', durationStr='2 D',
            barSizeSetting=CONFIG['bar_size'],
            whatToShow='TRADES', useRTH=False,
            formatDate=1, keepUpToDate=True,
        )
        self.live.updateEvent += self._on_bar
        self.ib.disconnectedEvent += self._on_disc

        startup_msg = (
            f"{mode} port:{self.port}\n"
            f"2Hバー:{len(self.engine.bars_2h)}本 pos={self.state['position']}\n"
            f"bars_since_entry={self.state['bars_since_entry']}"
        )
        log.info("=" * 60)
        log.info("監視開始")
        log.info(f"  {startup_msg.replace(chr(10), ' | ')}")
        log.info("=" * 60)
        self.tg.startup("N225ミニ 2Hフェード", startup_msg)
        return True

    # ── ポジション同期 ────────────────────────────────────────────────

    def _sync_pos(self):
        for p in self.ib.positions():
            if p.contract.symbol == CONFIG['symbol']:
                ip = int(p.position)
                if ip != self.state['position']:
                    log.warning(
                        f"Pos mismatch: local={self.state['position']} IBKR={ip}"
                    )
                    self.tg.warn(
                        "2Hフェード",
                        f"Pos mismatch: {self.state['position']}->{ip}"
                    )
                    self.state['position'] = ip
                    save_state(self.state, CONFIG['state_file'])
                return
        if self.state['position'] != 0:
            log.warning("IBKRポジションなし → ローカルを0にリセット")
            self.state['position'] = 0
            self.state['bars_since_entry'] = 0
            save_state(self.state, CONFIG['state_file'])

    # ── バー更新コールバック ──────────────────────────────────────────

    def _on_bar(self, bars, has_new):
        if not has_new:
            return
        b = bars[-1]
        bt = b.date if isinstance(b.date, datetime) else pd.to_datetime(str(b.date))
        log.info(
            f"1H受信: {bt} "
            f"O={b.open:.0f} H={b.high:.0f} L={b.low:.0f} C={b.close:.0f}"
        )

        # 2Hバー確定チェック
        completed = self.engine.on_new_1h_bar(b)
        if completed is None:
            return

        # ─ 2Hバー確定 ───────────────────────────────────────────────
        log.info(
            f"★2H確定: {completed['datetime']} "
            f"O={completed['open']:.0f} H={completed['high']:.0f} "
            f"L={completed['low']:.0f} C={completed['close']:.0f} "
            f"(累計{len(self.engine.bars_2h)}本)"
        )

        # ポジション保有中 → エグジットカウント
        if self.state['position'] != 0:
            self.state['bars_since_entry'] += 1
            log.info(
                f"  経過バー: {self.state['bars_since_entry']}/{CONFIG['exit_bars']} "
                f"(pos={self.state['position']})"
            )

            if self.state['bars_since_entry'] >= CONFIG['exit_bars']:
                log.info("  ★ エグジット条件成立")
                self._close_position()
                # 決済後は同バーでの新規エントリーを行わない
            save_state(self.state, CONFIG['state_file'])
            return

        # ノーポジ → シグナル判定
        signal = self.engine.compute_signal()
        log.info(f"  シグナル: {signal}")

        if signal == 'flat':
            return

        self._enter(signal, completed['close'])
        save_state(self.state, CONFIG['state_file'])

    # ── エントリー ────────────────────────────────────────────────────

    def _enter(self, signal, ref_price):
        """
        フェードエントリー。
        バー確定直後に成行発注 → 次バーのopen価格で約定（近似）。
        """
        action = 'SELL' if signal == 'short' else 'BUY'
        qty = CONFIG['max_contracts']

        log.info("=" * 50)
        log.info(f"*** エントリー: {action} {qty}枚  ref={ref_price:.0f}  ({signal}) ***")
        log.info("=" * 50)

        account = self.ib.managedAccounts()[0]
        order = MarketOrder(action, qty)
        order.account = account
        trade = self.ib.placeOrder(self.contract, order)
        log.info(f"  発注: MKT {action} {qty}枚")

        for _ in range(30):
            self.ib.sleep(1)
            if trade.orderStatus.status in ('Filled', 'Inactive', 'Cancelled'):
                break

        if trade.orderStatus.status == 'Filled':
            fill_price = trade.orderStatus.avgFillPrice
            log.info(f"  約定: {action} {qty}枚 @ {fill_price:.0f}")
            self.tg.trade(
                "2Hフェード",
                f"{action} {qty}枚 @ {fill_price:.0f}\n"
                f"signal={signal}  ref={ref_price:.0f}"
            )
            self.state['position'] = qty if action == 'BUY' else -qty
            self.state['entry_price'] = fill_price
            self.state['bars_since_entry'] = 0
            self.state['last_signal'] = signal
        else:
            log.warning(f"  約定タイムアウト: status={trade.orderStatus.status}")
            self.tg.warn("2Hフェード", f"約定タイムアウト: {action} {qty}枚")
            self._sync_pos()

    # ── エグジット ────────────────────────────────────────────────────

    def _close_position(self):
        """5本経過後の成行決済"""
        cur = self.state['position']
        if cur == 0:
            return

        action = 'SELL' if cur > 0 else 'BUY'
        qty = abs(cur)
        ref_price = self.live[-1].close if self.live else 0.0

        log.info("=" * 50)
        log.info(f"*** エグジット: {action} {qty}枚  ref={ref_price:.0f} ***")
        log.info("=" * 50)

        account = self.ib.managedAccounts()[0]
        order = MarketOrder(action, qty)
        order.account = account
        trade = self.ib.placeOrder(self.contract, order)
        log.info(f"  発注: MKT {action} {qty}枚")

        for _ in range(30):
            self.ib.sleep(1)
            if trade.orderStatus.status in ('Filled', 'Inactive', 'Cancelled'):
                break

        if trade.orderStatus.status == 'Filled':
            fill_price = trade.orderStatus.avgFillPrice
            pnl_pts = (fill_price - self.state['entry_price']) * (
                1 if action == 'SELL' else -1
            )
            log.info(
                f"  約定: {action} {qty}枚 @ {fill_price:.0f}  "
                f"PnL≈{pnl_pts:+.0f}点"
            )
            self.tg.exit(
                "2Hフェード",
                f"決済 {action} {qty}枚 @ {fill_price:.0f}\n"
                f"entry={self.state['entry_price']:.0f}  "
                f"PnL≈{pnl_pts:+.0f}点"
            )
        else:
            log.warning(f"  決済タイムアウト: status={trade.orderStatus.status}")
            self.tg.warn("2Hフェード", f"決済タイムアウト: {action} {qty}枚")
            self._sync_pos()
            return

        # 決済完了 → ステートリセット
        self.state['position'] = 0
        self.state['entry_price'] = 0.0
        self.state['bars_since_entry'] = 0
        self.state['last_signal'] = 'flat'

    # ── 切断 / 再接続 ─────────────────────────────────────────────────

    def _on_disc(self):
        log.warning("=" * 50)
        log.warning("IB Gateway 切断!")
        log.warning("=" * 50)
        self.tg.warn("2Hフェード", "IB Gateway切断")
        self._needs_reconnect = True

    def _reconn(self):
        try:
            try:
                self.ib.disconnect()
            except Exception:
                pass

            self.ib = IB()
            self.ib.connect(
                CONFIG['host'], self.port,
                clientId=CONFIG['client_id'], timeout=20
            )
            log.info("再接続OK")
            self.tg.send("2Hフェード 再接続OK")

            self.contract = Future(
                symbol=CONFIG['symbol'],
                lastTradeDateOrContractMonth=CONFIG['last_trade_date'],
                exchange=CONFIG['exchange'],
                currency=CONFIG['currency'],
            )
            self.ib.qualifyContracts(self.contract)
            self._sync_pos()

            # 2Hバーを再構築
            bars = self.ib.reqHistoricalData(
                self.contract, endDateTime='',
                durationStr=CONFIG['history_duration'],
                barSizeSetting=CONFIG['bar_size'],
                whatToShow='TRADES', useRTH=False,
                formatDate=1, keepUpToDate=False,
            )
            if bars:
                self.engine = FadeEngine(CONFIG)
                self.engine.initialize(bars)

            # ライブ購読再開
            self.live = self.ib.reqHistoricalData(
                self.contract, endDateTime='', durationStr='2 D',
                barSizeSetting=CONFIG['bar_size'],
                whatToShow='TRADES', useRTH=False,
                formatDate=1, keepUpToDate=True,
            )
            self.live.updateEvent += self._on_bar
            self.ib.disconnectedEvent += self._on_disc
            self._needs_reconnect = False
            log.info("復元完了")
            return True
        except Exception as e:
            log.error(f"再接続失敗: {e}")
            self.tg.error("2Hフェード", f"再接続失敗: {e}")
            return False

    # ── メインループ ──────────────────────────────────────────────────

    def run(self):
        wait = 10
        while True:
            try:
                if self._needs_reconnect:
                    log.info(f"再接続試行（{wait}s後）...")
                    time.sleep(wait)
                    if self._reconn():
                        wait = 10
                    else:
                        wait = min(wait * 2, 300)
                    continue
                self.ib.sleep(1)
            except KeyboardInterrupt:
                log.info("手動停止")
                self.tg.send("2Hフェード 停止")
                break
            except Exception as e:
                log.error(f"メインループエラー: {e}")
                self.tg.error("2Hフェード", f"Error: {e}")
                self._needs_reconnect = True


# ─── エントリポイント ──────────────────────────────────────────────────────
def main():
    is_live = '--live' in sys.argv

    if is_live:
        print("WARNING: LIVE モードで起動します。続行しますか？ (yes/no)")
        if input().strip().lower() != 'yes':
            print("Cancelled.")
            sys.exit(0)

    eng = Fade2HRealtimeEngine(is_live=is_live)

    for i in range(1, 31):
        try:
            if eng.connect():
                break
            log.warning(f"初期化失敗 {i}/30")
            time.sleep(10)
        except Exception as e:
            log.warning(f"接続失敗 ({i}/30): {e}")
            time.sleep(10)
            if i == 30:
                eng.tg.error("2Hフェード", "起動失敗30回 → 終了")
                sys.exit(1)
            eng.ib = IB()

    eng.run()


if __name__ == '__main__':
    main()
