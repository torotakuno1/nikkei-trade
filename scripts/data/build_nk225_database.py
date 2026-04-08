"""
日経225ミニ先物 1分足データベース構築スクリプト
J-Quants DataCube → 連続先物 → 各タイムフレーム集約

使い方:
  python build_nk225_database.py /path/to/zip_folder

入力: J-Quants DataCubeからDLしたZIPファイルが入ったフォルダ
      (future_ohlc_minute_19_YYYYMM.csv を含むZIP)

出力 (同フォルダに生成):
  nk225m_1min_continuous.csv   - 1分足連続先物
  nk225m_5min_continuous.csv   - 5分足
  nk225m_1h_continuous.csv     - 1時間足
  nk225m_daily_continuous.csv  - 日足
  nk225m_build_log.txt         - 構築ログ（ロール日・限月情報）
"""

import os
import sys
import glob
import zipfile
import calendar
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
import numpy as np

# ============================================================
# 設定
# ============================================================
# Index_Type 19 = 日経225ミニ先物
INDEX_TYPE = 19

# ロール: SQ日（第2金曜）の前営業日。安全策として第2金曜の7日前にロール
ROLL_DAYS_BEFORE_SQ = 7

# セッションID
SESSION_NIGHT = 3    # 夜間 (16:30-翌6:00 JST)
SESSION_DAY = 999    # 日中 (9:00-15:15 JST)


def log(msg, log_lines=None):
    print(msg)
    if log_lines is not None:
        log_lines.append(msg)


# ============================================================
# 1. ZIP解凍
# ============================================================
def extract_zips(zip_folder):
    """ZIPファイルを全て解凍し、CSVファイルパスのリストを返す"""
    zip_folder = Path(zip_folder)
    csv_dir = zip_folder / "extracted_csv"
    csv_dir.mkdir(exist_ok=True)

    zip_files = sorted(glob.glob(str(zip_folder / "*.zip")))
    print(f"ZIPファイル: {len(zip_files)}個")

    csv_paths = []
    for zf in zip_files:
        try:
            with zipfile.ZipFile(zf, 'r') as z:
                for name in z.namelist():
                    if name.endswith('.csv') and 'future_ohlc_minute_19' in name:
                        z.extract(name, csv_dir)
                        csv_paths.append(csv_dir / name)
        except zipfile.BadZipFile:
            print(f"  警告: {zf} は不正なZIPファイル。スキップ。")

    # 直接CSVがフォルダにある場合も拾う
    direct_csvs = glob.glob(str(zip_folder / "future_ohlc_minute_19_*.csv"))
    for dc in direct_csvs:
        if Path(dc) not in csv_paths:
            csv_paths.append(Path(dc))

    csv_paths = sorted(set(csv_paths))
    print(f"CSVファイル: {len(csv_paths)}個")
    return csv_paths


# ============================================================
# 2. CSV読み込み・結合
# ============================================================
def load_all_csvs(csv_paths):
    """全CSVを読み込んで1つのDataFrameに結合"""
    # J-Quantsのカラム名が時期によって異なる（大文字→小文字）ため統一する
    COL_MAP = {
        'trade_date': 'Trade_Date',
        'index_type': 'Index_Type',
        'security_code': 'Security_Code',
        'session_id': 'Session_ID',
        'interval_time': 'Interval_Time',
        'open_price': 'Open_Price',
        'high_price': 'High_Price',
        'low_price': 'Low_Price',
        'close_price': 'Close_Price',
        'trade_volume': 'Trade_Volume',
        'vwap': 'VWAP',
        'number_of_trade': 'Number_of_Trade',
        'record_no': 'Record_No',
        'contract_month': 'Contract_Month',
    }

    dfs = []
    for i, path in enumerate(csv_paths):
        try:
            df = pd.read_csv(path)
            # カラム名を統一（小文字→大文字始まり）
            df = df.rename(columns=COL_MAP)
            dfs.append(df)
            if (i + 1) % 20 == 0:
                print(f"  読み込み中... {i+1}/{len(csv_paths)}")
        except Exception as e:
            print(f"  警告: {path} 読み込み失敗: {e}")

    if not dfs:
        raise ValueError("CSVファイルが見つかりません")

    combined = pd.concat(dfs, ignore_index=True)
    print(f"全データ: {len(combined):,}行")
    return combined


