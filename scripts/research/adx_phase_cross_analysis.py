"""
adx_phase_cross_analysis.py
位相別の勝率・リターン統計 + クロス集計を行う。
"""

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings("ignore")

# ── パラメータ ────────────────────────────────────────────────
INPUT_CSV  = r"C:\Users\CH07\Desktop\jquants_data\nk225m_1h_adx_phase.csv"
OUTPUT_CSV = r"C:\Users\CH07\Desktop\jquants_data\adx_phase_cross_result.csv"
MIN_SAMPLE = 30   # クロス集計で表示する最小サンプル数

PHASE_ORDER = ["芽生え", "成長", "成熟", "衰退"]


# ── ヘルパー ───────────────────────────────────────────────────
def win_rate(s: pd.Series) -> float:
    return (s > 0).mean() * 100

def stats(group: pd.DataFrame, ret_col: str) -> dict:
    r = group[ret_col].dropna()
    if len(r) < MIN_SAMPLE:
        return {"n": len(r), "avg_ret_%": np.nan, "win_rate_%": np.nan}
    return {
        "n":          len(r),
        "avg_ret_%":  round(r.mean() * 100, 4),
        "win_rate_%": round(win_rate(r), 2),
    }

def build_stats_table(df: pd.DataFrame, group_col: str,
                      ret_cols=("ret_1", "ret_3", "ret_5"),
                      order=None) -> pd.DataFrame:
    rows = []
    groups = order if order else sorted(df[group_col].dropna().unique())
    for g in groups:
        sub = df[df[group_col] == g]
        for rc in ret_cols:
            s = stats(sub, rc)
            rows.append({group_col: g, "horizon": rc, **s})
    return pd.DataFrame(rows)

def print_table(title: str, df: pd.DataFrame):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")
    print(df.to_string(index=False))

def session_label(hour: int) -> str:
    if 9 <= hour <= 11:
        return "東京前場(9-11)"
    elif 12 <= hour <= 15:
        return "東京後場(12-15)"
    else:
        return "ナイト(16-翌6)"


