#!/usr/bin/env python3
"""
Earnings Surprise Check - 決算サプライズ ニアリアルタイム通知
=============================================================
options_earnings_snapshot.py が書き出した pending_surprise.json を読み、
Finnhub で epsActual を確認して結果判明済み銘柄を Telegram 通知する。

配置: nikkei-trade/scripts/execution/earnings_surprise_check.py
実行: タスクスケジューラ
  - "Earnings Surprise BMO" 毎日 23:00 JST (ET 10:00 寄り後)
  - "Earnings Surprise AMC" 毎日 06:00 JST (ET 17:00 引け後)

環境変数:
  FINNHUB_API_KEY             - Finnhub APIキー
  TELEGRAM_BOT_TOKEN_EARNINGS - 決算Bot トークン
  TELEGRAM_CHAT_ID_EARNINGS   - 決算Bot チャットID
"""
import os
import sys
import ssl
import json
import time
import html
import logging
import urllib.request
from datetime import datetime, timedelta, date
from pathlib import Path

# ============================================================
# パス解決
# ============================================================
SCRIPT_DIR = Path(__file__).resolve().parent
PENDING_SURPRISE_PATH = SCRIPT_DIR / "logs" / "pending_surprise.json"

# ============================================================
# ロギング
# ============================================================
LOG_DIR = SCRIPT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"earnings_surprise_{datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger('earnings_surprise')

# ============================================================
# 設定
# ============================================================
FINNHUB_KEY = os.environ.get('FINNHUB_API_KEY', '')
TG_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN_EARNINGS', '')
TG_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID_EARNINGS', '')

# Beat/Miss 判定閾値 (%)
BEAT_MISS_THRESHOLD = 1.0

# Implied Move 比較閾値
IM_SLIGHTLY_EXCEEDED = 1.5  # im_pct × この係数を超えたら「織込やや超」
IM_LARGE_MOVE = 2.0         # im_pct × この係数を超えたら「想定外の大変動」

