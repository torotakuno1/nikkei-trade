#!/usr/bin/env python3
"""
v6 + 案C Nikkei Auto-Reconnect Signal Engine
==============================================
IB Gateway自動再接続 + ポジション/オーダー同期機能付き

Usage:
    python nk_signal_engine_reconnect.py
"""

import asyncio
import logging
from datetime import datetime, time, timedelta
from pathlib import Path
import sys

from ib_async import IB, Stock, Contract, util

# ============================================================
# ロギング設定
# ============================================================
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"nk_signals_{datetime.now().strftime('%Y%m%d')}.log"

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
# 接続設定
# ============================================================
class IBConfig:
    HOST = "127.0.0.1"
    PORT = 4001  # IB Gateway Paper: 4002, Live: 4001
    CLIENT_ID = 1
    RECONNECT_DELAY = 10  # 再接続前の待機秒数（clientId競合回避）
    MAX_RECONNECT_ATTEMPTS = 3


# ============================================================
# 自動再接続マネージャー
# ============================================================
class IBReconnectManager:
    """IB Gateway自動再接続 + 状態同期マネージャー"""
    
    def __init__(self, ib: IB, config: IBConfig):
        self.ib = ib
        self.config = config
        self.reconnect_count = 0
        self.is_connected = False
        self.last_positions = []
        self.last_orders = []
        
        # イベントハンドラー登録
        self.ib.disconnectedEvent += self._on_disconnect
        self.ib.connectedEvent += self._on_connect
        self.ib.errorEvent += self._on_error
        
    async def connect(self):
        """初回接続"""
        try:
            await self.ib.connectAsync(
                host=self.config.HOST,
                port=self.config.PORT,
                clientId=self.config.CLIENT_ID,
                timeout=20
            )
            self.is_connected = True
            logger.info(f"✅ IB Gateway接続成功: {self.config.HOST}:{self.config.PORT}")
            await self._sync_state()
        except Exception as e:
            logger.error(f"❌ IB Gateway接続失敗: {e}")
            raise
    
    def _on_disconnect(self):
        """接続断時のハンドラー（同期関数）"""
        self.is_connected = False
        logger.warning(f"⚠️ IB Gateway接続断 at {datetime.now()}")
        
        # 非同期再接続をイベントループにスケジュール
        asyncio.create_task(self._attempt_reconnect())
    
    def _on_connect(self):
        """再接続成功時のハンドラー（同期関数）"""
        self.is_connected = True
        logger.info(f"✅ IB Gateway再接続成功 at {datetime.now()}")
        
        # 非同期状態同期をイベントループにスケジュール
        asyncio.create_task(self._sync_state())
    
    def _on_error(self, reqId, errorCode, errorString, contract):
        """エラーハンドラー"""
        # 重要なエラーのみログ出力（ノイズ削減）
        if errorCode in [1100, 1101, 1102, 2104, 2106, 2158]:
            # 1100: 接続性喪失, 1101: 接続性回復, 1102: 接続性回復（データ農場）
            # 2104/2106: データ農場接続ステータス, 2158: セキュアゲートウェイ接続
            logger.info(f"IB Status [{errorCode}]: {errorString}")
        elif errorCode >= 2000:
            # Warning系（2000番台）
            logger.warning(f"IB Warning [{errorCode}] reqId={reqId}: {errorString}")
        elif errorCode >= 100:
            # Error系
            logger.error(f"IB Error [{errorCode}] reqId={reqId}: {errorString}")
            if contract:
                logger.error(f"  Contract: {contract}")
    
    async def _attempt_reconnect(self):
        """自動再接続試行"""
        if self.reconnect_count >= self.config.MAX_RECONNECT_ATTEMPTS:
            logger.critical(f"❌ 再接続上限到達 ({self.config.MAX_RECONNECT_ATTEMPTS}回)")
            return
        
        self.reconnect_count += 1
        logger.info(f"🔄 {self.config.RECONNECT_DELAY}秒後に再接続試行 ({self.reconnect_count}/{self.config.MAX_RECONNECT_ATTEMPTS})")
        
        await asyncio.sleep(self.config.RECONNECT_DELAY)
        
        try:
            await self.ib.connectAsync(
                host=self.config.HOST,
                port=self.config.PORT,
                clientId=self.config.CLIENT_ID,
                timeout=20
            )
            self.reconnect_count = 0  # 成功したらカウントリセット
            logger.info(f"✅ 再接続成功 at {datetime.now()}")
        except Exception as e:
            logger.error(f"❌ 再接続失敗 ({self.reconnect_count}/{self.config.MAX_RECONNECT_ATTEMPTS}): {e}")
            # 次回の再接続を再スケジュール
            await self._attempt_reconnect()
    
    async def _sync_state(self):
        """ポジション・オーダー状態同期"""
        try:
            # ポジション同期
            positions = self.ib.positions()
            if positions != self.last_positions:
                logger.info(f"📊 ポジション変化検出:")
                for pos in positions:
                    logger.info(f"  {pos.contract.localSymbol}: {pos.position}枚 @ {pos.avgCost:.0f}")
                self.last_positions = positions
            
            # オーダー同期
            orders = self.ib.openOrders()
            if orders != self.last_orders:
                logger.info(f"📋 オープンオーダー変化検出:")
                for order in orders:
                    logger.info(f"  {order.order.action} {order.order.totalQuantity}枚 @ {order.order.orderType}")
                self.last_orders = orders
            
        except Exception as e:
            logger.error(f"❌ 状態同期エラー: {e}")


# ============================================================
# メインループ（シグナルエンジン統合想定）
# ============================================================
async def main():
    """メインループ"""
    ib = IB()
    config = IBConfig()
    
    # 再接続マネージャー初期化
    reconnect_mgr = IBReconnectManager(ib, config)
    
    try:
        # 初回接続
        await reconnect_mgr.connect()
        
        # メインループ（ここにv6/案Cのシグナルロジックを統合）
        logger.info("🚀 シグナルエンジン開始")
        
        while True:
            if reconnect_mgr.is_connected:
                # ここにシグナル計算・発注ロジックを追加
                # 例: v6/案Cのシグナル検出 → ブラケットオーダー発注
                pass
            else:
                logger.warning("⚠️ 接続断のため待機中...")
            
            await asyncio.sleep(60)  # 1分ごとに状態確認
        
    except KeyboardInterrupt:
        logger.info("🛑 ユーザーによる停止")
    except Exception as e:
        logger.critical(f"❌ 致命的エラー: {e}", exc_info=True)
    finally:
        if ib.isConnected():
            ib.disconnect()
            logger.info("✅ IB Gateway切断完了")


if __name__ == '__main__':
    asyncio.run(main())
