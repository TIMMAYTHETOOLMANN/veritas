// zk_circuits/poseidon_helper.mjs — compute circuit-compatible Poseidon commitments.
// Modes (match circuit arities EXACTLY):
//   node  <left> <right>   -> Poseidon(2)([left, right])          (SMT node hash)
//   leaf  <addr> <r0> <r1> <fee_bps> -> Poseidon(4)([addr,r0,r1,fee_bps])  (pool leaf)
//   stateRoot <poolA> <rA0> <rA1> <pathA0> <pathA1> <poolB> <rB0> <rB1> <pathB0> <pathB1>
//     -> Poseidon(2)([Poseidon(5)(A...), Poseidon(5)(B...)])     (legacy)
//   nullifier <poolA> <poolB> <stateRoot> -> Poseidon(3)([poolA, poolB, stateRoot])
import { buildPoseidon } from "circomlibjs";

const argv = process.argv.slice(2);
const mode = argv[0];
const nums = argv.slice(1).map(BigInt);

const F = await buildPoseidon();
const poseidon = async (inputs) => {
  const h = await F(inputs);
  return BigInt(F.F.toObject(h));
};

if (mode === "node") {
  const [l, r] = nums;
  const n = await poseidon([l, r]);
  console.log(n.toString(10));
} else if (mode === "leaf") {
  const [addr, r0, r1, fee] = nums;
  const n = await poseidon([addr, r0, r1, fee]);
  console.log(n.toString(10));
} else if (mode === "stateRoot") {
  const [pa, ra0, ra1, pA0, pA1, pb, rb0, rb1, pB0, pB1] = nums;
  const rootA = await poseidon([pa, ra0, ra1, pA0, pA1]);
  const rootB = await poseidon([pb, rb0, rb1, pB0, pB1]);
  const root = await poseidon([rootA, rootB]);
  console.log(root.toString(10));
} else if (mode === "nullifier") {
  const [pa, pb, sr] = nums;
  const n = await poseidon([pa, pb, sr]);
  console.log(n.toString(10));
} else {
  console.error("usage: poseidon_helper.mjs node|leaf|stateRoot|nullifier <decimal inputs...>");
  process.exit(1);
}