pragma circom 0.5.46;
include "circomlib/circuits/poseidon.circom";
include "circomlib/circuits/comparators.circom";

template Test() {
   signal input in;
   signal output out;
   out <== in * in;
}

component main = Test();
