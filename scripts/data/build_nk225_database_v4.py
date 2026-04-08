"""
日経225ミニ先物 1分足データベース構築スクリプト v4
J-Quants DataCube → 連続先物（無調整） → 各タイムフレーム集約

v2→v4 変更点:
  1. OSE取引時間の4時代に完全対応:
     Era A (2013/1-2014/2):   日中 9:00-15:15, 夜間 16:30-翌3:00
     Era B (2014/3-2021/9/20): 日中 9:00-15:15, 夜間 16:30-翌5:30
     Era C (2021/9/21-2024/11/4): 日中 8:45-15:15, 夜間 16:30-翌6:00
     Era D (2024/11/5-):      日中 8:45-15:45, 夜間 17:00-翌6:00
  2. build_daily()の夜間セッション境界を時代対応
  3. 限月調整なし（無調整版 = ライブデータと同一）
  4. 祝日取引(2022/9/23-)はタイムスタンプベースで自動処理（特別対応不要）
  (v2からの継続: SQ当日ロール、TVバー境界一致)

使い方:
  python build_nk225_database_v4.py /path/to/zip_folder

入力: J-Quants DataCubeからDLしたZIPファイルが入ったフォルダ
      (future_ohlc_minute_19_YYYYMM.csv を含むZIP)

出力 (同フォルダに生成):
  nk225m_1min_continuous.csv   - 1分足連続先物
  nk225m_5min_continuous.csv   - 5分足
  nk225m_1h_continuous.csv     - 1時間足 (TVバー境界一致)
  nk225m_daily_continuous.csv  - 日足
  nk225m_build_log.txt         - 構築ログ（ロール日・限月情報）
"""

import os
import sys
import glob
import zipfile
import calendar
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
import pandas as pd
import numpy as np

# ============================================================
# 設定
# ============================================================
INDEX_TYPE = 19  # 日経225ミニ先物

# OSE取引時間の時代区分 (JST)
# Era A: 2013/01 - 2014/02   日中 9:00-15:15, 夜間 16:30-翌3:00
# Era B: 2014/03 - 2021/09/20 日中 9:00-15:15, 夜間 16:30-翌5:30
# Era C: 2021/09/21 - 2024/11/04 日中 8:45-15:15, 夜間 16:30-翌6:00
# Era D: 2024/11/05 -          日中 8:45-15:45, 夜間 17:00-翌6:00
ERA_B_START = datetime(2014, 3, 24)   # 2014/3限月ロール後（夜間5:30延長）
ERA_C_START = datetime(2021, 9, 21)   # J-GATE 3.0: 日中8:45開始、夜間翌6:00
ERA_D_START = datetime(2024, 11, 5)   # arrowhead 4.0: 日中15:45、夜間17:00開始

# セッションID (J-Quants)
SESSION_NIGHT = 3
SESSION_DAY = 999


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
    Trade_Date + Interval_Time + Session_ID → datetime (JST)

    夜間セッション(Session_ID=3):
      - Interval_Time 1630-2359 (旧) / 1700-2359 (新): 当日
      - Interval_Time 0000-0600: 翌日
        ※ J-Quantsでは夜間セッションの翌日分もTrade_Dateが前日のまま
    日中セッション(Session_ID=999):
      - Interval_Time 0845-1545: 当日そのまま
    """
    df = df.copy()
    df['Trade_Date'] = df['Trade_Date'].astype(float).astype(int)
    trade_date = df['Trade_Date'].astype(str)

    df['Interval_Time'] = df['Interval_Time'].astype(float).astype(int)
    it = df['Interval_Time']
    hour = it // 100
    minute = it % 100

    base_dt = pd.to_datetime(trade_date, format='%Y%m%d')

    is_night = df['Session_ID'] == SESSION_NIGHT
    is_next_day = is_night & (it < 700)

    dt = base_dt + pd.to_timedelta(hour, unit='h') + pd.to_timedelta(minute, unit='m')
    dt = dt.where(~is_next_day, dt + pd.Timedelta(days=1))

    df['datetime'] = dt
    return df


# ============================================================
# 4. SQ日計算・ロール日決定 (v2: SQ当日ロール)
# ============================================================
def get_sq_date(year, month):
    """指定年月の第2金曜日(SQ日)を返す"""
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
    v2: TVと同じくSQ当日(第2金曜)にロール。
    SQ前営業日(木曜)までが旧限月、SQ当日(金曜)から次限月。
    """
    contract_months = get_contract_months()
    schedule = []

    for i, (y, m) in enumerate(contract_months):
        sq = get_sq_date(y, m)
        # v2: SQ当日にロール (TV NK225M1!と同じ)
        roll_date = sq

        contract = y * 100 + m
        schedule.append({
            'contract_month': contract,
            'sq_date': sq,
            'roll_date': roll_date,
        })

    log(f"ロールスケジュール: {len(schedule)}限月 (SQ当日ロール)", log_lines)
    return schedule


