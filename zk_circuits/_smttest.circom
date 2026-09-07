pragma circom 2.1.8;
include "circomlib/circuits/poseidon.circom";
include "circomlib/circuits/mux1.circom";
include "circomlib/circuits/bitify.circom";

template SmtMembership() {
    signal input leaf;
    signal input siblings[32];
    signal input root;
    signal input dirl;
    component hashers[33];
    component lsel[32];
    component rsel[32];
    component dirl_bits;
    signal level[33];
    level[0] <== leaf;
    dirl_bits = Num2Bits(32);
    dirl_bits.in <== dirl;
    for (var i = 0; i < 32; i++) {
        hashers[i] = Poseidon(2);
        lsel[i] = Mux1();
        lsel[i].s <== dirl_bits.out[i];
        lsel[i].c[0] <== level[i];
        lsel[i].c[1] <== siblings[i];
        rsel[i] = Mux1();
        rsel[i].s <== dirl_bits.out[i];
        rsel[i].c[0] <== siblings[i];
        rsel[i].c[1] <== level[i];
        hashers[i].inputs[0] <== lsel[i].out;
        hashers[i].inputs[1] <== rsel[i].out;
        level[i+1] <== hashers[i].out;
    }
    level[32] === root;
}
component main = SmtMembership();
