pragma circom 2.1.8;
include "circomlib/circuits/poseidon.cir";
include "circomlib/circuits/comparators.cir";
template ArbProofVerifier() {
   signal input in;
   signal output out;
   out <== in;
}
component main = ArbProofVerifier();
