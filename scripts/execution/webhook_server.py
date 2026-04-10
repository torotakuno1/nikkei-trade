"""
TV Webhook -> IBKR Bridge Server
=================================
TradingView Webhook を受信し、IB Gateway経由でブラケットオーダーを発注。
v6 / 案C 両対応。Gold EWMACは対象外（既存Pythonエンジン継続）。

使い方:
  python webhook_server.py              # ペーパー(4002)
  python webhook_server.py --live       # 本番(4001) ※確認プロンプト付き
  python webhook_server.py --port 5001  # Webhookリスンポート変更

TV Alert JSON format:
  {"system":"v6", "action":"entry_long",  "price":38500}
  {"system":"v6", "action":"entry_short", "price":38500}
  {"system":"v6", "action":"exit_long",   "price":38500}
  {"system":"v6", "action":"exit_short",  "price":38500}
  {"system":"caseC", "action":"entry_long",  "price":38500}
  ...

Architecture:
  - Main thread: ib_insync event loop (ib.sleep)
  - Sub thread:  HTTP server (http.server)
  - Queue:       thread-safe queue bridges HTTP -> IB thread
"""
import sys
import os
import json
import time
import asyncio
import logging
import threading
import queue
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from pathlib import Path
from ib_insync import IB, Future, MarketOrder, StopOrder, util

try:
    from telegram_notify import TelegramNotifier
except ImportError:
    class TelegramNotifier:
        enabled = False
        def send(self, *a): pass
        def trade(self, *a): pass
        def exit(self, *a): pass
        def warn(self, *a): pass
        def error(self, *a): pass
        def startup(self, *a): pass
        def status(self, *a): pass

# ============================================================
# Config
# ============================================================

CONFIG = {
    # IB Gateway
    'ib_host': '127.0.0.1',
    'paper_port': 4002,
    'live_port': 4001,
    'client_id': 40,  # v6=10, caseC=20, gold=3, fade2h=30, webhook=40

    # Futures contract (update before SQ roll)
    'symbol': 'N225M',
    'last_trade_date': '20260611',
    'exchange': 'OSE.JPN',
    'currency': 'JPY',

    # Risk
    'stop_loss': 300,  # JPY per contract

    # Webhook server
    'webhook_port': 5001,
    'webhook_secret': '',  # optional: TV alert key for auth

    # Qty per system (VT logic stays in Pine Script)
    'v6_qty': 1,
    'caseC_qty': 1,

    # State
    'state_file': 'webhook_state.json',
    'log_file': 'webhook_server.log',

    # Reconnect
    'max_reconnect': 10,
    'reconnect_base_delay': 10,
}

# ============================================================
# Logging
# ============================================================

def setup_logging(log_file):
    logger = logging.getLogger('webhook')
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    fh = logging.FileHandler(log_file, encoding='utf-8')
    ch = logging.StreamHandler()
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S')
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger

log = setup_logging(CONFIG['log_file'])

# ============================================================
# State persistence
# ============================================================

def load_state(fp):
    p = Path(fp)
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return {
        'v6_position': 0,       # 1=long, -1=short, 0=flat
        'v6_entry_price': 0.0,
        'caseC_position': 0,
        'caseC_entry_price': 0.0,
    }

def save_state(fp, state):
    with open(fp, 'w') as f:
        json.dump(state, f, indent=2)

# ============================================================
# Webhook HTTP Handler
# ============================================================

# Global queue: HTTP thread -> IB main thread
signal_queue = queue.Queue()


