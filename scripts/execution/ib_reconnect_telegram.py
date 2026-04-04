#!/usr/bin/env python3
"""
IB Gateway Auto-Reconnect Manager with Telegram Notification
=============================================================
本番運用向け: Telegram通知 + 状態永続化 + ヘルスチェック

環境変数:
    TELEGRAM_BOT_TOKEN: Telegramボットトークン
    TELEGRAM_CHAT_ID: 通知先チャットID

Usage:
    python ib_reconnect_telegram.py
"""

import asyncio
import logging
import os
import json
from datetime import datetime
from pathlib import Path
import sys
from typing import Optional

from ib_async import IB
import requests


# ============================================================
# ロギング設定
# ============================================================
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"ib_reconnect_{datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


# ============================================================
# Telegram通知
# ============================================================
class TelegramNotifier:
    """Telegram通知クラス"""
    
    def __init__(self):
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID')
        self.enabled = bool(self.bot_token and self.chat_id)
        
        if not self.enabled:
            logger.warning("⚠️ Telegram環境変数未設定 (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)")
        else:
            logger.info("✅ Telegram通知有効化")
    
    def send(self, message: str, level: str = "INFO"):
        """Telegram通知送信"""
        if not self.enabled:
            return
        
        emoji = {
            "INFO": "ℹ️",
            "WARNING": "⚠️",
            "ERROR": "❌",
            "CRITICAL": "🚨",
            "SUCCESS": "✅"
        }.get(level, "📢")
        
        text = f"{emoji} *{level}*\n{message}\n`{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`"
        
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "Markdown"
            }
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            logger.error(f"Telegram送信失敗: {e}")


# ============================================================
# 状態永続化
# ============================================================
class StateManager:
    """状態永続化マネージャー"""
    
    def __init__(self, state_file: Path = Path("state/ib_state.json")):
        self.state_file = state_file
        self.state_file.parent.mkdir(exist_ok=True)
        self.state = self._load()
    
    def _load(self) -> dict:
        """状態読み込み"""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"状態読み込み失敗: {e}")
        return {
            "last_connect": None,
            "last_disconnect": None,
            "disconnect_count": 0,
            "positions": [],
            "orders": []
        }
    
    def save(self):
        """状態保存"""
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"状態保存失敗: {e}")
    
    def update_connect(self):
        """接続時刻更新"""
        self.state["last_connect"] = datetime.now().isoformat()
        self.save()
    
    def update_disconnect(self):
        """切断時刻更新"""
        self.state["last_disconnect"] = datetime.now().isoformat()
        self.state["disconnect_count"] = self.state.get("disconnect_count", 0) + 1
        self.save()
    
    def update_positions(self, positions: list):
        """ポジション更新"""
        self.state["positions"] = [
            {"symbol": p.contract.localSymbol, "position": p.position, "avgCost": p.avgCost}
            for p in positions
        ]
        self.save()
    
    def update_orders(self, orders: list):
        """オーダー更新"""
        self.state["orders"] = [
            {"action": o.order.action, "qty": o.order.totalQuantity, "type": o.order.orderType}
            for o in orders
        ]
        self.save()


