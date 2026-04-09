"""
adx_phase_analyzer.py
N225ミニ 1H OHLCデータに対してADX(14)分析を行い、トレンド位相ラベルを付与する。

位相分類:
  芽生え: ADX↑ かつ ADX < 25
  成長  : ADX↑ かつ (ADX >= 25 または 加速度 > 0)
  成熟  : ADX高水準 かつ 加速度 <= 0
  衰退  : ADX↓
"""

import pandas as pd
import numpy as np

# ── パラメータ ────────────────────────────────────────────────
INPUT_CSV  = r"C:\Users\CH07\Desktop\jquants_data\nk225m_1h_continuous.csv"
OUTPUT_CSV = r"C:\Users\CH07\Desktop\jquants_data\nk225m_1h_adx_phase.csv"
ADX_PERIOD = 14
ADX_THRESHOLD = 25.0  # 芽生え/成長の境界


# ── ADX計算 ───────────────────────────────────────────────────
def calc_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Wilder方式のADX(+DI, -DI)を計算してdfに列追加して返す。"""
    high  = df["high"]
    low   = df["low"]
    close = df["close"]

    # True Range
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Directional Movement
    up_move   = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm  = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    # Wilder平滑化（EWMでalpha=1/period近似）
    alpha = 1.0 / period
    atr      = pd.Series(tr,       index=df.index).ewm(alpha=alpha, adjust=False).mean()
    plus_di  = pd.Series(plus_dm,  index=df.index).ewm(alpha=alpha, adjust=False).mean()
    minus_di = pd.Series(minus_dm, index=df.index).ewm(alpha=alpha, adjust=False).mean()

    plus_di_pct  = 100 * plus_di  / atr
    minus_di_pct = 100 * minus_di / atr

    dx = 100 * (plus_di_pct - minus_di_pct).abs() / (plus_di_pct + minus_di_pct)
    adx = dx.ewm(alpha=alpha, adjust=False).mean()

    df = df.copy()
    df["adx"]      = adx.round(4)
    df["plus_di"]  = plus_di_pct.round(4)
    df["minus_di"] = minus_di_pct.round(4)
    return df


# ── 微分（速度・加速度）─────────────────────────────────────
def calc_derivatives(df: pd.DataFrame) -> pd.DataFrame:
    """ADXの1次差分（速度）と2次差分（加速度）を算出。"""
    df = df.copy()
    df["adx_velocity"]     = df["adx"].diff().round(6)        # 1次微分
    df["adx_acceleration"] = df["adx_velocity"].diff().round(6)  # 2次微分
    return df


# ── 位相分類 ───────────────────────────────────────────────────
def classify_phase(row) -> str:
    """
    ADX速度(adx_velocity)・加速度(adx_acceleration)・ADX値から位相を判定。

    芽生え: velocity > 0 かつ ADX < 25
    成長  : velocity > 0 かつ (ADX >= 25 または acceleration > 0)
    成熟  : velocity <= 0 かつ ADX >= 25  (高水準だが減速)
    衰退  : velocity <= 0 かつ ADX < 25
    """
    v   = row["adx_velocity"]
    a   = row["adx_acceleration"]
    adx = row["adx"]

    if pd.isna(v) or pd.isna(a):
        return "不明"

    if v > 0:
        if adx < ADX_THRESHOLD:
            return "芽生え"
        else:  # ADX >= 25, または加速度 > 0 の条件も満たす
            return "成長"
    else:  # v <= 0: ADX下降中
        if adx >= ADX_THRESHOLD:
            return "成熟"
        else:
            return "衰退"


# ── メイン ────────────────────────────────────────────────────
def main():
    print(f"[1/4] CSVを読み込み中: {INPUT_CSV}")
    df = pd.read_csv(INPUT_CSV, parse_dates=["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    print(f"      行数: {len(df):,}  期間: {df['datetime'].iloc[0]} 〜 {df['datetime'].iloc[-1]}")

    print(f"[2/4] ADX({ADX_PERIOD}), +DI, -DI を計算中...")
    df = calc_adx(df, period=ADX_PERIOD)

    print("[3/4] 速度・加速度を算出中...")
    df = calc_derivatives(df)

    print("[4/4] 位相分類を付与中...")
    df["phase"] = df.apply(classify_phase, axis=1)

    # 位相ラベルの分布を表示
    phase_counts = df["phase"].value_counts()
    print("\n─── 位相分布 ───────────────────────────────")
    for label, cnt in phase_counts.items():
        pct = cnt / len(df) * 100
        print(f"  {label:4s}: {cnt:6,} 本 ({pct:5.1f}%)")
    print("────────────────────────────────────────────")

    # ADX統計
    print(f"\n─── ADX統計 ─────────────────────────────────")
    print(f"  平均  : {df['adx'].mean():.2f}")
    print(f"  中央値: {df['adx'].median():.2f}")
    print(f"  最大  : {df['adx'].max():.2f}")
    print(f"  最小  : {df['adx'].min():.2f}")
    print("────────────────────────────────────────────")

    print(f"\nCSVを保存中: {OUTPUT_CSV}")
    df.to_csv(OUTPUT_CSV, index=False)
    print("完了。")

    # サンプル表示（最新10行）
    cols = ["datetime", "close", "adx", "plus_di", "minus_di",
            "adx_velocity", "adx_acceleration", "phase"]
    print("\n─── 最新10行サンプル ────────────────────────")
    print(df[cols].tail(10).to_string(index=False))


if __name__ == "__main__":
    main()
