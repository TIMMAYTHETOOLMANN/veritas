// zk_circuits/poseidon_helper.mjs — compute circuit-compatible Poseidon commitments.
// Usage:
//   node poseidon_helper.mjs stateRoot <poolA> <rA0> <rA1> <pathA0> <pathA1> <poolB> <rB0> <rB1> <pathB0> <pathB1>
//     -> prints decimal state_root = Poseidon2(Poseidon5(A...), Poseidon5(B...))
//   node poseidon_helper.mjs nullifier <poolA> <poolB> <stateRoot>
//     -> prints decimal nullifier = Poseidon3([poolA, poolB, stateRoot])
import { buildPoseidon } from "circomlibjs";

const argv = process.argv.slice(2);
const mode = argv[0];
const nums = argv.slice(1).map(BigInt);

const F = await (async () => {
  const p = await buildPoseidon();
  return p;
})();

const poseidon = async (inputs) => {
  const h = await F(inputs);
  return BigInt(F.F.toObject(h));
};

if (mode === "stateRoot") {
  // [poolA, rA0, rA1, pathA0, pathA1, poolB, rB0, rB1, pathB0, pathB1]
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
  console.error("usage: poseidon_helper.mjs stateRoot|nullifier <decimal inputs...>");
  process.exit(1);
}
