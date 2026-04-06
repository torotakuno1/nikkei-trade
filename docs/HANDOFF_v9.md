# NIKKEI TRADE 統合システム - 引き継ぎ v9

## ⚠️ Claude運用ルール

- **Pine Script → チャット本文にコードブロック**（TVコピペ用。アーティファクト不可）
- **Python → ファイル出力**（Mac/PCで実行するため）

---

## ドキュメント構成

本ファイル = 意思決定・結論・教訓のみ。コード・詳細データは別ファイル参照。
- scripts/pine/ → v6, 案C Pine Script
- scripts/signal_engine/ → v6 Python移植版, gold_ewmac.py
- scripts/execution/ → v6, 案C, Gold EWMAC リアルタイムエンジン（稼働中）
- research/ → macro_analysis_results.md, academic_validation_JP.md
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
VT20% Max5, IDM=1.2, 慣性10% | 商品: MGC (COMEX, IBKR Japan取引可 確認済み)

成績(2019/1-2026/4, 7年超): NP 977.8万 | DD 57.1万(28.6%) | RF 17.14 | Active 42%

### USD/JPY — 保留（直近3年赤字、55年RF 14.44）

---

## VolTarget確定構成

- **v6**: VT20 Max5 DDなし（VTがDD期ボラ拡大で自動縮小→DDステップダウン冗長）
- **案C**: VT1+DD固定2枚（DD -30%, RF +34%）
- **Gold EWMAC**: VT20% Max2（300万口座、IDM=1.2、慣性10%）

---

## Gold EWMAC 開発結論

約4,500+パターンテスト。最良構成: 2H barbell(8,32)+(64,256) LO, GVZ_z>0。

GVZ_z>0 採用理由: WF合格(IS 8.08→OOS 10.13) / 台地-0.5〜+1.0 / Active 42% / VRP文献裏付け / 上位組み合わせはActive低すぎ

**GVZフィルター年別一貫性検証（2026/4/5, 7年超データ）:**

| Year | NP_BL万 | RF_BL | NP_GVZ万 | RF_GVZ | Act% | 判定 |
|---|---|---|---|---|---|---|
| 2019 | -8.6 | -0.11 | +112.5 | 3.65 | 34 | ✓ |
| 2020 | +104.3 | 1.67 | +129.9 | 2.28 | 44 | ✓ |
| 2021 | -11.3 | -0.12 | +74.9 | 2.94 | 43 | ✓ |
| 2022 | +20.3 | 0.37 | +48.4 | 1.70 | 45 | ✓ |
| 2023 | +32.3 | 0.53 | +125.1 | 6.62 | 37 | ✓ |
| 2024 | +81.2 | 2.53 | +131.7 | 4.44 | 39 | ✓ |
| 2025 | +266.9 | 7.20 | +262.7 | 6.86 | 45 | ▼微 |
| 2026 | +112.8 | 4.13 | +92.5 | 3.97 | 63 | ▼微 |

全8年プラス（BL 6/8）、LOYO全8年✓、BL赤字年→プラス転換(2019,2021)

効かないもの: DMA傾き, VIXターム構造, ADX閾値, ボラフィルター, SQ除外, 時間帯除外, 代替シグナル(Breakout/ROC/RSI/MACD)

---

## マクロフィルター結論

**Gold採用: GVZ_z>0（RF 5.99→17.14）** — 詳細ランキング: research/macro_analysis_results.md

**v6/案C: マクロフィルター追加なし**（教訓#29: 改善主軸はポジションサイジング層）

**N225追加フィルター分析（2026/4/5, 全て見送り）:**
- 1570 ETF出来高: 見かけRF 18.00 → T-1遅延補正後RF 1.94（先読みバイアス）
- NKVI<25: 2019年以降先物が構造的バックワーデーション→閾値が永久に満たされ無意味
- OI↑1日: 案Cで+3.08改善だが、Pine検証でデータ期間不足(118trades, 86%減)→信頼性不足

**RSI極値フィルター検証（2026/4/6）:**
- v6: 10パターン全て同一結果（効果ゼロ。多層AND条件が構造的に極値を排除）
- 案C: 最良（RSI<65ロングのみ）+2.5%、誤差範囲→追加不要

**商品間非対称性:** GVZ→Gold専用, VIXレベル→N225専用, 同指標が逆方向に効く

---

## 教訓（v9追加分 #34-42）

過去の教訓#1-33は確立済み — 必要時はv7またはGit履歴を参照。

