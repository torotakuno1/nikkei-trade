"""
Telegram通知モジュール（共通）
==============================
全エンジン(v6, 案C, Gold EWMAC)から使用。

セットアップ:
  setx TELEGRAM_BOT_TOKEN "your-token"
  setx TELEGRAM_CHAT_ID "your-chat-id"

使い方:
  from telegram_notify import TelegramNotifier
  tg = TelegramNotifier()
  tg.send("テスト通知")
"""
import os
import ssl
import urllib.request
import urllib.parse
import json
import logging
from datetime import datetime

log = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self):
        self.bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
        self.enabled = bool(self.bot_token and self.chat_id)
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE
        if not self.enabled:
            log.warning("Telegram未設定 (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")

    def _post(self, text):
        if not self.enabled:
            return
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = json.dumps({
                'chat_id': self.chat_id,
                'text': text,
                'parse_mode': 'Markdown'
            }).encode('utf-8')
            req = urllib.request.Request(url, data=payload,
                                        headers={'Content-Type': 'application/json'})
            urllib.request.urlopen(req, timeout=10, context=self._ssl_ctx)
        except Exception as e:
            log.warning(f"Telegram送信失敗: {e}")

    def send(self, msg):
        ts = datetime.now().strftime('%H:%M:%S')
        self._post(f"📢 {msg}\n`{ts}`")

    def trade(self, system, detail):
        ts = datetime.now().strftime('%H:%M:%S')
        self._post(f"💰 *{system}*\n{detail}\n`{ts}`")

    def exit(self, system, detail):
        ts = datetime.now().strftime('%H:%M:%S')
        self._post(f"🔄 *{system}*\n{detail}\n`{ts}`")

    def warn(self, system, detail):
        ts = datetime.now().strftime('%H:%M:%S')
        self._post(f"⚠️ *{system}*\n{detail}\n`{ts}`")

    def error(self, system, detail):
        ts = datetime.now().strftime('%H:%M:%S')
        self._post(f"❌ *{system}*\n{detail}\n`{ts}`")

    def startup(self, system, detail=""):
        ts = datetime.now().strftime('%H:%M:%S')
        self._post(f"🚀 *{system}* 起動\n{detail}\n`{ts}`")

    def status(self, system, detail):
        ts = datetime.now().strftime('%H:%M:%S')
        self._post(f"📊 *{system}*\n{detail}\n`{ts}`")


if __name__ == '__main__':
    tg = TelegramNotifier()
    if tg.enabled:
        tg.send("Telegram通知テスト成功！")
        print("通知送信OK")
    else:
        print("環境変数未設定。以下を実行:")
        print('  setx TELEGRAM_BOT_TOKEN "your-token"')
        print('  setx TELEGRAM_CHAT_ID "your-chat-id"')
