# NIKKEI TRADE 統合システム - 引き継ぎ v12

## ⚠️ Claude運用ルール

- **Pine Script → チャット本文にコードブロック**（TVコピペ用。アーティファクト不可）
- **Python → ファイル出力**（Mac/PCで実行するため）

---

## ドキュメント構成

本ファイル = 意思決定・結論・教訓のみ。コード・詳細データは別ファイル参照。
- scripts/pine/ → v6, 案C Pine Script
- scripts/execution/ → v6, 案C, Gold EWMAC リアルタイムエンジン（**Webhook方式に移行予定**）
- scripts/data/ → build_nk225_database_v3.py（Panama Canal adjustment付き。ただしWFA用途ではTV CSVが正）
- research/ → regime_analysis_results.csv, wfa_results/
- data/README.md → データファイル所在

---

## システム構成と成績

### v6 — 日経225 1H トレンドフォロー（主力）

イン: NTサイン(MACD12/26/9, RSI14, DI14/ADX20閾値20, KVO34/55, スコア≥2) + VM(StdDev26, BB21σ0.6)同方向 + MTF(1H EMA20+MACD)同方向 + D20MA方向一致 + D20MA傾き≥0.05% + LDN16除外 + SQ週除外
アウト: NT反転 | SL 300円 | VT20 Max5枚

成績(2020/1-2026/3, TV strategy tester): NP 894.2万 | DD 72.6万 | RF 12.23 | 180trades
全期間(2013-2026, TV 360trades/1枚): NP +146.9万 | PF 1.50 | RF 2.96

### 案C — 日経225 1H ハイブリッド（補完）

イン: (VM新規+NTスコア) OR (NTフル+VM背景) + D20MA方向 + LDN16除外 + VR>1.00
アウト: VM消灯 OR NT反転 | SL 300円 | VT1+DD固定2枚

成績(同期間, TV strategy tester): NP 509.0万 | DD 57.7万 | RF 8.77 | 726trades
全期間(2008-2026, TV 1809trades/1枚): NP +294.7万 | PF 1.27 | RF 5.02

### Gold EWMAC — 2H トレンドフォロー

シグナル: EWMAC barbell(8,32)+(64,256) ロングのみ
フィルター: GVZ zscore > 0
VT20% Max5, IDM=1.2, 慣性10% | 商品: MGC or GC

成績(2019/1-2026/4, 7年超): NP 977.8万 | DD 57.1万 | RF 17.14 | Active 42%

### USD/JPY — 保留

---

## v6/案C ウォークフォワード分析（WFA）— TV トレードリスト直接分析

**方法:** Pine Script strategy testerのトレードリストCSVを直接IS/OOS分割。Pythonシグナルエンジンは使用しない（理由は教訓#50参照）。
**設定:** IS=18mo, OOS=6mo, step=6mo。パラメータ最適化なし（固定パラメータのOOS損益のみ評価）。1枚あたりに正規化。

### v6 WFA結果

| 期間 | 窓数 | OOS黒字率 | 平均PF | NP/1枚 | PF | RF |
|------|------|-----------|--------|--------|-----|-----|
| 全期間 2013-2026 | 22 | 14/22 (**64%**) | 2.58 | +146.9万 | 1.50 | 2.96 |
| 前半 2013-2017 | 8 | 2/8 (25%) | — | -45.1万 | 0.57 | -1.01 |
| **後半 2018-2026** | **13** | **10/13 (77%)** | **3.63** | **+192.1万** | **2.02** | **5.26** |

**解釈:** v6は2018年以降の市場構造でロバスト（77%黒字）。2013-2017はPF 0.57で構造的にマイナス。v6のエッジは全時代普遍ではなく、2018年以降の市場構造（高ボラ・トレンド発生頻度）に依存。

### 案C WFA結果

| 期間 | 窓数 | OOS黒字率 | 平均PF | NP/1枚 | PF | RF |
|------|------|-----------|--------|--------|-----|-----|
| 全期間 2008-2026 | 33 | 26/33 (**79%**) | 1.29 | +295.9万 | 1.28 | 5.04 |
| **2018年以降** | **14** | **13/14 (93%)** | **1.42** | **+294.3万** | **1.43** | **7.10** |

