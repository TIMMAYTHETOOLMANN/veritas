pragma circom 2.1.8;
include "circomlib/circuits/poseidon.circom";
template Main() {
    signal input a;
    signal input b;
    signal output h;
    component p = Poseidon(2);
    p.inputs[0] <== a;
    p.inputs[1] <== b;
    h <== p.out;
}
component main = Main();
