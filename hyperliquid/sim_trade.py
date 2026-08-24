# sim_trade.py — Layer 2: anvil-fork execution gate for the two-pool arb path
#
# What it does (ALL on a local anvil fork of Base — $0, no real funds):
#   1. Fork Base at the current head block (pinned, reproducible).
#   2. Discover the UniV2 WETH/USDC pair and Aero vAMM WETH/USDC pool (factories).
#   3. anvil_setBalance a fresh SIM wallet with ETH for gas; impersonate a
#      WETH-rich pool and transfer test WETH to the SIM wallet.
#   4. Execute the real trade sequence the executor contract will encode:
#        leg 1: WETH -> USDC  on the UniV2 pair (pre-transfer + pair.swap)
#        leg 2: USDC -> WETH  on the Aero vAMM pool (pre-transfer + pool.swap)
#   5. Measure PnL from BALANCES (ground truth — includes every fee, curve
#      impact, and any donation), report gas per leg.
#
# This validates calldata encoding, ordering, and sequence — the machinery
# the executor contract will use — before a single cent is spent on-chain.
#
# Usage: python sim_trade.py [--fork-url https://base.drpc.org] [--size-weth 0.5]
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.rpc import RPC, uint
from core.selectors import kec256

# ---- verified Base addresses -------------------------------------------
WETH = "0x4200000000000000000000000000000000000006"
USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
UNIV2_FACTORY = "0x8909Dc15e40173Ff4699343b6eB8132c65e18eC6"
AERO_FACTORY = "0x420DD381b31aEf6683db6B902084cB0FFECe40Da"

SIM_WALLET = "0x0000000000000000000000000000000000000BEE"  # throwaway, fork-only

# ---- selectors ----------------------------------------------------------
SEL = {
    "getPair":   "0xe6a43905",
    "getPool":   "0x1698ee82",
    "reserves":  "0x0902f1ac",
    "token0":    "0x0dfe1681",
    "token1":    "0xd21220a7",
    "transfer":  "0xa9059cbb",   # transfer(address,uint256)
    "swap":      "0x022c0d9f",   # swap(uint256,uint256,address,bytes)
    "balanceOf": "0x70a08231",
    "decimals":  "0x313ce567",
}


def pad_addr(a):
    return a.lower().replace("0x", "").rjust(64, "0")


def pad_u256(v):
    return f"{int(v):064x}"


def parse_addr(res):
    if not res or res == "0x" or len(res) < 42:
        return None
    tail = res[2:][-40:]
    if set(tail) == {"0"}:
        return None
    return "0x" + tail


# ---- anvil lifecycle (rehearsal.py pattern) -----------------------------

def find_anvil(explicit=None):
    here = os.path.dirname(os.path.abspath(__file__))
    for c in (explicit,
              os.path.join(here, "tools", "anvil.exe"),
              os.path.join(os.path.expanduser("~"), ".foundry", "anvil.exe")):
        if c and os.path.isfile(c):
            return c
    import shutil
    return shutil.which("anvil")


def free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def kill_tree(proc):
    import subprocess
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


def launch_fork(anvil, fork_url, pinned):
    import subprocess
    port = free_port()
    cmd = [anvil, "--port", str(port), "--fork-url", fork_url]
    if pinned:
        cmd += ["--fork-block-number", str(pinned)]
    print(f"[sim] launching: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, creationflags=getattr(
            subprocess, "CREATE_NO_WINDOW", 0))
    host = f"http://127.0.0.1:{port}"
    rpc = RPC(host, timeout=5, retries=1)
    deadline = time.time() + 150
    while time.time() < deadline:
        try:
            rpc.call("eth_blockNumber", [])
            return proc, host
        except Exception:
            time.sleep(1.5)
    kill_tree(proc)
    return None, None


# ---- fork helpers --------------------------------------------------------

