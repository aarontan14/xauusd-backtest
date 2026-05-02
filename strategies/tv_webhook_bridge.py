"""
TradingView -> IBKR webhook bridge.

Pair this with the PineScript: TradingView fires JSON alerts to a webhook URL,
this Flask app receives them and routes BUY/SELL orders to IBKR via ib_insync.

DEPLOY:
  pip install flask ib_insync
  python tv_webhook_bridge.py --port 8080 --secret YOUR_SECRET --instrument CFD
  Expose with ngrok / Cloudflare Tunnel:
      ngrok http 8080
  Put the public URL into TradingView alert webhook with HEADER:
      X-Auth-Token: YOUR_SECRET
  Pine alert "Message" field (already in the .pine file):
      {"action":"buy","symbol":"XAUUSD","price":"{{close}}","time":"{{timenow}}"}

SECURITY:
  - Always set --secret. The bridge rejects requests without the matching header.
  - Run on a private VPS or your own machine. Never expose without TLS in production.
  - Test with --paper first (default port 7497).
"""
from __future__ import annotations
import argparse, json, logging, os, threading
from flask import Flask, request, jsonify

LOG = logging.getLogger("tv_bridge")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = Flask(__name__)
CONFIG = {}
_lock = threading.Lock()


def place_order(payload: dict):
    """Translate webhook payload into an IBKR order."""
    from ib_insync import IB, Future, CFD, MarketOrder, StopOrder

    action = payload.get("action", "").lower()
    if action not in ("buy", "sell"):
        LOG.warning(f"Unknown action: {action}")
        return {"ok": False, "error": "bad_action"}

    ib = IB()
    ib.connect(CONFIG["host"], CONFIG["port"], clientId=CONFIG["client_id"])
    try:
        instrument = CONFIG["instrument"]
        if instrument == "CFD":
            contract = CFD("XAUUSD", "SMART", "USD")
        elif instrument == "MGC":
            contract = Future("MGC", exchange="COMEX", currency="USD")
        else:
            contract = Future("GC", exchange="COMEX", currency="USD")
        ib.qualifyContracts(contract)

        account = CONFIG.get("account") or ""
        summary = ib.accountSummary(account)
        nl = next((float(x.value) for x in summary if x.tag == "NetLiquidation"), None)
        positions = [p for p in ib.positions(account) if p.contract.conId == contract.conId]
        pos_qty = positions[0].position if positions else 0

        [tk] = ib.reqTickers(contract)
        last = tk.marketPrice()
        LOG.info(f"NL={nl} pos={pos_qty} last={last}")

        if action == "buy" and pos_qty == 0:
            notional = nl * CONFIG["leverage"]
            if instrument == "CFD":
                qty = round(notional / last, 2)
            elif instrument == "MGC":
                qty = max(1, int(notional / (last * 10)))
            else:
                qty = max(1, int(notional / (last * 100)))
            t = ib.placeOrder(contract, MarketOrder("BUY", qty, account=account or None))
            LOG.info(f"BUY sent qty={qty}")
            return {"ok": True, "action": "buy", "qty": qty}

        if action == "sell" and pos_qty > 0:
            ib.cancelAllOpenOrders()
            t = ib.placeOrder(contract, MarketOrder("SELL", pos_qty, account=account or None))
            LOG.info(f"SELL sent qty={pos_qty}")
            return {"ok": True, "action": "sell", "qty": pos_qty}

        return {"ok": True, "action": "noop", "pos": pos_qty}
    finally:
        ib.disconnect()


@app.post("/webhook")
def webhook():
    if request.headers.get("X-Auth-Token") != CONFIG["secret"]:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    try:
        # TradingView sends raw text body that should be JSON
        if request.is_json:
            payload = request.get_json(force=True)
        else:
            payload = json.loads(request.data.decode("utf-8"))
    except Exception as e:
        return jsonify({"ok": False, "error": f"bad_payload:{e}"}), 400
    LOG.info(f"Received: {payload}")
    if CONFIG.get("dry_run"):
        return jsonify({"ok": True, "dry_run": True, "payload": payload})
    with _lock:  # serialize broker calls
        result = place_order(payload)
    return jsonify(result)


@app.get("/health")
def health():
    return jsonify({"ok": True})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--secret", required=True, help="X-Auth-Token shared secret")
    ap.add_argument("--instrument", choices=["CFD","MGC","GC"], default="CFD")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--ibkr-port", type=int, default=7497)
    ap.add_argument("--client-id", type=int, default=43)
    ap.add_argument("--account", default=None)
    ap.add_argument("--leverage", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    CONFIG.update(dict(secret=args.secret, instrument=args.instrument,
                       host=args.host, port=args.ibkr_port,
                       client_id=args.client_id, account=args.account,
                       leverage=args.leverage, dry_run=args.dry_run))

    LOG.info(f"Starting bridge on :{args.port} dry_run={args.dry_run}")
    app.run(host="0.0.0.0", port=args.port, debug=False)


if __name__ == "__main__":
    main()
