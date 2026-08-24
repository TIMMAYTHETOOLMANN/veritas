#!/usr/bin/env python3
"""
watch_position.py — READ-ONLY watchdog for the live hot-wallet position.

Absolutely no order placement, no cancels, no signing, no state changes.
Polls the public Hyperliquid /info API once per minute and:
  - logs a heartbeat every ~15 min to watch_position.log (visible pulse)
  - EXITS with an alert the moment:
      * the position goes flat (stop tripped or closed manually)   -> exit 2
      * equity drops below EQUITY_FLOOR (slow bleed)               -> exit 3
The exit triggers a process-completion notification back into the chat.

Deployed 2026-08-23 after both engines were killed by the Hermes app
restart: the exchange-side stop survives process death; the engine does not.
This script is the gap-filler until the commander decides on a restart.
"""
import datetime
import json
import sys
import time
import urllib.request

ADDR = "0x1a0d467974E70e3c1a2b7b84Fec21183Fc4eB60f"
LOG = "watch_position.log"
EQUITY_FLOOR = 15.50   # stop-trip lands ~$9.5; this catches slow bleed before that
POLL_SEC = 60
HEARTBEAT_EVERY = 15   # polls (~15 min)


def post(payload):
    req = urllib.request.Request(
        "https://api.hyperliquid.xyz/info",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def log(msg):
    line = f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def main():
    log("WATCHDOG START — read-only. MOVE long 2326 @ 0.008371, "
        "exchange stop 0.005365, equity floor $%.2f" % EQUITY_FLOOR)
    n = 0
    while True:
        try:
            st = post({"type": "clearinghouseState", "user": ADDR})
            eq = float(st["marginSummary"]["accountValue"])
            poss = [ap["position"] for ap in st.get("assetPositions", [])
                    if float(ap["position"].get("szi", 0) or 0) != 0]
            n += 1
            if n % HEARTBEAT_EVERY == 1:
                ps = "; ".join(f"{p['coin']} {p['szi']} uPnL {p['unrealizedPnl']}"
                               for p in poss) or "FLAT"
                log(f"pulse equity=${eq:.2f} pos=[{ps}]")
            if not poss:
                log(f"ALERT: POSITION FLAT (equity ${eq:.2f}) — "
                    f"stop tripped or position closed. Investigate fills.")
                sys.exit(2)
            if eq < EQUITY_FLOOR:
                upnl = sum(float(p.get("unrealizedPnl") or 0) for p in poss)
                log(f"ALERT: equity ${eq:.2f} below floor ${EQUITY_FLOOR:.2f} "
                    f"(uPnL ${upnl:.2f}) — bleed without position flat.")
                sys.exit(3)
        except Exception as e:
            log(f"poll error (continuing): {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
