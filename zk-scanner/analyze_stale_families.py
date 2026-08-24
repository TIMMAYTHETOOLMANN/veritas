# analyze_stale_families.py — resolve the 8 STALE reachability verdicts.
#
# The DB still holds pre-fix verdicts (ts=1787266683) for 8 targets:
#   Family A (bytecode cd997f10, 5999B): 0x0836222f, 0x4736dcf1, 0xfd8610d2
#            -> wrongly NO_MONEY_PATH ("0x06394c9b = plain transfer" — actually changeOperator)
#   Family B (bytecode 1363399e, 7521B): 0x23773e65, 0x07687e70   -> UNKNOWN ($7.5M DAI!)
#   Family C (bytecode d829dca8, 7586B): 0x44477f47, 0x7f58bd64, 0x374e14a3 -> UNKNOWN (V=0)
#
# Method (the corrected doctrine): proper PUSH4 disassembly (respect PUSH widths),
# resolve every dispatched selector via known map + 4byte.directory, read
# verifier()/operator()/owner()/isMigrated() storage, capture exact revert data.
# Read-only, $0.
import json, sys, time, urllib.request
sys.path.insert(0, ".")
from core.rpc import RPC, uint

STALE = [
    "0x23773e65ed146a459791799d01336db287f25334",  # B  6.5M DAI
    "0x07687e702b410fa43f4cb4af7fa097918ffd2730",  # B  1.03M DAI
    "0x44477f474edd2123ba6d6547d3da710a22658513",  # C  0 USDT
    "0x7f58bd64454dbcb387491032b1d19534b138b070",  # C  0 USDT
    "0x374e14a350576d0b4dfe1b7737224a4c13755540",  # C  0 aEthUSDT
    "0x0836222f2b2b24a3f36f98668ed8f0b38d1a872f",  # A  39K USDT
    "0x4736dcf1b7a3d580672cce6e7c65cd5cc9cfba9d",  # A  25K USDC
    "0xfd8610d20aa15b7b2e3be39b396a1bc3516c7144",  # A  115K DAI
]
BASE_ORPHAN = "0x1b3bbce7241b357d8a8e3523f6d91ee50f37333a"

# Known tornado-clone family selectors (verified 2026-08-20)
KNOWN = {
    "21a0adb6": "withdraw(bytes,bytes32,bytes32,address,address,uint256,uint256)",
    "695ef6f9": "verifyProof(bytes,uint256[6])",
    "97fc007c": "updateVerifier(address)",
    "06394c9b": "changeOperator(address)",
    "2b7ac3f3": "verifier()",
    "570ca735": "operator()",
    "b06faf62": "isMigrated()",
    "88d761f2": "finishMigration()",
    "e5285dcc": "isSpent(bytes32)",
    "9fa12d0b": "isSpentArray(bytes32[])",
    "ba70f757": "getLastRoot()",
    "8bca6d16": "denomination()",
    "414a37ba": "FIELD_SIZE()",
    "38bf282e": "hashLeftRight(bytes32,bytes32)",
    "f47d33b5": "MiMCSponge(uint256,uint256)",
    "cd87a3b4": "ROOT_HISTORY_SIZE()",
    "90eeb02b": "currentRootIndex()",
    "c2b40ae4": "roots(uint256)",
    "839df945": "commitments(bytes32)",
    "17cc915c": "nullifierHashes(bytes32)",
    "6d9833e3": "isKnownRoot(bytes32)",
    "fc7e9c6f": "nextIndex()",
    "f178e47c": "filledSubtrees(uint256)",
    "e8295588": "zeros(uint256)",
    "ec732959": "ZERO_VALUE()",
    "8da5cb5b": "owner()",
    "f340fa01": "deposit(bytes32)",
    "fc0c546a": "token()",
    "70a08231": "balanceOf(address)",
    "18160ddd": "totalSupply()",
    "dd62ed3e": "allowance(address,address)",
    "a9059cbb": "transfer(address,uint256)",
    "23b872dd": "transferFrom(address,address,uint256)",
    "095ea7b3": "approve(address,uint256)",
    "313ce567": "decimals()",
    "95d89b41": "symbol()",
    "06fdde03": "name()",
}

