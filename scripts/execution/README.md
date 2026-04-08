# 発注エンジン（Phase 5-6 完了）

3エンジンがペーパー口座(port 4002)で稼働中。

## 稼働中エンジン

| エンジン | ファイル | clientId | 特徴 |
|---|---|---|---|
| v6 N225 | v6_realtime_engine.py | 10 | ブラケット発注, SL300円固定 |
| 案C N225 | caseC_realtime_engine.py | 20 | ブラケット発注, SL300円固定 |
| Gold EWMAC | gold_ewmac_engine.py | 3 | 成行+保護STP(ATR×4.0) |

## 共通機能

- Telegram通知（telegram_notify.py, SSL検証無効化）
- 自動再接続（フラグ方式, 指数バックオフ）
- 状態永続化（JSON）
- --live フラグ（確認プロンプト付き）

## 起動

start_trading.bat（shell:startup登録済み）で自動起動:
IBC → 60s → v6 → 5s → 案C → 5s → Gold EWMAC

## 参考ファイル

- nk_signal_engine_reconnect.py: 再接続基本版
- ib_reconnect_telegram.py: 再接続+Telegram本番版
- README_RECONNECT.md: 再接続システム詳細
