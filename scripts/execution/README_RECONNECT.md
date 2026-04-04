# IB Gateway 自動再接続システム

Phase 4完了時の課題「自動再接続ロジック未実装」を解決。

---

## 概要

IB Gateway/TWSは**接続断が常態**。以下の実装により無人運用を実現:

1. **自動再接続**: 接続断検出 → 10秒待機 → 再接続試行（最大5回）
2. **状態同期**: 再接続後にポジション・オーダー自動同期
3. **Telegram通知**: 接続断・再接続・エラーをリアルタイム通知
4. **状態永続化**: JSON形式で状態保存（クラッシュ後の復元用）
5. **ヘルスチェック**: 5分ごとの接続確認

---

## ファイル構成

| ファイル | 用途 |
|---|---|
| `nk_signal_engine_reconnect.py` | 基本版: v6/案C統合用 |
| `ib_reconnect_telegram.py` | 本番版: Telegram通知 + 状態永続化 |

---

## セットアップ

### 1. 依存ライブラリ

```bash
pip install ib_async requests --break-system-packages
```

### 2. Telegram設定（本番版のみ）

#### BotFatherでトークン取得

1. Telegramで`@BotFather`を検索
2. `/newbot` → ボット名・ユーザー名設定
3. トークン取得（例: `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`）

#### チャットID取得

1. ボットに任意のメッセージ送信
2. ブラウザで開く: `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. `"chat":{"id":123456789...}` の数値がチャットID

#### 環境変数設定（Windows）

```cmd
setx TELEGRAM_BOT_TOKEN "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
setx TELEGRAM_CHAT_ID "123456789"
```

確認:
```cmd
echo %TELEGRAM_BOT_TOKEN%
echo %TELEGRAM_CHAT_ID%
```

### 3. ディレクトリ準備

```bash
mkdir -p logs state
```

---

## 使用方法

### 基本版（Telegram通知なし）

```bash
cd C:\nikkei-trade\scripts\execution
python nk_signal_engine_reconnect.py
```

### 本番版（Telegram通知あり）

```bash
python ib_reconnect_telegram.py
```

---

## 動作確認

### 接続断シミュレーション

1. スクリプト起動
2. IB Gateway/TWSを手動で再起動
3. 以下を確認:
   - ログに`⚠️ IB Gateway接続断`表示
   - 10秒後に`🔄 再接続試行`
   - `✅ 再接続成功`後にポジション/オーダー同期
   - Telegram通知受信（本番版）

### ログ確認

```bash
# 最新ログ表示
tail -f logs/ib_reconnect_*.log

# エラーのみ抽出
grep ERROR logs/ib_reconnect_*.log
```

---

## v6/案Cシグナルエンジンへの統合

`nk_signal_engine_reconnect.py`のメインループ内に既存のシグナルロジックを統合:

```python
while True:
    if reconnect_mgr.is_connected:
        # === ここに既存のv6/案Cシグナル計算を統合 ===
        
        # 1. 1H足データ取得
        bars = await ib.reqHistoricalDataAsync(...)
        
        # 2. v6/案Cシグナル計算
        v6_signal = calculate_v6_signal(bars)
        caseC_signal = calculate_caseC_signal(bars)
        
        # 3. ブラケットオーダー発注
        if v6_signal == 'LONG' and current_position <= 0:
            bracket = create_bracket_order(...)
            await place_order(bracket)
        
        # 4. Telegram通知
        reconnect_mgr.telegram.send(f"v6: {v6_signal}", "INFO")
        
    else:
        logger.warning("⚠️ 接続断のため待機中...")
    
    await asyncio.sleep(60)  # 1分ごとに状態確認
```

---

## トラブルシューティング

### 1. `再接続上限到達`エラー

**原因**: IB Gateway/TWSが完全停止、またはポート競合

**対処**:
1. IB Gateway/TWSを手動再起動
2. ポート確認（Paper: 4002, Live: 4001）
3. `CLIENT_ID`を変更（1 → 2）

### 2. Telegram通知が来ない

**原因**: 環境変数未設定、またはトークン/チャットID誤り

**対処**:
```bash
# 環境変数確認
echo %TELEGRAM_BOT_TOKEN%
echo %TELEGRAM_CHAT_ID%

# 再設定後、PowerShellを再起動
```

### 3. `permId not found`エラー

**原因**: 再接続後にオーダー状態が同期されていない

**対処**: `_sync_state()`内で`ib.openOrders()`を明示的に呼び出し

---

## 次のステップ

- [ ] v6/案Cシグナルロジックを`nk_signal_engine_reconnect.py`に統合
- [ ] ブラケットオーダー発注機能追加（Phase 5）
- [ ] VT20枚数計算ロジック実装
- [ ] Gold EWMAC用の再接続マネージャー作成
- [ ] 状態復元テスト（クラッシュ後の自動復帰）

---

## 参考

- PINE_TO_PYTHON_IBKR_MIGRATION_GUIDE.md: 接続断・再接続の詳細解説
- ib_async公式: https://github.com/ib-api-reloaded/ib_async
- IBC (IB Controller): 自動ログイン・再起動管理