**解釈:** 案Cは全期間でも79%黒字。2018以降は93%で圧倒的。OR条件の高頻度トレード（N=946）が分散効果を生み、時代依存性がv6より大幅に低い。

### v6 vs 案C 比較（2018年以降）

| 指標 | v6 | 案C | 優位 |
|------|-----|-----|------|
| OOS黒字率 | 77% | **93%** | 案C |
| PF | **2.02** | 1.43 | v6 |
| RF | 5.26 | **7.10** | 案C |
| NP/1枚 | +192万 | **+294万** | 案C |
| N | 222 | **946** | 案C |

**結論:** AND条件厳選型(v6)はPF高いが時代依存強い。OR条件高頻度型(案C)はPFは低いがロバスト性圧倒的。2システム並行運用の設計判断は正しい。

---

## ★ 方針転換: v6/案C ライブ執行を TV Webhook 方式に変更

### 経緯

v6 WFA 13年をPythonシグナルエンジンで実行したところ、全23窓IS/OOS全マイナス（PF 0.05）。原因調査の結果、**Pine ScriptとPythonのシグナル生成が構造的に一致しない**ことが判明。

TV トレードリスト360件とPythonシグナルを突合せた結果:
- Pythonが検出できたのは**23%のみ**（77%が不一致）
- 不一致の主因: EMA初期値差異、BB帯幅のddof、intrabar path simulation、request.security()再現困難性など**最低6層の構造的差異**
- 限月調整（Panama Canal backward adjustment）を実装しても改善なし（相対指標には影響しないため）
- 業界コンセンサス: 「Pine→Python 100%一致は構造的に不可能」（QuantConnect公式見解）

### 新方式

```
TV Pine Script (v6/案C)
  ↓ Webhook (HTTP POST, alert発火時)
Python サーバー (miniPC, Flask/FastAPI)
  ↓ パース → 発注ロジック
IBKR IB Gateway (ブラケットオーダー)
  ↓
Telegram通知
```

**メリット:**
- Pine Scriptがシグナル源 → 一致率100%（PythonはIBKR発注のみ担当）
- 現行のブラケット注文・再接続・Telegram通知コードは流用可能
- Pythonシグナルエンジン部分が不要になりコードが大幅簡素化

**要件:**
- TradingView Premium（契約済み）
- miniPCでWebhookサーバー常時稼働
- レイテンシー: 1-5秒（1H足戦略には十分）

### Gold EWMACの扱い

Gold EWMACはPythonファーストで設計されたシステムであり、Pine Script依存がない。**現行のPythonエンジン（gold_ewmac_engine.py）をそのまま継続**。Webhook化の対象外。

---

## Phase 状態

- Phase 0-4: ✅完了
- Phase 5-6: ✅完了（3エンジン稼働中。ただしv6/案Cは**Webhook方式に移行予定**）
- **Phase 7: 🔄 ペーパートレード中** → Webhook移行後に再開
- Phase 8: 未着手（ライブ）

**次のマイルストーン:** v6/案C Webhook実装 → ペーパー並行稼働 → ライブ

---

## 教訓（v12追加分 #50-53）

過去の教訓#1-49はv8/v10/v11参照。

50. **Pine Script→Python完全一致は構造的に不可能。** EMA初期値(first value vs SMA seed)、intrabar path simulation、request.security()のlookahead、BB帯幅のddof(N vs N-1で2.6%差)、連続先物構築方式、取引所セッション定義の少なくとも6層で差異が発生。QuantConnect公式: 「100%一致は通常不可能であり目的にすべきでない」。PyneCore(14-15桁精度)で指標計算は解決可能だが、データ層の差異は解決不能。

51. **限月調整(Panama Canal backward adjustment)は相対指標(MACD/RSI/ADX/BB/EMA)に影響しない。** 各限月期間内ではオフセットが一定であり、差分・比率ベースの指標は不変。build_nk225_database_v3.pyで実装・検証済み。v6 WFA結果はv2(無調整)とv3(調整済み)で同一。教訓#48の「影響軽微」判断は正しかった。

