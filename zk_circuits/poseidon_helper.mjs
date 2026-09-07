// zk_circuits/poseidon_helper.mjs — compute circuit-compatible Poseidon commitments.
//
// CRITICAL: circomlib's `Poseidon(n)` template uses t = n+1, nRoundsF = 8, and
// nRoundsP = N_ROUNDS_P[n-1] (from poseidon.circom). circomlibjs `buildPoseidon`
// accepts the SAME {t, nRoundsF, nRoundsP} and shares iden3 constants, so we must
// parameterize per-arity instead of using the default fixed-width permutation.
//
// N_ROUNDS_P[16] = [56,57,56,60,60,63,64,63,60,66,60,65,70,60,64,68]
//
// Modes:
//   node  <left> <right>        -> Poseidon(2)  (t=3,  P=57)
//   leaf  <addr> <r0> <r1> <fee> -> Poseidon(4)  (t=5,  P=60)
//   stateRoot <a..j> (10 vals)  -> legacy 2 x Poseidon(5) + Poseidon(2)
//   nullifier <a> <b> <c>       -> Poseidon(3)  (t=4,  P=56)
import { buildPoseidon } from "circomlibjs";

const N_ROUNDS_P = [56, 57, 56, 60, 60, 63, 64, 63, 60, 66, 60, 65, 70, 60, 64, 68];

async function poseidon_t(nInputs, vals) {
  const t = nInputs + 1;
  const nRoundsF = 8;
  const nRoundsP = N_ROUNDS_P[t - 2];
  const F = await buildPoseidon({ t, nRoundsF, nRoundsP });
  const h = await F(vals.map(BigInt));
  return BigInt(F.F.toObject(h));
}

const argv = process.argv.slice(2);
const mode = argv[0];
const nums = argv.slice(1).map(BigInt);

if (mode === "node") {
  const n = await poseidon_t(2, nums);
  console.log(n.toString(10));
} else if (mode === "leaf") {
  const n = await poseidon_t(4, nums);
  console.log(n.toString(10));
} else if (mode === "nullifier") {
  const n = await poseidon_t(3, nums);
  console.log(n.toString(10));
} else if (mode === "stateRoot") {
  const [pa, ra0, ra1, pA0, pA1, pb, rb0, rb1, pB0, pB1] = nums;
  const rootA = await poseidon_t(5, [pa, ra0, ra1, pA0, pA1]);
  const rootB = await poseidon_t(5, [pb, rb0, rb1, pB0, pB1]);
  const root = await poseidon_t(2, [rootA, rootB]);
  console.log(root.toString(10));
} else {
  console.error("usage: poseidon_helper.mjs node|leaf|stateRoot|nullifier <decimal inputs...>");
  process.exit(1);
}