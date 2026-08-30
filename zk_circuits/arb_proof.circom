pragma circom 2.1.8;
include "circomlib/circuits/poseidon.circom";
include "circomlib/circuits/comparators.circom";

template ArbProofVerifier() {
    // === PUBLIC INPUTS ===
    signal input eth_usd;
    signal input gas_usd;
    signal input safety_margin;
    signal input state_root;

    // === PRIVATE INPUTS ===
    signal pool_a_addr;
    signal pool_b_addr;
    signal reserve_a0;
    signal reserve_a1;
    signal reserve_b0;
    signal reserve_b1;
    signal amount_in;   // WETH in (1e18)
    signal fee_a;       // bips * 100
    signal fee_b;
    signal verkle_witness_a[32];
    signal verkle_witness_b[32];
    signal verkle_path_a[5];
    signal verkle_path_b[5];

    // --- CPMM Leg 1: Pool A (WETH -> quote) ---
    signal fee_mult_a;
    fee_mult_a <== 10000 - fee_a;                     // constant division fine

    signal amount_in_net;
    amount_in_net <== amount_in * fee_mult_a / 10000; // constant denominator

    // Compute quote_out = reserve_a1 * amount_in_net / (reserve_a0 + amount_in_net)
    signal denom_a;
    denom_a <== reserve_a0 + amount_in_net;

    signal num_a;
    num_a <== reserve_a1 * amount_in_net;

    signal quote_out;
    quote_out <== num_a / denom_a;   // Generates constraint: quote_out * denom_a === num_a

    // --- CPMM Leg 2: Pool B (quote -> WETH) ---
    signal fee_mult_b;
    fee_mult_b <== 10000 - fee_b;

    signal quote_out_net;
    quote_out_net <== quote_out * fee_mult_b / 10000;

    // Compute weth_back = reserve_b0 * quote_out_net / (reserve_b1 + quote_out_net)
    signal denom_b;
    denom_b <== reserve_b1 + quote_out_net;

    signal num_b;
    num_b <== reserve_b0 * quote_out_net;

    signal weth_back;
    weth_back <== num_b / denom_b;   // Constraint: weth_back * denom_b === num_b

    // --- Profit Calculation ---
    signal profit_weth;
    profit_weth <== weth_back - amount_in;

    signal profit_usd;
    profit_usd <== profit_weth * eth_usd / 1000000;   // constant denominator

    signal net_profit_usd;
    net_profit_usd <== profit_usd - gas_usd;

    // --- CONSTRAINTS ---
    // 1. Gross profit must be positive
    component gt0 = GreaterThan(64);
    gt0.in[0] <== profit_weth;
    gt0.in[1] <== 0;
    gt0.out === 1;

    // 2. Net profit must exceed safety margin
    component gt_safety = GreaterThan(64);
    gt_safety.in[0] <== net_profit_usd;
    gt_safety.in[1] <== safety_margin;
    gt_safety.out === 1;

    // 3. Reserves must be positive
    component gt_res_a0 = GreaterThan(64);
    gt_res_a0.in[0] <== reserve_a0;
    gt_res_a0.in[1] <== 0;
    gt_res_a0.out === 1;

    component gt_res_a1 = GreaterThan(64);
    gt_res_a1.in[0] <== reserve_a1;
    gt_res_a1.in[1] <== 0;
    gt_res_a1.out === 1;

    component gt_res_b0 = GreaterThan(64);
    gt_res_b0.in[0] <== reserve_b0;
    gt_res_b0.in[1] <== 0;
    gt_res_b0.out === 1;

    component gt_res_b1 = GreaterThan(64);
    gt_res_b1.in[0] <== reserve_b1;
    gt_res_b1.in[1] <== 0;
    gt_res_b1.out === 1;

    // 4. Amount in must be positive
    component gt_amt = GreaterThan(64);
    gt_amt.in[0] <== amount_in;
    gt_amt.in[1] <== 0;
    gt_amt.out === 1;

    // --- Verkle Root Verification (simplified with Poseidon hashes) ---
    // Compute root from pool data and witness paths (placeholder; replace with real KZG later)
    signal computed_root_a;
    computed_root_a <== Poseidon(5)([pool_a_addr, reserve_a0, reserve_a1, verkle_path_a[0], verkle_path_a[1]]);

    signal computed_root_b;
    computed_root_b <== Poseidon(5)([pool_b_addr, reserve_b0, reserve_b1, verkle_path_b[0], verkle_path_b[1]]);

    signal computed_state_root;
    computed_state_root <== Poseidon(2)([computed_root_a, computed_root_b]);

    // Must equal the public state_root
    signal root_eq;
    root_eq <== computed_state_root - state_root;
    root_eq === 0;

    // --- Nullifier (prevents replay) ---
    signal output nullifier;
    nullifier <== Poseidon(3)([pool_a_addr, pool_b_addr, state_root]);

    // --- Public outputs (for on-chain verification) ---
    signal output profit_usd_out;
    signal output net_profit_usd_out;
    profit_usd_out <== profit_usd;
    net_profit_usd_out <== net_profit_usd;
}
component main = ArbProofVerifier();