52. **AND条件厳選型(v6)はPF高いが時代依存が強い。OR条件高頻度型(案C)はPF低いがロバスト性が圧倒的。** v6: 2013-2017 PF 0.57（構造的マイナス）、2018-2026 PF 2.02。案C: 全期間OOS黒字79%、2018以降93%。高頻度トレードの分散効果が時代依存性を緩和する。

53. **Pine Scriptで開発したシステムのライブ執行は、Webhook中継が最も合理的。** Pythonでのロジック再実装は6層の構造的差異により一致率23%（v6突合せ実績）。Webhook方式なら一致率100%でレイテンシー1-5秒（1H足戦略に十分）。Pythonファーストで設計したシステム(Gold EWMAC)にはこの問題は発生しない。

54. **次元独立性: 同一次元の指標複数（RSI+DI差分=モメンタム×2）はRF 1.40。多次元独立フィルター（トレンド×モメンタム×ボラ×MTF×時間）はRF 12.17。** 新システム設計時、採用指標の次元分類を最初に行い、同一次元2つ以上は冗長と判断すべき。

55. **エグジット遅延は最も危険な変更。** NT反転エグジットに「ADX高い間ホールド」等の遅延ロジック追加 → RF 12.26→1.91〜7.56。エグジットは早すぎる方が安全。

56. **VM条件は再エントリーで迂回不可。** VM再点灯待ちだと0回発火、VM不要にするとRF悪化。

57. **最適化済みAND条件は追加も削除も不可。** A-Kプリセット全滅（全てRF 12.17以下）。条件緩和も条件追加も改善しない。

58. **トレンド指標は「水準」より「位相」（速度・加速度）で使え。** ADX≥25等のレベルフィルターより、ADX velocity（1次微分）の方がfeature importance 1位。Gold EWMACのGVZ zscore>0も同じ原則。

59. **位相ラベルの離散化（芽生え/成長/成熟/衰退の4値）はfeature importance最下位。** 連続値（velocity, acceleration）のまま使う方が情報量を保持する。

60. **NTスコア+VM構造はN225固有のエッジ。** ESではv6 RF 0.86（NP全体の74.5%が1トレード依存）、案C RF 0.31（PF 1.02、781トレードでほぼランダム）。商品拡大にはパラメータフリーのEWMAC方式が適する。

61. **ゴールドへのキャリー追加は無効。** コンタンゴ88%の商品ではキャリーFCが常にショート方向（平均-12.1）→ long only EWMACと逆行。EWMAC60+Carry40 LO: RF 3.10 vs ベースラインRF 6.78。キャリーが有効なのは金利先物など期間構造が変動する商品。

62. **EWMACへのADXフィルター追加は無効（7パターン全滅）。** EWMACのフォーキャスト値自体がトレンド強度を内包しており、ADXは同一次元の冗長情報。最良でもΔRF -0.39。教訓#54の再確認。

63. **4年データの商品スクリーニングは統計的に不十分。** pysystemtradeの長期データ（数十年）で結論が逆転する商品がある（MCL: 4年SR -0.55 → 長期SR 0.465）。商品選定は最低10年以上のデータで判断すべき。

64. **フェード2HシステムのPythonバックテストはTVと大幅乖離する。** Python RF 500超 vs TV RF 1.41。2Hバー構築の構造的不一致（pd.resample vs TVセッション境界対応）が原因。教訓#50と同じ構造。フェード2Hの全検証はTV strategy tester + xlsxエクスポートで実施すること。

65. **フェードシステムにSLは逆効果。** SL 1000pt発動7件中6件でSLの方が損失拡大（合計-88万→-140万）。深い逆行を耐えて戻るのがこの戦略の構造。保護はSLではなくモメンタム反転検出で行う。

66. **エントリーフィルターの裏返しがエグジットに最適。** MACD+KVOブルクロスでエントリー → ベアクロスでエグジット。RF 3.23→6.05（+87%）、DD 65万→43万（-34%）。エントリー根拠の消失＝撤退は論理的に一貫。

67. **保護エグジットの最適時間足はエントリーと同一（1H）。** 下位足（30m/45m）はノイズで早切り、上位足（90m/2H）は反応遅延。5TF×3条件を網羅テスト済み。

---

## 未テスト・将来の検討事項

