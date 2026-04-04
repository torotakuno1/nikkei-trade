# NIKKEI TRADE 統合システム — 引き継ぎ v8 slim

## ⚠️ Claude運用ルール

- **Pine Script → チャット本文にコードブロック**（TV コピペ用。アーティファクト不可）
- **Python → アーティファクト/ファイル出力**

---

## システム構成と成績

### v6 — 日経225 1H トレンドフォロー（主力）

イン: NTサイン(MACD12/26/9, RSI14, DI14/ADX20閾値20, KVO34/55, スコア≥2, クロス, 平均足)
　+ VM(StdDev26, BB21σ0.6)同方向 + MTF(1H EMA20+MACD)同方向
　+ D20MA↑↓一致 + D20MA傾き≥0.05% + LDN16除外 + SQ週除外
アウト: NT反転 | SL 300円固定
商品: NK225MC(×10) | VT20 Max5枚

成績(2020/1-2026/3): NP 894.2万 | DD 72.6万(36.3%) | RF 12.23 | Sharpe 0.309 | 180trades
コード: scripts/pine/v6_final.pine

### 案C — 日経225 1H ハイブリッド（補完）

イン: (VM新規点灯+NTスコア) OR (NTフル+VM背景) + D20MA方向 + LDN16除外 + VR>1.00
アウト: VM消灯 OR NT反転 | SL 300円(PKG A)
商品: NK225MC(×10) | VT1+DD固定2枚

成績(同期間): NP 509.0万 | DD 57.7万(28.9%) | RF 8.77 | Sharpe 0.345 | 726trades
コード: scripts/pine/caseC_final.pine

### v6+案C 合算

NP 1,403.2万 | DD 85.9万(43.0%) | RF 16.33 | 全年+ | DD分散34%軽減

### Gold EWMAC — 2H トレンドフォロー

シグナル: EWMAC barbell(8,32)+(64,256) ロングのみ
フィルター: GVZ zscore>0
VT20% Max5 IDM=1.2 慣性10%
商品: MGC or GC

成績(2022/4-2026/4): NP 587.7万 | DD 40.6万(20.3%) | RF 14.46 | 年率+73.5% | Active 42%
コード: scripts/signal_engine/gold_ewmac.py

### USD/JPY — 保留（直近3年赤字、55年RF 14.44）

---

## VolTarget確定構成

| システム | 構成 | 理由 |
|---|---|---|
| v6 | VT20 Max5 DDなし | VTがDD期ボラ拡大で自動縮小→DDステップダウン冗長 |
| 案C | VT1+DD固定2枚 | DD -30%, RF +34% |
| Gold EWMAC | VT20% Max5 DDなし | v6と同じ結論 |

---

## マクロフィルター最終結論

**Gold採用: GVZ_z>0（RF 8.19→14.46, 台地-0.5〜+1.0, WF合格, VRP文献裏付け）**

上位組み合わせ(DXY+GVZ等)はActive 21-25%で稼働率低すぎ→見送り。
詳細ランキング・検証データ: research/macro_analysis_results.md

**v6/案C: マクロフィルター追加なし**（教訓#29: ポジションサイジング層が改善の主軸）

**商品間非対称性（教訓#34）:** GVZ→Gold専用、VIXレベル→N225専用。同指標が逆方向に効く。

---

## 直近の教訓（v8追加分）

34. フィルターは商品固有。GVZ方向はGoldを支配、N225に無関係。VIXレベルはN225を支配、Goldに逆効果
35. GVZ方向(IV拡大/縮小)がGold TF成否を決定。台地-0.5〜+1.0で過剰最適化ではない
36. SKEW>140は3システム全てで一貫有効（VIXと直交するテール情報）
37. FOMC週はGoldトレンドを破壊（PnL -15.8万, VIX非依存）。除外でRF +67%
38. 実質金利>1.5%でGold TF好成績（Jermann 2025の非線形デュレーション理論と整合）
39. Gold: ロングのみが最良（ショートトレンド弱く短い）
40. EWMAC速度: barbell(最速+最遅)が最良（Man AHL相関0.17と一致）

※教訓#1〜#33は確立済み。全文: 旧HANDOFF_v8.md参照

---

## 口座・商品

口座: IBKR Japan UXXXXXXXX | 純資産200万予定(現約92万)
N225MC: OSE:NK225MC1! ×10, 1ティック5円=50円, 証拠金~2.5-3万/枚
IB Gateway: port 4001 | Tailscale: xxx.xxx.xxx.xxx
Gold MGC: IBKR Japanでの取引可否要確認

---

## 自動売買パイプライン

- [x] Phase 0-3: HW・OS・IB Gateway・データ取得
- [x] Phase 4: v6シグナルエンジン（一致率92.4%, 残差異はペーパーで検証）
- [ ] Phase 5-6: 発注エンジン・監視（ブラケットSL必須, VT20計算, Telegram通知）
- [ ] Phase 7: ペーパートレード（Pine並行1-2週間）
- [ ] Phase 8: ライブ（1枚固定→VT20 Max5）

案Cの Python移植は未着手。

---

## 未テスト・次ステップ

**優先高:**
1. 口座200万 or 300万判断（合算DD 43% vs 29%）
2. Gold MGC IBKR Japan取引可否
3. Gold EWMAC+GVZ本番スクリプト
4. Phase 5-8完成

**優先中:**
5. GVZフィルター年別一貫性チェック
6. USD/JPY — 次トレンド発生時に小ポジション

**構造的課題:**
- 合算DD 85.9万/200万=43%（300万なら29%）
- さえない期間(2021/9-2023/4): v6 +45.1万でヘッジ薄い
- Gold EWMAC追加で3商品体制→DD分散さらに改善見込み

---

## 参照ファイル（必要時にアップロード）

| ファイル | 用途 |
|---|---|
| scripts/pine/v6_final.pine | v6修正時 |
| scripts/pine/caseC_final.pine | 案C修正時 |
| scripts/signal_engine/gold_ewmac.py | Gold EWMAC修正時 |
| docs/PINE_TO_PYTHON_IBKR_MIGRATION_GUIDE.md | Phase 5-6着手時 |
| research/academic_validation_JP.md | 学術検証参照時 |
| research/macro_analysis_results.md | フィルター詳細確認時 |
| 旧HANDOFF_v8.md | 教訓#1-33、バージョン履歴、テスト詳細 |
