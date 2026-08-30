pragma circom 2.0.0;

include "circomlib/circuits/poseidon.circom";

template Test() {
    signal input a;
    signal output b;
    b <== a * a;
}

component main = Test();
