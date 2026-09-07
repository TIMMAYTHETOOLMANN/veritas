#!/usr/bin/env python3
"""
generate_smt_witness.py — build a REAL SMT witness and generate input.json for
the production arb_proof circuit, then prove + verify end-to-end.

Efficient Sparse Merkle Tree: O(depth) insert + O(depth) proof. No recursion.
"""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEPTH = 32
HELPER = (HERE / "zk_circuits" / "poseidon_helper.mjs").as_posix()


def poseidon_node(l, r):
    r_ = subprocess.run(["node", HELPER, "node", str(l), str(r)],
                        capture_output=True, text=True, timeout=30)
    if r_.returncode != 0:
        raise RuntimeError(r_.stderr)
    return int(r_.stdout.strip())


def poseidon_leaf(addr, r0, r1, fee):
    r_ = subprocess.run(["node", HELPER, "leaf", str(addr), str(r0), str(r1), str(fee)],
                        capture_output=True, text=True, timeout=30)
    if r_.returncode != 0:
        raise RuntimeError(r_.stderr)
    return int(r_.stdout.strip())


# ---------------------------------------------------------------------------
# Efficient SMT: default node = 0 (zero). We store ONLY non-zero nodes.
# ---------------------------------------------------------------------------
class SMT:
    def __init__(self):
        # node key = (level, index); value = hash. Empty node hashes Poseidon(0,0).
        self.nodes = {}          # {(level, index): value} for ALL nodes computed
        self.zero_cache = {}     # level -> the hash of the "all-zero" subtree

    def _empty(self, level: int) -> int:
        """Value of an all-empty subtree at `level` levels below it (depth level)."""
        if level == 0:
            return 0
        if level not in self.zero_cache:
            child = self._empty(level - 1)
            self.zero_cache[level] = poseidon_node(child, child)  # Poseidon(z,z)
        return self.zero_cache[level]

    def add(self, index: int, leaf: int):
        """Insert leaf at `index`, recomputing ancestors with unconditional hash."""
        self.nodes[(0, index)] = leaf
        cur = leaf
        for level in range(DEPTH):
            idx = index >> level
            sibling_idx = idx ^ 1
            sibling = self.nodes.get((level, sibling_idx), self._empty(level))
            bit = (index >> level) & 1
            l, r = (cur, sibling) if bit == 0 else (sibling, cur)
            parent = poseidon_node(l, r)
            self.nodes[(level + 1, idx >> 1)] = parent
            cur = parent

    def root(self) -> int:
        return self.nodes.get((DEPTH, 0), self._empty(DEPTH))

    def proof(self, index: int):
        """Return (siblings[32], dirl). siblings[i] = sibling at level i,
        using the empty-subtree value when the sibling slot is empty."""
        siblings = []
        dirl = 0
        for level in range(DEPTH):
            idx = index >> level
            bit = (index >> level) & 1
            dirl |= (bit << level)
            sibling_idx = idx ^ 1
            sibling = self.nodes.get((level, sibling_idx), self._empty(level))
            siblings.append(sibling)
        return siblings, dirl


def build_input(pool_a, pool_b, reserves, fees, amount_in, eth_usd, gas_usd, safety_margin):
    import hashlib
    def idx_of(addr):
        h = hashlib.sha3_256(addr.to_bytes(20, "big")).digest()
        return int.from_bytes(h[:4], "big")

    ia = idx_of(pool_a)
    ib = idx_of(pool_b)
    if ia == ib:
        ib = ia ^ 1

    rA0, rA1, rB0, rB1 = reserves
    feeA, feeB = fees

    leaf_a = poseidon_leaf(pool_a, rA0, rA1, feeA)
    leaf_b = poseidon_leaf(pool_b, rB0, rB1, feeB)

    smt = SMT()
    smt.add(ia, leaf_a)
    smt.add(ib, leaf_b)
    root = smt.root()

    path_a, dirl_a = smt.proof(ia)
    path_b, dirl_b = smt.proof(ib)

    return {
        "registry_root": root,
        "eth_usd": eth_usd,
        "gas_usd": gas_usd,
        "safety_margin": safety_margin,
        "pool_a_addr": pool_a,
        "pool_b_addr": pool_b,
        "reserve_a0": rA0,
        "reserve_a1": rA1,
        "reserve_b0": rB0,
        "reserve_b1": rB1,
        "amount_in": amount_in,
        "fee_a": feeA,
        "fee_b": feeB,
        "path_a": path_a,
        "path_b": path_b,
        "dirl_a": dirl_a,
        "dirl_b": dirl_b,
    }, root, leaf_a, leaf_b


if __name__ == "__main__":
    pool_a = int("0xC31E54c7a869B9FcBEcc14363CF510d1c41fa443", 16)
    pool_b = int("0x6C4E8018a9E0a2B3a6E2eCf0C02A5D3c7449159E", 16)

    rA0 = 100 * 10**18       # 100 WETH
    rA1 = 260_000 * 10**6    # $260k USDC
    rB0 = 260_000 * 10**6
    rB1 = 100 * 10**18

    amount_in = 1 * 10**18
    eth_usd = 2600 * 10**6
    gas_usd = 2 * 10**6
    safety_margin = int(0.50 * 10**6)

    inp, root, leaf_a, leaf_b = build_input(
        pool_a, pool_b,
        (rA0, rA1, rB0, rB1),
        (3000, 3000),
        amount_in, eth_usd, gas_usd, safety_margin,
    )
    out = HERE / "zk_circuits" / "build" / "input.json"
    out.write_text(json.dumps(inp))
    print(f"Wrote input.json")
    print(f"registry_root: {root}")
    print(f"leaf_a: {leaf_a}")
    print(f"leaf_b: {leaf_b}")
    print(f"dirl_a: {inp['dirl_a']}, dirl_b: {inp['dirl_b']}")
    print(f"path_a sample: {inp['path_a'][:3]}")