# _probe_leg2.py — diagnose why Aero vAMM swap() reverts after USDC pre-transfer.
# Fork Base, fund SIM via UniV2 leg1 exactly as sim_trade.py does, then call
# Aero pool swap() with graduated outputs and capture the revert reason.
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.rpc import RPC, uint

WETH = "0x4200000000000000000000000000000000000006"
USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
UNIV2 = "0x88a43bbdf9d098eec7bceda4e2494615dfd9bb9c"
AERO = "0xcdac0d6c6c59727a65f871236188350531885c43"
SIM = "0x0000000000000000000000000000000000000BEE"
PORT = 56500


def b32(a):
    return a.replace("0x", "").rjust(64, "0")


def u256(v):
    return f"{int(v):064x}"


def main():
    proc = subprocess.Popen(
        [os.path.expanduser("~/.foundry/anvil.exe"), "--port", str(PORT),
         "--fork-url", "https://mainnet.base.org"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, creationflags=0x08000000)
    rpc = RPC(f"http://127.0.0.1:{PORT}", timeout=15, retries=1)
    for _ in range(80):
        try:
            rpc.call("eth_blockNumber", [])
            break
        except Exception:
            time.sleep(1.5)
    print("fork ready")

    try:
        rpc.call("anvil_setBalance", [SIM, hex(10 * 10**18)])
        rpc.call("anvil_impersonateAccount", [SIM])
        # fund 1 WETH from a rich pool
        rpc.call("anvil_impersonateAccount", [AERO])
        rpc.call("anvil_setBalance", [AERO, hex(10 * 10**18)])
        tx = rpc.call("eth_sendTransaction", [{
            "from": AERO, "to": WETH, "gas": hex(100000),
            "data": "0xa9059cbb" + b32(SIM) + u256(10**18)}])
        for _ in range(30):
            if rpc.call("eth_getTransactionReceipt", [tx]) is not None:
                break
            time.sleep(0.5)
        rpc.call("anvil_stopImpersonatingAccount", [AERO])
        print("funded SIM with 1 WETH")

        # ---- leg 1: WETH -> USDC on UniV2 (works) --------------------
        t0 = rpc.eth_call(UNIV2, "0x0dfe1681")
        is_t0_weth = t0[-40:].lower() == WETH[2:].lower()
        h = rpc.eth_call(UNIV2, "0x0902f1ac")[2:]
        r0, r1 = int(h[:64], 16), int(h[64:128], 16)
        r_in = r0 if is_t0_weth else r1
        r_out = r1 if is_t0_weth else r0
        ain = 10**18
        est = r_out * ain * 997 // (r_in * 1000 + ain * 997)

        tx = rpc.call("eth_sendTransaction", [{
            "from": SIM, "to": WETH, "gas": hex(100000),
            "data": "0xa9059cbb" + b32(UNIV2) + u256(ain)}])
        for _ in range(30):
            if rpc.call("eth_getTransactionReceipt", [tx]) is not None:
                break
            time.sleep(0.5)
        want1 = int(est * 0.995)
        out0 = 0 if is_t0_weth else want1
        out1 = want1 if is_t0_weth else 0
        tx = rpc.call("eth_sendTransaction", [{
            "from": SIM, "to": UNIV2, "gas": hex(300000),
            "data": "0x022c0d9f" + u256(out0) + u256(out1) + b32(SIM)
            + u256(0x80) + u256(0)}])
        for _ in range(30):
            if rpc.call("eth_getTransactionReceipt", [tx]) is not None:
                break
            time.sleep(0.5)
        usdc = int(rpc.eth_call(USDC, "0x70a08231" + b32(SIM)), 16)
        print(f"leg1 done: SIM holds {usdc/1e6:,.2f} USDC")

        # ---- leg 2 diagnosis: Aero pool token order + swap revert ------
        a_t0 = rpc.eth_call(AERO, "0x0dfe1681")
        a_weth_is_t0 = a_t0[-40:].lower() == WETH[2:].lower()
        print(f"Aero token0 is WETH: {a_weth_is_t0}")
        h = rpc.eth_call(AERO, "0x0902f1ac")[2:]
        ar0, ar1 = int(h[:64], 16), int(h[64:128], 16)
        print(f"Aero reserves: r0={ar0/1e18:,.2f} r1={ar0 and ar1/1e6:,.2f}"
              .replace("r1=", "r1="))

        # pre-transfer USDC to Aero pool
        tx = rpc.call("eth_sendTransaction", [{
            "from": SIM, "to": USDC, "gas": hex(100000),
            "data": "0xa9059cbb" + b32(AERO) + u256(usdc)}])
        for _ in range(30):
            if rpc.call("eth_getTransactionReceipt", [tx]) is not None:
                break
            time.sleep(0.5)
        print("USDC pre-transferred to Aero pool")

        # try swap with graduated outputs, capture revert via debug_trace
        a_r_in = ar1 if a_weth_is_t0 else ar0   # USDC side
        a_r_out = ar0 if a_weth_is_t0 else ar1  # WETH side
        est2 = a_r_out * usdc * 997 // (a_r_in * 1000 + usdc * 997)
        print(f"estimated WETH out: {est2/1e18:.6f}")

        for frac in (0.995, 0.99, 0.98, 0.95):
            want = int(est2 * frac)
            out0 = 0 if a_weth_is_t0 else want    # WETH out if token1
            out1 = want if a_weth_is_t0 else 0
            tx = rpc.call("eth_sendTransaction", [{
                "from": SIM, "to": AERO, "gas": hex(400000),
                "data": "0x022c0d9f" + u256(out0) + u256(out1) + b32(SIM)
                + u256(0x80) + u256(0)}])
            rec = None
            for _ in range(30):
                rec = rpc.call("eth_getTransactionReceipt", [tx])
                if rec is not None:
                    break
                time.sleep(0.5)
            status = rec.get("status") if rec else None
            print(f"frac {frac}: status={status}")
            if status != "0x1":
                # revert reason: trace it
                try:
                    tr = rpc.call("debug_traceTransaction",
                                  [tx, {"tracer": "callTracer"}])
                    import json
                    s = json.dumps(tr)
                    # find revert reason string if present
                    idx = s.find("revert")
                    print("  trace head:", s[:400])
                except Exception as e:
                    print("  trace failed:", e)
                # also try eth_call for a clean revert string
                try:
                    out = rpc.call("eth_call", [{
                        "from": SIM, "to": AERO,
                        "data": "0x022c0d9f" + u256(out0) + u256(out1)
                        + b32(SIM) + u256(0x80) + u256(0)}, "latest"])
                    print("  eth_call (no state override) ok?!", out[:40])
                except Exception as e:
                    print("  eth_call revert:", str(e)[:200])
            else:
                w = int(rpc.eth_call(WETH, "0x70a08231" + b32(SIM)), 16)
                print(f"  SUCCESS frac={frac}: SIM WETH={w/1e18:.6f}")
                break

        return 0
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