class WebhookHandler(BaseHTTPRequestHandler):
    """Minimal HTTP POST handler for TradingView webhooks."""

    def do_POST(self):
        try:
            content_len = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_len).decode('utf-8')
            data = json.loads(body)

            # Optional secret check
            secret = CONFIG.get('webhook_secret', '')
            if secret and data.get('key') != secret:
                log.warning(f"Webhook auth failed: {data}")
                self._respond(403, {'error': 'forbidden'})
                return

            # Validate required fields
            system = data.get('system', '').lower()
            action = data.get('action', '').lower()
            price = float(data.get('price', 0))

            if system not in ('v6', 'casec'):
                self._respond(400, {'error': f'unknown system: {system}'})
                return

            valid_actions = [
                'entry_long', 'entry_short',
                'exit_long', 'exit_short',
            ]
            if action not in valid_actions:
                self._respond(400, {'error': f'unknown action: {action}'})
                return

            log.info(f"Webhook received: system={system} action={action} price={price}")
            signal_queue.put({
                'system': system,
                'action': action,
                'price': price,
                'timestamp': datetime.now().isoformat(),
            })
            self._respond(200, {'status': 'queued', 'system': system, 'action': action})

        except json.JSONDecodeError:
            log.warning(f"Invalid JSON: {body[:200]}")
            self._respond(400, {'error': 'invalid json'})
        except Exception as e:
            log.error(f"Webhook handler error: {e}")
            self._respond(500, {'error': str(e)})

    def do_GET(self):
        """Health check endpoint."""
        self._respond(200, {'status': 'ok', 'uptime': 'running'})

    def _respond(self, code, data):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def log_message(self, format, *args):
        """Suppress default access logs (we log in do_POST)."""
        pass


def start_http_server(port):
    """Run HTTP server in a daemon thread."""
    server = HTTPServer(('0.0.0.0', port), WebhookHandler)
    log.info(f"Webhook HTTP server listening on port {port}")
    server.serve_forever()

# ============================================================
# IBKR Bridge
# ============================================================

