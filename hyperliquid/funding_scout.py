# funding_scout.py — repeatable funding-extreme trade discovery for Hyperliquid
#
# Packages the exact analysis that identified the PURR 2026-08-22 trade:
#   1. Pull all perp markets, rank by |funding|
#   2. For top candidates: 72h funding history (persistence check),
#      L2 book (spread/depth), 48h candles (range position, violence),
#      spot mark when the coin trades on Hyperliquid spot (premium/discount)
#   3. Score: persistent |funding| + perp-spot premium alignment + liquidity
#   4. Emit a trade card with sizing math at 3x/5x/10x, stops, and exit rules
#
# READ-ONLY market data via the public /info endpoint. Stdlib only.
#
# Usage: python funding_scout.py [--top 5] [--equity 20] [--json]
import argparse
import json
import urllib.request

API = "https://api.hyperliquid.xyz/info"


def info(body):
    req = urllib.request.Request(
        API, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def fmt(x, n=2):
    return f"{x:,.{n}f}"


def candles_ok_range(coin, hours=48):
    """48h candle stats: range position %, biggest 1h move, last close."""
    now_ms = None
    import time
    now_ms = int(time.time() * 1000)
    rows = info({"type": "candleSnapshot", "req": {
        "coin": coin, "interval": "1h",
        "startTime": now_ms - hours * 3600_000,
        "endTime": now_ms}})
    if not rows:
        return None
    hi = max(float(r["h"]) for r in rows)
    lo = min(float(r["l"]) for r in rows)
    close = float(rows[-1]["c"])
    biggest = max(abs(float(r["c"]) / float(r["o"]) - 1) for r in rows
                  if float(r["o"]) > 0)
    pos = (close - lo) / (hi - lo) if hi > lo else 0.5
    return {"high": hi, "low": lo, "close": close, "range_pos": pos,
            "biggest_1h": biggest}


def funding_history(coin, hours=72):
    rows = info({"type": "fundingHistory", "coin": coin,
                 "startTime": int((__import__("time").time() - hours * 3600) * 1000)})
    if not rows:
        return None
    rates = [float(r["fundingRate"]) for r in rows]
    return {"n": len(rates), "avg": sum(rates) / len(rates),
            "last": rates[0], "max": max(rates, key=abs)}


def spot_mark(coin):
    """Spot mark if the coin trades on Hyperliquid spot, else None."""
    try:
        sd = info({"type": "spotMetaAndAssetCtxs"})
    except Exception:
        return None
    for row in sd[1]:
        name = row.get("coin", "")
        base = name.split("/")[0] if "/" in name else name
        if base == coin:
            px = row.get("markPx")
            return float(px) if px else None
    return None


def analyze(equity=20.0, top=5, taker_fee=0.00045, min_oi=1_000_000):
    out = {"generated": __import__("time").strftime("%Y-%m-%d %H:%M:%S UTC",
                                                    __import__("time").gmtime()),
           "equity": equity, "candidates": []}

    # 1. rank by |funding|
    markets = info({"type": "metaAndAssetCtxs"})
    universe = []
    meta = markets[0]
    for name, ctx in zip(meta.get("universe", []), markets[1]):
        if name.get("isDelisted"):
            continue
        coin = name.get("name", "?")
        f = float(ctx.get("funding", 0) or 0)
        oi = float(ctx.get("openInterest", 0) or 0)
        mark = float(ctx.get("markPx", 0) or 0)
        if mark > 0 and oi >= min_oi:
            universe.append({"coin": coin, "funding": f, "oi": oi, "mark": mark})
    universe.sort(key=lambda m: -abs(m["funding"]))

    # 2-3. deepen top candidates
    for m in universe[:top]:
        coin = m["coin"]
        fh = funding_history(coin)
        cnd = funding_history_ok = fh
        cand = dict(m)
        cand["funding_72h"] = fh
        sm = spot_mark(coin)
        if sm:
            cand["spot"] = sm
            cand["premium"] = m["mark"] / sm - 1
        cand["candles48h"] = candles_ok_range(coin)
        out["candidates"].append(cand)

    # 4. scoring: persistence x premium alignment x direction
    for c in out["candidates"]:
        fh = c.get("funding_72h") or {}
        pers = min(1.0, (fh.get("avg", 0) / c["funding"]) if c["funding"] else 0)
        base = abs(c["funding"])
        prem = c.get("premium")
        prem_score = 0.0
        if prem is not None:
            # positive funding + positive premium -> short perp aligned
            aligned = (c["funding"] > 0) == (prem > 0)
            prem_score = min(1.0, abs(prem) * 40) * (1 if aligned else -1)
        liq = min(1.0, c["oi"] / 50e6)
        c["score"] = round(
            (min(base * 400, 1.0) * 0.45 + pers * 0.25 + max(prem_score, 0) * 0.20
             + liq * 0.10), 3)
        c["direction"] = "SHORT" if c["funding"] > 0 else "LONG"

    out["candidates"].sort(key=lambda c: -c["score"])

    # 5. trade card for the winner
    best = out["candidates"][0] if out["candidates"] else None
    if best:
        coin = best["coin"]
        card = {"coin": coin, "direction": best["direction"],
                "entry": best["mark"], "venue": "Hyperliquid"}
        if best.get("spot"):
            card["spot"] = best["spot"]
            card["premium"] = round(best["premium"] * 100, 2)
        fh = best.get("funding_72h") or {}
        card["funding_hr"] = best["funding"]
        card["funding_7h_avg"] = round(fh.get("avg", 0), 6)
        c48 = best.get("candles48h") or {}
        if c48:
            card["high_48h"] = c48["high"]
            card["range_pos"] = round(c48["range_pos"] * 100, 0)
        # sizing at 3x/5x/10x
        sizes = {}
        dir_mult = 1 if best["direction"] == "LONG" else -1
        for lev in (3, 5, 10):
            notional = equity * lev
            fees = 2 * notional * taker_fee
            liq_move = 0.925 / lev
            stop = (best["mark"] * (1 + liq_move) if best["direction"] == "SHORT"
                    else best["mark"] * (1 - liq_move))
            # carry: you RECEIVE funding when your side is opposite the sign
            # (positive funding -> shorts receive; negative -> longs receive)
            f12 = -dir_mult * notional * (fh.get("avg", 0) / 2) * 12
            conv = notional * (abs(best.get("premium") or 0) / 2)
            sizes[f"{lev}x"] = {
                "notional": round(notional, 2), "fees_rt": round(fees, 3),
                "liq_price": round(stop, 6),
                "est_12h_carry_usd": round(f12, 2),
                "est_convergence_usd": round(conv, 2),
            }
        card["sizing"] = sizes
        card["exit_rules"] = [
            "EXIT when hourly funding crosses toward 0 (sign flip or <20% of entry avg)",
            "EXIT when |perp-spot premium| < 0.3%",
            "TIME STOP: flat at 24h",
            "HARD STOP: set at entry, beyond 48h extreme (UI stop, not mental)",
        ]
        out["trade_card"] = card

    return out


def report(out):
    print(f"=== FUNDING SCOUT — {out['generated']} — equity ${out['equity']} ===\n")
    print(f"{'coin':<10}{'fund/hr':>9}{'7h avg':>9}{'premium':>9}{'OI $M':>8}"
          f"{'range%':>8}{'score':>7}{'dir':>7}")
    for c in out["candidates"]:
        fh = c.get("funding_72h") or {}
        prem = f"{c['premium']*100:+.2f}%" if c.get("premium") is not None else "n/a"
        c48 = c.get("candles48h") or {}
        rp = f"{c48.get('range_pos', 0)*100:.0f}%" if c48 else "n/a"
        print(f"{c['coin']:<10}{c['funding']*100:>8.4f}%"
              f"{fh.get('avg', 0)*100:>8.4f}%{prem:>9}{c['oi']/1e6:>8.1f}"
              f"{rp:>8}{c['score']:>7.2f}{c['direction']:>7}")
    tc = out.get("trade_card")
    if tc:
        print(f"\n--- TRADE CARD: {tc['direction']} {tc['coin']} @ {tc['entry']} ---")
        if tc.get("premium") is not None:
            print(f"    spot {tc['spot']} | premium {tc['premium']}%")
        print(f"    funding now {tc['funding_hr']*100:.4f}%/hr | "
              f"7h avg {tc['funding_7h_avg']*100:.4f}%/hr")
        if tc.get("high_48h"):
            print(f"    48h high {tc['high_48h']} | range pos {tc['range_pos']}%")
        for lev, s in tc["sizing"].items():
            print(f"    {lev}: notional ${s['notional']:.0f} | fees ${s['fees_rt']:.3f} | "
                  f"liq {s['liq_price']} | 12h carry ${s['est_12h_carry_usd']:.2f} | "
                  f"conv ${s['est_convergence_usd']:.2f}")
        print("    exits:")
        for r in tc["exit_rules"]:
            print(f"      - {r}")


def main():
    ap = argparse.ArgumentParser(description="funding-extreme trade scout")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--equity", type=float, default=20.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    out = analyze(equity=args.equity, top=args.top)
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        report(out)


if __name__ == "__main__":
    main()