# ============================================================
# 拡張版再接続マネージャー
# ============================================================
class IBReconnectManagerPro:
    """本番運用版: Telegram通知 + 状態永続化 + ヘルスチェック"""
    
    def __init__(self, ib: IB, host: str = "127.0.0.1", port: int = 4001, client_id: int = 1):
        self.ib = ib
        self.host = host
        self.port = port
        self.client_id = client_id
        
        self.telegram = TelegramNotifier()
        self.state_mgr = StateManager()
        
        self.is_connected = False
        self.reconnect_count = 0
        self.max_reconnect_attempts = 5
        self.reconnect_delay = 10
        
        # イベントハンドラー登録
        self.ib.disconnectedEvent += self._on_disconnect
        self.ib.connectedEvent += self._on_connect
        self.ib.errorEvent += self._on_error
        
        logger.info("🔧 IBReconnectManagerPro初期化完了")
    
    async def connect(self):
        """初回接続"""
        try:
            await self.ib.connectAsync(
                host=self.host,
                port=self.port,
                clientId=self.client_id,
                timeout=20
            )
            self.is_connected = True
            self.state_mgr.update_connect()
            
            msg = f"IB Gateway接続成功\nHost: {self.host}:{self.port}\nClientID: {self.client_id}"
            logger.info(f"✅ {msg}")
            self.telegram.send(msg, "SUCCESS")
            
            await self._sync_state()
        except Exception as e:
            msg = f"IB Gateway接続失敗: {e}"
            logger.error(f"❌ {msg}")
            self.telegram.send(msg, "ERROR")
            raise
    
    def _on_disconnect(self):
        """接続断ハンドラー"""
        self.is_connected = False
        self.state_mgr.update_disconnect()
        
        msg = f"IB Gateway接続断検出\n切断回数: {self.state_mgr.state['disconnect_count']}"
        logger.warning(f"⚠️ {msg}")
        self.telegram.send(msg, "WARNING")
        
        asyncio.create_task(self._attempt_reconnect())
    
    def _on_connect(self):
        """再接続成功ハンドラー"""
        self.is_connected = True
        self.state_mgr.update_connect()
        
        msg = f"IB Gateway再接続成功\n試行回数: {self.reconnect_count}"
        logger.info(f"✅ {msg}")
        self.telegram.send(msg, "SUCCESS")
        
        asyncio.create_task(self._sync_state())
    
    def _on_error(self, reqId, errorCode, errorString, contract):
        """エラーハンドラー"""
        # 接続性関連エラー
        if errorCode in [1100, 1101, 1102]:
            logger.info(f"接続性: [{errorCode}] {errorString}")
            if errorCode == 1100:  # 接続喪失
                self.telegram.send(f"接続性喪失\n{errorString}", "WARNING")
        # データ農場接続
        elif errorCode in [2104, 2106, 2158]:
            logger.info(f"Data Farm: [{errorCode}] {errorString}")
        # Warning系
        elif errorCode >= 2000:
            logger.warning(f"IB Warning [{errorCode}]: {errorString}")
        # Error系（重要）
        elif errorCode >= 100:
            msg = f"IB Error [{errorCode}] reqId={reqId}\n{errorString}"
            logger.error(msg)
            self.telegram.send(msg, "ERROR")
    
    async def _attempt_reconnect(self):
        """再接続試行"""
        if self.reconnect_count >= self.max_reconnect_attempts:
            msg = f"再接続上限到達 ({self.max_reconnect_attempts}回)\n手動介入が必要です"
            logger.critical(f"🚨 {msg}")
            self.telegram.send(msg, "CRITICAL")
            return
        
        self.reconnect_count += 1
        logger.info(f"🔄 {self.reconnect_delay}秒後に再接続試行 ({self.reconnect_count}/{self.max_reconnect_attempts})")
        
        await asyncio.sleep(self.reconnect_delay)
        
        try:
            await self.ib.connectAsync(
                host=self.host,
                port=self.port,
                clientId=self.client_id,
                timeout=20
            )
            self.reconnect_count = 0  # 成功したらリセット
        except Exception as e:
            logger.error(f"❌ 再接続失敗 ({self.reconnect_count}/{self.max_reconnect_attempts}): {e}")
            await self._attempt_reconnect()  # 再試行
    
    async def _sync_state(self):
        """ポジション・オーダー状態同期"""
        try:
            # ポジション同期
            positions = self.ib.positions()
            self.state_mgr.update_positions(positions)
            
            if positions:
                pos_str = "\n".join([
                    f"  {p.contract.localSymbol}: {p.position}枚 @ {p.avgCost:.0f}"
                    for p in positions
                ])
                logger.info(f"📊 ポジション同期:\n{pos_str}")
            
            # オーダー同期
            orders = self.ib.openOrders()
            self.state_mgr.update_orders(orders)
            
            if orders:
                order_str = "\n".join([
                    f"  {o.order.action} {o.order.totalQuantity}枚 @ {o.order.orderType}"
                    for o in orders
                ])
                logger.info(f"📋 オーダー同期:\n{order_str}")
            
            # 同期完了通知
            if positions or orders:
                msg = f"状態同期完了\nポジション: {len(positions)}件\nオーダー: {len(orders)}件"
                self.telegram.send(msg, "INFO")
        
        except Exception as e:
            logger.error(f"❌ 状態同期エラー: {e}")
            self.telegram.send(f"状態同期失敗\n{e}", "ERROR")
    
    async def health_check(self):
        """ヘルスチェック（定期実行）"""
        while True:
            await asyncio.sleep(300)  # 5分ごと
            
            if not self.is_connected:
                logger.warning("⚠️ ヘルスチェック: 接続断状態")
                continue
            
            try:
                # IB接続確認
                positions = self.ib.positions()
                logger.info(f"✅ ヘルスチェックOK (ポジション: {len(positions)}件)")
            except Exception as e:
                logger.error(f"❌ ヘルスチェック失敗: {e}")
                self.telegram.send(f"ヘルスチェック失敗\n{e}", "WARNING")


# ============================================================
# 使用例
# ============================================================
async def main():
    """メイン実行例"""
    ib = IB()
    
    # 再接続マネージャー初期化
    reconnect_mgr = IBReconnectManagerPro(
        ib,
        host="127.0.0.1",
        port=4001,  # Paper: 4002, Live: 4001
        client_id=1
    )
    
    try:
        # 初回接続
        await reconnect_mgr.connect()
        
        # ヘルスチェック開始
        asyncio.create_task(reconnect_mgr.health_check())
        
        # メインループ（シグナルエンジン統合想定）
        logger.info("🚀 自動再接続エンジン稼働開始")
        
        while True:
            if reconnect_mgr.is_connected:
                # ここにv6/案C/Gold EWMACのシグナルロジックを統合
                pass
            else:
                logger.warning("⚠️ 接続断のため待機中...")
            
            await asyncio.sleep(60)
    
    except KeyboardInterrupt:
        logger.info("🛑 ユーザーによる停止")
        reconnect_mgr.telegram.send("システム停止", "INFO")
    except Exception as e:
        logger.critical(f"❌ 致命的エラー: {e}", exc_info=True)
        reconnect_mgr.telegram.send(f"致命的エラー\n{e}", "CRITICAL")
    finally:
        if ib.isConnected():
            ib.disconnect()
            logger.info("✅ IB Gateway切断完了")


if __name__ == '__main__':
    asyncio.run(main())