**最優先:**
- pysystemtradeでレジーム別SR（直近5年 vs 長期）を商品別に出す → 商品選定確定
- 選定した商品のEWMACエンジン実装（gold_ewmac_engine.pyフレームワーク流用）

**優先度高:**
- pysystemtrade導入済み（WS07: C:\Users\CH07\pysystemtrade）。253商品の長期データ利用可能
- 商品候補の長期SR: US10 0.533, CRUDE_W 0.465, NASDAQ 0.361, EUR 0.296, GOLD 0.246, SP500 0.110
- FirstRate Complete Intraday購入 → レジーム調査 + 5分足アルゴ商品選定
- Carverの『Advanced Futures Trading Strategies』読了
- 商品数拡大検討（MES/MCL等マイクロ先物）

**優先度中:**
- Gold EWMACのトレードリスト作成 → レジーム分析
- USD/JPY次トレンド時に再開
- v6の2013-2017マイナス期のレジーム特定（既存VIX/USDJPY日足データで実施可能）

**保留/棄却:**
- Pythonシグナルエンジンの一致率改善 → Webhook方式で不要に
- PyneCore導入 → 将来の新システムがPine依存する場合のみ検討

---

## 口座・商品情報

口座: IBKR Japan | 純資産: 300万（2026/4増資済み）
商品: NK225MC(OSE.JPN, ×10) | MGC(COMEX, 取引可確認済)
miniPC: Getorli Ryzen 5300U/16GB/Win11Pro | IB Gateway port 4002(paper)/4001(live)
リモート: Tailscale + Parsec

---

## インフラ構成（v9と同一。Webhook追加予定）

（詳細はv9参照。v12での変更: v6/案Cエンジンを Webhook受信サーバーに置換予定）

---

## テスト完了サマリー

N225: 約100パターン（エントリー90+ / ポジションサイジング13）
Gold EWMAC: 約4,500+パターン
N225レジーム×マクロ: 9指標×複数ビン
v6 WFA: TV直接分析22窓（全期間64%、2018以降77%黒字）
案C WFA: TV直接分析33窓（全期間79%、2018以降93%黒字）
Pine→Python一致検証: 突合せ実施（一致率23%、構造的不一致確定）
**総計: 約4,700+パターン**

---

## v11→v12 変更履歴（2026/4/8-9）

- v6/案C WFA完了（TVトレードリスト直接分析方式）
- Pine→Python構造的不一致を確定（突合せ一致率23%、6層の差異を特定）
- build_nk225_database_v3.py作成（Panama Canal adjustment実装）→ 限月調整は相対指標に無影響と確認
- **方針転換: v6/案Cライブ執行をTV Webhook方式に変更決定**
- Gold EWMACは現行Pythonエンジン継続（Webhook対象外）
- 教訓#50-53追加
- Pine→Python移植リサーチレポート作成（6層の差異、業界事例、解決策3パターン）
- 教訓#54-59追加（次元独立性、エグジット遅延危険性、VM条件、AND条件固定、ADX位相分析）
- ADX位相分析スクリプト追加（scripts/research/: adx_phase_analyzer.py, adx_phase_cross_analysis.py, adx_feature_importance.py）

---

## v12→v13 変更履歴（2026/4/10）

### インフラ変更
- IBC config: IbLoginId=rhhane248（日本居住者はペーパーユーザー名で直接ログイン必須。kuri3desubot追加により旧方式が破綻）
- ペーパーアカウント: rhhane248 / DU8696858（kuri3desu/kuri3desubot共通）
- IB Key 2FA: rhhane248に対してIBKR Mobileアプリから直接登録（ペーパー側Client Portalには設定項目なし）
- Cloudflare Tunnel導入: webhook.torotakuno1.com → localhost:5001（Windowsサービスとして自動起動。ngrok不要に）
- ドメイン: torotakuno1.com（Cloudflare Registrar、年$10.46）

### 教訓
54. **日本居住者のIBKRペーパーアカウントは、ライブユーザー名+TradingMode=paperではログインできない。** ペーパー専用ユーザー名（rhhane248等）で直接ログインする必要がある。複数ユーザー追加時にGatewayが「multiple Paper Trading users」エラーを出す。
55. **Webhook受信にはトンネルサービスが必須。** ngrok無料は毎回URL変更で運用不可。Cloudflare Tunnel（無料+ドメイン年$10）が固定URL・自動起動・高安定性で最適。