# ============================================================
# 3. datetime構築
# ============================================================
def build_datetime(df):
    """
    Trade_Date (YYYYMMDD) + Interval_Time (HHMM) + Session_ID → datetime (JST)

    夜間セッション(Session_ID=3):
      - Interval_Time 1630-2359: 当日の16:30-23:59
      - Interval_Time 0000-0600: 翌日の00:00-06:00
        ※ J-Quantsでは夜間セッションの翌日分もTrade_Dateが前日のまま
    日中セッション(Session_ID=999):
      - Interval_Time 0900-1515: 当日そのまま
    """
    # Trade_Dateが float (20130104.0) の場合があるので int 変換してから文字列化
    df = df.copy()
    df['Trade_Date'] = df['Trade_Date'].astype(float).astype(int)
    trade_date = df['Trade_Date'].astype(str)

    # Interval_Time も同様に int 変換
    df['Interval_Time'] = df['Interval_Time'].astype(float).astype(int)
    it = df['Interval_Time']
    hour = it // 100
    minute = it % 100

    # ベース日時
    base_dt = pd.to_datetime(trade_date, format='%Y%m%d')

    # 夜間セッションで 0000-0600 は翌日
    is_night = df['Session_ID'] == SESSION_NIGHT
    is_next_day = is_night & (it < 700)

    dt = base_dt + pd.to_timedelta(hour, unit='h') + pd.to_timedelta(minute, unit='m')
    dt = dt.where(~is_next_day, dt + pd.Timedelta(days=1))

    df = df.copy()
    df['datetime'] = dt
    return df


# ============================================================
# 4. SQ日計算・ロール日決定
# ============================================================
def get_sq_date(year, month):
    """指定年月の第2金曜日を返す"""
    cal = calendar.monthcalendar(year, month)
    fri_count = 0
    for week in cal:
        if week[calendar.FRIDAY] != 0:
            fri_count += 1
            if fri_count == 2:
                return datetime(year, month, week[calendar.FRIDAY])
    return None


def get_contract_months():
    """日経225ミニの限月リスト（3,6,9,12月）を2013-2027で生成"""
    months = []
    for y in range(2013, 2028):
        for m in [3, 6, 9, 12]:
            months.append((y, m))
    return months


def get_roll_schedule(log_lines=None):
    """
    ロールスケジュールを生成。
    各期間: ロール日（SQ-7日）の翌日 〜 次のロール日
    """
    contract_months = get_contract_months()
    schedule = []

    for i, (y, m) in enumerate(contract_months):
        sq = get_sq_date(y, m)
        roll_date = sq - timedelta(days=ROLL_DAYS_BEFORE_SQ)

        # 限月コード: YYYYMM
        contract = y * 100 + m
        schedule.append({
            'contract_month': contract,
            'sq_date': sq,
            'roll_date': roll_date,
        })

    log(f"ロールスケジュール: {len(schedule)}限月", log_lines)
    return schedule


def assign_front_contract(df, log_lines=None):
    """
    各バーに対して「この時点での期近限月」を割り当てる。
    ロール日基準: SQの7日前にロールオーバー。
    """
    schedule = get_roll_schedule(log_lines)

    # ロール日のリストを作成
    roll_info = []
    for i in range(len(schedule) - 1):
        roll_info.append({
            'start': schedule[i]['roll_date'] if i > 0 else datetime(2012, 1, 1),
            'end': schedule[i + 1]['roll_date'],
            'contract_month': schedule[i + 1]['contract_month'],  # ロール後の限月
        })

    # 各バーの日時に基づいて限月を割り当て
    df = df.copy()
    df['front_contract'] = 0

    assigned = 0
    for ri in roll_info:
        mask = (df['datetime'] >= ri['start']) & (df['datetime'] < ri['end'])
        df.loc[mask, 'front_contract'] = ri['contract_month']
        n = mask.sum()
        if n > 0:
            assigned += n

    log(f"期近限月割り当て: {assigned:,}/{len(df):,}行", log_lines)

    # 割り当てられなかった行を確認
    unassigned = df['front_contract'] == 0
    if unassigned.sum() > 0:
        log(f"  警告: {unassigned.sum():,}行が限月未割り当て（除外されます）", log_lines)

    return df[df['front_contract'] > 0].copy()


