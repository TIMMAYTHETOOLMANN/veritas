#!/usr/bin/env python3
"""
generate_smt_witness.py — build a REAL SMT witness and generate input.json for
the production arb_proof circuit, then prove + verify end-to-end.

Steps:
  1. Build a depth-32 Sparse Merkle Tree over {pool_a, pool_b} (+ optional filler)
  2. Compute registry_root and each pool's 32-sibling path + dirl bits
  3. Emit input.json matching the circuit's private inputs
  4. (caller then runs snarkjs witness + prove + verify)
"""
from __future__ import annotations
import json
import subprocess
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


class SMT:
    """Depth-32 SMT. Sparse: only two leaves populated, everything else = 0.

    Uses the standard SMT convention where empty leaves/subtrees hash to 0
    (zero node). The circuit's SmtMembership also treats the sibling as an
    input and hashes Poseidon(path_node, sibling) at each level based on dirl.
    """
    def __init__(self):
        self.leaves = {}   # index -> leaf

    def add(self, index, leaf):
        self.leaves[index] = leaf

    def root(self):
        # compute root bottom-up
        nodes = dict(self.leaves)
        # iterate 32 levels
        for level in range(DEPTH):
            parents = {}
            for idx, val in nodes.items():
                p = idx >> 1
                if p not in parents:
                    parents[p] = [0, 0]
                parents[p][idx & 1] = val
            nodes = {}
            for p, (l, r) in parents.items():
                if l == 0 and r == 0:
                    val = 0
                else:
                    val = poseidon_node(l, r)
                nodes[p] = val
        return nodes.get(0, 0)

    def proof(self, index):
        """Return (siblings[32], dirl) for leaf at `index`.

        siblings[i] = sibling node at level i; dirl = 32-bit little-endian of index.
        """
        # We need the sibling at each level. Build full path bottom-up with all
        # leaves, capturing the sibling of the path node at each level.
        siblings = []
        dirl = 0
        # Track the current path node value
        path_node = self.leaves.get(index, 0)

        # Build level dictionaries from leaves upward, remembering each node
        level_nodes = [dict(self.leaves)]  # level 0 = leaves
        nodes = dict(self.leaves)
        for _ in range(DEPTH):
            parents = {}
            for idx, val in nodes.items():
                p = idx >> 1
                if p not in parents:
                    parents[p] = [0, 0]
                parents[p][idx & 1] = val
            nodes = {}
            for p, (l, r) in parents.items():
                nodes[p] = poseidon_node(l, r) if (l or r) else 0
            level_nodes.append(nodes)

        # Now extract sibling at each level: sibling of path index
        for i in range(DEPTH):
            idx = index >> i
            bit = (index >> i) & 1
            dirl |= (bit << i)
            sibling_idx = idx ^ 1
            # sibling value at this level
            # level_nodes[i] has nodes at path position idx>>i ... but that's
            # not directly the sibling. We recompute: go down from root would need
            # full tree. Instead recompute sibling subtree root.
            sibling_val = self._subtree_root(sibling_idx, i)
            siblings.append(sibling_val)
        return siblings, dirl

    def _subtree_root(self, idx, remaining_levels):
        """Root of subtree at `idx` with `remaining_levels` levels beneath it."""
        if remaining_levels == 0:
            return self.leaves.get(idx, 0)
        l = self._subtree_root(idx << 1, remaining_levels - 1)
        r = self._subtree_root((idx << 1) | 1, remaining_levels - 1)
        if l == 0 and r == 0:
            return 0
        return poseidon_node(l, r)


def build_input(pool_a, pool_b, reserves, fees, amount_in, eth_usd, gas_usd, safety_margin):
    """Construct the full circuit input.json dict."""
    # Deterministic SMT positions (keccak of address, truncated)
    import hashlib
    def idx_of(addr):
        h = hashlib.sha3_256(addr.to_bytes(20, "big")).digest()
        return int.from_bytes(h[:4], "big")

    ia = idx_of(pool_a)
    ib = idx_of(pool_b)
    if ia == ib:
        # avoid collision by forcing distinct indices
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
    # Realistic Arbitrum WETH/USDC and USDC/WETH pool pair
    pool_a = int("0xC31E54c7a869B9FcBEcc14363CF510d1c41fa443", 16)  # example WETH/USDC
    pool_b = int("0x6C4E8018a9E0a2B3a6E2eCf0C02A5D3c7449159E", 16)   # example USDC/WETH

    # WETH ~ $2600, USDC 6 decimals. Use 1e18 for WETH, 1e6 for USDC.
    # Pool A: WETH/USDC (reserve0=WETH, reserve1=USDC)
    rA0 = 100 * 10**18       # 100 WETH
    rA1 = 260_000 * 10**6    # $260k USDC
    # Pool B: USDC/WETH (reserve0=USDC, reserve1=WETH) — but circuit treats
    # reserve0 as WETH-side and reserve1 as quote. Keep consistent: reserve0=WETH.
    rB0 = 260_000 * 10**6
    rB1 = 100 * 10**18

    # amount_in = 1 WETH (1e18), fee 30 bps (=3000 in bps*100)
    amount_in = 1 * 10**18
    eth_usd = 2600 * 10**6
    gas_usd = 2 * 10**6
    safety_margin = 0.50 * 10**6  # $0.50

    inp, root, leaf_a, leaf_b = build_input(
        pool_a, pool_b,
        (rA0, rA1, rB0, rB1),
        (3000, 3000),
        amount_in, eth_usd, gas_usd, safety_margin,
    )
    out = HERE / "zk_circuits" / "build" / "input.json"
    out.write_text(json.dumps(inp))
    print(f"Wrote input.json -> {out}")
    print(f"registry_root: {root}")
    print(f"leaf_a: {leaf_a}")
    print(f"leaf_b: {leaf_b}")
    print(f"path_a[0..3]: {inp['path_a'][:4]}")
    print(f"dirl_a: {inp['dirl_a']}, dirl_b: {inp['dirl_b']}")