def assign_front_contract(df, log_lines=None):
    """
    各バーに対して「この時点での期近限月」を割り当てる。
    v2: SQ当日(第2金曜)にロール。SQ前日(木曜)までが旧限月。
    """
    schedule = get_roll_schedule(log_lines)

    roll_info = []
    for i in range(len(schedule) - 1):
        roll_info.append({
            'start': schedule[i]['roll_date'] if i > 0 else datetime(2012, 1, 1),
            'end': schedule[i + 1]['roll_date'],
            'contract_month': schedule[i + 1]['contract_month'],
        })

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
    Panama Canal調整なし（無調整版）。
    """
    log("連続先物構築中...", log_lines)

    df = df[df['Contract_Month'] == df['front_contract']].copy()
    log(f"  期近限月データ: {len(df):,}行", log_lines)

    df = df.sort_values(['datetime', 'Trade_Volume'], ascending=[True, False])
    df = df.drop_duplicates(subset='datetime', keep='first')
    log(f"  重複除去後: {len(df):,}行", log_lines)

    contract_changes = df[df['front_contract'] != df['front_contract'].shift()]
    for _, row in contract_changes.iterrows():
        log(f"  ロール: {row['datetime'].strftime('%Y-%m-%d %H:%M')} → 限月{row['front_contract']}", log_lines)

    result = df[['datetime', 'Open_Price', 'High_Price', 'Low_Price', 'Close_Price',
                 'Trade_Volume', 'Contract_Month']].copy()
    result.columns = ['datetime', 'open', 'high', 'low', 'close', 'volume', 'contract_month']
    result = result.sort_values('datetime').reset_index(drop=True)

    return result


# ============================================================
# 6. タイムフレーム集約 (v2: TVバー境界一致)
# ============================================================

def assign_tv_1h_bucket(dt_series):
    """
    各1分足のdatetimeに対して、TV NK225M1!と同じ1Hバー開始時刻を割り当てる。

    OSE取引時間:
      ～2024/11/4 (旧): 日中 08:45-15:15, 夜間 16:30-翌05:30
      2024/11/5～ (新): 日中 08:45-15:45, 夜間 17:00-翌06:00

    TVバー境界:
      旧: 夜間 :30始まり (16:30,17:30,...), 日中 08:45 + :30始まり (09:30,10:30,...)
      新: 夜間 :00始まり (17:00,18:00,...), 日中 08:45 + :00始まり (09:00,10:00,...)
    """
    result = pd.Series(index=dt_series.index, dtype='datetime64[ns]')

    for idx, dt in dt_series.items():
        h, m = dt.hour, dt.minute
        d = dt.normalize()  # 日付部分のみ

        is_new_era = dt >= OSE_TIME_CHANGE_DATE

        if is_new_era:
            # === 2024/11/5～ (新時間) ===
            if 8 <= h < 9 and m >= 45:
                # 日中セッション開始: 08:45-08:59 → bucket 08:45
                result.at[idx] = d + pd.Timedelta(hours=8, minutes=45)
            elif (9 <= h <= 15):
                # 日中: :00始まり (09:00, 10:00, ..., 15:00)
                result.at[idx] = d + pd.Timedelta(hours=h)
            elif h == 8 and m < 45:
                # 08:00-08:44: セッション外 → skip (NaT)
                result.at[idx] = pd.NaT
            elif h >= 17 or h < 6:
                # 夜間: :00始まり (17:00, 18:00, ..., 05:00)
                result.at[idx] = d + pd.Timedelta(hours=h)
            elif h == 6 and m == 0:
                # 06:00 クロージング → 05:00バーに含める? or 独自バー?
                # TVでは06:00バーが存在する（スクショで確認）
                result.at[idx] = d + pd.Timedelta(hours=5)
            elif h == 16:
                # 16:00-16:59: セッション間ギャップ → skip
                result.at[idx] = pd.NaT
            else:
                # 06:01-08:44, 15:46-16:59: セッション外
                result.at[idx] = pd.NaT
        else:
            # === ～2024/11/4 (旧時間) ===
            if 8 <= h < 9 and m >= 45:
                # 日中セッション開始: 08:45-09:29 → bucket 08:45
                result.at[idx] = d + pd.Timedelta(hours=8, minutes=45)
            elif h == 9 and m < 30:
                # 09:00-09:29 → still 08:45 bucket
                result.at[idx] = d + pd.Timedelta(hours=8, minutes=45)
            elif (9 <= h <= 15) and not (h == 9 and m < 30):
                # 日中: :30始まり (09:30, 10:30, ..., 14:30)
                if m >= 30:
                    result.at[idx] = d + pd.Timedelta(hours=h, minutes=30)
                else:
                    result.at[idx] = d + pd.Timedelta(hours=h - 1, minutes=30)
            elif h == 8 and m < 45:
                result.at[idx] = pd.NaT
            elif h == 16 and m >= 30:
                # 夜間セッション開始: 16:30-17:29 → bucket 16:30
                result.at[idx] = d + pd.Timedelta(hours=16, minutes=30)
            elif h >= 17 or h < 6:
                # 夜間: :30始まり (17:30, 18:30, ..., 04:30)
                if m >= 30:
                    result.at[idx] = d + pd.Timedelta(hours=h, minutes=30)
                else:
                    result.at[idx] = d + pd.Timedelta(hours=h - 1, minutes=30)
            elif h == 5 and m <= 30:
                # 05:00-05:30 → bucket 04:30
                result.at[idx] = d + pd.Timedelta(hours=4, minutes=30)
            elif h == 16 and m < 30:
                # 16:00-16:29: セッション間ギャップ
                result.at[idx] = pd.NaT
            else:
                result.at[idx] = pd.NaT

    return result


def assign_tv_1h_bucket_fast(dt_series):
    """
    assign_tv_1h_bucket のベクトル化版（高速）。
    4時代のOSE取引時間に対応。

    TV 1Hバー境界ルール:
      - Era A/B/C (～2024/11/4): 日中 :30始まり(初回08:45 or 09:00), 夜間 :30始まり
      - Era D (2024/11/5～): 日中 :00始まり(初回08:45), 夜間 :00始まり

    セッション外のデータはNaTを返す。
    """
    n = len(dt_series)
    result = pd.Series(pd.NaT, index=dt_series.index, dtype='datetime64[ns]')

    h = dt_series.dt.hour
    m = dt_series.dt.minute
    d = dt_series.dt.normalize()

    era_d = dt_series >= ERA_D_START
    era_c = (dt_series >= ERA_C_START) & ~era_d
    era_b = (dt_series >= ERA_B_START) & (dt_series < ERA_C_START)
    era_a = dt_series < ERA_B_START

    # ============================================================
    # Era D (2024/11/5～): 日中 8:45-15:45, 夜間 17:00-翌6:00
    # バー境界: :00始まり (初回08:45)
    # ============================================================
    # 日中: 08:45-08:59 → 08:45
    mask = era_d & (h == 8) & (m >= 45)
    result[mask] = d[mask] + pd.Timedelta(hours=8, minutes=45)

    # 日中: 09:00-15:59 → :00始まり
    mask = era_d & (h >= 9) & (h <= 15)
    result[mask] = d[mask] + pd.to_timedelta(h[mask], unit='h')

    # 夜間: 17:00-23:59 → :00始まり
    mask = era_d & (h >= 17)
    result[mask] = d[mask] + pd.to_timedelta(h[mask], unit='h')

    # 夜間: 00:00-05:59 → :00始まり
    mask = era_d & (h >= 0) & (h <= 5)
    result[mask] = d[mask] + pd.to_timedelta(h[mask], unit='h')

    # ============================================================
    # Era C (2021/9/21-2024/11/4): 日中 8:45-15:15, 夜間 16:30-翌6:00
    # バー境界: :30始まり (初回08:45)
    # ============================================================
    # 日中: 08:45-09:29 → 08:45
    mask = era_c & (((h == 8) & (m >= 45)) | ((h == 9) & (m < 30)))
    result[mask] = d[mask] + pd.Timedelta(hours=8, minutes=45)

    # 日中: 09:30-15:29 → :30始まり
    mask = era_c & (((h == 9) & (m >= 30)) | ((h >= 10) & (h <= 15)))
    bucket_h = h[mask].copy()
    sub_mask = m[mask] < 30
    bucket_h[sub_mask] = bucket_h[sub_mask] - 1
    result[mask] = d[mask] + pd.to_timedelta(bucket_h, unit='h') + pd.Timedelta(minutes=30)

    # 夜間: 16:30-16:59 → 16:30
    mask = era_c & (h == 16) & (m >= 30)
    result[mask] = d[mask] + pd.Timedelta(hours=16, minutes=30)

    # 夜間: 17:00-23:59 → :30始まり
    mask = era_c & (h >= 17)
    bucket_h2 = h[mask].copy()
    sub_mask2 = m[mask] < 30
    bucket_h2[sub_mask2] = bucket_h2[sub_mask2] - 1
    result[mask] = d[mask] + pd.to_timedelta(bucket_h2, unit='h') + pd.Timedelta(minutes=30)

    # 夜間: 00:00-05:59 → :30始まり
    mask = era_c & (h >= 0) & (h <= 5)
    bucket_h3 = h[mask].copy()
    sub_mask3 = m[mask] < 30
    bucket_h3[sub_mask3] = bucket_h3[sub_mask3] - 1
    result[mask] = d[mask] + pd.to_timedelta(bucket_h3, unit='h') + pd.Timedelta(minutes=30)

    # ============================================================
    # Era B (2014/3-2021/9/20): 日中 9:00-15:15, 夜間 16:30-翌5:30
    # バー境界: :30始まり (初回09:00)
    # 日中開始が9:00なので、09:00-09:29→09:00バケット
    # ============================================================
    # 日中: 09:00-09:29 → 09:00 (TVでは9:00始まりの60分バー)
    mask = era_b & (h == 9) & (m < 30)
    result[mask] = d[mask] + pd.Timedelta(hours=9)

    # 日中: 09:30-15:29 → :30始まり
    mask = era_b & (((h == 9) & (m >= 30)) | ((h >= 10) & (h <= 15)))
    bucket_h = h[mask].copy()
    sub_mask = m[mask] < 30
    bucket_h[sub_mask] = bucket_h[sub_mask] - 1
    result[mask] = d[mask] + pd.to_timedelta(bucket_h, unit='h') + pd.Timedelta(minutes=30)

    # 夜間: 16:30-16:59 → 16:30
    mask = era_b & (h == 16) & (m >= 30)
    result[mask] = d[mask] + pd.Timedelta(hours=16, minutes=30)

    # 夜間: 17:00-23:59 → :30始まり
    mask = era_b & (h >= 17)
    bucket_h2 = h[mask].copy()
    sub_mask2 = m[mask] < 30
    bucket_h2[sub_mask2] = bucket_h2[sub_mask2] - 1
    result[mask] = d[mask] + pd.to_timedelta(bucket_h2, unit='h') + pd.Timedelta(minutes=30)

    # 夜間: 00:00-05:29 → :30始まり
    mask = era_b & (h >= 0) & (h <= 5) & ~((h == 5) & (m >= 30))
    bucket_h3 = h[mask].copy()
    sub_mask3 = m[mask] < 30
    bucket_h3[sub_mask3] = bucket_h3[sub_mask3] - 1
    result[mask] = d[mask] + pd.to_timedelta(bucket_h3, unit='h') + pd.Timedelta(minutes=30)

    # ============================================================
    # Era A (2013/1-2014/2): 日中 9:00-15:15, 夜間 16:30-翌3:00
    # バー境界: :30始まり (初回09:00)
    # ============================================================
    # 日中: 09:00-09:29 → 09:00
    mask = era_a & (h == 9) & (m < 30)
    result[mask] = d[mask] + pd.Timedelta(hours=9)

    # 日中: 09:30-15:29 → :30始まり
    mask = era_a & (((h == 9) & (m >= 30)) | ((h >= 10) & (h <= 15)))
    bucket_h = h[mask].copy()
    sub_mask = m[mask] < 30
    bucket_h[sub_mask] = bucket_h[sub_mask] - 1
    result[mask] = d[mask] + pd.to_timedelta(bucket_h, unit='h') + pd.Timedelta(minutes=30)

    # 夜間: 16:30-16:59 → 16:30
    mask = era_a & (h == 16) & (m >= 30)
    result[mask] = d[mask] + pd.Timedelta(hours=16, minutes=30)

    # 夜間: 17:00-23:59 → :30始まり
    mask = era_a & (h >= 17)
    bucket_h2 = h[mask].copy()
    sub_mask2 = m[mask] < 30
    bucket_h2[sub_mask2] = bucket_h2[sub_mask2] - 1
    result[mask] = d[mask] + pd.to_timedelta(bucket_h2, unit='h') + pd.Timedelta(minutes=30)

    # 夜間: 00:00-02:59 → :30始まり (翌3:00まで)
    mask = era_a & (h >= 0) & (h <= 2)
    bucket_h3 = h[mask].copy()
    sub_mask3 = m[mask] < 30
    bucket_h3[sub_mask3] = bucket_h3[sub_mask3] - 1
    result[mask] = d[mask] + pd.to_timedelta(bucket_h3, unit='h') + pd.Timedelta(minutes=30)

    return result


def resample_1h_tv(df, log_lines=None):
    """
    1分足をTV NK225M1!と同じ1Hバー境界で集約する。
    """
    log("1H集約 (TVバー境界)...", log_lines)

    df = df.copy()
    df['tv_1h_bucket'] = assign_tv_1h_bucket_fast(df['datetime'])

    # NaTを除外（セッション外データ）
    valid = df['tv_1h_bucket'].notna()
    dropped = (~valid).sum()
    if dropped > 0:
        log(f"  セッション外除外: {dropped:,}行", log_lines)
    df = df[valid].copy()

    # バケットでグループ化
    grouped = df.groupby('tv_1h_bucket').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
    })

    grouped = grouped.dropna(subset=['open'])
    grouped = grouped.reset_index().rename(columns={'tv_1h_bucket': 'datetime'})
    grouped = grouped.sort_values('datetime').reset_index(drop=True)

    log(f"  1H足: {len(grouped):,}行", log_lines)
    return grouped


def resample_ohlcv(df, rule, label='left'):
    """1分足を指定タイムフレームに集約（5分足等に使用）"""
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
    """
    日足構築（取引日ベース）。
    夜間セッション開始以降のデータは翌営業日の取引日に属する。
    Era A-C: 16:30以降 = 翌営業日
    Era D: 17:00以降 = 翌営業日
    """
    df = df.copy()
    df['trade_date'] = df['datetime'].dt.date

    # Era D (2024/11/5-): 17:00以降が翌営業日
    era_d_night = (df['datetime'] >= ERA_D_START) & (df['datetime'].dt.hour >= 17)
    # Era A-C (～2024/11/4): 16:30以降が翌営業日
    era_abc_night = (df['datetime'] < ERA_D_START) & (
        (df['datetime'].dt.hour >= 17) |
        ((df['datetime'].dt.hour == 16) & (df['datetime'].dt.minute >= 30))
    )

    night_mask = era_d_night | era_abc_night
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
# 欠損月チェック
# ============================================================
def check_missing_months(csv_paths, start_ym='201301', end_ym='202604'):
    found = set()
    for p in csv_paths:
        name = str(p)
        parts = name.split('_')
        for part in parts:
            if len(part) >= 6 and part[:6].isdigit():
                ym = part[:6]
                if '2013' <= ym[:4] <= '2026':
                    found.add(ym)
                break

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
        print(f"\n  ! 欠損: {len(missing)}ヶ月")
        for ym in missing:
            print(f"    - {ym[:4]}/{ym[4:]}")
    else:
        print(f"\n  OK 欠損なし！全月揃っています。")

    if extra:
        print(f"\n  i 範囲外: {', '.join(extra)}")

    return missing


# ============================================================
# メイン
# ============================================================
def main():
    if len(sys.argv) < 2:
        print("使い方: python build_nk225_database_v2.py /path/to/zip_or_csv_folder")
        print("  ZIPファイルまたはCSVファイルが入ったフォルダを指定")
        sys.exit(1)

    folder = Path(sys.argv[1])
    if not folder.exists():
        print(f"エラー: フォルダが見つかりません: {folder}")
        sys.exit(1)

    log_lines = []
    log(f"{'='*60}", log_lines)
    log(f"日経225ミニ 連続先物データベース構築 v4", log_lines)
    log(f"  バー境界: TVバー境界一致 (4時代対応)", log_lines)
    log(f"  ロール: SQ当日(第2金曜)", log_lines)
    log(f"  限月調整: なし（無調整版）", log_lines)
    log(f"入力: {folder}", log_lines)
    log(f"開始: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", log_lines)
    log(f"{'='*60}", log_lines)

    # Step 1: ZIP解凍 + CSV収集
    log("\n[Step 1] ZIP解凍・CSV収集", log_lines)
    csv_paths = extract_zips(folder)
    if not csv_paths:
        print("エラー: CSVファイルが見つかりません")
        sys.exit(1)

    missing = check_missing_months(csv_paths)
    if missing:
        ans = input(f"\n{len(missing)}ヶ月の欠損があります。続行しますか？ (y/N): ")
        if ans.lower() != 'y':
            print("中断しました。欠損月をDLしてから再実行してください。")
            sys.exit(1)

    # Step 2: 全CSV読み込み
    log("\n[Step 2] CSV読み込み・結合", log_lines)
    raw = load_all_csvs(csv_paths)

    raw = raw[raw['Index_Type'] == INDEX_TYPE].copy()
    for col in ['Index_Type', 'Security_Code', 'Session_ID', 'Contract_Month']:
        if col in raw.columns:
            raw[col] = raw[col].astype(float).astype(int)
    log(f"Index_Type={INDEX_TYPE}のみ: {len(raw):,}行", log_lines)

    # Step 3: datetime構築
    log("\n[Step 3] datetime構築", log_lines)
    raw = build_datetime(raw)
    log(f"期間: {raw['datetime'].min()} ~ {raw['datetime'].max()}", log_lines)

    # Step 4: 期近限月割り当て
    log("\n[Step 4] 期近限月割り当て (SQ当日ロール)", log_lines)
    raw = assign_front_contract(raw, log_lines)

    # Step 5: 連続先物構築
    log("\n[Step 5] 連続先物構築", log_lines)
    continuous = build_continuous(raw, log_lines)
    log(f"連続先物: {len(continuous):,}行", log_lines)
    log(f"期間: {continuous['datetime'].min()} ~ {continuous['datetime'].max()}", log_lines)

    # Step 6: 各タイムフレームに集約
    log("\n[Step 6] タイムフレーム集約", log_lines)

    # 1分足
    out_1min = folder / "nk225m_1min_continuous.csv"
    continuous.to_csv(out_1min, index=False)
    log(f"  1分足: {len(continuous):,}行 → {out_1min.name}", log_lines)

    # 5分足
    df_5min = resample_ohlcv(continuous, '5min')
    out_5min = folder / "nk225m_5min_continuous.csv"
    df_5min.to_csv(out_5min, index=False)
    log(f"  5分足: {len(df_5min):,}行 → {out_5min.name}", log_lines)

    # 1時間足 (TVバー境界)
    df_1h = resample_1h_tv(continuous, log_lines)
    out_1h = folder / "nk225m_1h_continuous.csv"
    df_1h.to_csv(out_1h, index=False)
    log(f"  1H足: {len(df_1h):,}行 → {out_1h.name}", log_lines)

    # 日足
    df_daily = build_daily(continuous)
    out_daily = folder / "nk225m_daily_continuous.csv"
    df_daily.to_csv(out_daily, index=False)
    log(f"  日足: {len(df_daily):,}行 → {out_daily.name}", log_lines)

    # ログ保存
    log(f"\n{'='*60}", log_lines)
    log(f"完了: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", log_lines)
    log(f"{'='*60}", log_lines)

    log_file = folder / "nk225m_build_log.txt"
    with open(log_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(log_lines))
    print(f"\nログ: {log_file}")

    print(f"\n{'─'*40}")
    print(f"出力ファイル:")
    for out_path in [out_1min, out_5min, out_1h, out_daily]:
        size = os.path.getsize(out_path) / 1024 / 1024
        print(f"  {out_path.name:<35} ({size:.1f} MB)")
    print(f"{'─'*40}")


if __name__ == '__main__':
    main()
