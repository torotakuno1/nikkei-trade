# データファイル

CSVファイルはサイズが大きいためGit管理外。以下のソースから取得。

## 1H足データ（TradingView CSVエクスポート）

| データ | 期間 | ファイル名 |
|---|---|---|
| NK225M 1H | 2022/4-2026/4 | NK225M_1H_TV_FULL.csv |
| Gold GC1! 1H | 2022/4-2026/4 | GOLD_1H_TV_FULL.csv |
| USD/JPY 1H | 2023/1-2026/4 | USDJPY_1H_TV_FULL.csv |

## 日足データ

| データ | ソース | ファイル名 |
|---|---|---|
| NK225M 日足 | TV: OSE:NK225M1! | OSE_NK225M1_1D_dd526.csv |
| Gold 日足 | TV: COMEX:GC1! | COMEX_DL_GC1_1D_0f966.csv |
| USD/JPY 日足 | TV: FX:USDJPY | FX_USDJPY_1D_8a1df.csv |
| VIX / VIX3M | IBKR API | VIX_daily.csv / VIX3M_daily.csv |
| GVZ | TV: CBOE:GVZ | CBOE_GVZ_1D_e6781.csv |
| SKEW | TV: CBOE:SKEW | CBOE_SKEW_1D_8b43a.csv |
| DXY | TV: TVC:DXY | TVC_DXY_1D_8b14c.csv |
| 実質金利 | TV: FRED:DFII10 | FRED_DFII10_1D_76610.csv |
| GLD ETF | TV: BATS:GLD | BATS_GLD_1D_412fd.csv |
| Gold OI | TV: COMEX:GC1! OI | COMEX_DL_GC1OI_1D_2a4d6.csv |

## トレードリスト

| データ | ソース | ファイル名 |
|---|---|---|
| v6 トレードリスト | TV Strategy Tester Export | v6_trades.csv |
| 案C トレードリスト | TV Strategy Tester Export | caseC_trades.csv |

## 保管場所

- **Claude Project**: プロジェクトファイルとして登録
- **ミニPC**: `C:\nikkei-trade\data\` (要手動コピー)
