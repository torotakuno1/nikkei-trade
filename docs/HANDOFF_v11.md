# NIKKEI TRADE 統合システム - 引き継ぎ v11

## ⚠️ Claude運用ルール

- **Pine Script → チャット本文にコードブロック**（TVコピペ用。アーティファクト不可）
- **Python → ファイル出力**（Mac/PCで実行するため）

---

## ドキュメント構成

本ファイル = 意思決定・結論・教訓のみ。コード・詳細データは別ファイル参照。
- scripts/pine/ → v6, 案C Pine Script
- scripts/signal_engine/ → v6 Python移植版(一致率92.4%), gold_ewmac.py
- scripts/execution/ → v6, 案C, Gold EWMAC リアルタイムエンジン（稼働中）
- scripts/data/ → build_nk225_database_v2.py（J-Quants→連続先物変換、TV一致確認済み）
- research/ → macro_analysis_results.md, academic_validation_JP.md, regime_analysis_results.csv
- data/README.md → データファイル所在

---

## システム構成と成績

### v6 — 日経225 1H トレンドフォロー（主力）

イン: NTサイン(MACD12/26/9, RSI14, DI14/ADX20閾値20, KVO34/55, スコア≥2) + VM(StdDev26, BB21σ0.6)同方向 + MTF(1H EMA20+MACD)同方向 + D20MA方向一致 + D20MA傾き≥0.05% + LDN16除外 + SQ週除外
アウト: NT反転 | SL 300円 | VT20 Max5枚

成績(2020/1-2026/3): NP 894.2万 | DD 72.6万(36.3%) | RF 12.23 | Sharpe 0.309 | 180trades

### 案C — 日経225 1H ハイブリッド（補完）

イン: (VM新規+NTスコア) OR (NTフル+VM背景) + D20MA方向 + LDN16除外 + VR>1.00
アウト: VM消灯 OR NT反転 | SL 300円 | VT1+DD固定2枚

成績(同期間): NP 509.0万 | DD 57.7万(28.9%) | RF 8.77 | Sharpe 0.345 | 726trades

### v6+案C合算

NP 1,403.2万 | DD 85.9万(43.0%) | RF 16.33 | 全年+ | DD分散34%軽減

### Gold EWMAC — 2H トレンドフォロー

シグナル: EWMAC barbell(8,32)+(64,256) ロングのみ
フィルター: GVZ zscore > 0
VT20% Max5, IDM=1.2, 慣性10% | 商品: MGC or GC

成績(2019/1-2026/4, 7年超): NP 977.8万 | DD 57.1万(28.6%) | RF 17.14 | Active 42%

### USD/JPY — 保留

---

## Phase 状態

- Phase 0-4: ✅完了（HW→IB Gateway→データ→シグナルエンジン v6一致率92.4%）
- Phase 5-6: ✅完了（発注エンジン・Telegram通知。3エンジン稼働中。port 4002 paper）
- **Phase 7: 🔄 ペーパートレード中**（v6, 案C, Gold EWMAC全稼働）
- Phase 8: 未着手（ライブ）

---

## 2026/4/8-9 セッション成果

### A. J-Quantsデータ→連続先物変換の修正完了（build_nk225_database_v2.py）

**問題:** v1のbuild_nk225_database.pyで生成した1Hデータが、TVデータと完全一致率1.4%・平均352円乖離（教訓#46）。

**原因2点を特定・修正:**

1. **バー境界**: v1は`resample('1h')`で:00始まり固定。TVはOSE取引時間に基づきセッション開始時刻始まり。
   - OSE取引時間変更（2024/11/5）でTVバー境界が変化することを発見:
     - ～2024/11/4: 夜間:30始まり(16:30,17:30,...), 日中:30始まり(08:45,09:30,10:30,...)
     - 2024/11/5～: 夜間:00始まり(17:00,18:00,...), 日中:00始まり(08:45,09:00,10:00,...)
   - v2: カスタム`assign_tv_1h_bucket_fast()`で両時代に対応

2. **ロール処理**: v1は`ROLL_DAYS_BEFORE_SQ=7`（SQ 7日前固定）。TVはSQ当日ロール。
   - TV「つなぎ足の限月の切り替え」で実データ確認: 2025-12-12, 2026-03-13 = SQ当日
   - v2: `roll_date = sq_date`（SQ当日ロール）に変更

**TV一致検証結果:**

| 日時 | 項目 | J-Quants v2 | TV（通常） | 差 |
|---|---|---|---|---|
| 2024-01-15 09:30 | O | 35,640 | 35,640 | **0** |
| 2024-01-15 09:30 | H | 35,860 | 35,860 | **0** |
| 2024-01-15 09:30 | L | 35,605 | 35,605 | **0** |
| 2024-01-15 09:30 | C | 35,765 | 35,765 | **0** |
| 2024-01-15 09:30 | Vol | 120,878 | 120,880 | +2 |

