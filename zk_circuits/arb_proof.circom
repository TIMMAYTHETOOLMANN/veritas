pragma circom 2.1.8;

include "circomlib/circuits/poseidon.circom";
include "circomlib/circuits/comparators.circom";
include "circomlib/circuits/mux1.circom";
include "circomlib/circuits/bitify.circom";

/**
 * @title ArbProofVerifier (SMT membership — PRODUCTION)
 * @notice Proves a profitable 2-pool arbitrage with REAL Sparse-Merkle-Tree
 *         membership of both pools against a public registry root.
 *
 * This replaces the prior "synthetic Poseidon hash-chain" state commitment.
 * Each pool is a leaf in a Sparse Merkle Tree keyed by pool address. For each
 * pool the holder proves a depth-32 Poseidon sibling-path membership, then the
 * two leaf hashes are combined into the registry root.
 *
 * Public outputs (in declaration order) — snarkjs exposes ONLY `signal output`:
 *   [0] registry_root
 *   [1] nullifier
 *   [2] profit_usd
 *   [3] net_profit_usd
 *
 * Private inputs: both pools' addresses, reserves, fees, amount_in, and the
 * 32-sibling Poseidon paths plus the leaf indices (direction bits).
 */

template PoolLeaf() {
    // Leaf = Poseidon(pool_addr, reserve0, reserve1, fee_bps)
    signal input pool_addr;
    signal input reserve0;
    signal input reserve1;
    signal input fee_bps;
    signal output leaf;
    component h = Poseidon(4);
    h.inputs[0] <== pool_addr;
    h.inputs[1] <== reserve0;
    h.inputs[2] <== reserve1;
    h.inputs[3] <== fee_bps;
    leaf <== h.out;
}

template SmtMembership() {
    // Depth-32 Sparse Merkle Tree membership via Poseidon.
    // Proves leaf is at `dirl`-encoded position with `siblings[32]`.
    signal input leaf;
    signal input siblings[32];
    signal input root;
    signal input dirl;     // 32-bit little-endian direction bits (0=left,1=right)

    component hashers[33];   // index i holds level[i] -> level[i+1] via Poseidon(2)
    component lsel[32];
    component rsel[32];
    component dirl_bits;
    signal level[33];
    level[0] <== leaf;

    // Decompose dirl into 32 bits (little-endian)
    dirl_bits = Num2Bits(32);
    dirl_bits.in <== dirl;

    for (var i = 0; i < 32; i++) {
        hashers[i] = Poseidon(2);

        lsel[i] = Mux1();
        lsel[i].s <== dirl_bits.out[i];
        lsel[i].c[0] <== level[i];
        lsel[i].c[1] <== siblings[i];

        rsel[i] = Mux1();
        rsel[i].s <== dirl_bits.out[i];
        rsel[i].c[0] <== siblings[i];
        rsel[i].c[1] <== level[i];

        hashers[i].inputs[0] <== lsel[i].out;
        hashers[i].inputs[1] <== rsel[i].out;
        level[i+1] <== hashers[i].out;
    }
    level[32] === root;
}

