#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flash_hunter import get_rpc

def test_sushi():
    rpc, _ = get_rpc()
    print(f"RPC URL: {rpc.url}")
    SUSHI_WETH_USDC = "0x57b85fef094e10b5eecdf350af688299e9553378"
    data = "0x70a08231" + SUSHI_WETH_USDC[2:].lower().rjust(64, '0')
    print(f"Data: {data}")
    try:
        result = rpc.call("eth_call", [{
            "to": SUSHI_WETH_USDC,
            "data": data
        }, "latest"])
        print(f"Raw result: {result}")
        if result.startswith('0x'):
            result = result[2:]
        if len(result) == 0:
            print("Empty result")
            return
        # The result is a uint256 (price * 1e6? Actually the call returns uint112? Let's see)
        # The function is likely slot0? Actually 0x70a08231 is slot0() for Uniswap V3? Wait, for Sushi V2, getReserves is 0x0902f1ac.
        # The hunter uses a different method: they call with data "0x70a08231" + ... which is actually the function selector for "token0()"?
        # Let's check: In flash_hunter.py line 169-173, they have:
        #     SUSHI_WETH_USDC = "0x57b85fef094e10b5eecdf350af688299e9553378"
        #     "0x70a08231" + SUSHI_WETH_USDC[2:].lower().rjust(64, '0')
        #     "0x70a08231" + SUSHI_WETH_USDC[2:].lower().rjust(64, '0')
        # That looks like they are calling token0()? Actually 0x70a08231 is the selector for token0()? Let's compute: keccak256("token0()") = 0x70a08231? Yes, that's token0().
        # So they are calling token0() and token1()? Actually they have two lines, one for token0 and one for token1? They duplicated.
        # Then they compute eth_usd = usdc_res / weth_res if weth_res > 0 else 2450.0
        # But they are using the same call twice? Actually they have two identical lines? Let's look at the code again.
        # We'll just print the result.
        print(f"Result length: {len(result)}")
        # Try to decode as uint256
        val = int(result, 16)
        print(f"Value as uint256: {val}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_sushi()