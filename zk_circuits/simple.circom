pragma circom 2.0.0;

template Multiplier2() {
   signal input in;
   signal output out;
   out <== in * 2;
}
component main = Multiplier2();
