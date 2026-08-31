// zk_circuits/test_roundtrip.mjs — build a PROFITABLE witness and validate the
// constraint system end-to-end (witness only; prove/verify run separately).
// Trade: buy quote at pool A (cheap), sell at pool B (rich). ~0.108 WETH gross.
import { buildPoseidon } from "circomlibjs";
import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";

const F = await buildPoseidon();
const poseidon = async (inputs) => {
  const h = await F(inputs);
  return BigInt(F.F.toObject(h));
};

// --- Profitable CPMM trade (all raw units) ---
const poolA = 0x1111111111111111111111111111111111111111n;
const poolB = 0x2222222222222222222222222222222222222222n;
const rA0 = 100n * 10n ** 18n;    // WETH-side reserve at A
const rA1 = 250000n * 10n ** 18n; // quote reserve at A   (price 0.0004 WETH/quote)
const rB0 = 120n * 10n ** 18n;    // WETH-side reserve at B
const rB1 = 250000n * 10n ** 18n; // quote reserve at B   (price 0.00048 -> edge)
const amountIn = 1n * 10n ** 18n; // 1 WETH
const feeA = 30n;                 // 30 bps (circuit denominator is 10000)
const feeB = 30n;
const pathA = [1n, 2n, 3n, 4n, 5n];
const pathB = [50n, 51n, 52n, 53n, 54n];
const witnessA = Array.from({ length: 32 }, (_, i) => BigInt(i + 1));
const witnessB = Array.from({ length: 32 }, (_, i) => BigInt(i + 100));

// Sanity: CPMM math in JS (must be profitable before we ask the circuit)
const amtNet = (amountIn * (10000n - feeA)) / 10000n;
const quoteOut = (rA1 * amtNet) / (rA0 + amtNet);
const quoteNet = (quoteOut * (10000n - feeB)) / 10000n;
const wethBack = (rB0 * quoteNet) / (rB1 + quoteNet);
const profitWeth = wethBack - amountIn;
const profitUsd = (profitWeth * 2500n * 10n ** 6n) / 10n ** 18n; // eth_usd 2500
const netUsd = profitUsd - 2n * 10n ** 6n;                       // gas $2
console.log("JS check: profitWeth =", profitWeth.toString(10),
  "| profitUsd =", (Number(profitUsd) / 1e6).toFixed(2),
  "| netUsd =", (Number(netUsd) / 1e6).toFixed(2));
if (profitWeth <= 0n || netUsd <= 500000n) throw new Error("test trade not profitable");

// --- state_root commitment (matches circuit Poseidon2(Poseidon5, Poseidon5)) ---
const rootA = await poseidon([poolA, rA0, rA1, pathA[0], pathA[1]]);
const rootB = await poseidon([poolB, rB0, rB1, pathB[0], pathB[1]]);
const stateRoot = await poseidon([rootA, rootB]);

const input = {
  eth_usd: 2500000000,          // $2500.00 in 1e6
  gas_usd: 2000000,             // $2.00
  safety_margin: 500000,        // $0.50
  state_root: stateRoot.toString(10),
  pool_a_addr: poolA.toString(10),
  pool_b_addr: poolB.toString(10),
  reserve_a0: rA0.toString(10),
  reserve_a1: rA1.toString(10),
  reserve_b0: rB0.toString(10),
  reserve_b1: rB1.toString(10),
  amount_in: amountIn.toString(10),
  fee_a: feeA.toString(10),
  fee_b: feeB.toString(10),
  verkle_witness_a: witnessA.map(String),
  verkle_witness_b: witnessB.map(String),
  verkle_path_a: pathA.map(String),
  verkle_path_b: pathB.map(String),
};
writeFileSync("build/input.json", JSON.stringify(input, null, 2));
console.log("input.json written (state_root =", stateRoot.toString(16).slice(0, 24) + "...)");

// --- witness generation ---
try {
  execFileSync("node", [
    "build/arb_proof_js/generate_witness.js",
    "build/arb_proof_js/arb_proof.wasm",
    "build/input.json",
    "build/witness.wtns",
  ], { stdio: "inherit" });
  console.log("WITNESS OK");
} catch (e) {
  console.error("WITNESS FAILED:", e.message?.slice(0, 500));
  process.exit(1);
}

// --- nullifier preview (public[0]) ---
const nullifier = await poseidon([poolA, poolB, stateRoot]);
console.log("expected nullifier (public[0]) =", nullifier.toString(10));
console.log("expected profit_usd_out (public[1]) =", profitUsd.toString(10));
console.log("expected net_profit_usd_out (public[2]) =", netUsd.toString(10));
