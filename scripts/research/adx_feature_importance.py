"""
adx_feature_importance.py
v6エントリー足 vs 非エントリー足 の2値分類で
ADX系特徴量の重要度を RandomForest で評価する。
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")          # GUI不要で描画
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import warnings
warnings.filterwarnings("ignore")

from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, roc_auc_score
from sklearn.preprocessing import LabelEncoder

# ── パス ────────────────────────────────────────────────────
ADX_CSV = r"C:\Users\CH07\Desktop\jquants_data\nk225m_1h_adx_phase.csv"
V6_CSV  = r"C:\Users\CH07\Desktop\jquants_data\v6_VT20_Max5_[Strategy]_OSE_NK225M1!_2026-04-09_6ed08.csv"
OUT_FIG = r"C:\Users\CH07\Desktop\jquants_data\adx_feature_importance.png"

PHASE_MAP  = {"芽生え": 1, "成長": 2, "成熟": 3, "衰退": 4, "不明": 0}
TRAIN_RATIO = 0.7   # 時系列分割比率
SMOOTHING_W  = 5    # velocity移動平均窓


# ── 日本語フォント設定 ──────────────────────────────────────
def setup_font():
    candidates = ["MS Gothic", "Yu Gothic", "Meiryo", "IPAexGothic",
                  "Noto Sans CJK JP", "TakaoPGothic"]
    available = {f.name for f in fm.fontManager.ttflist}
    for c in candidates:
        if c in available:
            plt.rcParams["font.family"] = c
            return c
    plt.rcParams["font.family"] = "sans-serif"
    return "sans-serif"


# ── メイン ───────────────────────────────────────────────────
def main():
    # ────────────────────────────────────
    # 1. ADX位相CSVを読み込む
    # ────────────────────────────────────
    print("[1/6] ADX位相データ読み込み...")
    adx = pd.read_csv(ADX_CSV, parse_dates=["datetime"])
    adx = adx.sort_values("datetime").reset_index(drop=True)
    print(f"      行数: {len(adx):,}  期間: {adx['datetime'].iloc[0]} - {adx['datetime'].iloc[-1]}")

    # ────────────────────────────────────
    # 2. v6エントリー行を抽出
    # ────────────────────────────────────
    print("[2/6] v6 CSVを読み込み・エントリー行を抽出...")
    v6 = pd.read_csv(V6_CSV, encoding="utf-8-sig")

    # カラム確認
    print(f"      カラム: {list(v6.columns)}")
    print(f"      行数  : {len(v6)}")
    type_col = v6.columns[1]    # 'タイプ'
    dt_col   = v6.columns[2]    # '日時'

    entries = v6[v6[type_col].str.contains("エントリー", na=False)].copy()
    entries["entry_dt"] = pd.to_datetime(entries[dt_col])
    entries_s = entries.sort_values("entry_dt").reset_index(drop=True)
    print(f"      エントリー数: {len(entries_s)}")
    print(f"      期間: {entries_s['entry_dt'].iloc[0]} - {entries_s['entry_dt'].iloc[-1]}")

    # ────────────────────────────────────
    # 3. merge_asof でADXデータに結合
    #    (エントリー時刻の直前バー、35分以内)
    # ────────────────────────────────────
    print("[3/6] datetimeマッチング（merge_asof, tolerance=35min）...")
    matched = pd.merge_asof(
        entries_s[["entry_dt"]],
        adx.rename(columns={"datetime": "bar_dt"}),
        left_on="entry_dt",
        right_on="bar_dt",
        tolerance=pd.Timedelta("35min"),
        direction="backward"
    ).dropna(subset=["bar_dt"])

    print(f"      マッチ成功: {len(matched)} / {len(entries_s)}")
    entry_datetimes = set(matched["bar_dt"])

    # ────────────────────────────────────
    # 4. ラベル列 + 特徴量エンジニアリング
    # ────────────────────────────────────
    print("[4/6] 特徴量を構築...")
    df = adx.copy()

    # エントリー/非エントリーラベル
    df["label"] = df["datetime"].isin(entry_datetimes).astype(int)

    # 位相を数値エンコード
    df["phase_num"] = df["phase"].map(PHASE_MAP).fillna(0).astype(int)

    # DI差分
    df["di_diff"]     = df["plus_di"]  - df["minus_di"]
    df["di_diff_abs"] = df["di_diff"].abs()

    # 時間帯
    df["hour"] = df["datetime"].dt.hour

    # velocity の smoothing
    df["velocity_smooth"] = (
        df["adx_velocity"]
        .rolling(SMOOTHING_W, min_periods=1)
        .mean()
    )

    FEATURES = [
        "adx", "plus_di", "minus_di",
        "adx_velocity", "adx_acceleration",
        "phase_num",
        "di_diff", "di_diff_abs",
        "hour",
        "velocity_smooth",
    ]

    # 有効行（位相が不明でない、かつ特徴量が揃っている）
    valid = df[df["phase"].isin(["芽生え","成長","成熟","衰退"])].copy()
    valid = valid.dropna(subset=FEATURES + ["label"])
    valid = valid.sort_values("datetime").reset_index(drop=True)
    print(f"      有効行数: {len(valid):,}  エントリー足: {valid['label'].sum()}")

    # ────────────────────────────────────
    # 5. 時系列分割 → RandomForest
    # ────────────────────────────────────
    print(f"[5/6] RandomForest学習（時系列分割 {int(TRAIN_RATIO*100)}%/{int((1-TRAIN_RATIO)*100)}%）...")
    split = int(len(valid) * TRAIN_RATIO)
    train = valid.iloc[:split]
    test  = valid.iloc[split:]

    X_train, y_train = train[FEATURES], train["label"]
    X_test,  y_test  = test[FEATURES],  test["label"]

    print(f"      train: {len(train):,} 行  (エントリー: {y_train.sum()})")
    print(f"      test : {len(test):,} 行  (エントリー: {y_test.sum()})")

    clf = RandomForestClassifier(
        n_estimators=300,
        max_depth=6,
        class_weight="balanced",   # 不均衡クラス対応
        random_state=42,
        n_jobs=-1
    )
    clf.fit(X_train, y_train)

    # ────────────────────────────────────
    # 6. 評価 + 重要度表示
    # ────────────────────────────────────
    print("[6/6] 評価...")
    y_pred = clf.predict(X_test)
    y_prob = clf.predict_proba(X_test)[:, 1]

    print(f"\n{'='*60}")
    print("  Classification Report (test period)")
    print(f"{'='*60}")
    print(classification_report(y_test, y_pred, target_names=["非エントリー", "エントリー"]))

    auc = roc_auc_score(y_test, y_prob)
    print(f"  ROC-AUC: {auc:.4f}")

    # 特徴量重要度
    imp = pd.Series(clf.feature_importances_, index=FEATURES).sort_values(ascending=False)
    print(f"\n{'='*60}")
    print("  Feature Importance (Mean Decrease in Impurity)")
    print(f"{'='*60}")
    for feat, score in imp.items():
        bar = "#" * int(score * 300)
        print(f"  {feat:22s}: {score:.4f}  {bar}")

    # ────────────────────────────────────
    # 棒グラフ保存
    # ────────────────────────────────────
    font_used = setup_font()
    fig, ax = plt.subplots(figsize=(9, 6))
    imp_sorted = imp.sort_values()
    colors = plt.cm.RdYlGn(np.linspace(0.2, 0.85, len(imp_sorted)))
    bars = ax.barh(imp_sorted.index, imp_sorted.values, color=colors)

    # 値ラベル
    for bar, val in zip(bars, imp_sorted.values):
        ax.text(val + 0.001, bar.get_y() + bar.get_height()/2,
                f"{val:.4f}", va="center", fontsize=9)

    ax.set_xlabel("Feature Importance (MDI)", fontsize=11)
    ax.set_title(
        f"ADX Feature Importance  |  RF n=300  |  AUC={auc:.3f}\n"
        f"train~{valid['datetime'].iloc[split-1].date()}  "
        f"test:{valid['datetime'].iloc[split].date()}~{valid['datetime'].iloc[-1].date()}",
        fontsize=11
    )
    ax.set_xlim(0, imp.max() * 1.25)
    plt.tight_layout()
    plt.savefig(OUT_FIG, dpi=150, bbox_inches="tight")
    print(f"\n  グラフ保存: {OUT_FIG}")

    # テスト期間の日付範囲
    print(f"\n  テスト期間: {test['datetime'].iloc[0].date()} - {test['datetime'].iloc[-1].date()}")
    print("完了。")


if __name__ == "__main__":
    main()