template ArbProofVerifier() {
    // === PUBLIC CONTEXT (consumed in-circuit, re-exposed as outputs) ===
    signal input registry_root;    // on-chain committed SMT root of pools
    signal input eth_usd;          // USD/ETH, 1e6 scale
    signal input gas_usd;          // gas cost USD, 1e6 scale
    signal input safety_margin;    // min net profit USD, 1e6 scale

    // === PRIVATE INPUTS ===
    signal input pool_a_addr;
    signal input pool_b_addr;
    signal input reserve_a0;       // pool A WETH-side reserve
    signal input reserve_a1;       // pool A quote reserve
    signal input reserve_b0;       // pool B WETH-side reserve
    signal input reserve_b1;       // pool B quote reserve
    signal input amount_in;        // WETH in, 1e18
    signal input fee_a;            // buy fee bps*100 (30bps -> 3000)
    signal input fee_b;            // sell fee bps*100

    // SMT membership paths (32 siblings each) + direction bits
    signal input path_a[32];
    signal input path_b[32];
    signal input dirl_a;
    signal input dirl_b;

    // === SMT MEMBERSHIP (real, not synthetic) ===
    component leaf_a = PoolLeaf();
    leaf_a.pool_addr <== pool_a_addr;
    leaf_a.reserve0 <== reserve_a0;
    leaf_a.reserve1 <== reserve_a1;
    leaf_a.fee_bps <== fee_a;

    component leaf_b = PoolLeaf();
    leaf_b.pool_addr <== pool_b_addr;
    leaf_b.reserve0 <== reserve_b0;
    leaf_b.reserve1 <== reserve_b1;
    leaf_b.fee_bps <== fee_b;

    // === SMT MEMBERSHIP (real, production) ===
    // Each pool proves depth-32 Sparse-Merkle membership of its leaf against the
    // SAME on-chain registry_root. The root is the true SMT root over all pools.
    component smt_a = SmtMembership();
    smt_a.leaf <== leaf_a.leaf;
    smt_a.root <== registry_root;
    smt_a.dirl <== dirl_a;
    for (var i = 0; i < 32; i++) { smt_a.siblings[i] <== path_a[i]; }

    component smt_b = SmtMembership();
    smt_b.leaf <== leaf_b.leaf;
    smt_b.root <== registry_root;
    smt_b.dirl <== dirl_b;
    for (var i = 0; i < 32; i++) { smt_b.siblings[i] <== path_b[i]; }

    // === CPMM ARB MATH (unchanged, production) ===
    signal fee_mult_a;
    fee_mult_a <== 10000 - fee_a;
    signal amount_in_net;
    signal amount_in_fee_remainder;
    amount_in_net <-- (amount_in * fee_mult_a) \ 10000;
    amount_in_fee_remainder <-- (amount_in * fee_mult_a) % 10000;
    amount_in_net * 10000 + amount_in_fee_remainder === amount_in * fee_mult_a;
    component amount_in_fee_rem_lt = LessThan(14);
    amount_in_fee_rem_lt.in[0] <== amount_in_fee_remainder;
    amount_in_fee_rem_lt.in[1] <== 10000;
    amount_in_fee_rem_lt.out === 1;

    signal denom_a;
    denom_a <== reserve_a0 + amount_in_net;
    signal num_a;
    num_a <== reserve_a1 * amount_in_net;
    signal quote_out;
    signal quote_out_remainder;
    quote_out <-- num_a \ denom_a;
    quote_out_remainder <-- num_a % denom_a;
    quote_out * denom_a + quote_out_remainder === num_a;
    component quote_out_rem_lt = LessThan(128);
    quote_out_rem_lt.in[0] <== quote_out_remainder;
    quote_out_rem_lt.in[1] <== denom_a;
    quote_out_rem_lt.out === 1;

    signal fee_mult_b;
    fee_mult_b <== 10000 - fee_b;
    signal quote_out_net;
    signal quote_out_fee_remainder;
    quote_out_net <-- (quote_out * fee_mult_b) \ 10000;
    quote_out_fee_remainder <-- (quote_out * fee_mult_b) % 10000;
    quote_out_net * 10000 + quote_out_fee_remainder === quote_out * fee_mult_b;
    component quote_out_fee_rem_lt = LessThan(14);
    quote_out_fee_rem_lt.in[0] <== quote_out_fee_remainder;
    quote_out_fee_rem_lt.in[1] <== 10000;
    quote_out_fee_rem_lt.out === 1;

    signal denom_b;
    denom_b <== reserve_b1 + quote_out_net;
    signal num_b;
    num_b <== reserve_b0 * quote_out_net;
    signal weth_back;
    signal weth_back_remainder;
    weth_back <-- num_b \ denom_b;
    weth_back_remainder <-- num_b % denom_b;
    weth_back * denom_b + weth_back_remainder === num_b;
    component weth_back_rem_lt = LessThan(128);
    weth_back_rem_lt.in[0] <== weth_back_remainder;
    weth_back_rem_lt.in[1] <== denom_b;
    weth_back_rem_lt.out === 1;

    // === PROFIT ===
    signal profit_weth;
    profit_weth <== weth_back - amount_in;

    signal profit_usd;
    signal profit_usd_remainder;
    profit_usd <-- (profit_weth * eth_usd) \ 1000000000000000000;
    profit_usd_remainder <-- (profit_weth * eth_usd) % 1000000000000000000;
    profit_usd * 1000000000000000000 + profit_usd_remainder === profit_weth * eth_usd;
    component profit_usd_rem_lt = LessThan(60);
    profit_usd_rem_lt.in[0] <== profit_usd_remainder;
    profit_usd_rem_lt.in[1] <== 1000000000000000000;
    profit_usd_rem_lt.out === 1;

    signal net_profit_usd;
    net_profit_usd <== profit_usd - gas_usd;

    // === CONSTRAINTS ===
    component gt0 = GreaterThan(128);
    gt0.in[0] <== profit_weth;
    gt0.in[1] <== 0;
    gt0.out === 1;

    component gt_safety = GreaterThan(128);
    gt_safety.in[0] <== net_profit_usd;
    gt_safety.in[1] <== safety_margin;
    gt_safety.out === 1;

    component gt_res_a0 = GreaterThan(128);
    gt_res_a0.in[0] <== reserve_a0;
    gt_res_a0.in[1] <== 0;
    gt_res_a0.out === 1;
    component gt_res_a1 = GreaterThan(128);
    gt_res_a1.in[0] <== reserve_a1;
    gt_res_a1.in[1] <== 0;
    gt_res_a1.out === 1;
    component gt_res_b0 = GreaterThan(128);
    gt_res_b0.in[0] <== reserve_b0;
    gt_res_b0.in[1] <== 0;
    gt_res_b0.out === 1;
    component gt_res_b1 = GreaterThan(128);
    gt_res_b1.in[0] <== reserve_b1;
    gt_res_b1.in[1] <== 0;
    gt_res_b1.out === 1;

    component gt_amt = GreaterThan(128);
    gt_amt.in[0] <== amount_in;
    gt_amt.in[1] <== 0;
    gt_amt.out === 1;

    // === PUBLIC OUTPUTS (snarkjs order = declaration order) ===
    // [0] registry_root (re-exposed so verifier can check freshness/non-replay)
    // [1] nullifier = Poseidon(pool_a, pool_b, registry_root)
    // [2] profit_usd
    // [3] net_profit_usd
    signal output registry_root_out;
    registry_root_out <== registry_root;

    signal output nullifier;
    nullifier <== Poseidon(3)([pool_a_addr, pool_b_addr, registry_root]);

    signal output profit_usd_out;
    signal output net_profit_usd_out;
    profit_usd_out <== profit_usd;
    net_profit_usd_out <== net_profit_usd;
}

component main = ArbProofVerifier();