34. フィルターは商品固有。GVZ→Gold, VIX→N225。同指標が逆方向に効く
35. GVZ_z>0でGold EWMAC RF +76%。台地-0.5〜+1.0。VRP文献が根拠
36. SKEW>140が3システム(v6,案C,Gold)で一貫有効。学術的に未踏
37. FOMC週はゴールドTFを破壊(RF+67%)。政策不確実性が原因
38. 実質金利>1.5%でゴールドTF好成績。高金利=低下余地大=強トレンド源
39. ゴールドはロングのみが最良。ショートトレンドは弱く短い
40. EWMAC速度はbarbell最良: 最速(8,32)+最遅(64,256)。中間不要
41. GVZ_z>0は7年超年別一貫性テスト合格。全8年+, LOYO全✓, データ延長でRF改善(過適合の逆)
42. 多層フィルターシステム(v6/案C)は指標極値を構造的に排除する。RSI極値フィルター等の追加は冗長（v6: 10パターン全同一、案C: 最良+2.5%で誤差範囲）

---

## 口座・商品情報

口座: IBKR Japan U14203671 | 純資産: 300万（2026/4増資済み）
商品: NK225MC(×10, 証拠金約2.5-3万/枚) | MGC(COMEX, IBKR Japan取引可確認済み, 証拠金~$2,000-2,500/枚)
3システム同時稼働: 300万口座でv6 Max5 + 案C 2枚 + Gold Max2で証拠金・DD許容範囲内

---

## 自動売買パイプライン

- Phase 0-4: ✅完了（HW→IB Gateway→データ→シグナルエンジン v6一致率92.4%）
- Phase 5-6: ✅完了（ブラケット発注, Telegram通知, 3エンジン同時起動）
- Phase 7: 🔄ペーパートレード中（3エンジンペーパー口座稼働中）
- Phase 8: 未着手（ライブ稼働）

---

## インフラ構成（確定）

### miniPC
- Getorli AMD Ryzen 5300U / 16GB / Win11 Pro
- Tailscale: 100.97.76.83
- Parsec: Mac/WS07からリモート操作

### IB Gateway
- IB Gateway Offline v1037 + IBC 3.23.0
- ペーパー: port 4002 | ライブ: port 4001

### IBC設定（C:\Users\Riku\Documents\IBC\config.ini）
- AutoRestartTime=06:30（日経空白時間06:00-08:45内）
- AcceptIncomingConnectionAction=accept
- ReloginAfterSecondFactorAuthenticationTimeout=yes
- CommandServerPort=7462

### Gateway UI
- Auto Restart = 7:00 AM（IBCの保険。通常はIBCが06:30に先行）

### BIOS設定
- Restore AC Power Loss = Power On（停電復旧時に自動起動）
- Lan Wake Up From FCH = S3/S4/S5 Support
- MAC: 68-1D-EF-5E-B5-4B（WoL用）

### 週次運用
- 毎週月曜朝: 手動2FAログイン必須（IBKR週次トークン失効: 日曜ET 1:00AM）
- IBC AutoRestart(06:30)がTelegram通知を送信→スマホで2FA承認

### 稼働エンジン（C:\Users\Riku\Desktop\tv_data\）
| エンジン | clientId | ファイル |
|---|---|---|
| v6 N225 | 10 | v6_realtime_engine.py |
| 案C N225 | 20 | caseC_realtime_engine.py |
| Gold EWMAC | 3 | gold_ewmac_engine.py |

- 全エンジンにTelegram通知実装済み（telegram_notify.py, SSL検証無効化）
- start_trading.bat: IBC→60s待機→v6→5s→案C→5s→Gold、shell:startup登録済み
- Windowsスリープ無効化済み

### 未購入・未実施
- UPS: CyberPower CP550JP推奨（未購入）
- モバイルホットスポット予備回線（未実施）
- SwitchBotプラグミニ: 外部からのminiPC起動用（未購入）
- Telegram双方向コマンド（/status, /flatten等）: ライブ移行前に実装予定

---

## テスト完了サマリー

N225: 約100パターン（エントリー90+ / ポジションサイジング13）
Gold EWMAC: 約4,500+パターン（TF×速度2500 / 時間帯510 / イベント224 / マクロ200+ / VT13 / 代替シグナル28 / 年別検証50）
N225追加フィルター: OI/NKVI/ETF/RSI極値 約50パターン（全見送り）
**総計: 約4,700パターン**

---

## v8→v9 変更履歴（2026/4/6）

- Phase 5-6完了を反映（3エンジンペーパー稼働中）
- 口座: 92万→300万に増資反映
- MGC取引可確認済み（COMEX, IBKR Japan）
- Gold EWMAC VT20% Max2（300万口座ベース）
- IBC設定詳細追加（AutoRestartTime=06:30他）
- BIOS/WoL設定追加
- 週次2FAルーティン追加
- インフラ構成セクション新設（port, clientId, ファイルパス等を集約）
- N225追加フィルター分析結果追加（OI/NKVI/ETF/RSI極値、全見送り）
- 教訓#42追加（多層フィルターの極値排除冗長性）
- テスト総計更新（4,650→4,700）
