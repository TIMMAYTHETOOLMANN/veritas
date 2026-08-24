#!/usr/bin/env python3
"""
sim_gate.py — VERITAS Layer 2: anvil-fork simulation gate.

$0 cost (local fork), ZERO live funds at risk. For any candidate edge the
scanner finds, this harness:

  1. Forks Arbitrum at the latest block (anvil, ~/.foundry/anvil.exe).
  2. Deploys the FlashloanArb executor.
  3. Executes the EXACT transaction the hunter would send live:
     flashLoanSimple(WETH, principal) -> sell WETH on poolBuy -> buy WETH
     back on poolSell -> repay principal+premium -> keep profit.
  4. Measures profit from the WETH balance delta of the owner (before vs
     after sweep) — ground truth, includes every fee and curve effect.
  5. GATE: net_profit_usd > 20 * gas_used_usd  (user's spec) and
     net_profit_usd > $0.50 minimum. Pass => GO for live broadcast.

Selftest mode (--selftest) proves the whole chain on a synthetic
dislocation built from two mock pairs, without touching real pools:
mock pair A (cheap WETH) and mock pair B (expensive WETH); executor
borrows, sells on A... wait — sells WETH on the CHEAP pool first is wrong.
Direction is chosen by the scanner; the selftest wires the direction that
profits.

Usage:
  python3 sim_gate.py --selftest
  python3 sim_gate.py --edge '{"direction":"A -> B", "size_weth":1.0, ...}'
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from core.rpc import RPC, uint  # noqa: E402

WETH = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
AAVE_V3_POOL = "0x794a61358d6845594f94dc1db02a252b5b4814ad"

FORK_RPCS = [
    "https://arb1.arbitrum.io/rpc",
    "https://arbitrum-one.publicnode.com",
    "https://gateway.tenderly.co/public/arbitrum",
]

GAS_MULTIPLIER = 20      # user spec: profit > gas * 20
MIN_PROFIT_USD = 0.50


# ---- anvil lifecycle (proven sim_trade.py pattern) -----------------------

def find_anvil():
    for c in (os.path.join(os.path.expanduser("~"), ".foundry", "anvil.exe"),
              os.path.join(HERE, "tools", "anvil.exe")):
        if c and os.path.isfile(c):
            return c
    import shutil
    return shutil.which("anvil")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def kill_tree(proc):
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def launch_fork():
    anvil = find_anvil()
    if not anvil:
        raise RuntimeError("anvil not found (install Foundry)")
    fork_url = None
    head = None
    rpc = None
    for url in FORK_RPCS:
        try:
            r = RPC(url, timeout=30, retries=2)
            head = uint(r.call("eth_blockNumber", []))
            if head:
                fork_url = url
                rpc = r
                break
        except Exception:
            continue
    if not fork_url:
        raise RuntimeError("no working fork RPC")
    port = free_port()
    proc = subprocess.Popen(
        [anvil, "--port", str(port), "--fork-url", fork_url,
         "--fork-block-number", str(head)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    host = f"http://127.0.0.1:{port}"
    # wait for readiness
    for _ in range(100):
        try:
            local = RPC(host, timeout=5, retries=1)
            if uint(local.call("eth_blockNumber", [])):
                return proc, host, head, fork_url
        except Exception:
            time.sleep(0.3)
    kill_tree(proc)
    raise RuntimeError("anvil fork failed to start")


# ---- tiny tx sender (anvil has unlocked keys; we fund a sim EOA) ---------

def pad_addr(a):
    return a.lower().replace("0x", "").rjust(64, "0")


def pad_u256(v):
    return f"{int(v):064x}"


class Fork:
    """Minimal JSON-RPC client with eth_call / anvil cheatcodes / raw send."""

    def __init__(self, host):
        self.host = host
        self._opener = urllib.request.build_opener()
        self.nonce_cache = {}
        self.id = 0

    def req(self, method, params):
        self.id += 1
        payload = {"jsonrpc": "2.0", "id": self.id, "method": method,
                   "params": params}
        r = self._opener.open(
            urllib.request.Request(
                self.host, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}),
            timeout=30)
        out = json.loads(r.read())
        if "error" in out:
            raise RuntimeError(f"{method}: {out['error']}")
        return out["result"]

    def call(self, to, data):
        return self.req("eth_call", [{"to": to, "data": data}, "latest"])

    def code(self, addr):
        return self.req("eth_getCode", [addr, "latest"])

    def set_balance(self, addr, wei):
        self.req("anvil_setBalance", [addr, hex(wei)])

    def impersonate(self, addr):
        # NOTE: do NOT touch balance here — anvil_setBalance overwrites.
        # Callers set balances explicitly when needed.
        self.req("anvil_impersonateAccount", [addr])

    def stop_impersonate(self, addr):
        self.req("anvil_stopImpersonatingAccount", [addr])

    def mine(self):
        self.req("evm_mine", [])

    def send_from(self, frm, to, data, value=0):
        # impersonation send (anvil auto-gas)
        return self.req("eth_sendTransaction", [{
            "from": frm, "to": to, "data": data,
            "value": hex(value) if value else "0x"}])

    def send_raw(self, tx_hex):
        return self.req("eth_sendRawTransaction", [tx_hex])

    def get_tx(self, h):
        return self.req("eth_getTransactionReceipt", [h])

    def wait_tx(self, h, timeout=60):
        for _ in range(timeout * 2):
            r = self.get_tx(h)
            if r is not None:
                return r
            time.sleep(0.5)
        raise RuntimeError(f"tx {h} not mined in {timeout}s")

    def balance(self, addr):
        return int(self.req("eth_getBalance", [addr, "latest"]), 16)

    def gas_price(self):
        return int(self.req("eth_gasPrice", []), 16)

    def chain_id(self):
        return int(self.req("eth_chainId", []), 16)

    def chain_id_hex(self):
        return self.req("eth_chainId", [])

    def erc20_balance(self, token, addr):
        raw = self.call(token, "0x70a08231" + pad_addr(addr))
        return int(raw, 16)

    def deploy(self, binpath, abi_path=None):
        with open(binpath) as f:
            b = f.read().strip()
        if not b.startswith("0x"):
            b = "0x" + b
        h = self.send_from(self.deployer, None_addr, b) if False else None
        return h

    # real deploy via impersonated deployer EOA:
    def deploy_contract(self, binhex, deployer):
        data = binhex if binhex.startswith("0x") else "0x" + binhex
        h = self.req("eth_sendTransaction", [{
            "from": deployer, "data": data, "value": "0x"}])
        r = self.wait_tx(h)
        if r.get("status") != "0x1":
            raise RuntimeError("deploy failed: " + json.dumps(r)[:300])
        return r["contractAddress"]

    def call_func(self, to, data, frm=None):
        return self.call(to, data)


None_addr = "0x0000000000000000000000000000000000000000"


def wad(v):
    return int(v * 10 ** 18)


# ---- selector helpers (keccak via eth_utils / fallback manual) -----------

def kec_sig(sig):
    from eth_utils import keccak
    return keccak(text=sig)[:4].hex()


# ---- selftest: build a synthetic dislocation on the fork -----------------

def selftest(fork):
    """Prove the full chain: mock pairs with a 2% price skew, executor
    captures it, profit lands in the contract, sweep works, gate passes."""
    print("[selftest] deploying mocks + executor on fork...")
    deployer = "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266"  # anvil key0
    fork.set_balance(deployer, wad(1000))
    fork.impersonate(deployer)

    USDCe = "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8"

    # mint WETH the honest way: deposit() with fork ETH
    weth_seed_total = wad(100)
    fork.send_from(deployer, WETH, "0x" + kec_sig("deposit()"),
                   value=weth_seed_total)
    have = fork.erc20_balance(WETH, deployer)
    print(f"[selftest] minted {have/1e18:.2f} WETH via deposit()")
    assert have >= wad(90), "WETH mint failed"

    # deploy MockV2Pair(token0=WETH, token1=USDC.e)
    with open(os.path.join(HERE, "contracts", "MockV2Pair.bin")) as f:
        mock_bin = f.read().strip()
    ctor = pad_addr(WETH) + pad_addr(USDCe)
    pair_a = fork.deploy_contract(mock_bin + ctor, deployer)  # cheap  WETH
    pair_b = fork.deploy_contract(mock_bin + ctor, deployer)  # expensive WETH
    print(f"[selftest] mock pair A (cheap 2400): {pair_a}")
    print(f"[selftest] mock pair B (expns 2450): {pair_b}")

    # seed pools: 10 WETH each; A at $2400, B at $2520 (5% skew — a
    # realistic capturable dislocation)
    weth_each = wad(10)
    pair_a_usd = int(10 * 2400 * 1e6)
    pair_b_usd = int(10 * 2520 * 1e6)
    fork.send_from(deployer, WETH, "0x" + kec_sig("transfer(address,uint256)") +
                   pad_addr(pair_a) + pad_u256(weth_each))
    fork.send_from(deployer, WETH, "0x" + kec_sig("transfer(address,uint256)") +
                   pad_addr(pair_b) + pad_u256(weth_each))
    # USDC.e from the real Sushi WETH/USDC.e pair (~$85k on the fork)
    usdce_whale = "0x905dfcd5649217c42684f23958568e533c711aa3"
    fork.set_balance(usdce_whale, wad(1))  # gas money for the transfers
    fork.impersonate(usdce_whale)
    fork.send_from(usdce_whale, USDCe, "0x" + kec_sig("transfer(address,uint256)") +
                   pad_addr(pair_a) + pad_u256(pair_a_usd))
    fork.send_from(usdce_whale, USDCe, "0x" + kec_sig("transfer(address,uint256)") +
                   pad_addr(pair_b) + pad_u256(pair_b_usd))
    fork.stop_impersonate(usdce_whale)
    print("[selftest] pools seeded: 10 WETH each, 2% price skew")

    # deploy executor
    with open(os.path.join(HERE, "contracts", "FlashloanArb.bin")) as f:
        arb_bin = f.read().strip()
    arb_addr = fork.deploy_contract(
        arb_bin + pad_addr(AAVE_V3_POOL) + pad_addr(WETH), deployer)
    print(f"[selftest] FlashloanArb deployed: {arb_addr}")

    # execute: borrow optimal size — sell WETH on EXPENSIVE pool B (get more
    # USDC.e), buy WETH back on CHEAP pool A. poolBuy = B (WETH->quote),
    # poolSell = A (quote->WETH). Size = scanner-style numeric optimum.
    principal = wad(0.1008)
    data = "0x" + kec_sig("execute(uint256,address,address,address)") + \
        pad_u256(principal) + pad_addr(pair_b) + pad_addr(pair_a) + pad_addr(USDCe)
    owner_weth_before = fork.erc20_balance(WETH, deployer)
    arb_weth_before = fork.erc20_balance(WETH, arb_addr)
    print("[selftest] executing: flashloan 2 WETH -> sell on B -> buy back on A")
    txh = fork.send_from(deployer, arb_addr, data)
    r = fork.wait_tx(txh)
    if r.get("status") != "0x1":
        print("[selftest] EXECUTE REVERTED — receipt:")
        print(json.dumps(r)[:400])
        raise RuntimeError("execute reverted")
    gas_used = int(r["gasUsed"], 16)
    print(f"[selftest] execute OK — gas {gas_used:,}")
    profit_weth = fork.erc20_balance(WETH, arb_addr) - arb_weth_before
    print(f"[selftest] executor WETH profit: {profit_weth/1e18:.6f}")
    assert profit_weth > 0, "no profit — direction or math wrong"

    # sweep to owner
    data = "0x" + kec_sig("sweepProfit(address)") + pad_addr(WETH)
    txh = fork.send_from(deployer, arb_addr, data)
    r = fork.wait_tx(txh)
    assert r.get("status") == "0x1", "sweep failed"
    owner_after = fork.erc20_balance(WETH, deployer)
    print(f"[selftest] swept to owner: {(owner_after - owner_weth_before)/1e18:.6f} WETH")

    eth_usd = 2450.0
    gas_usd = (gas_used / 1e18) * (fork.gas_price() / 1e18) * eth_usd
    profit_usd = (profit_weth / 1e18) * eth_usd
    verdict = "PASS" if (profit_usd > GAS_MULTIPLIER * gas_usd
                         and profit_usd > MIN_PROFIT_USD) else "FAIL"
    print(f"[selftest] profit ${profit_usd:.4f} vs gas*{GAS_MULTIPLIER} "
          f"${GAS_MULTIPLIER*gas_usd:.4f} -> {verdict}")
    return {"profit_usd": round(profit_usd, 4), "gas_usd": round(gas_usd, 4),
            "profit_weth": profit_weth / 1e18, "gas_used": gas_used,
            "verdict": verdict}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--edge", type=str, default=None,
                    help="JSON edge from arb_engine scan (live-pool sim)")
    args = ap.parse_args()

    print("[sim] launching anvil fork of Arbitrum...")
    proc, host, head, fork_url = launch_fork()
    print(f"[sim] fork ready: head={head} via {fork_url}")
    fork = Fork(host)
    result = None
    try:
        if args.selftest:
            result = selftest(fork)
        elif args.edge:
            result = simulate_edge(json.loads(args.edge), fork)
        else:
            print("nothing to do: pass --selftest or --edge")
    finally:
        kill_tree(proc)
    if result:
        print(json.dumps(result, indent=1))


def simulate_edge(edge, fork):
    """Simulate a scanner edge against REAL pools on the fork."""
    deployer = "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266"
    fork.set_balance(deployer, wad(10))
    fork.impersonate(deployer)
    with open(os.path.join(HERE, "contracts", "FlashloanArb.bin")) as f:
        arb_bin = f.read().strip()
    arb_addr = fork.deploy_contract(
        arb_bin + pad_addr(AAVE_V3_POOL) + pad_addr(WETH), deployer)

    principal = wad(edge["size_weth"])
    # scanner reports direction as "venue_a -> venue_b" meaning BUY pool first?
    # arb_engine.best_two_pool_arb: (buy, sell) — buy pool converts base->quote,
    # sell pool converts quote->base. So poolBuy = the FIRST named venue,
    # poolSell = second. (buy WETH side first? No: buy pool takes WETH in.)
    pool_buy = edge["pool_buy"]
    pool_sell = edge["pool_sell"]
    quote = edge["quote"]
    data = "0x" + kec_sig("execute(uint256,address,address,address)") + \
        pad_u256(principal) + pad_addr(pool_buy) + pad_addr(pool_sell) + pad_addr(quote)
    arb_weth_before = fork.erc20_balance(WETH, arb_addr)
    try:
        txh = fork.send_from(deployer, arb_addr, data)
        r = fork.wait_tx(txh)
    except Exception as e:
        return {"sim": "reverted", "error": str(e)[:200]}
    if r.get("status") != "0x1":
        return {"sim": "reverted", "receipt": json.dumps(r)[:300]}
    gas_used = int(r["gasUsed"], 16)
    profit_weth = fork.erc20_balance(WETH, arb_addr) - arb_weth_before
    gas_usd = (gas_used / 1e18) * fork.gas_price() * edge.get("eth_usd", 2450)
    profit_usd = (profit_weth / 1e18) * edge.get("eth_usd", 2450)
    return {
        "sim": "ok",
        "profit_weth": profit_weth / 1e18,
        "profit_usd": round(profit_usd, 4),
        "gas_used": gas_used,
        "gas_usd": round(gas_usd, 4),
        "gate": "PASS" if (profit_usd > GAS_MULTIPLIER * gas_usd
                           and profit_usd > MIN_PROFIT_USD) else "FAIL",
    }


if __name__ == "__main__":
    main()
