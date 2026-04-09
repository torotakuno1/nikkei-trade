# N225ミニ 2H フェード執行エンジン

## 概要

| 項目 | 値 |
|---|---|
| 商品 | N225ミニ先物（OSE） |
| TF | 2H（日中セッション境界対応） |
| clientId | 30 |
| ポート | 4002（ペーパー）/ 4001（本番） |
| ロジック | フェード（高値/安値ブレイクアウトの逆張り） |

## ロジック詳細

### 2H バースロット（JST）

```
09:00 ─── 11:00  ← 11:00確定（スロット1）
11:00 ─── 13:00  ← 13:00確定（スロット2、昼休みを跨ぐ）
13:00 ─── 15:00  ← 15:00確定（スロット3）
```

1H バー（IBKR）を SLOT_MAP で振り分けて集約。  
昼休み（11:30〜12:30）を跨ぐスロット2は、前場・後場の bars を合算して OHLC を構成。

### シグナル判定（バー確定時）

```
prev_10_high = max(直近10本のhigh)   ← 確定バーの1本前まで遡る
prev_10_low  = min(直近10本のlow)

close > prev_10_high → SHORT エントリー（高値ブレイクをフェード）
close < prev_10_low  → LONG  エントリー（安値ブレイクをフェード）
```

### エントリー・エグジット

- **エントリー**: バー確定直後に成行発注 → 次バー open 価格で約定（近似）
- **エグジット**: エントリー後 5 本目のバー確定時に成行決済
- **ポジション上限**: 1 枚（保有中は新規シグナル無視）
- **SL**: なし

---

## セットアップ

### 1. コントラクト確認（重要）

IB Gateway の Contract Search で N225M を検索し、以下を確認する：

```
Symbol    : N225M
SecType   : FUT
Exchange  : OSE.JPN
Currency  : JPY
```

見つからない場合は `N225` や `NKY` も試す。

### 2. 限月を更新

`fade_2h_engine.py` の CONFIG を直近限月に更新する：

```python
'last_trade_date': '202609',  # ← 直近限月の YYYYMM
```

OSE N225ミニの限月：3月・6月・9月・12月の第2金曜日が最終取引日。

限月カレンダー確認コマンド（Python）：

```python
from ib_insync import IB, Future
ib = IB(); ib.connect('127.0.0.1', 4002, clientId=99)
details = ib.reqContractDetails(Future(symbol='N225M', exchange='OSE.JPN', currency='JPY'))
for d in details: print(d.contract.lastTradeDateOrContractMonth, d.contract.localSymbol)
ib.disconnect()
```

### 3. 環境変数（Telegram）

```bat
setx TELEGRAM_BOT_TOKEN "your-token"
setx TELEGRAM_CHAT_ID   "your-chat-id"
```

---

## 起動方法

### 単体起動

```bat
cd C:\Users\CH07\nikkei-trade\scripts\execution

# ペーパー
python fade_2h_engine.py

# 本番（確認プロンプトあり）
python fade_2h_engine.py --live
```

### start_trading.bat 経由（自動起動）

`start_trading.bat` に追記済み（ステップ 6）。  
IB Gateway 起動後、他エンジンと一緒に自動起動される。

> **注意**: bat 内のパスが `C:\Users\CH07\nikkei-trade\scripts\execution` になっていることを確認すること。  
> 他エンジンは `C:\Users\Riku\Desktop\tv_data` をカレントにしているが、  
> `fade_2h_engine.py` は上記パスで起動するため `logs/` や `fade_2h_state.json` は  
> `C:\Users\CH07\nikkei-trade\scripts\execution\` 以下に生成される。

---

## 動作確認手順

### Step 1: コントラクト疎通確認

```python
# contract_check.py として保存して実行
from ib_insync import IB, Future
ib = IB(); ib.connect('127.0.0.1', 4002, clientId=99)
c = Future(symbol='N225M', lastTradeDateOrContractMonth='202606',
           exchange='OSE.JPN', currency='JPY')
ib.qualifyContracts(c)
print(c)
ib.disconnect()
```

エラーなく `localSymbol` が表示されれば OK。

### Step 2: ヒストリカルデータ確認

```python
from ib_insync import IB, Future
ib = IB(); ib.connect('127.0.0.1', 4002, clientId=99)
c = Future(symbol='N225M', lastTradeDateOrContractMonth='202606',
           exchange='OSE.JPN', currency='JPY')
ib.qualifyContracts(c)
bars = ib.reqHistoricalData(c, endDateTime='', durationStr='5 D',
       barSizeSetting='1 hour', whatToShow='TRADES',
       useRTH=False, formatDate=1, keepUpToDate=False)
for b in bars[-10:]: print(b.date, b.close)
ib.disconnect()
```

直近の 1H バーが表示されれば OK。

### Step 3: エンジン単体起動（ペーパー）

```bat
python fade_2h_engine.py
```

起動ログ例：
```
=== N225ミニ 2Hフェードエンジン v1.0 === Paper port:4002
接続OK: ['DU1234567']
Contract: N225M2606 conId=12345678
ヒストリカルデータ: 1200本(1H)
2Hバー初期構築: 300本 (2025-04-15 11:00:00 ～ 2026-04-09 11:00:00)
監視開始 | Paper port:4002 | 2Hバー:300本 pos=0 | bars_since_entry=0
```

`2Hバー初期構築: XXX本` で lookback(10) + 1 = 11本以上あれば正常。

### Step 4: シグナル発火確認（ログ確認）

翌営業日に以下のようなログが出ることを確認する：

```
★2H確定: 2026-04-10 11:00:00 O=35000 H=35200 L=34900 C=35150 (累計301本)
  signal計算: close=35150 high_10=35100 low_10=34800
  シグナル: short
*** エントリー: SELL 1枚  ref=35150  (short) ***
  発注: MKT SELL 1枚
  約定: SELL 1枚 @ 35148
```

---

## ファイル構成

```
scripts/execution/
├── fade_2h_engine.py        # 本ファイル
├── fade_2h_state.json       # ランタイム状態（自動生成）
├── telegram_notify.py       # Telegram通知（共通）
└── logs/
    └── fade_2h_YYYYMMDD.log # 日次ログ（自動生成）
```

---

## 注意事項

- **昼休み跨ぎスロット（11:00-13:00）**: IBKR から 11:00 と 12:00（または 12:30）の 1H バーが来る。  
  それらを合算して 2H バーを構成するため、前場・後場の価格ギャップが close に反映される。

- **限月ロールオーバー**: `last_trade_date` は手動で更新が必要。  
  最終取引日の 1〜2 週前に次限月へ切り替えること。

- **夜間セッション**: `SLOT_MAP` に 15 時以降の時間は定義されていないため、  
  夜間バー到着時に 13:00-15:00 スロットが自動確定する仕組みになっている。

- **再起動時のポジション**: `fade_2h_state.json` にポジションと `bars_since_entry` が  
  保存されているため、再起動後も引き継がれる。ただし IBKR 側のポジションと  
  乖離がある場合は起動時の `_sync_pos()` で警告ログが出る。
