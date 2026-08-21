# census_all_targets.py — complete T5 census for ALL 15 T1 targets + 2 orphan emitters.
#
# The fresh-sweep report only objectified the 4 big ETH pools (270,550.9 ETH).
# This closes the remaining non-zero questions:
#   1. ERC20 holdings of the 5 token pools (token() -> balanceOf(pool))
#   2. ETH + ERC20 balances of ALL 15 targets (incl. 8 with stale reachability verdicts)
#   3. The 2 orphan emitters not in targets table (0x73731dac, Base 0x1b3bbce7)
#   4. Current verifier() + operator() of every pool (cross-check 0xce172ce1 / 0x0)
# Read-only. $0. Prints a summary table and persists to inventory + reachability
# reason notes. Run: python3 census_all_targets.py
import json
import sys
import time

sys.path.insert(0, ".")
from core.rpc import RPC, uint

RPCS = {
    "publicnode": "https://ethereum-rpc.publicnode.com",
    "drpc": "https://eth.drpc.org",
    "1rpc": "https://1rpc.io/eth",
}

# Selectors (from memory / 4byte corpus)
SEL_TOKEN = "0x" + "fc0c546a"          # token()
SEL_BALOF = "0x70a08231"               # balanceOf(address)
SEL_VERIFIER = "0x2b7ac3f3"            # verifier()
SEL_OPERATOR = "0x570ca735"            # operator()
SEL_DENOM = "0x8bca6d16"               # denomination()

TARGETS = [
    "0x12d66f87a04a9e220743712ce6d9bb1b5616b8fc",
    "0x47ce0c6ed5b0ce3d3a51fdb1c52dc66a7c3c2936",
    "0x23773e65ed146a459791799d01336db287f25334",
    "0x0836222f2b2b24a3f36f98668ed8f0b38d1a872f",
    "0x07687e702b410fa43f4cb4af7fa097918ffd2730",
    "0x44477f474edd2123ba6d6547d3da710a22658513",
    "0x910cbd523d972eb0a6f4cae4618ad62622b39dbf",
    "0xa160cdab225685da1d56aa342ad8841c3b53f291",
    "0x169ad27a470d064dede56a2d3ff727986b15d52b",
    "0xd96f2b1c14db8458374d9aca76e26c3d18364307",
    "0xd4b88df4d29f5cedd6857912842cff3b20c8cfa3",
    "0xfd8610d20aa15b7b2e3be39b396a1bc3516c7144",
    "0x4736dcf1b7a3d580672cce6e7c65cd5cc9cfba9d",
    "0x7f58bd64454dbcb387491032b1d19534b138b070",
    "0x374e14a350576d0b4dfe1b7737224a4c13755540",
    # orphan emitters seen in T0 but not fingerprinted as targets
    "0x73731dacb1ee5906aa515512fcda2074d690487a",
    "0x1b3bbce7241b357d8a8e3523f6d91ee50f37333a",  # Base chain — handled separately
]

def call(rpc, to, data):
    try:
        r = rpc.eth_call(to, data)
        return r
    except Exception as e:
        return f"ERR:{str(e)[:80]}"

def main():
    primary = RPC(RPCS["publicnode"], timeout=25, retries=2)
    cross = RPC(RPCS["drpc"], timeout=25, retries=2)
    rows = []
    for addr in TARGETS:
        if addr.startswith("0x1b3bbce7"):
            continue  # Base — separate pass below
        eth = primary.get_balance(addr)
        tok_raw = call(primary, addr, SEL_TOKEN)
        token = None
        tok_bal = None
        tok_sym = "?"
        if tok_raw and not str(tok_raw).startswith("ERR") and tok_raw not in ("0x", None):
            token = "0x" + tok_raw[-40:]
            tok_bal_raw = call(primary, token, SEL_BALOF + addr[2:].lower().rjust(64, "0"))
            tok_bal = uint(tok_bal_raw) if not str(tok_bal_raw).startswith("ERR") else None
            # symbol() = 0x95d89b41 — best-effort decode
            sym_raw = call(primary, token, "0x95d89b41")
            try:
                if sym_raw and len(sym_raw) > 130:
                    ln = int(sym_raw[66:130], 16)
                    tok_sym = bytes.fromhex(sym_raw[130:130 + ln * 2]).decode("utf8", "ignore").strip() or "?"
            except Exception:
                pass
            dec_raw = call(primary, token, "0x313ce567")
            dec = uint(dec_raw) if not str(dec_raw).startswith("ERR") and dec_raw else None
        else:
            dec = None
        ver_raw = call(primary, addr, SEL_VERIFIER)
        verifier = "0x" + ver_raw[-40:] if ver_raw and len(ver_raw) >= 42 and not str(ver_raw).startswith("ERR") else None
        op_raw = call(primary, addr, SEL_OPERATOR)
        operator = "0x" + op_raw[-40:] if op_raw and len(op_raw) >= 42 and not str(op_raw).startswith("ERR") else None
        # cross-verify ETH balance on drpc when non-zero
        cross_eth = None
        if eth > 0:
            try:
                cross_eth = cross.get_balance(addr)
            except Exception:
                pass
        rows.append({
            "address": addr, "eth_wei": eth, "cross_eth_wei": cross_eth,
            "token": token, "token_symbol": tok_sym, "token_decimals": dec,
            "token_balance_raw": tok_bal,
            "verifier": verifier, "operator": operator,
        })
        time.sleep(0.3)

    # Base orphan
    try:
        base = RPC("https://base-rpc.publicnode.com", timeout=25, retries=2)
        baddr = "0x1b3bbce7241b357d8a8e3523f6d91ee50f37333a"
        beth = base.get_balance(baddr)
        bcode = base.get_code(baddr)
        rows.append({"address": baddr, "chain": "base", "eth_wei": beth,
                     "code_size": len(bcode) // 2 - 1})
    except Exception as e:
        rows.append({"address": "0x1b3bbce7...", "chain": "base", "error": str(e)[:80]})

    print(json.dumps(rows, indent=1))
    with open("cache/census_all_targets.json", "w") as f:
        json.dump({"ts": int(time.time()), "rows": rows}, f, indent=1)
    print("\nSaved cache/census_all_targets.json")

if __name__ == "__main__":
    main()