# ============================================================
# 5. 連続先物構築
# ============================================================
def build_continuous(df, log_lines=None):
    """
    期近限月のデータのみ抽出して連続先物を構築。
    Panama Canal (backward adjustment) は行わない（無調整版）。
    理由: v6/案Cのバックテストは無調整連続先物で行われているため。
    """
    log("連続先物構築中...", log_lines)

    # 期近限月のデータのみ抽出
    df = df[df['Contract_Month'] == df['front_contract']].copy()
    log(f"  期近限月データ: {len(df):,}行", log_lines)

    # 重複除去（同一datetime）
    df = df.sort_values(['datetime', 'Trade_Volume'], ascending=[True, False])
    df = df.drop_duplicates(subset='datetime', keep='first')
    log(f"  重複除去後: {len(df):,}行", log_lines)

    # ロール遷移のログ
    contract_changes = df[df['front_contract'] != df['front_contract'].shift()]
    for _, row in contract_changes.iterrows():
        log(f"  ロール: {row['datetime'].strftime('%Y-%m-%d %H:%M')} → 限月{row['front_contract']}", log_lines)

    # 必要カラムのみ
    result = df[['datetime', 'Open_Price', 'High_Price', 'Low_Price', 'Close_Price',
                 'Trade_Volume', 'Contract_Month']].copy()
    result.columns = ['datetime', 'open', 'high', 'low', 'close', 'volume', 'contract_month']
    result = result.sort_values('datetime').reset_index(drop=True)

    return result


# ============================================================
# 6. タイムフレーム集約
# ============================================================
def resample_ohlcv(df, rule, label='left'):
    """1分足を指定タイムフレームに集約"""
    df = df.set_index('datetime')

    resampled = df.resample(rule, label=label).agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
    }).dropna(subset=['open'])

    resampled = resampled.reset_index()
    return resampled


def build_daily(df):
    """日足構築（取引日ベース）"""
    df = df.copy()
    # 夜間セッション(16:30-翌6:00)は翌営業日に属するが、
    # 簡便のため日付でグループ化
    # 実際のOSE取引日は前日夜間+当日日中なので、
    # 17:00以降は翌日扱いにする
    df['trade_date'] = df['datetime'].dt.date
    # 17:00以降は翌営業日
    night_mask = df['datetime'].dt.hour >= 17
    df.loc[night_mask, 'trade_date'] = (df.loc[night_mask, 'datetime'] + timedelta(days=1)).dt.date

    daily = df.groupby('trade_date').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
    }).reset_index()
    daily = daily.rename(columns={'trade_date': 'datetime'})
    daily['datetime'] = pd.to_datetime(daily['datetime'])
    return daily


# ============================================================
# メイン
# ============================================================

