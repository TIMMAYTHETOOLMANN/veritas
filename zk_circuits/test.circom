pragma circom 2.1.8;
include "../node_modules/circomlib/circuits/poseidon.circom";
template Test() { signal input in; signal output out; out <== in; } component main = Test();