class Fork:
    def __init__(self, host):
        self.rpc = RPC(host, timeout=30, retries=2)

    def call(self, method, params):
        return self.rpc.call(method, params)

    def eth_call(self, to, data):
        return self.rpc.eth_call(to, data)

    def impersonate(self, addr):
        self.call("anvil_impersonateAccount", [addr])

    def set_balance(self, addr, wei):
        self.call("anvil_setBalance", [addr, hex(wei)])

    def send(self, frm, to, data, gas=600_000):
        return self.call("eth_sendTransaction", [{
            "from": frm, "to": to, "data": data, "gas": hex(gas),
        }])

    def tx_ok(self, txh, wait_s=15):
        """Poll for the receipt (anvil auto-mines, but the first tx on a
        pinned fork can lag while state is lazily fetched upstream)."""
        deadline = time.time() + wait_s
        r = None
        while time.time() < deadline:
            try:
                r = self.call("eth_getTransactionReceipt", [txh])
            except Exception:
                r = None
            if r is not None:
                break
            time.sleep(0.4)
        return r is not None and r.get("status") == "0x1", r

    def balance_of(self, token, who):
        raw = self.eth_call(token, SEL["balanceOf"] + pad_addr(who))
        return int(raw, 16)

    def pool_info(self, addr):
        t0 = parse_addr(self.eth_call(addr, SEL["token0"]))
        t1 = parse_addr(self.eth_call(addr, SEL["token1"]))
        raw = self.eth_call(addr, SEL["reserves"])
        h = raw[2:]
        return t0, t1, int(h[0:64], 16), int(h[64:128], 16)


def cp_out_int(r_in, r_out, amount_in, fee_num=997):
    """Integer constant-product amountOut (UniswapV2 0.3%)."""
    if amount_in <= 0:
        return 0
    return (r_out * amount_in * fee_num) // (r_in * 1000 + amount_in * fee_num)


def encode_transfer(to_addr, amount):
    return SEL["transfer"] + pad_addr(to_addr) + pad_u256(amount)


def encode_swap(out0, out1, to_addr):
    # swap(uint256 amount0Out, uint256 amount1Out, address to, bytes data)
    # bytes data = empty dynamic: offset 0x80, length 0
    return (SEL["swap"] + pad_u256(out0) + pad_u256(out1)
            + pad_addr(to_addr) + pad_u256(0x80) + pad_u256(0))


def leg(fork, pool, token_in, token_out, amount_in, sim, label,
        min_out_fracs=(0.995, 0.99, 0.98, 0.97, 0.95)):
    """Execute one swap leg on `pool` by pre-transferring token_in and calling
    swap() with a graduated (conservative) requested output. Returns
    (amount_out_raw, gas_used, requested_frac) or raises on total failure."""
    t0, t1, r0, r1 = fork.pool_info(pool)
    if token_in == t0:
        r_in, r_out, in_is_t0 = r0, r1, True
    elif token_in == t1:
        r_in, r_out, in_is_t0 = r1, r0, False
    else:
        raise RuntimeError(f"{label}: token not in pool")

    est = cp_out_int(r_in, r_out, amount_in)
    if est <= 0:
        raise RuntimeError(f"{label}: estimated output is zero")

    # 1. pre-transfer exact input into the pool (from the SIM wallet)
    tx = fork.send(sim, token_in, encode_transfer(pool, amount_in))
    ok, _ = fork.tx_ok(tx)
    if not ok:
        raise RuntimeError(f"{label}: pre-transfer failed")

    # 2. swap() with graduated conservative outputs (excess input donates to
    #    LPs — invariant-safe on both UniV2 and Solidly curves)
    gas_used = 0
    for frac in min_out_fracs:
        want = est * frac
        want = int(want) if want < est else est - 1
        out0 = want if in_is_t0 is False else 0   # out = the OTHER token
        out1 = want if in_is_t0 is True else 0
        # if token_in is t0, we receive t1 out, and vice versa
        out0 = 0 if in_is_t0 else want
        out1 = want if in_is_t0 else 0
        tx = fork.send(sim, pool, encode_swap(out0, out1, sim))
        ok, rec = fork.tx_ok(tx)
        if ok:
            gas_used = int(rec.get("gasUsed", "0x0"), 16)
            # measure what actually arrived: balance delta of token_out
            bal = fork.balance_of(token_out, sim)
            return bal, gas_used, frac
    raise RuntimeError(f"{label}: swap reverted at all conservative outputs")