class IBKRBridge:
    """
    Manages IB Gateway connection and order execution.
    Runs in the main thread with ib.sleep() loop.
    """

    def __init__(self, live=False):
        self.ib = IB()
        self.live = live
        self.port = CONFIG['live_port'] if live else CONFIG['paper_port']
        self.tg = TelegramNotifier()
        self.state = load_state(CONFIG['state_file'])
        self.contract = None
        self._needs_reconnect = False
        self._reconnect_count = 0

    def connect(self):
        self.ib.connect(
            CONFIG['ib_host'], self.port,
            clientId=CONFIG['client_id'], timeout=30
        )
        self.ib.disconnectedEvent += self._on_disconnect
        log.info(f"IB Gateway connected (port {self.port}, clientId {CONFIG['client_id']})")

        # Qualify contract
        self.contract = Future(
            symbol=CONFIG['symbol'],
            lastTradeDateOrContractMonth=CONFIG['last_trade_date'],
            exchange=CONFIG['exchange'],
            currency=CONFIG['currency'],
        )
        self.ib.qualifyContracts(self.contract)
        log.info(f"Contract qualified: {self.contract.localSymbol}")

        # Sync existing positions
        self._sync_positions()

        # Notify
        mode = "LIVE" if self.live else "PAPER"
        self.tg.startup("Webhook Server",
                        f"Mode={mode} Port={CONFIG['webhook_port']} "
                        f"Contract={self.contract.localSymbol}")

    def _on_disconnect(self):
        log.warning("IB Gateway disconnected!")
        self.tg.warn("Webhook Server", "IB Gateway disconnected")
        self._needs_reconnect = True

    def _reconnect(self):
        if self._reconnect_count >= CONFIG['max_reconnect']:
            msg = f"Reconnect limit reached ({CONFIG['max_reconnect']})"
            log.critical(msg)
            self.tg.error("Webhook Server", msg)
            return

        self._reconnect_count += 1
        delay = CONFIG['reconnect_base_delay'] * min(self._reconnect_count, 6)
        log.info(f"Reconnect attempt #{self._reconnect_count} in {delay}s...")

        time.sleep(delay)

        try:
            if self.ib.isConnected():
                self.ib.disconnect()
            self.ib.connect(
                CONFIG['ib_host'], self.port,
                clientId=CONFIG['client_id'], timeout=30
            )
            self.ib.qualifyContracts(self.contract)
            self._sync_positions()
            self._needs_reconnect = False
            self._reconnect_count = 0
            log.info(f"Reconnected successfully (attempt #{self._reconnect_count})")
            self.tg.send("Webhook Server reconnected OK")
        except Exception as e:
            log.error(f"Reconnect failed: {e}")
            self._needs_reconnect = True

    def _sync_positions(self):
        """Sync internal state with IBKR actual positions."""
        positions = self.ib.positions()
        n225_pos = 0
        for p in positions:
            if p.contract.symbol == CONFIG['symbol']:
                n225_pos += int(p.position)

        if n225_pos != 0:
            log.info(f"Synced N225 position: {n225_pos}")
        # Note: cannot distinguish v6 vs caseC positions from IBKR side.
        # State file is the source of truth for per-system tracking.

    def process_signal(self, sig):
        """Process one signal from the queue."""
        system = sig['system']
        action = sig['action']
        price = sig['price']

        pos_key = f'{system}_position' if system == 'v6' else 'caseC_position'
        entry_key = f'{system}_entry_price' if system == 'v6' else 'caseC_entry_price'
        qty = CONFIG['v6_qty'] if system == 'v6' else CONFIG['caseC_qty']
        label = 'v6 N225' if system == 'v6' else 'Case C N225'
        current_pos = self.state.get(pos_key, 0)

        log.info(f"Processing: {system} {action} price={price} current_pos={current_pos}")

        # --- EXIT ---
        if action == 'exit_long':
            if current_pos > 0:
                self._close_all_positions(system, 'SELL', qty, label)
                self.state[pos_key] = 0
                self.state[entry_key] = 0.0
                self.tg.exit(label, f"Exit Long @ {price}")
            else:
                log.info(f"  Skip exit_long: no long position ({system})")
            save_state(CONFIG['state_file'], self.state)
            return

        if action == 'exit_short':
            if current_pos < 0:
                self._close_all_positions(system, 'BUY', qty, label)
                self.state[pos_key] = 0
                self.state[entry_key] = 0.0
                self.tg.exit(label, f"Exit Short @ {price}")
            else:
                log.info(f"  Skip exit_short: no short position ({system})")
            save_state(CONFIG['state_file'], self.state)
            return

        # --- ENTRY ---
        if action == 'entry_long':
            if current_pos > 0:
                log.info(f"  Skip entry_long: already long ({system})")
                return
            if current_pos < 0:
                # Flatten short first
                self._close_all_positions(system, 'BUY', qty, label)
                self.tg.exit(label, f"Flatten Short -> Long @ {price}")

            self._enter_bracket(system, 'BUY', qty, price, label)
            self.state[pos_key] = 1
            self.state[entry_key] = price
            self.tg.trade(label, f"LONG @ {price}")

        elif action == 'entry_short':
            if current_pos < 0:
                log.info(f"  Skip entry_short: already short ({system})")
                return
            if current_pos > 0:
                # Flatten long first
                self._close_all_positions(system, 'SELL', qty, label)
                self.tg.exit(label, f"Flatten Long -> Short @ {price}")

            self._enter_bracket(system, 'SELL', qty, price, label)
            self.state[pos_key] = -1
            self.state[entry_key] = price
            self.tg.trade(label, f"SHORT @ {price}")

        save_state(CONFIG['state_file'], self.state)

    def _enter_bracket(self, system, action, qty, ref_price, label):
        """Place MKT entry + STP stop-loss (bracket order)."""
        sl_offset = CONFIG['stop_loss']
        if action == 'BUY':
            sl_price = ref_price - sl_offset
            tp_price = ref_price + 99999  # effectively no TP (exit via TV signal)
        else:
            sl_price = ref_price + sl_offset
            tp_price = max(1, ref_price - 99999)

        bracket = self.ib.bracketOrder(
            action=action,
            quantity=qty,
            limitPrice=ref_price,
            takeProfitPrice=tp_price,
            stopLossPrice=sl_price,
        )
        # Override parent to MKT
        bracket.parent.orderType = 'MKT'
        bracket.parent.lmtPrice = 0

        account = self.ib.managedAccounts()[0]
        for order in bracket:
            order.account = account
            self.ib.placeOrder(self.contract, order)
            if order.orderType == 'STP':
                log.info(f"  Order: STP {order.action} @ {sl_price}")
            elif order.orderType == 'MKT':
                log.info(f"  Order: MKT {order.action} x{order.totalQuantity}")

    def _close_all_positions(self, system, action, qty, label):
        """Close position: cancel open orders then send MKT."""
        # Cancel all open orders for this contract
        open_orders = self.ib.openOrders()
        for trade in self.ib.openTrades():
            if (trade.contract.symbol == CONFIG['symbol'] and
                    trade.order.orderType in ('STP', 'LMT') and
                    trade.orderStatus.status not in ('Cancelled', 'Inactive')):
                self.ib.cancelOrder(trade.order)
                log.info(f"  Cancelled order: {trade.order.orderType} "
                         f"{trade.order.action} @ {trade.order.lmtPrice or trade.order.auxPrice}")

        self.ib.sleep(0.5)  # Wait for cancellations

        # Flatten with MKT
        order = MarketOrder(action, qty)
        order.account = self.ib.managedAccounts()[0]
        self.ib.placeOrder(self.contract, order)
        log.info(f"  Flatten: MKT {action} x{qty}")

    def run(self):
        """Main loop: process signal queue + keep IB alive."""
        log.info("Main loop started. Waiting for webhooks...")

        try:
            while True:
                # Handle reconnect
                try:
                    self.ib.sleep(0.1)
                except (ConnectionError, OSError, asyncio.CancelledError):
                    self._needs_reconnect = True

                if self._needs_reconnect:
                    self._reconnect()
                    continue

                # Process all queued signals
                while not signal_queue.empty():
                    try:
                        sig = signal_queue.get_nowait()
                        self.process_signal(sig)
                    except queue.Empty:
                        break
                    except Exception as e:
                        log.error(f"Signal processing error: {e}")
                        self.tg.error("Webhook Server", f"Error: {e}")

        except KeyboardInterrupt:
            log.info("Shutdown requested (Ctrl+C)")
        finally:
            self.tg.send("Webhook Server shutting down")
            if self.ib.isConnected():
                self.ib.disconnect()
            log.info("Disconnected. Bye.")


# ============================================================
# Main
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description='TV Webhook -> IBKR Bridge')
    parser.add_argument('--live', action='store_true', help='Use live port (4001)')
    parser.add_argument('--port', type=int, default=CONFIG['webhook_port'],
                        help=f'Webhook listen port (default: {CONFIG["webhook_port"]})')
    parser.add_argument('--secret', type=str, default='',
                        help='Webhook authentication key')
    args = parser.parse_args()

    CONFIG['webhook_port'] = args.port
    if args.secret:
        CONFIG['webhook_secret'] = args.secret

    if args.live:
        print("=" * 50)
        print("  WARNING: LIVE TRADING MODE")
        print("=" * 50)
        confirm = input("Type 'YES' to confirm: ")
        if confirm != 'YES':
            print("Aborted.")
            sys.exit(0)

    # Start HTTP server in background thread
    http_thread = threading.Thread(
        target=start_http_server,
        args=(CONFIG['webhook_port'],),
        daemon=True,
    )
    http_thread.start()

    # Connect to IB and run main loop
    bridge = IBKRBridge(live=args.live)
    bridge.connect()
    bridge.run()


if __name__ == '__main__':
    main()