---

## v13→v14 変更履歴（2026/4/10）

### Webhook E2Eテスト完了
- webhook_server.py clientId: 30→40に変更（fade_2h_engine.py clientId=30との衝突回避）
- start_trading.bat更新: v6_realtime_engine.py/caseC_realtime_engine.py削除、webhook_server.py/fade_2h_engine.py追加
- fade_2h_engine.pyのパスを修正（旧WS07パス→tv_data）
- E2Eテスト成功: curl→Cloudflare Tunnel→webhook_server→IBKR発注→Telegram通知

### clientId一覧（確定）
| clientId | エンジン |
|----------|----------|
| 3 | gold_ewmac_engine.py |
| 10 | v6_realtime_engine.py（廃止予定） |
| 20 | caseC_realtime_engine.py（廃止予定） |
| 30 | fade_2h_engine.py |
| 40 | webhook_server.py（v6+案C統合） |

### 教訓
56. clientIdは全エンジンで一意にすること。同一clientIdで2プロセスが接続するとIBKRが先行接続を切断する。新エンジン追加時は既存の割当表を必ず確認。

---

## v14→v15 変更履歴（2026/4/10）

### テスト結果
- v6/案CのES移植テスト: 両方とも機能せず（v6 RF 0.86、案C RF 0.31）。N225固有のエッジと確定
- Gold EWMACキャリー追加: コンタンゴ88%で逆方向。RF 6.78→3.10に悪化
- Gold EWMAC ADXフィルター: 7パターン全滅（同一次元）
- MNQ/MCL/M6E EWMAC 4年データ: 全滅（SR 0.064/-0.550/-0.050）
- pysystemtrade長期データ: 上記商品が数十年ではSR 0.3-0.5でプラス。4年データの結論は不十分

### インフラ
- pysystemtrade導入: WS07 C:\Users\CH07\pysystemtrade（253商品、長期データ付き）

### 教訓
- #60-63追加（上記参照）

---

## v15→v16 変更履歴（2026/4/12）

### フェード2H 最終構成確定
- エントリー: 日中2Hバー close < 直近20本安値 → ロングのみ
- F2: MACD(12,26,9) AND KVO(34,55) ブルクロス 10本以内
- F3: VR (VIX3M/VIX) > 1.00
- FOMC±1日除外
- エグジット: Hold 10回(slotConfirm単位) OR 1H MACD+KVO ベアクロス3本以内
- SLなし
- 成績: NP +259万 / DD 42.9万 / RF 6.05 / N=119 / PF 1.814 / WR 63%

### 検証経緯
- F1(ギャップ)12パターン → 全滅棄却
- F2(MACD+KVO)16パターン → #16(AND 10本)が最良
- F3(VR)7パターン → VR>1.00採用
- F4(カレンダー)8パターン → FOMC±1除外が最良、SQ週除外は逆効果
- Hold期間バグ発覚: slotConfirm単位のholdCountが実質5日保有。修正するとエッジ消失→スイング戦略として受容
- DMAフィルター4パターン → DMA2(LO)+FOMCがRF 4.39だがN=55で統計不足。フィルターなしLO(N=119)を採用
- エグジット21パターン+保護EX: 1H MACD+KVO逆クロスがRF 6.05で最良
- 保護EX時間足: 30m/45m/90m/2H全てが1Hに劣後（5TFテスト済み）
- SLシミュレーション: 200-1000pt全て逆効果
- v6/案Cとの補完性: 月次相関0.36、Fade独自保有日55%、ポートフォリオ+15%

### ポートフォリオ構成（4システム）
| Layer | システム | 商品 | TF | RF | 方式 |
|-------|----------|------|-----|------|------|
| 1 | v6 | N225マイクロ | 1H | 12.23 | TV Webhook |
| 1 | 案C | N225マイクロ | 1H | 8.77 | TV Webhook |
| 2 | Gold EWMAC | MGC | 2H | 17.14 | Pythonネイティブ |
| 3 | フェード2H | N225ミニ | 1H(2Hスロット) | 6.05 | TV Webhook(実装予定) |

### 教訓
- #64-67追加（上記参照）
