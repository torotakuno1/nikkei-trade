# NIKKEI TRADE 統合システム - 引き継ぎ v10

## ⚠️ Claude運用ルール

- **Pine Script → チャット本文にコードブロック**（TVコピペ用。アーティファクト不可）
- **Python → ファイル出力**（Mac/PCで実行するため）

---

## ドキュメント構成

本ファイル = 意思決定・結論・教訓のみ。コード・詳細データは別ファイル参照。
- scripts/pine/ → v6, 案C Pine Script
- scripts/signal_engine/ → v6 Python移植版, gold_ewmac.py
- scripts/execution/ → v6, 案C, Gold EWMAC リアルタイムエンジン（稼働中）
- scripts/data/ → build_nk225_database.py（J-Quants→連続先物変換）
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

## 2026/4/8 セッション成果

### A. レジーム×マクロ指標 事後分析（v6: 523トレード、案C: 1,808トレード）

VIX/VIX3M/GVZ/SKEW/DXY/実質金利/USD-JPYの日次データとトレードリストを結合し、レジーム別PF/勝率を集計。さらに前半/後半安定性、年別推移、方向別、複合レジーム、連続負け分析を実施。

**主要発見:**

1. **実質金利 0-1% = 「死のゾーン」（【断定】前半/後半で一貫）**
   - v6: PF 0.63 (N=227, -317万)。前半PF 0.58、後半PF 0.71。両方損失。
   - 案C: PF 0.93 (N=785)。前半PF 0.95、後半PF 0.89。同様。
   - ロング/ショート両方で悪い。構造的な不調。
   - 理由推測: 中途半端な金利水準→市場テーマ不在→トレンド消失

2. **v6と案Cのレジーム非対称性（2システム並行の分散効果を裏付け）**
   - v6: リスクオフ(高VIX/円高)で強い。VIX>30 → PF 3.79
   - 案C: リスクオン(円安)で強い。USDJPY_ROC5>1% → PF 1.68
   - VIX>30: v6 PF 3.79 vs 案C PF 0.97（正反対）

3. **SKEW≥130の効果は時期依存（【格下げ】前半で不安定）**
   - v6前半: SKEW≥130 PF 0.96 vs SKEW<130 PF 0.75（差なし）
   - v6後半: SKEW≥130 PF 2.63 vs SKEW<130 PF 1.04（劇的差）
   - 2021年以降ほぼ全トレードがSKEW≥130環境→「最近好調」を拾っただけの疑い

4. **GVZ_z>1がN225でも有効（想定外の発見）**
   - v6: PF 2.25 (N=99)、案C: PF 2.20 (N=265)
   - ゴールド恐怖→グローバルリスクオフ→N225トレンド発生の連鎖

5. **複合レジーム: SKEW≥130 + RY≥1%が突出**
   - v6: PF 3.79, avg +103,300円 (N=75)
   - ただしSKEW≥130 + RY 0-1%はPF 0.68で損失→実質金利が支配的

**活用方針:** Entry/Exitロジックは変更しない（教訓#29）。サイズ調整の裁量参照・DD心理準備として使用。

→ 詳細: research/regime_analysis_results.csv

### B. v6 ウォークフォワード分析（WFA）

**4年データ（TV 1H, 2022/4-2026/4）でフルロジック WFA:**
- IS=18mo, OOS=6mo, step=6mo, 5窓
- パラメータグリッド: slope_thresh×adx_thresh (12組)
- OOS黒字率: **4/5 (80%)**
- 平均OOS PF: **6.45**
- adx_thresh=20が全5窓で選択（完全安定）
- **結論: v6はWFA的に堅牢。パラメータではなくロジック構造がエッジの源泉。**

**13年データ（J-Quants 1H, 2013/1-2026/4）での WFA:**
- 22窓で実行したが、OOS黒字率 0/22
- **原因: J-Quantsデータ→連続先物変換に問題あり（バー境界30分ズレ + ロール処理不一致）**
- TVデータとの比較で完全一致率1.4%、平均352円乖離を確認
- **→ build_nk225_database.pyの修正が必要（次回セッション持ち越し）**

### C. データ購入・インフラ

1. **J-Quants DataCube 購入完了**
   - 日経225ミニ 1分足 2013/01-2026/03（159ヶ月）
   - 160ファイルDL済み、build_nk225_database.pyで連続先物構築済み
   - **要修正:** バー境界(:00→:30始まりに統一)、ロール処理(TV NK225M1!と一致させる)

2. **FirstRate Complete Intraday Bundle（¥127,192）— 未購入・判断済み**
   - J-Quantsデータ修正・WFA完了後に購入予定
   - 用途: レジーム調査(クロスアセット相関)、5分足アルゴ商品選定、GC長期検証

3. **build_nk225_database.py 作成**
   - scripts/data/ に配置
   - ZIP解凍→CSV結合→限月ロール→連続先物→1min/5min/1H/日足集約
   - 欠損月自動チェック機能付き
   - J-Quantsのカラム名変更(2022年頃PascalCase→lowercase)に対応済み

---

## 教訓（v10追加分 #42-46）

過去の教訓#1-41はv8参照。

42. 多層フィルターシステムは指標極値を構造的に排除→極値フィルター追加は冗長
43. **実質金利0-1%はv6/案C両方の「死のゾーン」。前半/後半/方向別で一貫。構造的。**
44. **SKEW≥130の効果は時期依存。2021年以降のSKEW水準構造変化によりフィルターとして信頼できない。**
45. **v6のエッジはパラメータではなくロジック構造(NT+VM+MTF+DMA傾き+SQ週+LDN16)にある。MTF/SQ/LDN16を省略するとエッジ消失（WFA v1 vs v2で実証）。**
46. **J-Quantsデータ→連続先物変換は、バー境界とロール処理がTVデータと一致しないと全く別物になる。データソース統一は最重要。**

---

## 未テスト・将来の検討事項

**最優先（次回セッション）:**
- build_nk225_database.py修正（バー境界:30始まり、ロール処理TV一致）
- 修正後データでv6 WFA 13年分再実行
- 案CのWFA

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

## テスト完了サマリー

N225: 約100パターン（エントリー90+ / ポジションサイジング13）
Gold EWMAC: 約4,500+パターン
N225レジーム×マクロ: 9指標×複数ビン（本セッション追加）
v6 WFA: 4年5窓(OK) + 13年22窓(データ不一致で無効→要再実行)
**総計: 約4,700+パターン**
