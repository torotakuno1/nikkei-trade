#!/usr/bin/env python3
"""
Options Earnings Snapshot - 決算オプション環境通知
===================================================
翌日決算銘柄のオプションデータ（PCR + 25d RR）を
IB Gateway 経由で取得し Telegram 送信。

配置: nikkei-trade/scripts/execution/options_earnings_snapshot.py
実行: タスクスケジューラ 毎日 20:50 JST
接続: IB Gateway port 4002 (paper) / clientId=50
データ: reqMarketDataType(3) 遅延データ ($0)

環境変数:
  FINNHUB_API_KEY             - Finnhub APIキー
  TELEGRAM_BOT_TOKEN_EARNINGS - 決算Bot トークン
  TELEGRAM_CHAT_ID_EARNINGS   - 決算Bot チャットID

watchlist: data/earnings_watchlist.csv (us-market-calendar と同一形式)
  ticker,tier,sector,subsector,notes

依存: pip install ib_insync requests --break-system-packages
"""
import os
import sys
import ssl
import csv
import json
import time
import math
import logging
import urllib.request
from datetime import datetime, timedelta, date
from pathlib import Path

from ib_insync import IB, Stock, Option, util

# ============================================================
# パス解決
# ============================================================
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent  # scripts/execution/ → repo root
WATCHLIST_PATH = REPO_ROOT / "data" / "earnings_watchlist.csv"

# ============================================================
# ロギング
# ============================================================
LOG_DIR = SCRIPT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"options_earnings_{datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger('options_earnings')

# ============================================================
# 設定
# ============================================================
IB_HOST = '127.0.0.1'
IB_PORT = 4002       # paper
IB_CLIENT_ID = 50

FINNHUB_KEY = os.environ.get('FINNHUB_API_KEY', '')
TG_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN_EARNINGS', '')
TG_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID_EARNINGS', '')

# ============================================================
# Telegram（EARNINGS Bot 専用）
# ============================================================
def send_telegram(text: str):
    """Markdown形式でTelegram送信"""
    if not TG_TOKEN or not TG_CHAT_ID:
        log.info(f"[TG OFF] {text[:120]}...")
        return
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        payload = json.dumps({
            'chat_id': TG_CHAT_ID,
            'text': text,
            'parse_mode': 'Markdown',
        }).encode('utf-8')
        req = urllib.request.Request(
            url, data=payload,
            headers={'Content-Type': 'application/json'},
        )
        urllib.request.urlopen(req, timeout=15, context=ctx)
    except Exception as e:
        log.warning(f"Telegram送信失敗: {e}")


