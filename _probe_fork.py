# _probe_fork.py — throwaway diagnostic: why does the impersonated WETH
# transfer fail on the fork? Prints pool balances + tx status + revert trace.
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.rpc import RPC

WETH = "0x4200000000000000000000000000000000000006"
SIM = "0x0000000000000000000000000000000000000BEE"
AERO_POOL = "0xcdac0d6c6c59727a65f871236188350531885c43"
PORT = 55444


def b32(a):
    return a.replace("0x", "").rjust(64, "0")


def main():
    proc = subprocess.Popen(
        [os.path.expanduser("~/.foundry/anvil.exe"), "--port", str(PORT),
         "--fork-url", "https://base.drpc.org",
         "--fork-block-number", "50291985"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, creationflags=0x08000000)
    host = f"http://127.0.0.1:{PORT}"
    rpc = RPC(host, timeout=10, retries=1)
    ready = False
    for _ in range(80):
        try:
            rpc.call("eth_blockNumber", [])
            ready = True
            break
        except Exception:
            time.sleep(1.5)
    if not ready:
        print("fork never became ready")
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
        return 1

    print("fork ready")
    pw = int(rpc.eth_call(WETH, "0x70a08231" + b32(AERO_POOL)), 16)
    pe = int(rpc.call("eth_getBalance", [AERO_POOL, "latest"]), 16)
    print(f"Aero pool WETH={pw/1e18:.4f} ETH={pe/1e18:.4f}")

    rpc.call("anvil_impersonateAccount", [AERO_POOL])
    rpc.call("anvil_setBalance", [AERO_POOL, hex(10 * 10**18)])
    try:
        txh = rpc.call("eth_sendTransaction", [{
            "from": AERO_POOL, "to": WETH, "gas": hex(100000),
            "data": "0xa9059cbb" + b32(SIM) + f"{5*10**18:064x}"}])
        print("tx:", txh)
        for _ in range(20):
            r = rpc.call("eth_getTransactionReceipt", [txh])
            if r is not None:
                break
            time.sleep(0.5)
        print("status:", r.get("status"), "gasUsed:", r.get("gasUsed"))
        if r.get("status") != "0x1":
            try:
                import json
                tr = rpc.call("debug_traceTransaction",
                              [txh, {"tracer": "callTracer"}])
                print("trace:", json.dumps(tr)[:800])
            except Exception as e2:
                print("trace failed:", e2)
    except Exception as e:
        print("send failed:", type(e).__name__, str(e)[:200])

    sb = int(rpc.eth_call(WETH, "0x70a08231" + b32(SIM)), 16)
    print(f"SIM WETH after: {sb/1e18:.4f}")
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                   capture_output=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
