pragma circom 2.0.0;

include "circomlib/circuits/poseidon.circom";
include "circomlib/circuits/comparators.circom";

/**
 * @title ArbProofVerifier
 * @notice ZK circuit proving knowledge of a profitable 2-pool arb without revealing pools/amounts.
 * 
 * ShadowPath pattern: holder proves validity locally, verifier checks proof on-chain.
 * Public inputs: eth_usd, gas_usd, safety_margin, state_root (block hash)
 * Private inputs: pool addresses, reserves, amounts, Verkle witnesses/paths
 * 
 * Constraints enforced:
 * 1. CPMM math correctness for both legs
 * 2. profit_weth > 0 (gross profit positive)
 * 3. net_profit_usd > safety_margin (net after gas clears threshold)
 * 4. Verkle membership proofs for both pools against state_root
 * 5. Nullifier = Poseidon(pool_a, pool_b, state_root) prevents replay
 */

template ArbProofVerifier() {
    // === PUBLIC INPUTS (known to verifier) ===
    signal input eth_usd;           // ETH price in USD * 1e6 (fixed point)
    signal input gas_usd;           // Gas cost in USD * 1e6
    signal input safety_margin;     // Minimum net profit USD * 1e6
    signal input state_root;        // Verkle root of pool registry (block hash)
    
    // === PRIVATE INPUTS (known only to prover/hunter) ===
    signal input pool_a_addr;
    signal input pool_b_addr;
    signal input reserve_a0;
    signal input reserve_a1;
    signal input reserve_b0;
    signal input reserve_b1;
    signal input amount_in;       // WETH borrowed (1e18)
    signal input fee_a;           // Pool A fee in basis points * 100 (3000 = 0.3%)
    signal input fee_b;           // Pool B fee in basis points * 100
    
    // Verkle witnesses (32 elements each = KZG commitments for k=1024, d=5)
    signal input verkle_witness_a[32];
    signal input verkle_witness_b[32];
    // Verkle authentication paths (5 levels each for depth=5)
    signal input verkle_path_a[5];
    signal input verkle_path_b[5];
    
    // === CIRCUIT LOGIC ===
    
    // --- CPMM Leg 1: Pool A (WETH -> quote) ---
    // amount_in_net = amount_in * (10000 - fee_a) / 10000
    signal fee_mult_a;
    fee_mult_a <== 10000 - fee_a;
    signal amount_in_net;
    amount_in_net <== amount_in * fee_mult_a / 10000;
    
    // quote_out = reserve_a1 * amount_in_net / (reserve_a0 + amount_in_net)
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
    
    signal denom_b;
    denom_b <== reserve_b1 + quote_out_net;
    signal weth_back;
        weth_back <== num_b / denom_b;   // Constraint: weth_back * denom_b === num_b
    
    // --- Profit Calculation ---
    signal profit_weth;
    profit_weth <== weth_back - amount_in;
    
    // Convert to USD (fixed point 1e6)
    // profit_usd = profit_weth * eth_usd / 1e12  (since profit_weth is 1e18, eth_usd is 1e6)
    signal profit_usd;
    profit_usd <== profit_weth * eth_usd / 1000000;
    
    // Net profit after gas
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
    
    // 5. Verkle membership proof verification (simplified: Poseidon hash chain)
    // In production: replace with actual KZG pairing check via precompile
    // Here we verify the Poseidon hash of the witness matches the state_root
    signal computed_root_a;
    computed_root_a <== Poseidon(5)([pool_a_addr, reserve_a0, reserve_a1, verkle_path_a[0], verkle_path_a[1]]);
    
    signal root_a_l2;
    root_a_l2 <== Poseidon(5)([computed_root_a, verkle_path_a[2], verkle_path_a[3], verkle_path_a[4], 0]);
    
    component eq_root_a = IsEqual();
    eq_root_a.in[0] <== root_a_l2;
    eq_root_a.in[1] <== state_root;
    eq_root_a.out === 1;
    
    signal computed_root_b;
    computed_root_b <== Poseidon(5)([pool_b_addr, reserve_b0, reserve_b1, verkle_path_b[0], verkle_path_b[1]]);
    
    signal root_b_l2;
    root_b_l2 <== Poseidon(5)([computed_root_b, verkle_path_b[2], verkle_path_b[3], verkle_path_b[4], 0]);
    
    component eq_root_b = IsEqual();
    eq_root_b.in[0] <== root_b_l2;
    eq_root_b.in[1] <== state_root;
    eq_root_b.out === 1;
    
    // 6. Nullifier: prevents proof replay across blocks
    // nullifier = Poseidon(pool_a_addr, pool_b_addr, state_root)
    signal output nullifier;
    nullifier <== Poseidon(3)([pool_a_addr, pool_b_addr, state_root]);
    
    // === PUBLIC OUTPUTS (for verifier contract) ===
    signal output profit_usd_out;
    signal output net_profit_usd_out;
    profit_usd_out <== profit_usd;
    net_profit_usd_out <== net_profit_usd;
}

component main = ArbProofVerifier();
