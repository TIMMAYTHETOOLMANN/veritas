pragma circom 0.5.46;

template Multiplier2() {
   signal input in;
   signal output out;
   out <== in * in;
}

component main = Multiplier2();
