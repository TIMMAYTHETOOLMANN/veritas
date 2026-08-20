#!/usr/bin/env python3
"""Inspect REAL PUSH4 selectors in tornado pool cores (proper disassembly)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.rpc import RPC
from core.selectors import selectors_map

def real_push4(code):
    out = []
    i = 0
    n = len(code)
    while i < n:
        op = code[i]
        if 0x60 <= op <= 0x7f:  # PUSH1..PUSH32
            sz = op - 0x5f
            val = code[i+1:i+1+sz].hex()
            if sz == 4:
                out.append(val)
            i += sz + 1
        else:
            i += 1
    return out

rpc = RPC('https://ethereum-rpc.publicnode.com', timeout=30, retries=3)
sm = selectors_map()
wanted = {v: k for k, v in sm.items()}
pools = [
    '0x47ce0c6ed5b0ce3d3a51fdb1c52dc66a7c3c2936',
    '0x12d66f87a04a9e220743712ce6d9bb1b5616b8fc',
    '0x0836222f2b2b24a3f36f98668ed8f0b38d1a872f',
    '0x07687e702b410fa43f4cb4af7fa097918ffd2730',
    '0x23773e65ed146a459791799d01336db287f25334',
    '0x83584f83f26af4edda9cbe8c730bc87c364b28fe',
]
for a in pools:
    code = bytes.fromhex(rpc.get_code(a)[2:])
    pushes = real_push4(code)
    present = set(pushes)
    matches = {wanted[p] for p in present if p in wanted}
    print(f'{a}  size={len(code)}')
    print('   matches in SIGS:', sorted(matches) or 'NONE')
    print('   std withdraw f2b8180e:', 'f2b8180e' in present)
    print('   std verify   f5c9d69e:', 'f5c9d69e' in present)
    print('   total literal PUSH4:', len(pushes))
    print()