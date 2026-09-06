import time, json, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from funding_scout import scan_opportunities as scan_hyperliquid

class DiscoveryEngine:
    def __init__(self):
        self.venues = {
            "hyperliquid": self._scan_hyperliquid,
            "dydx": self._scan_dydx,
            "aave": self._scan_aave,
            "cex_binance": self._scan_binance,
        }
    
    def scan_all(self):
        all_opps = []
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(v): k for k, v in self.venues.items()}
            for f in as_completed(futs):
                try:
                    for op in f.result():
                        op["venue"] = futs[f]
                        all_opps.append(op)
                except Exception as e:
                    print(f"[discovery] {futs[f]} error: {e}")
        all_opps.sort(key=lambda x: x.get("profit_usd", 0), reverse=True)
        return all_opps
    
    def _scan_hyperliquid(self):
        ops = scan_hyperliquid(min_rate=0.0001, min_oi=500_000, top_n=10)
        return [{"type": "funding", "coin": o["coin"], "direction": o["direction"],
                 "rate": o["funding_rate"], "profit_usd": 1000 * abs(o["funding_rate"])} for o in ops]
    
    def _scan_dydx(self):
        try:
            req = urllib.request.Request("https://api.dydx.exchange/v3/markets", headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            return [{"type": "perp_funding", "coin": m.get("market", ""), "rate": float(m.get("nextFundingRate", 0)),
                     "profit_usd": 1000 * abs(float(m.get("nextFundingRate", 0)))}
                    for m in data.get("markets", {}).values() if abs(float(m.get("nextFundingRate", 0))) > 0.0005]
        except Exception:
            return []
    
    def _scan_aave(self):
        try:
            url = "https://api.thegraph.com/subgraphs/name/aave/protocol-v3"
            query = """{users(first:100,where:{healthFactor_lt:1},orderBy:healthFactor){id healthFactor totalCollateralBase}}"""
            req = urllib.request.Request(url, data=json.dumps({"query": query}).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            return [{"type": "liquidation", "target": u.get("id", "")[:10], "health_factor": float(u.get("healthFactor", 1)),
                     "profit_usd": float(u.get("totalCollateralBase", 0)) * 0.05}
                    for u in data.get("data", {}).get("users", []) if float(u.get("healthFactor", 1)) < 1.0]
        except Exception:
            return []
    
    def _scan_binance(self):
        try:
            req = urllib.request.Request("https://api.binance.com/api/v3/ticker/24hr")
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            return [{"type": "cex_price", "coin": t["symbol"].replace("USDT", ""), "price": float(t.get("lastPrice", 0)),
                     "volume": float(t.get("quoteVolume", 0))}
                    for t in data if t.get("symbol", "").endswith("USDT") and float(t.get("quoteVolume", 0)) > 1e6][:20]
        except Exception:
            return []


if __name__ == "__main__":
    print("=" * 70)
    print("DISCOVERY ENGINE")
    print("=" * 70)
    eng = DiscoveryEngine()
    t0 = time.time()
    ops = eng.scan_all()
    print(f"Scan: {time.time()-t0:.1f}s | Opportunities: {len(ops)}")
    for i, o in enumerate(ops[:10], 1):
        coin = o.get("coin", o.get("target", "?"))
        print(f"{i:2d}. [{o.get('venue','?'):<12}] {o.get('type','?'):<12} {coin:<10} ${o.get('profit_usd',0):.2f}")
