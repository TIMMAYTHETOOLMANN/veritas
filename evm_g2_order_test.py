# evm_g2_order_test.py — empirically determine EVM G2 word order via eth_call to pairing precompile 0x08
# Known-good: e(G1,G2)*e(-G1,G2) == 1  -> precompile must return 0x...01
# Known-bad:  e(G1,G2)*e(G1,G2)  != 1  -> must return 0x...00
import sys
sys.setrecursionlimit(1_000_000)
sys.path.insert(0, r"C:\Users\timot\OneDrive\Documents\VERITAS")
import pyecc_patch
from py_ecc.bn128 import G1, G2, neg
from core.rpc import RPC

rpc = RPC("https://ethereum-rpc.publicnode.com")
PAIRING_PRECOMPILE = "0x0000000000000000000000000000000000000008"

def w(v): return f"{int(v):064x}"
def g1_words(p): return w(p[0]) + w(p[1])
def g2_words(p, imag_first):
    # p = (FQ2 x, FQ2 y); coeffs = [c0, c1] -> value = c0 + c1*u
    if imag_first:
        return w(p[0].coeffs[1]) + w(p[0].coeffs[0]) + w(p[1].coeffs[1]) + w(p[1].coeffs[0])
    return w(p[0].coeffs[0]) + w(p[0].coeffs[1]) + w(p[1].coeffs[0]) + w(p[1].coeffs[1])

def call_precompile(data_hex):
    return rpc.call("eth_call", [{"to": PAIRING_PRECOMPILE, "data": data_hex}, "latest"])

for imag_first in (True, False):
    tag = "IMAG-FIRST [c1,c0]" if imag_first else "REAL-FIRST [c0,c1]"
    good = g1_words(G1) + g2_words(G2, imag_first) + g1_words(neg(G1)) + g2_words(G2, imag_first)
    bad  = g1_words(G1) + g2_words(G2, imag_first) + g1_words(G1)       + g2_words(G2, imag_first)
    try:
        r_good = call_precompile("0x" + good)
        r_bad  = call_precompile("0x" + bad)
        print(f"{tag}: e*e-1 -> {r_good[:10]}...  e*e -> {r_bad[:10]}...")
    except Exception as e:
        print(f"{tag}: RPC/execution error: {e}")