def push4_set(code: bytes):
    """Real PUSH4 disassembly respecting PUSH1..PUSH32 widths."""
    sels, i, n = set(), 0, len(code)
    while i < n:
        op = code[i]
        if 0x60 <= op <= 0x7f:
            sz = op - 0x5f
            if sz == 4 and i + 5 <= n:
                sels.add(code[i+1:i+5].hex())
            i += sz + 1
        else:
            i += 1
    return sels

_4b_cache = {}
def lookup4(sel):
    if sel in KNOWN:
        return KNOWN[sel]
    if sel in _4b_cache:
        return _4b_cache[sel]
    try:
        url = f"https://www.4byte.directory/api/v1/signatures/?hex_signature=0x{sel}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read())
        res = data.get("results", [])
        v = res[0]["text_signature"] if res else None
    except Exception as e:
        v = None
    _4b_cache[sel] = v
    time.sleep(0.15)
    return v

def probe(rpc, addr, selhex):
    """eth_call with EXACT outcome capture: ('ok', hex) / ('revert', data) / ('err', msg)."""
    try:
        r = rpc.eth_call(addr, "0x" + selhex)
        return ("ok", r)
    except Exception as e:
        msg = str(e)
        if "execution reverted" in msg.lower() or "revert" in msg.lower():
            return ("revert", msg[-140:])
        return ("err", msg[:140])

def word_addr(hexstr):
    if not hexstr or hexstr in ("0x",) or len(hexstr) < 42:
        return None
    return "0x" + hexstr[-40:]

def main():
    rpc = RPC("https://ethereum-rpc.publicnode.com", timeout=25, retries=2)
    out = {}
    for addr in STALE:
        code = bytes.fromhex(rpc.get_code(addr)[2:])
        sels = push4_set(code)
        resolved = {}
        for s in sorted(sels):
            resolved[s] = lookup4(s)
        probes = {}
        for name, sel in [("verifier", "2b7ac3f3"), ("operator", "570ca735"),
                          ("owner", "8da5cb5b"), ("isMigrated", "b06faf62"),
                          ("denomination", "8bca6d16"), ("token", "fc0c546a"),
                          ("getLastRoot", "ba70f757"), ("ZERO_VALUE", "ec732959")]:
            st, val = probe(rpc, addr, sel)
            if st == "ok" and name in ("verifier", "operator", "owner", "token"):
                val = word_addr(val)
            elif st == "ok" and val and val != "0x":
                try:
                    val = str(int(val, 16))
                except Exception:
                    val = val[:66]
            probes[name] = f"{st}:{val}"
        out[addr] = {"code_size": len(code), "n_push4": len(sels),
                     "selectors": resolved, "probes": probes}
        time.sleep(0.2)

    # Base orphan
    try:
        brpc = RPC("https://base-rpc.publicnode.com", timeout=25, retries=2)
        code = bytes.fromhex(brpc.get_code(BASE_ORPHAN)[2:])
        sels = push4_set(code)
        resolved = {s: lookup4(s) for s in sorted(sels)}
        probes = {}
        for name, sel in [("verifier", "2b7ac3f3"), ("operator", "570ca735"),
                          ("token", "fc0c546a")]:
            st, val = probe(brpc, BASE_ORPHAN, sel)
            if st == "ok" and name in ("verifier", "operator", "token"):
                val = word_addr(val)
            probes[name] = f"{st}:{val}"
        out[BASE_ORPHAN + " (base)"] = {"code_size": len(code), "n_push4": len(sels),
                                        "selectors": resolved, "probes": probes}
    except Exception as e:
        out[BASE_ORPHAN + " (base)"] = {"error": str(e)[:120]}

    with open("cache/stale_families_analysis.json", "w") as f:
        json.dump({"ts": int(time.time()), "targets": out}, f, indent=1)
    # compact print
    for addr, info in out.items():
        print("=" * 90)
        print(addr, "| code:", info.get("code_size"), "| PUSH4 count:", info.get("n_push4"))
        if "error" in info:
            print("  ERROR:", info["error"]); continue
        for s, sig in info["selectors"].items():
            mark = ""
            if s in ("21a0adb6", "695ef6f9", "97fc007c", "06394c9b"):
                mark = "   <== MONEY-PATH RELEVANT"
            print(f"  0x{s}  {sig or '??? UNKNOWN'}{mark}")
        for name, res in info["probes"].items():
            print(f"  probe {name:12s} -> {res}")
    print("\nSaved cache/stale_families_analysis.json")

if __name__ == "__main__":
    main()
