# Nikkei Trade System

日経225マイクロ先物 + ゴールド先物の自動売買システム。

## システム構成

| Layer | システム | 商品 | TF | RF | 年率% |
|---|---|---|---|---|---|
| 1 | v6 (AND条件TF) | N225マイクロ | 1H | 12.23 | +74.5% |
| 1 | 案C (ORハイブリッド) | N225マイクロ | 1H | 8.77 | — |
| 2 | Gold EWMAC+GVZ | Gold (MGC) | 2H | 14.46 | +73.5% |
| 3 | USD/JPY | — | — | — | 保留 |

## フォルダ構成

```
docs/           HANDOFF、移植ガイド等のドキュメント
scripts/pine/   TradingView Pine Script (v6, 案C)
scripts/signal_engine/  Python シグナルエンジン
scripts/execution/      IBKR発注エンジン（3エンジン稼働中）
data/           データの所在・取得方法（CSVはGit管理外）
research/       学術検証、マクロ指標分析結果
```

## 実行環境

- **取引PC**: Getorli AMD Ryzen 5300U / 16GB / Win11 Pro
- **ブローカー**: IBKR Japan (U14203671)
- **API**: IB Gateway Offline v1037 + IBC 3.23.0 (port 4002 paper / 4001 live)
- **リモート**: Tailscale (100.97.76.83) + Parsec

## 開発状況

- [x] Phase 0-4: ハードウェア〜シグナルエンジン
- [x] Phase 5-6: 発注エンジン・Telegram通知・3エンジン同時起動
- [ ] Phase 7: ペーパートレード（現在実施中）
- [ ] Phase 8: ライブ稼働