def main():
    ap = argparse.ArgumentParser(description="anvil-fork arb execution gate")
    ap.add_argument("--fork-url", default="https://base.drpc.org")
    ap.add_argument("--fallback-url", default="https://mainnet.base.org")
    ap.add_argument("--size-weth", type=float, default=0.5)
    ap.add_argument("--anvil-path", default=None)
    args = ap.parse_args()

    anvil = find_anvil(args.anvil_path)
    if not anvil:
        print("[sim] anvil.exe not found (tools/anvil.exe or ~/.foundry)")
        return 1

    # ---- discover pools upstream (factories), then fork -----------------
    up = RPC(args.fork_url, timeout=30, retries=3)
    head = uint(up.call("eth_blockNumber", []))
    print(f"[sim] upstream head={head}")

    univ2 = parse_addr(up.eth_call(
        UNIV2_FACTORY, SEL["getPair"] + pad_addr(WETH) + pad_addr(USDC)))
    aero = parse_addr(up.eth_call(
        AERO_FACTORY, SEL["getPool"] + pad_addr(WETH) + pad_addr(USDC)
        + pad_u256(0)))
    if not univ2 or not aero:
        print(f"[sim] pool discovery failed: univ2={univ2} aero={aero}")
        return 2
    print(f"[sim] UniV2 pair={univ2}")
    print(f"[sim] Aero vAMM={aero}")

    proc, host = launch_fork(anvil, args.fork_url, head)
    if proc is None:
        print("[sim] primary fork failed; trying fallback URL")
        proc, host = launch_fork(anvil, args.fallback_url, head)
    if proc is None:
        print("[sim] anvil never became ready")
        return 3

    try:
        f = Fork(host)
        fhead = uint(f.call("eth_blockNumber", []))
        print(f"[sim] fork ready at {host} head={fhead} (pinned {head})")

        # ---- fund the SIM wallet (fork-only tricks) --------------------
        f.set_balance(SIM_WALLET, 10 * 10**18)          # ETH for gas
        f.impersonate(SIM_WALLET)
        # Fund the SIM wallet with WETH by impersonating the Aero pool —
        # WETH-rich but the pool itself holds no ETH, so give it gas money
        # for the impersonated transfer (anvil does not auto-fund).
        f.impersonate(aero)
        f.set_balance(aero, 10 * 10**18)
        tx = f.send(aero, WETH, encode_transfer(SIM_WALLET, 5 * 10**18))
        ok, _ = f.tx_ok(tx)
        if not ok:
            print("[sim] WETH funding transfer failed")
            return 4
        f.call("anvil_stopImpersonatingAccount", [aero])
        print(f"[sim] SIM wallet funded: 5 WETH + 10 ETH (fork-only)")

        size = int(args.size_weth * 1e18)
        weth_before = f.balance_of(WETH, SIM_WALLET)
        usdc_before = f.balance_of(USDC, SIM_WALLET)
        print(f"[sim] start: {weth_before/1e18:.6f} WETH, "
              f"{usdc_before/1e6:.2f} USDC")

        # ---- leg 1: WETH -> USDC on UniV2 -----------------------------
        out1, gas1, fr1 = leg(f, univ2, WETH, USDC, size, SIM_WALLET, "leg1")
        print(f"[sim] leg1 WETH->USDC on UniV2: got {out1/1e6:,.2f} USDC "
              f"(gas {gas1:,}, minOut frac {fr1})")

        # ---- leg 2: USDC -> WETH on Aero vAMM -------------------------
        out2, gas2, fr2 = leg(f, aero, USDC, WETH, out1, SIM_WALLET, "leg2")
        print(f"[sim] leg2 USDC->WETH on Aero: got {out2/1e18:.6f} WETH "
              f"(gas {gas2:,}, minOut frac {fr2})")

        # ---- PnL ------------------------------------------------------
        weth_after = f.balance_of(WETH, SIM_WALLET)
        usdc_after = f.balance_of(USDC, SIM_WALLET)
        d_weth = (weth_after - weth_before) / 1e18
        d_usdc = (usdc_after - usdc_before) / 1e6
        gas_total = gas1 + gas2
        print("\n[sim] ===== EXECUTION GATE RESULT =====")
        print(f"[sim] WETH delta : {d_weth:+.6f}")
        print(f"[sim] USDC delta : {d_usdc:+,.2f}")
        print(f"[sim] round-trip on {args.size_weth} WETH: "
              f"{(d_weth/args.size_weth)*100:+.4f}% "
              f"(includes all fees + any minOut donation)")
        print(f"[sim] gas used   : {gas_total:,} (fork)")
        verdict = "PASS — sequence executes cleanly" if (out1 > 0 and out2 > 0) else "FAIL"
        print(f"[sim] verdict    : {verdict}")
        print("[sim] note: negative round-trip on majors is EXPECTED — the")
        print("[sim] fee wall (2x30bps) exceeds the 2-3bps dislocation; this")
        print("[sim] run validates the execution machinery, not the edge.")
        return 0
    finally:
        kill_tree(proc)
        print(f"[sim] anvil terminated (pid {proc.pid})")


if __name__ == "__main__":
    sys.exit(main())
