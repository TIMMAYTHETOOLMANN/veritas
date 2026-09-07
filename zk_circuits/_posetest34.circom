pragma circom 2.1.8;
include "circomlib/circuits/poseidon.circom";
template Main() {
    signal input a;
    signal input b;
    signal input c;
    signal input d;
    signal output h3;
    signal output h4;
    component p3 = Poseidon(3);
    p3.inputs[0] <== a;
    p3.inputs[1] <== b;
    p3.inputs[2] <== c;
    h3 <== p3.out;
    component p4 = Poseidon(4);
    p4.inputs[0] <== a;
    p4.inputs[1] <== b;
    p4.inputs[2] <== c;
    p4.inputs[3] <== d;
    h4 <== p4.out;
}
component main = Main();