# ============================================================
# Watchlist CSV 読み込み
# ============================================================
def load_watchlist() -> dict:
    """
    earnings_watchlist.csv → {ticker: {'tier': int, 'sector': str, ...}}
    """
    if not WATCHLIST_PATH.exists():
        log.error(f"watchlist not found: {WATCHLIST_PATH}")
        return {}

    watchlist = {}
    with open(WATCHLIST_PATH, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = (row.get('ticker') or '').strip().upper()
            if not ticker:
                continue
            try:
                tier = int(row.get('tier', '1'))
            except ValueError:
                tier = 1
            watchlist[ticker] = {
                'tier': tier,
                'sector': (row.get('sector') or '').strip(),
            }
    log.info(f"Loaded {len(watchlist)} tickers from watchlist")
    return watchlist


# ============================================================
# Finnhub: 翌日決算銘柄の特定
# ============================================================
def fetch_earnings_calendar(from_date: str, to_date: str) -> list:
    """Finnhub /calendar/earnings から決算予定を取得"""
    if not FINNHUB_KEY:
        log.error("FINNHUB_API_KEY 未設定")
        return []

    url = (
        f"https://finnhub.io/api/v1/calendar/earnings"
        f"?from={from_date}&to={to_date}&token={FINNHUB_KEY}"
    )
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    for attempt in range(3):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                data = json.loads(resp.read().decode())
            return data.get('earningsCalendar', [])
        except Exception as e:
            log.warning(f"Finnhub取得失敗 (attempt {attempt+1}): {e}")
            time.sleep(3)
    return []


def get_earnings_symbols(watchlist: dict) -> list:
    """
    24時間ウィンドウ方式で翌日決算銘柄を特定。
    20:50 JST 実行 → 今夜AMC(=今日ET) + 明朝BMO/TBD(=明日ET) を対象。

    Returns:
        [{'symbol': str, 'tier': int, 'hour': str, 'date': str, 'session': str}]
    """
    today = date.today()
    tomorrow = today + timedelta(days=1)
    from_str = today.strftime('%Y-%m-%d')
    to_str = tomorrow.strftime('%Y-%m-%d')

    rows = fetch_earnings_calendar(from_str, to_str)
    if not rows:
        log.info("Finnhubからの決算データなし")
        return []

    result = []
    for r in rows:
        sym = (r.get('symbol') or '').upper()
        if sym not in watchlist:
            continue
        ear_date = r.get('date', '')
        hour = (r.get('hour') or '').lower()

        # 今日ET AMC → 今夜発表
        if ear_date == from_str and hour == 'amc':
            session = 'today_amc'
        # 明日ET BMO/TBD → 明朝発表
        elif ear_date == to_str and hour in ('bmo', ''):
            session = 'tomorrow_bmo'
        else:
            continue

        result.append({
            'symbol': sym,
            'tier': watchlist[sym]['tier'],
            'hour': hour,
            'date': ear_date,
            'session': session,
            'eps_estimate': r.get('epsEstimate'),
            'revenue_estimate': r.get('revenueEstimate'),
        })

    # tier降順 → symbol順
    result.sort(key=lambda x: (-x['tier'], x['symbol']))
    log.info(f"翌日決算銘柄: {len(result)}件 — {[r['symbol'] for r in result]}")
    return result


# ============================================================
# Finnhub: 過去決算実績
# ============================================================
def fetch_past_earnings(symbol: str) -> dict:
    """
    Finnhub /stock/earnings から直近4四半期の実績を取得。

    Returns:
        {'last_eps_actual': float, 'last_eps_estimate': float,
         'last_beat_pct': float, 'beat_count': int, 'total': int,
         'avg_beat_pct': float} or None
    """
    if not FINNHUB_KEY:
        return None

    url = f"https://finnhub.io/api/v1/stock/earnings?symbol={symbol}&token={FINNHUB_KEY}"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        log.warning(f"  {symbol} 過去決算取得失敗: {e}")
        return None

    if not isinstance(data, list) or not data:
        return None

    # period降順ソート、直近4件
    data = sorted(data, key=lambda x: x.get('period', ''), reverse=True)[:4]

    beat_pcts = []
    beats = 0
    for e in data:
        actual = e.get('actual')
        estimate = e.get('estimate')
        if actual is None or estimate is None or estimate == 0:
            continue
        pct = (actual - estimate) / abs(estimate) * 100
        beat_pcts.append(pct)
        if actual > estimate:
            beats += 1

    if not beat_pcts:
        return None

    last = data[0]
    last_actual = last.get('actual')
    last_estimate = last.get('estimate')
    last_beat_pct = None
    if last_actual is not None and last_estimate not in (None, 0):
        last_beat_pct = round((last_actual - last_estimate) / abs(last_estimate) * 100, 1)

    return {
        'last_eps_actual': last_actual,
        'last_eps_estimate': last_estimate,
        'last_beat_pct': last_beat_pct,
        'beat_count': beats,
        'total': len(beat_pcts),
        'avg_beat_pct': round(sum(beat_pcts) / len(beat_pcts), 1),
    }


# ============================================================
# IBKR: PCR (OI/Volume) 取得
# ============================================================
def fetch_pcr(ib: IB, symbol: str) -> dict:
    """
    Stock の genericTickList="100,101" で集計OI/Volumeを取得。
    Tick 27: callOpenInterest, 28: putOpenInterest
    Tick 29: callVolume, 30: putVolume

    Returns:
        {'call_oi': int, 'put_oi': int, 'call_vol': int, 'put_vol': int,
         'pcr_oi': float, 'pcr_vol': float, 'total_oi': int} or None
    """
    try:
        stock = Stock(symbol, 'SMART', 'USD')
        ib.qualifyContracts(stock)

        # snapshot=False: genericTickList と snapshot は併用不可 (Error 321)
        ticker = ib.reqMktData(stock, genericTickList='100,101', snapshot=False)

        # ポーリング: OIデータ到着を最大15秒待つ
        call_oi = None
        put_oi = None
        for i in range(30):
            ib.sleep(0.5)
            raw_call = getattr(ticker, 'callOpenInterest', None)
            raw_put = getattr(ticker, 'putOpenInterest', None)
            # nan でない実データが来たら抜ける
            if (raw_call is not None and not (isinstance(raw_call, float) and math.isnan(raw_call))
                and raw_put is not None and not (isinstance(raw_put, float) and math.isnan(raw_put))):
                call_oi = raw_call
                put_oi = raw_put
                break

        call_vol = getattr(ticker, 'callVolume', None)
        put_vol = getattr(ticker, 'putVolume', None)
        ib.cancelMktData(stock)

        # nan → None
        if isinstance(call_vol, float) and math.isnan(call_vol):
            call_vol = None
        if isinstance(put_vol, float) and math.isnan(put_vol):
            put_vol = None

        # 値の検証
        if call_oi is None or put_oi is None:
            log.warning(f"  {symbol}: OIデータ取得失敗 (call={call_oi}, put={put_oi})")
            return None
        if call_oi <= 0 and put_oi <= 0:
            log.warning(f"  {symbol}: OIがゼロ")
            return None

        total_oi = (call_oi or 0) + (put_oi or 0)
        pcr_oi = (put_oi / call_oi) if call_oi and call_oi > 0 else None

        pcr_vol = None
        if call_vol and call_vol > 0 and put_vol is not None:
            pcr_vol = put_vol / call_vol

        return {
            'call_oi': call_oi or 0,
            'put_oi': put_oi or 0,
            'call_vol': call_vol or 0,
            'put_vol': put_vol or 0,
            'pcr_oi': round(pcr_oi, 2) if pcr_oi is not None else None,
            'pcr_vol': round(pcr_vol, 2) if pcr_vol is not None else None,
            'total_oi': total_oi,
        }
    except Exception as e:
        log.error(f"  {symbol} PCR取得エラー: {e}")
        return None


# ============================================================
# IBKR: Implied Move 取得（全銘柄）
# ============================================================
def fetch_implied_move(ib: IB, symbol: str) -> dict:
    """
    ATMストラドル価格からImplied Moveを算出。
    IM% = (ATM Call Mid + ATM Put Mid) / Stock Price × 100

    Returns:
        {'im_pct': float, 'straddle': float, 'stock_price': float,
         'expiry': str, 'atm_strike': float} or None
    """
    try:
        # 1. 原資産価格
        stock = Stock(symbol, 'SMART', 'USD')
        ib.qualifyContracts(stock)
        ticker = ib.reqMktData(stock, '', snapshot=False)

        price = None
        for i in range(30):
            ib.sleep(0.5)
            p = ticker.marketPrice()
            if p is not None and p == p and p > 0:
                price = p
                break
            for field in ['last', 'close']:
                v = getattr(ticker, field, None)
                if v is not None and isinstance(v, (int, float)) and v == v and v > 0:
                    price = v
                    break
            if price:
                break
        ib.cancelMktData(stock)

        if price is None or price <= 0:
            log.warning(f"  {symbol} IM: 原資産価格取得失敗")
            return None

        # 2. オプションチェーン定義
        chains = ib.reqSecDefOptParams(symbol, '', stock.secType, stock.conId)
        if not chains:
            log.warning(f"  {symbol} IM: チェーン定義なし")
            return None

        chain = None
        for c in chains:
            if c.exchange == 'SMART':
                chain = c
                break
        if chain is None:
            chain = chains[0]

        # 3. 最短満期（今日以降）
        today_str = date.today().strftime('%Y%m%d')
        valid_expiries = sorted([e for e in chain.expirations if e >= today_str])
        if not valid_expiries:
            log.warning(f"  {symbol} IM: 有効な満期なし")
            return None

        target_expiry = valid_expiries[0]
        if target_expiry == today_str and len(valid_expiries) > 1:
            target_expiry = valid_expiries[1]

        # 4. ATMストライク
        all_strikes = sorted(chain.strikes)
        atm_strike = min(all_strikes, key=lambda s: abs(s - price))

        # 5. ATM Call + Put を取得
        call_opt = Option(symbol, target_expiry, atm_strike, 'C', 'SMART')
        put_opt = Option(symbol, target_expiry, atm_strike, 'P', 'SMART')
        ib.qualifyContracts(call_opt, put_opt)

        if not call_opt.conId or not put_opt.conId:
            log.warning(f"  {symbol} IM: ATMオプション認証失敗")
            return None

        call_tk = ib.reqMktData(call_opt, '', snapshot=False)
        put_tk = ib.reqMktData(put_opt, '', snapshot=False)

        # ポーリング: bid/ask か last が来るまで待つ
        call_mid = None
        put_mid = None
        for i in range(30):
            ib.sleep(0.5)
            # Call
            if call_mid is None:
                bid, ask = call_tk.bid, call_tk.ask
                if (bid is not None and isinstance(bid, (int, float)) and bid == bid and bid > 0
                    and ask is not None and isinstance(ask, (int, float)) and ask == ask and ask > 0):
                    call_mid = (bid + ask) / 2
                else:
                    last = call_tk.last
                    if last is not None and isinstance(last, (int, float)) and last == last and last > 0:
                        call_mid = last
            # Put
            if put_mid is None:
                bid, ask = put_tk.bid, put_tk.ask
                if (bid is not None and isinstance(bid, (int, float)) and bid == bid and bid > 0
                    and ask is not None and isinstance(ask, (int, float)) and ask == ask and ask > 0):
                    put_mid = (bid + ask) / 2
                else:
                    last = put_tk.last
                    if last is not None and isinstance(last, (int, float)) and last == last and last > 0:
                        put_mid = last
            if call_mid and put_mid:
                break

        ib.cancelMktData(call_opt)
        ib.cancelMktData(put_opt)

        if call_mid is None or put_mid is None:
            log.warning(f"  {symbol} IM: ATMオプション価格取得失敗 (call={call_mid}, put={put_mid})")
            return None

        straddle = call_mid + put_mid
        im_pct = (straddle / price) * 100

        log.info(
            f"  {symbol} IM: ±{im_pct:.1f}% "
            f"(straddle ${straddle:.2f}, ATM {atm_strike}, exp {target_expiry})"
        )

        return {
            'im_pct': round(im_pct, 1),
            'straddle': round(straddle, 2),
            'stock_price': round(price, 2),
            'atm_strike': atm_strike,
            'expiry': target_expiry,
            'range_low': round(price - straddle, 2),
            'range_high': round(price + straddle, 2),
        }
    except Exception as e:
        log.error(f"  {symbol} IM取得エラー: {e}")
        return None


# ============================================================
# IBKR: 25d Risk Reversal 取得（★★★のみ）
# ============================================================
def fetch_risk_reversal(ib: IB, symbol: str) -> dict:
    """
    25-delta Risk Reversal = IV(25d Call) - IV(25d Put)

    手順:
    1. 原資産の現在値を取得
    2. reqSecDefOptParams でオプションチェーン定義を取得
    3. 決算後最短満期を選択
    4. ATM付近のストライクでオプション契約を作成
    5. tickOptionComputation で delta/IV を取得
    6. delta ≈ 0.25 の call/put を特定し IV差を計算

    Returns:
        {'rr_25d': float, 'atm_iv': float, 'call_25d_iv': float,
         'put_25d_iv': float, 'expiry': str} or None
    """
    try:
        # 1. 原資産価格（ストリーミング + ポーリング）
        stock = Stock(symbol, 'SMART', 'USD')
        ib.qualifyContracts(stock)
        ticker = ib.reqMktData(stock, '', snapshot=False)

        # ポーリング: 有効な価格が来るまで最大15秒待つ
        price = None
        for i in range(30):
            ib.sleep(0.5)
            # marketPrice() は last → close → bid/ask mid の順で試行
            p = ticker.marketPrice()
            if p is not None and p == p and p > 0:  # nan check
                price = p
                break
            # 個別フィールドも確認
            for field in ['last', 'close']:
                v = getattr(ticker, field, None)
                if v is not None and isinstance(v, (int, float)) and v == v and v > 0:
                    price = v
                    break
            if price:
                break
        ib.cancelMktData(stock)

        if price is None or price <= 0:
            log.warning(f"  {symbol}: 原資産価格取得失敗 ({price})")
            return None

        log.info(f"  {symbol}: 原資産 ${price:.2f}")

        # 2. オプションチェーン定義
        chains = ib.reqSecDefOptParams(symbol, '', stock.secType, stock.conId)
        if not chains:
            log.warning(f"  {symbol}: オプションチェーン定義なし")
            return None

        # SMART exchange のチェーンを優先
        chain = None
        for c in chains:
            if c.exchange == 'SMART':
                chain = c
                break
        if chain is None:
            chain = chains[0]

        # 3. 決算後最短満期を選択（今日以降で最短）
        today_str = date.today().strftime('%Y%m%d')
        valid_expiries = sorted([e for e in chain.expirations if e >= today_str])
        if not valid_expiries:
            log.warning(f"  {symbol}: 有効な満期なし")
            return None

        # 最短満期（通常は週次オプション = 決算直後）
        target_expiry = valid_expiries[0]
        # 直近すぎる場合（今日満期）は次を使う
        if target_expiry == today_str and len(valid_expiries) > 1:
            target_expiry = valid_expiries[1]

        log.info(f"  {symbol}: 満期 {target_expiry}")

        # 4. ATM付近のストライクを選択（±5本）
        all_strikes = sorted(chain.strikes)
        atm_idx = min(range(len(all_strikes)),
                      key=lambda i: abs(all_strikes[i] - price))
        start_idx = max(0, atm_idx - 5)
        end_idx = min(len(all_strikes), atm_idx + 6)
        selected_strikes = all_strikes[start_idx:end_idx]

        if len(selected_strikes) < 3:
            log.warning(f"  {symbol}: ストライク不足 ({len(selected_strikes)})")
            return None

        # 5. オプション契約を作成してIV/deltaを取得
        contracts = []
        for strike in selected_strikes:
            for right in ['C', 'P']:
                opt = Option(symbol, target_expiry, strike, right, 'SMART')
                contracts.append(opt)

        qualified = ib.qualifyContracts(*contracts)
        if not qualified:
            log.warning(f"  {symbol}: オプション契約の認証失敗")
            return None

        # ストリーミングでIV/delta取得（遅延データ対応）
        tickers = []
        for contract in contracts:
            if contract.conId:  # 認証成功したもののみ
                t = ib.reqMktData(contract, '', snapshot=False)
                tickers.append((contract, t))

        # ポーリング: greeksデータ到着を最大20秒待つ
        for i in range(40):
            ib.sleep(0.5)
            got_greeks = sum(1 for _, t in tickers if t.modelGreeks is not None)
            if got_greeks >= len(tickers) * 0.5:  # 半数以上取得で打ち切り
                break

        # 6. delta/IVデータの収集
        calls_data = []  # (strike, delta, iv)
        puts_data = []
        atm_iv = None

        for contract, t in tickers:
            greeks = t.modelGreeks
            if greeks is None:
                continue
            delta = greeks.delta
            iv = greeks.impliedVol
            if delta is None or iv is None:
                continue

            strike = contract.strike
            if contract.right == 'C':
                calls_data.append((strike, delta, iv))
                # ATM IV: deltaが0.5に最も近いcall
                if abs(delta - 0.5) < 0.15:
                    atm_iv = iv
            else:
                puts_data.append((strike, delta, iv))

            ib.cancelMktData(contract)

        # 残りのキャンセル
        for contract, t in tickers:
            try:
                ib.cancelMktData(contract)
            except Exception:
                pass

        if not calls_data or not puts_data:
            log.warning(f"  {symbol}: IV/deltaデータ不足 (calls={len(calls_data)}, puts={len(puts_data)})")
            return None

        # 25-delta call: delta ≈ +0.25 に最も近い
        call_25d = min(calls_data, key=lambda x: abs(x[1] - 0.25))
        # 25-delta put: delta ≈ -0.25 に最も近い
        put_25d = min(puts_data, key=lambda x: abs(x[1] - (-0.25)))

        rr_25d = (call_25d[2] - put_25d[2]) * 100  # vol pointsに変換

        log.info(
            f"  {symbol}: 25d RR = {rr_25d:+.1f}pt "
            f"(Call IV={call_25d[2]*100:.1f}% δ={call_25d[1]:.3f}, "
            f"Put IV={put_25d[2]*100:.1f}% δ={put_25d[1]:.3f})"
        )

        return {
            'rr_25d': round(rr_25d, 1),
            'call_25d_iv': round(call_25d[2] * 100, 1),
            'put_25d_iv': round(put_25d[2] * 100, 1),
            'call_25d_delta': round(call_25d[1], 3),
            'put_25d_delta': round(put_25d[1], 3),
            'atm_iv': round(atm_iv * 100, 1) if atm_iv else None,
            'expiry': target_expiry,
        }

    except Exception as e:
        log.error(f"  {symbol} RR取得エラー: {e}")
        return None


# ============================================================
# PCR 解釈ラベル
# ============================================================
def pcr_label(pcr: float) -> str:
    if pcr is None:
        return ''
    if pcr > 1.2:
        return '弱気'
    elif pcr < 0.7:
        return '強気'
    else:
        return '中立'


def rr_label(rr: float) -> str:
    if rr is None:
        return ''
    if rr < -3:
        return '下方警戒強'
    elif rr < -1:
        return 'やや下方警戒'
    elif rr <= 1:
        return '中立'
    elif rr <= 3:
        return 'やや上方期待'
    else:
        return '上方期待強'


# ============================================================
# Telegram メッセージ整形
# ============================================================
def format_message(earnings: list, results: dict) -> str:
    """
    earnings: get_earnings_symbols() の戻り値
    results: {symbol: {'pcr': dict, 'rr': dict, 'im': dict}} の辞書

    Returns: Markdown形式テキスト
    """
    today = date.today()
    tomorrow = today + timedelta(days=1)
    header = f"📊 *決算プレビュー {today.month}/{today.day}夜〜{tomorrow.month}/{tomorrow.day}朝*"
    lines = [header, f"対象: {len(earnings)}銘柄", ""]

    # セッション別グループ
    amc_syms = [e for e in earnings if e['session'] == 'today_amc']
    bmo_syms = [e for e in earnings if e['session'] == 'tomorrow_bmo']

    for group_label, group in [
        (f"━━━━ 🌆 今夜 AMC ━━━━", amc_syms),
        (f"━━━━ 🌅 明朝 BMO ━━━━", bmo_syms),
    ]:
        if not group:
            continue
        lines.append(group_label)

        for e in group:
            sym = e['symbol']
            tier = e['tier']
            stars = '★' * tier
            data = results.get(sym, {})
            pcr = data.get('pcr')
            rr = data.get('rr')
            im = data.get('im')
            past = data.get('past')

            lines.append(f"*{stars} {sym}*")

            # 株価 (IMから取得)
            if im and im.get('stock_price'):
                lines.append(f"  💰 ${im['stock_price']:,.2f}")

            # EPS/売上予想 (Finnhubカレンダーから)
            eps_est = e.get('eps_estimate')
            rev_est = e.get('revenue_estimate')
            if eps_est is not None:
                rev_str = ""
                if rev_est:
                    rev_b = rev_est / 1e9
                    rev_str = f" | 売上予想 ${rev_b:.2f}B" if rev_b >= 1 else f" | 売上予想 ${rev_est/1e6:.0f}M"
                lines.append(f"  🎯 EPS予想: ${eps_est:.2f}{rev_str}")

            # 過去実績 (Finnhub)
            if past:
                last_a = past.get('last_eps_actual')
                last_e = past.get('last_eps_estimate')
                last_bp = past.get('last_beat_pct')
                if last_a is not None and last_e is not None and last_bp is not None:
                    verdict = "Beat" if last_bp > 1 else ("Miss" if last_bp < -1 else "In-line")
                    lines.append(f"  📜 前回: ${last_a:.2f} vs ${last_e:.2f} → {last_bp:+.1f}% {verdict}")
                bc = past.get('beat_count', 0)
                total = past.get('total', 0)
                avg = past.get('avg_beat_pct')
                if total > 0 and avg is not None:
                    lines.append(f"  📊 直近{total}回: {bc}勝{total-bc}負, 平均{avg:+.1f}%")

            # PCR
            if pcr:
                oi_str = f"{pcr['total_oi']//1000}K" if pcr['total_oi'] >= 1000 else str(pcr['total_oi'])
                pcr_oi_lbl = pcr_label(pcr['pcr_oi'])
                line = f"  OI {oi_str}"
                if pcr['pcr_oi'] is not None:
                    line += f" | PCR {pcr['pcr_oi']:.2f}({pcr_oi_lbl})"
                if pcr['pcr_vol'] is not None:
                    pcr_vol_lbl = pcr_label(pcr['pcr_vol'])
                    line += f" | Vol {pcr['pcr_vol']:.2f}({pcr_vol_lbl})"
                lines.append(line)
            else:
                lines.append("  PCR: 取得失敗")

            # Implied Move (全銘柄)
            if im:
                lines.append(
                    f"  ⚡ Implied Move: ±{im['im_pct']:.1f}% "
                    f"(${im['straddle']:.2f})"
                )
                lines.append(
                    f"  想定レンジ: ${im['range_low']:,.2f}〜${im['range_high']:,.2f}"
                )
            else:
                lines.append("  ⚡ Implied Move: 取得失敗")

            # RR (★★★のみ)
            if tier >= 3 and rr:
                rr_lbl = rr_label(rr['rr_25d'])
                lines.append(f"  25d RR: {rr['rr_25d']:+.1f}pt ({rr_lbl})")
                if rr['atm_iv']:
                    lines.append(f"  ATM IV: {rr['atm_iv']:.1f}%")
            elif tier >= 3 and rr is None:
                lines.append("  25d RR: 取得失敗")

            lines.append("")

    return "\n".join(lines)


# ============================================================
# メイン
# ============================================================
def main():
    log.info("=== Options Earnings Snapshot 起動 ===")

    # 1. 環境変数チェック
    missing = []
    if not FINNHUB_KEY:
        missing.append('FINNHUB_API_KEY')
    if not TG_TOKEN:
        missing.append('TELEGRAM_BOT_TOKEN_EARNINGS')
    if not TG_CHAT_ID:
        missing.append('TELEGRAM_CHAT_ID_EARNINGS')
    if missing:
        log.error(f"環境変数未設定: {missing}")
        sys.exit(1)

    # 2. Watchlist 読み込み
    watchlist = load_watchlist()
    if not watchlist:
        log.error("watchlist 空 or 読込失敗")
        sys.exit(1)

    # 3. 翌日決算銘柄の特定
    earnings = get_earnings_symbols(watchlist)
    if not earnings:
        log.info("翌日決算銘柄なし → 無通知終了")
        return

    # 4. IB Gateway 接続
    ib = IB()
    try:
        ib.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID, timeout=15)
        log.info(f"IB Gateway 接続OK (port {IB_PORT}, clientId {IB_CLIENT_ID})")
    except Exception as e:
        log.error(f"IB Gateway 接続失敗: {e}")
        send_telegram(f"⚠️ *Options Snapshot*\nIB Gateway接続失敗: {e}")
        sys.exit(1)

    # 遅延データモード ($0)
    ib.reqMarketDataType(3)

    # 5. データ取得
    results = {}
    for e in earnings:
        sym = e['symbol']
        tier = e['tier']
        log.info(f"処理中: {sym} (tier={tier})")

        # 過去決算実績 (Finnhub — IB不要)
        past = fetch_past_earnings(sym)
        results[sym] = {'pcr': None, 'rr': None, 'im': None, 'past': past}
        time.sleep(0.5)  # Finnhubレートリミット

        # PCR (全銘柄)
        pcr = fetch_pcr(ib, sym)
        results[sym]['pcr'] = pcr
        time.sleep(2)  # ペーシング

        # Implied Move (全銘柄)
        im = fetch_implied_move(ib, sym)
        results[sym]['im'] = im
        time.sleep(2)

        # RR (★★★のみ)
        if tier >= 3:
            rr = fetch_risk_reversal(ib, sym)
            results[sym]['rr'] = rr
            time.sleep(2)

    # 6. IB切断
    ib.disconnect()
    log.info("IB Gateway 切断")

    # 7. メッセージ整形 & 送信
    msg = format_message(earnings, results)
    log.info(f"送信メッセージ ({len(msg)} chars)")
    send_telegram(msg)

    log.info("=== 完了 ===")


if __name__ == '__main__':
    main()
