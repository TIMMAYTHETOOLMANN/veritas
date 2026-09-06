pragma circom 2.1.8;
include "circomlib/circuits/poseidon.circom";
include "circomlib/circuits/comparators.circom";

template ArbProofVerifier() {
    // === PUBLIC INPUTS (private to the chain; only 3 outputs are public) ===
    signal input eth_usd;          // USD/ETH, 1e6 scale (e.g. 2500.00 -> 2500000000)
    signal input gas_usd;          // gas cost in USD, 1e6 scale
    signal input safety_margin;    // min net profit USD, 1e6 scale (e.g. 0.50 -> 500000)
    signal input state_root;       // Poseidon(2)([Poseidon(5)(poolA...), Poseidon(5)(poolB...)])
                                   // synthetic state commitment binding pools+reserves+paths

    // === PRIVATE INPUTS ===
    signal input pool_a_addr;      // buy venue (V2 pair or V3 pool), as integer
    signal input pool_b_addr;      // sell venue
    signal input reserve_a0;       // pool A reserve of WETH-side token, raw units
    signal input reserve_a1;       // pool A reserve of quote token, raw units
    signal input reserve_b0;       // pool B reserve of WETH-side token, raw units
    signal input reserve_b1;       // pool B reserve of quote token, raw units
    signal input amount_in;        // WETH in, 1e18 units
    signal input fee_a;            // buy fee, bps*100 (30bps -> 3000)
    signal input fee_b;            // sell fee, bps*100
    signal input verkle_witness_a[32];  // reserved for real Verkle/KZG witness
    signal input verkle_witness_b[32];
    signal input verkle_path_a[5];
    signal input verkle_path_b[5];

    // --- CPMM Leg 1: Pool A (WETH -> quote) ---
    signal fee_mult_a;
    fee_mult_a <== 10000 - fee_a;

    // Integer division uses quotient + remainder constraints. Requiring exact
    // divisibility would reject essentially every real CPMM trade.
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

    // --- CPMM Leg 2: Pool B (quote -> WETH) ---
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

    // --- Profit Calculation ---
    signal profit_weth;            // 1e18 units
    profit_weth <== weth_back - amount_in;

    signal profit_usd;             // micro-USD (1e6): profit_weth(1e18) * eth_usd(1e6) / 1e18
    signal profit_usd_remainder;
    profit_usd <-- (profit_weth * eth_usd) \ 1000000000000000000;
    profit_usd_remainder <-- (profit_weth * eth_usd) % 1000000000000000000;
    profit_usd * 1000000000000000000 + profit_usd_remainder === profit_weth * eth_usd;
    component profit_usd_rem_lt = LessThan(60);
    profit_usd_rem_lt.in[0] <== profit_usd_remainder;
    profit_usd_rem_lt.in[1] <== 1000000000000000000;
    profit_usd_rem_lt.out === 1;

    signal net_profit_usd;         // micro-USD
    net_profit_usd <== profit_usd - gas_usd;

    // --- CONSTRAINTS ---
    // 1. Gross profit must be positive (128-bit: raw WETH reserves exceed 2^64)
    component gt0 = GreaterThan(128);
    gt0.in[0] <== profit_weth;
    gt0.in[1] <== 0;
    gt0.out === 1;

    // 2. Net profit must exceed safety margin
    component gt_safety = GreaterThan(128);
    gt_safety.in[0] <== net_profit_usd;
    gt_safety.in[1] <== safety_margin;
    gt_safety.out === 1;

    // 3. Reserves must be positive
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

    // 4. Amount in must be positive
    component gt_amt = GreaterThan(128);
    gt_amt.in[0] <== amount_in;
    gt_amt.in[1] <== 0;
    gt_amt.out === 1;

    // --- State commitment verification (synthetic Poseidon root until real
    //     Verkle/KZG witnesses are wired; binds proof to pools+reserves+paths) ---
    signal computed_root_a;
    computed_root_a <== Poseidon(5)([pool_a_addr, reserve_a0, reserve_a1, verkle_path_a[0], verkle_path_a[1]]);

    signal computed_root_b;
    computed_root_b <== Poseidon(5)([pool_b_addr, reserve_b0, reserve_b1, verkle_path_b[0], verkle_path_b[1]]);

    signal computed_state_root;
    computed_state_root <== Poseidon(2)([computed_root_a, computed_root_b]);

    // Must equal the declared state_root input
    computed_state_root === state_root;

    // --- Nullifier (prevents replay; binds pool pair + state commitment) ---
    signal output nullifier;
    nullifier <== Poseidon(3)([pool_a_addr, pool_b_addr, state_root]);

    // --- Public outputs (for on-chain verification) ---
    // publicSignals order: [nullifier, profit_usd_out, net_profit_usd_out]
    signal output profit_usd_out;
    signal output net_profit_usd_out;
    profit_usd_out <== profit_usd;
    net_profit_usd_out <== net_profit_usd;
}
component main = ArbProofVerifier();