# ============================================================
# 欠損月チェック（main末尾から呼び出し可能）
# ============================================================
def check_missing_months(csv_paths, start_ym='201301', end_ym='202604'):
    """
    ダウンロード済みCSVから年月を抽出し、欠損月を報告する。
    """
    # CSVファイル名からYYYYMMを抽出
    found = set()
    for p in csv_paths:
        name = str(p)
        # future_ohlc_minute_19_YYYYMM.csv
        parts = name.split('_')
        for part in parts:
            if len(part) >= 6 and part[:6].isdigit():
                ym = part[:6]
                if '2013' <= ym[:4] <= '2026':
                    found.add(ym)
                break

    # 期待される全月リスト
    expected = set()
    y_start, m_start = int(start_ym[:4]), int(start_ym[4:6])
    y_end, m_end = int(end_ym[:4]), int(end_ym[4:6])

    y, m = y_start, m_start
    while y * 100 + m <= y_end * 100 + m_end:
        expected.add(f"{y:04d}{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1

    missing = sorted(expected - found)
    extra = sorted(found - expected)

    print(f"\n{'='*50}")
    print(f"欠損月チェック ({start_ym} ~ {end_ym})")
    print(f"{'='*50}")
    print(f"  期待: {len(expected)}ヶ月")
    print(f"  検出: {len(found)}ヶ月")

    if missing:
        print(f"\n  ⚠ 欠損: {len(missing)}ヶ月")
        for ym in missing:
            print(f"    - {ym[:4]}/{ym[4:]}")
    else:
        print(f"\n  ✓ 欠損なし！全月揃っています。")

    if extra:
        print(f"\n  ℹ 範囲外: {', '.join(extra)}")

    return missing



def main():
    if len(sys.argv) < 2:
        print("使い方: python build_nk225_database.py /path/to/zip_or_csv_folder")
        print("  ZIPファイルまたはCSVファイルが入ったフォルダを指定")
        sys.exit(1)

    folder = Path(sys.argv[1])
    if not folder.exists():
        print(f"エラー: フォルダが見つかりません: {folder}")
        sys.exit(1)

    log_lines = []
    log(f"{'='*60}", log_lines)
    log(f"日経225ミニ 連続先物データベース構築", log_lines)
    log(f"入力: {folder}", log_lines)
    log(f"開始: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", log_lines)
    log(f"{'='*60}", log_lines)

    # Step 1: ZIP解凍 + CSV収集
    log("\n[Step 1] ZIP解凍・CSV収集", log_lines)
    csv_paths = extract_zips(folder)
    if not csv_paths:
        print("エラー: CSVファイルが見つかりません")
        sys.exit(1)

    # 欠損月チェック
    missing = check_missing_months(csv_paths)
    if missing:
        ans = input(f"\n{len(missing)}ヶ月の欠損があります。続行しますか？ (y/N): ")
        if ans.lower() != 'y':
            print("中断しました。欠損月をDLしてから再実行してください。")
            sys.exit(1)

    # Step 2: 全CSV読み込み
    log("\n[Step 2] CSV読み込み・結合", log_lines)
    raw = load_all_csvs(csv_paths)

    # Index_Type=19 のみ
    raw = raw[raw['Index_Type'] == INDEX_TYPE].copy()
    # float列をint変換（CSVの結合時にfloatになることがある）
    for col in ['Index_Type', 'Security_Code', 'Session_ID', 'Contract_Month']:
        if col in raw.columns:
            raw[col] = raw[col].astype(float).astype(int)
    log(f"Index_Type={INDEX_TYPE}のみ: {len(raw):,}行", log_lines)

    # Step 3: datetime構築
    log("\n[Step 3] datetime構築", log_lines)
    raw = build_datetime(raw)
    log(f"期間: {raw['datetime'].min()} ~ {raw['datetime'].max()}", log_lines)

    # Step 4: 期近限月割り当て
    log("\n[Step 4] 期近限月割り当て", log_lines)
    raw = assign_front_contract(raw, log_lines)

    # Step 5: 連続先物構築
    log("\n[Step 5] 連続先物構築", log_lines)
    continuous = build_continuous(raw, log_lines)
    log(f"連続先物: {len(continuous):,}行", log_lines)
    log(f"期間: {continuous['datetime'].min()} ~ {continuous['datetime'].max()}", log_lines)

    # Step 6: 各タイムフレームに集約
    log("\n[Step 6] タイムフレーム集約", log_lines)

    # 1分足（そのまま保存）
    out_1min = folder / "nk225m_1min_continuous.csv"
    continuous.to_csv(out_1min, index=False)
    log(f"  1分足: {len(continuous):,}行 → {out_1min}", log_lines)

    # 5分足
    df_5min = resample_ohlcv(continuous, '5min')
    out_5min = folder / "nk225m_5min_continuous.csv"
    df_5min.to_csv(out_5min, index=False)
    log(f"  5分足: {len(df_5min):,}行 → {out_5min}", log_lines)

    # 1時間足
    df_1h = resample_ohlcv(continuous, '1h')
    out_1h = folder / "nk225m_1h_continuous.csv"
    df_1h.to_csv(out_1h, index=False)
    log(f"  1H足: {len(df_1h):,}行 → {out_1h}", log_lines)

    # 日足
    df_daily = build_daily(continuous)
    out_daily = folder / "nk225m_daily_continuous.csv"
    df_daily.to_csv(out_daily, index=False)
    log(f"  日足: {len(df_daily):,}行 → {out_daily}", log_lines)

    # ログ保存
    log(f"\n{'='*60}", log_lines)
    log(f"完了: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", log_lines)
    log(f"{'='*60}", log_lines)

    log_file = folder / "nk225m_build_log.txt"
    with open(log_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(log_lines))
    print(f"\nログ: {log_file}")

    # サマリー
    print(f"\n{'─'*40}")
    print(f"出力ファイル:")
    print(f"  {out_1min.name:<35} ({os.path.getsize(out_1min)/1024/1024:.1f} MB)")
    print(f"  {out_5min.name:<35} ({os.path.getsize(out_5min)/1024/1024:.1f} MB)")
    print(f"  {out_1h.name:<35} ({os.path.getsize(out_1h)/1024/1024:.1f} MB)")
    print(f"  {out_daily.name:<35} ({os.path.getsize(out_daily)/1024/1024:.1f} MB)")
    print(f"{'─'*40}")


if __name__ == '__main__':
    main()

# main() にフックを追加するためのスタンドアロン実行
if __name__ == '__main__' and '--check-only' in sys.argv:
    args = [a for a in sys.argv[1:] if a != '--check-only']
    folder = Path(args[0]) if args else Path('.')
    csv_paths = extract_zips(folder)
    missing = check_missing_months(csv_paths)
    if missing:
        print(f"\n上記 {len(missing)}ヶ月をDLしてから再実行してください。")
    sys.exit(0 if not missing else 1)