**→ OHLC4値完全一致。教訓#46の問題は解決。**

**追加発見: TVの「限月調整」モード**
- v6/案Cバックテストは「限月調整」(backward Panama Canal adjustment)で実行されていた
- J-Quantsは無調整 → 過去に遡るほど価格が系統的にシフト（2024/1で-1180円）
- ただしv6のシグナルロジックは全て相対指標(MACD,RSI,ADX,KVO,BB,EMA)なので、無調整データでのWFAで実質的な影響なし【仮説 Conf 0.85】

**生成データ（保存先: C:\Users\CH07\Desktop\jquants_data\）:**
- nk225m_1min_continuous.csv (221.5 MB) — 2013/1-2026/4
- nk225m_5min_continuous.csv (40.7 MB)
- nk225m_1h_continuous.csv (3.7 MB, 65,293行) — WFA用
- nk225m_daily_continuous.csv (0.2 MB)

### B. v6 WFA 13年 — Pythonロジックバグ判明

修正済みJ-Quantsデータでv6 WFA 13年を実行（v6_wfa_13y.py）:
- IS=18mo, OOS=6mo, step=6mo, 23窓
- **結果: OOS黒字率 0/23 (0%), IS期間も全窓マイナス**

**原因: WFAスクリプト内のv6ロジック実装にバグがある。**
- IS期間ですら全窓マイナス → データではなくシグナル生成の問題
- 既存のv6 Python移植版（scripts/signal_engine/、Pine一致率92.4%確認済み）とは別に新規実装したロジックが不一致
- **→ 次回セッションで検証済みエンジンをWFAに組み込んで再実行**

---

## 教訓（v11追加分 #47-49）

過去の教訓#1-46はv8/v10参照。

47. **OSE取引時間変更(2024/11/5)でTVの1Hバー境界が変化する。夜間:30→:00、日中セッション時間も延長。J-Quantsデータの集約は両時代対応が必須。**
48. **TVの「限月調整」と「通常」でOHLC値が異なる。バックテストがどちらで実行されたか記録必須。v6/案Cは「限月調整」。J-Quantsは無調整。相対指標ベースのシグナルには影響軽微。**
49. **Pine ScriptのロジックをPythonで再実装する際、検証済みエンジン（一致率確認済み）を流用すべき。新規実装は微妙なロジック差異（crossover判定、VM状態遷移、日足→1Hマッピング等）でバグが入りやすい。**

---

## 未テスト・将来の検討事項

**最優先（次回セッション）:**
- **v6 WFA 13年再実行**: 検証済みv6シグナルエンジン(scripts/signal_engine/)をWFAスクリプトに組み込み
- **案CのWFA**

**優先度高:**
- FirstRate Complete Intraday購入 → レジーム調査 + 5分足アルゴ商品選定
- Carverの『Advanced Futures Trading Strategies』読了
- 商品数拡大検討（MES/MCL等マイクロ先物）

**優先度中:**
- Gold EWMACのトレードリスト作成 → レジーム分析
- USD/JPY次トレンド時に再開

---

## 口座・商品情報

口座: IBKR Japan | 純資産: 300万（2026/4増資済み）
商品: NK225MC(OSE.JPN, ×10) | MGC(COMEX, 取引可確認済)
miniPC: Getorli Ryzen 5300U/16GB/Win11Pro | IB Gateway port 4002(paper)/4001(live)
リモート: Tailscale + Parsec

---

## インフラ構成（確定）

（v9/v10と同一、変更なし。詳細はv9参照。）

---

## テスト完了サマリー

N225: 約100パターン（エントリー90+ / ポジションサイジング13）
Gold EWMAC: 約4,500+パターン
N225レジーム×マクロ: 9指標×複数ビン
v6 WFA: 4年5窓(OK) + 13年23窓(Pythonバグで無効→検証済みエンジンで要再実行)
**総計: 約4,700+パターン**

---

## v10→v11 変更履歴（2026/4/9）

- build_nk225_database_v2.py完成・TV一致検証完了
- OSE取引時間変更(2024/11/5)対応のバー境界ロジック実装
- TVロールルール確定（SQ当日ロール、出来高ベース）
- TV「限月調整」vs「通常」の差異を記録
- v6 WFA 13年実行→Pythonロジックバグ判明→次回持越し
- 教訓#47-49追加
- J-Quantsデータ保存先記録（C:\Users\CH07\Desktop\jquants_data\）