# ── メイン ────────────────────────────────────────────────────
def main():
    # 1. 読み込み
    print("[1/5] データ読み込み...")
    df = pd.read_csv(INPUT_CSV, parse_dates=["datetime"])
    df = df[df["phase"].isin(PHASE_ORDER)].copy()   # 不明除外
    df = df.sort_values("datetime").reset_index(drop=True)
    print(f"      有効行数: {len(df):,}")

    # 2. リターン列
    print("[2/5] リターン列を作成...")
    for h in [1, 3, 5]:
        df[f"ret_{h}"] = df["close"].pct_change(h).shift(-h)   # h本後
        df[f"long_win_{h}"] = (df[f"ret_{h}"] > 0).astype(float)

    # 3. 補助列
    print("[3/5] 条件列を作成...")
    df["hour"]        = df["datetime"].dt.hour
    df["session"]     = df["hour"].map(session_label)
    df["di_diff_sign"]= np.where(df["plus_di"] - df["minus_di"] > 0, "+DI優勢", "-DI優勢")
    df["adx_bucket"]  = pd.cut(
        df["adx"],
        bins=[0, 15, 25, 35, 200],
        labels=["<15", "15-25", "25-35", "35+"],
        right=False
    ).astype(str)
    df["accel_sign"]  = np.where(df["adx_acceleration"] > 0, "加速度+", "加速度-")

    all_results = []   # CSV用に全テーブルを蓄積

    # ─────────────────────────────────────────
    # A. 位相別基本統計
    # ─────────────────────────────────────────
    print("[4/5] 集計中...")
    tbl_phase = build_stats_table(df, "phase", order=PHASE_ORDER)
    print_table("A. 位相別 勝率・平均リターン", tbl_phase)
    tbl_phase.insert(0, "table", "A_phase")
    all_results.append(tbl_phase)

    # ─────────────────────────────────────────
    # B-1. 位相 × 時間帯
    # ─────────────────────────────────────────
    SESSION_ORDER = ["東京前場(9-11)", "東京後場(12-15)", "ナイト(16-翌6)"]
    rows = []
    for phase in PHASE_ORDER:
        for sess in SESSION_ORDER:
            sub = df[(df["phase"] == phase) & (df["session"] == sess)]
            for rc in ["ret_1", "ret_3", "ret_5"]:
                s = stats(sub, rc)
                rows.append({"phase": phase, "session": sess, "horizon": rc, **s})
    tbl_session = pd.DataFrame(rows)
    print_table("B-1. 位相 × 時間帯 クロス集計", tbl_session)
    tbl_session.insert(0, "table", "B1_phase_x_session")
    all_results.append(tbl_session)

    # ─────────────────────────────────────────
    # B-2. 位相 × DI差分符号
    # ─────────────────────────────────────────
    rows = []
    for phase in PHASE_ORDER:
        for di in ["+DI優勢", "-DI優勢"]:
            sub = df[(df["phase"] == phase) & (df["di_diff_sign"] == di)]
            for rc in ["ret_1", "ret_3", "ret_5"]:
                s = stats(sub, rc)
                rows.append({"phase": phase, "di_diff_sign": di, "horizon": rc, **s})
    tbl_di = pd.DataFrame(rows)
    print_table("B-2. 位相 × DI差分符号 クロス集計", tbl_di)
    tbl_di.insert(0, "table", "B2_phase_x_di")
    all_results.append(tbl_di)

    # ─────────────────────────────────────────
    # B-3. 位相 × ADX水準バケット
    # ─────────────────────────────────────────
    ADX_BUCKET_ORDER = ["<15", "15-25", "25-35", "35+"]
    rows = []
    for phase in PHASE_ORDER:
        for bk in ADX_BUCKET_ORDER:
            sub = df[(df["phase"] == phase) & (df["adx_bucket"] == bk)]
            for rc in ["ret_1", "ret_3", "ret_5"]:
                s = stats(sub, rc)
                rows.append({"phase": phase, "adx_bucket": bk, "horizon": rc, **s})
    tbl_adxbk = pd.DataFrame(rows)
    print_table("B-3. 位相 × ADX水準バケット クロス集計", tbl_adxbk)
    tbl_adxbk.insert(0, "table", "B3_phase_x_adx_bucket")
    all_results.append(tbl_adxbk)

    # ─────────────────────────────────────────
    # B-4. 位相 × 加速度符号
    # ─────────────────────────────────────────
    rows = []
    for phase in PHASE_ORDER:
        for ac in ["加速度+", "加速度-"]:
            sub = df[(df["phase"] == phase) & (df["accel_sign"] == ac)]
            for rc in ["ret_1", "ret_3", "ret_5"]:
                s = stats(sub, rc)
                rows.append({"phase": phase, "accel_sign": ac, "horizon": rc, **s})
    tbl_accel = pd.DataFrame(rows)
    print_table("B-4. 位相 × 加速度符号 クロス集計", tbl_accel)
    tbl_accel.insert(0, "table", "B4_phase_x_accel")
    all_results.append(tbl_accel)

    # ─────────────────────────────────────────
    # C. 3重クロス: 芽生え × 加速度正 × DI差分正
    # ─────────────────────────────────────────
    sub3 = df[
        (df["phase"] == "芽生え") &
        (df["accel_sign"] == "加速度+") &
        (df["di_diff_sign"] == "+DI優勢")
    ]
    rows = []
    for rc in ["ret_1", "ret_3", "ret_5"]:
        s = stats(sub3, rc)
        rows.append({"phase": "芽生え", "accel_sign": "加速度+",
                     "di_diff_sign": "+DI優勢", "horizon": rc, **s})
    tbl_triple = pd.DataFrame(rows)
    print_table("C. 3重クロス: 芽生え × 加速度+ × +DI優勢", tbl_triple)
    tbl_triple.insert(0, "table", "C_triple_cross")
    all_results.append(tbl_triple)

    # ─────────────────────────────────────────
    # 参考: 全位相 × 加速度+ × +DI優勢 (3重)
    # ─────────────────────────────────────────
    rows = []
    for phase in PHASE_ORDER:
        sub = df[
            (df["phase"] == phase) &
            (df["accel_sign"] == "加速度+") &
            (df["di_diff_sign"] == "+DI優勢")
        ]
        for rc in ["ret_1", "ret_3", "ret_5"]:
            s = stats(sub, rc)
            rows.append({"phase": phase, "accel_sign": "加速度+",
                         "di_diff_sign": "+DI優勢", "horizon": rc, **s})
    tbl_triple_all = pd.DataFrame(rows)
    print_table("C'. 3重クロス(全位相): phase × 加速度+ × +DI優勢", tbl_triple_all)
    tbl_triple_all.insert(0, "table", "C2_triple_all_phases")
    all_results.append(tbl_triple_all)

    # ─────────────────────────────────────────
    # D. 方向別リターン（DI方向に合わせたエッジ）
    # ─────────────────────────────────────────
    # +DI優勢 → ロング方向そのまま / -DI優勢 → ショート方向（ret × -1）
    for h in [1, 3, 5]:
        rc = f"ret_{h}"
        df[f"directional_ret_{h}"] = np.where(
            df["di_diff_sign"] == "+DI優勢",
            df[rc],
            df[rc] * -1
        )

    dir_ret_cols = ["directional_ret_1", "directional_ret_3", "directional_ret_5"]

    # D-1. 位相別 directional_ret 集計
    rows = []
    for phase in PHASE_ORDER:
        sub = df[df["phase"] == phase]
        for rc in dir_ret_cols:
            r = sub[rc].dropna()
            if len(r) < MIN_SAMPLE:
                rows.append({"phase": phase, "horizon": rc,
                              "n": len(r), "avg_dir_ret_%": np.nan, "dir_win_rate_%": np.nan})
            else:
                rows.append({
                    "phase":          phase,
                    "horizon":        rc,
                    "n":              len(r),
                    "avg_dir_ret_%":  round(r.mean() * 100, 4),
                    "dir_win_rate_%": round(win_rate(r), 2),
                })
    tbl_dir_phase = pd.DataFrame(rows)
    print_table("D-1. 位相別 directional_ret (DI方向合わせ)", tbl_dir_phase)
    tbl_dir_phase.insert(0, "table", "D1_directional_by_phase")
    all_results.append(tbl_dir_phase)

    # D-2. 位相 × DI方向 × directional_ret  ← エッジの源泉確認
    rows = []
    for phase in PHASE_ORDER:
        for di in ["+DI優勢", "-DI優勢"]:
            sub = df[(df["phase"] == phase) & (df["di_diff_sign"] == di)]
            for rc in dir_ret_cols:
                r = sub[rc].dropna()
                if len(r) < MIN_SAMPLE:
                    rows.append({"phase": phase, "di_diff_sign": di, "horizon": rc,
                                 "n": len(r), "avg_dir_ret_%": np.nan, "dir_win_rate_%": np.nan})
                else:
                    rows.append({
                        "phase":          phase,
                        "di_diff_sign":   di,
                        "horizon":        rc,
                        "n":              len(r),
                        "avg_dir_ret_%":  round(r.mean() * 100, 4),
                        "dir_win_rate_%": round(win_rate(r), 2),
                    })
    tbl_dir_di = pd.DataFrame(rows)
    print_table("D-2. 位相 x DI方向 別 directional_ret", tbl_dir_di)
    tbl_dir_di.insert(0, "table", "D2_directional_phase_x_di")
    all_results.append(tbl_dir_di)

    # D-3. サマリー: directional_ret_5 の位相別エッジランキング（DI方向合算）
    dir_summary = (
        tbl_dir_phase[tbl_dir_phase["horizon"] == "directional_ret_5"]
        .sort_values("avg_dir_ret_%", ascending=False)
        [["phase", "n", "avg_dir_ret_%", "dir_win_rate_%"]]
    )

    # ─────────────────────────────────────────
    # 5. CSV出力
    # ─────────────────────────────────────────
    print(f"\n[5/5] CSV出力: {OUTPUT_CSV}")
    out = pd.concat(all_results, ignore_index=True)
    out.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    # サマリー表示
    print(f"\n{'='*60}")
    print("  [Summary] ret_1 phase win_rate ranking (raw long)")
    print(f"{'='*60}")
    summary = (
        tbl_phase[tbl_phase["horizon"] == "ret_1"]
        .sort_values("win_rate_%", ascending=False)
        [["phase", "n", "avg_ret_%", "win_rate_%"]]
    )
    print(summary.to_string(index=False))

    print(f"\n{'='*60}")
    print("  [Summary] directional_ret_5 phase edge ranking (DI-aligned)")
    print(f"{'='*60}")
    print(dir_summary.to_string(index=False))
    print(f"\n完了。出力: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