# ============================================================
# SSL コンテキスト（共通）
# ============================================================
def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ============================================================
# Telegram 送信
# ============================================================
def send_telegram(text: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        log.info(f"[TG OFF] {text[:120]}...")
        return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        payload = json.dumps({
            'chat_id': TG_CHAT_ID,
            'text': text,
            'parse_mode': 'HTML',
        }).encode('utf-8')
        req = urllib.request.Request(
            url, data=payload,
            headers={'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx()) as resp:
            body = resp.read().decode('utf-8')
            log.info(f"Telegram送信OK: {body[:200]}")
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        log.warning(f"Telegram送信失敗: {e} — {body}")
    except Exception as e:
        log.warning(f"Telegram送信失敗: {e}")


# ============================================================
# Finnhub: epsActual 確認
# ============================================================
def fetch_eps_actual(symbol: str, earnings_date: str) -> dict | None:
    """
    Finnhub /calendar/earnings で epsActual を取得。
    earnings_date: 'YYYY-MM-DD' (ET日付)

    Returns:
        {'eps_actual': float, 'eps_estimate': float} or None (未確定)
    """
    url = (
        f"https://finnhub.io/api/v1/calendar/earnings"
        f"?from={earnings_date}&to={earnings_date}&symbol={symbol}&token={FINNHUB_KEY}"
    )
    for attempt in range(3):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx()) as resp:
                data = json.loads(resp.read().decode())
            entries = data.get('earningsCalendar', [])
            if not entries:
                log.info(f"  {symbol}: Finnhub結果なし (date={earnings_date})")
                return None
            entry = entries[0]
            eps_actual = entry.get('epsActual')
            if eps_actual is None:
                log.info(f"  {symbol}: epsActual=null (未確定)")
                return None
            return {
                'eps_actual': eps_actual,
                'eps_estimate': entry.get('epsEstimate'),
            }
        except Exception as e:
            log.warning(f"  {symbol} Finnhub取得失敗 (attempt {attempt+1}): {e}")
            if attempt < 2:
                time.sleep(3)
    return None


# ============================================================
# Finnhub: 現在株価取得
# ============================================================
def fetch_quote(symbol: str) -> dict | None:
    """
    Finnhub /quote で現在株価と前日終値を取得。

    Returns:
        {'current': float, 'prev_close': float, 'change_pct': float} or None
    """
    url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={FINNHUB_KEY}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx()) as resp:
                data = json.loads(resp.read().decode())
            current = data.get('c')
            prev_close = data.get('pc')
            if not current or not prev_close or current <= 0 or prev_close <= 0:
                log.warning(f"  {symbol} 株価データ不正: c={current}, pc={prev_close}")
                return None
            change_pct = (current - prev_close) / prev_close * 100
            return {
                'current': round(current, 2),
                'prev_close': round(prev_close, 2),
                'change_pct': round(change_pct, 2),
            }
        except Exception as e:
            log.warning(f"  {symbol} 株価取得失敗 (attempt {attempt+1}): {e}")
            if attempt < 2:
                time.sleep(3)
    return None


# ============================================================
# 判定ロジック
# ============================================================
def beat_miss_label(eps_actual: float, eps_estimate: float) -> tuple[str, float]:
    """Returns (label, beat_pct)"""
    if eps_estimate is None or eps_estimate == 0:
        return ('N/A', 0.0)
    beat_pct = (eps_actual - eps_estimate) / abs(eps_estimate) * 100
    if beat_pct > BEAT_MISS_THRESHOLD:
        label = 'Beat'
    elif beat_pct < -BEAT_MISS_THRESHOLD:
        label = 'Miss'
    else:
        label = 'In-line'
    return (label, round(beat_pct, 1))


def im_comparison_label(change_pct: float, im_pct: float | None) -> str:
    """Implied Move vs 実際の変動を比較"""
    if im_pct is None or im_pct <= 0:
        return ''
    abs_change = abs(change_pct)
    if abs_change > im_pct * IM_LARGE_MOVE:
        return '想定外の大変動'
    elif abs_change > im_pct * IM_SLIGHTLY_EXCEEDED:
        return '織込やや超'
    else:
        return '織込内'


# ============================================================
# earnings_date 導出
# ============================================================
def get_earnings_date(base_date: str, session: str) -> str:
    """
    pending_surprise の top-level date + session から ET 決算日を算出。
    today_amc → base_date
    tomorrow_bmo / tomorrow_tbd → base_date + 1
    """
    d = date.fromisoformat(base_date)
    if session == 'today_amc':
        return d.strftime('%Y-%m-%d')
    else:
        return (d + timedelta(days=1)).strftime('%Y-%m-%d')


# ============================================================
# Telegram メッセージ整形
# ============================================================
def format_message(resolved: list, today_str: str) -> str:
    """
    resolved: [{'symbol', 'tier', 'eps_actual', 'eps_estimate',
                 'beat_label', 'beat_pct', 'quote', 'im_pct'}]
    """
    d = date.fromisoformat(today_str)
    header = f"🚨 <b>決算サプライズ {d.month}/{d.day}</b>"
    lines = [header, f"", f"━━━━ 結果判明: {len(resolved)}銘柄 ━━━━"]

    for r in resolved:
        sym = r['symbol']
        tier = r['tier']
        stars = '★' * tier
        eps_actual = r['eps_actual']
        eps_estimate = r['eps_estimate']
        beat_label = r['beat_label']
        beat_pct = r['beat_pct']
        quote = r.get('quote')
        im_pct = r.get('im_pct')

        lines.append(f"<b>{html.escape(stars + ' ' + sym)}</b>")

        # EPS結果
        if eps_estimate is not None:
            lines.append(
                f"📊 EPS: ${eps_actual:.2f} vs 予想${eps_estimate:.2f}"
                f" → {beat_pct:+.1f}% {beat_label}"
            )
        else:
            lines.append(f"📊 EPS: ${eps_actual:.2f} (予想なし) {beat_label}")

        # 株価反応
        if quote:
            arrow = '📈' if quote['change_pct'] >= 0 else '📉'
            lines.append(
                f"{arrow} 株価反応: {quote['change_pct']:+.1f}%"
                f" (${quote['current']:,.2f})"
            )

            # Implied Move 比較
            if im_pct:
                im_label = im_comparison_label(quote['change_pct'], im_pct)
                lines.append(
                    f"⚡ Implied Move ±{im_pct:.1f}%"
                    f" → 実際{quote['change_pct']:+.1f}% ({im_label})"
                )
        else:
            lines.append("📊 株価: 取得失敗")

        lines.append("")

    return "\n".join(lines)


# ============================================================
# メイン
# ============================================================
def main():
    log.info("=== Earnings Surprise Check 起動 ===")

    # 1. pending_surprise.json 読み込み
    if not PENDING_SURPRISE_PATH.exists():
        log.info("pending_surprise.json なし → 終了")
        return

    try:
        with open(PENDING_SURPRISE_PATH, 'r', encoding='utf-8') as f:
            pending = json.load(f)
        base_date = pending['date']
        symbols = pending['symbols']
    except Exception as e:
        log.error(f"pending_surprise.json 読込失敗: {e}")
        return

    if not symbols:
        log.info("pending_surprise: 銘柄なし → ファイル削除して終了")
        PENDING_SURPRISE_PATH.unlink(missing_ok=True)
        return

    log.info(f"チェック対象: {len(symbols)}銘柄 (base_date={base_date})")

    # 2. 各銘柄の決算結果を確認
    resolved = []   # 結果判明
    unresolved = [] # 未判明（再試行用に残す）

    for entry in symbols:
        sym = entry['symbol']
        session = entry['session']
        tier = entry['tier']
        eps_estimate_preview = entry.get('eps_estimate')  # プレビュー時の予想値
        im_pct = entry.get('im_pct')

        earnings_date = get_earnings_date(base_date, session)
        log.info(f"  {sym}: session={session}, earnings_date={earnings_date}")

        # epsActual チェック
        eps_data = fetch_eps_actual(sym, earnings_date)
        time.sleep(0.5)  # Finnhub レートリミット対策

        if eps_data is None:
            log.info(f"  {sym}: 未確定 → unresolved に保持")
            unresolved.append(entry)
            continue

        eps_actual = eps_data['eps_actual']
        # eps_estimate: Finnhub から取得できた値を優先、なければプレビュー時の値
        eps_estimate = eps_data.get('eps_estimate') or eps_estimate_preview
        beat_label, beat_pct = beat_miss_label(eps_actual, eps_estimate)

        # 株価取得
        quote = fetch_quote(sym)
        time.sleep(0.5)

        resolved.append({
            'symbol': sym,
            'tier': tier,
            'eps_actual': eps_actual,
            'eps_estimate': eps_estimate,
            'beat_label': beat_label,
            'beat_pct': beat_pct,
            'quote': quote,
            'im_pct': im_pct,
        })
        log.info(
            f"  {sym}: {beat_label} eps={eps_actual:.2f} vs {eps_estimate}"
            f", quote={quote}"
        )

    # 3. 結果送信
    if resolved:
        msg = format_message(resolved, base_date)
        log.info(f"Telegram送信: {len(resolved)}銘柄確定 ({len(msg)} chars)")
        send_telegram(msg)
    else:
        log.info("結果判明銘柄なし → 送信スキップ")

    # 4. pending_surprise.json 更新
    if unresolved:
        # 未確定分だけ再書き出し
        pending['symbols'] = unresolved
        with open(PENDING_SURPRISE_PATH, 'w', encoding='utf-8') as f:
            json.dump(pending, f, ensure_ascii=False, indent=2)
        log.info(f"未確定 {len(unresolved)}銘柄 → pending_surprise.json 再書き出し")
    else:
        # 全件確定 → ファイル削除
        PENDING_SURPRISE_PATH.unlink(missing_ok=True)
        log.info("全銘柄確定 → pending_surprise.json 削除")

    log.info("=== 完了 ===")


if __name__ == '__main__':
    main()
