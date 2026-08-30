pragma circom 2.0.0;

template Minimal() {
   signal input in;
   signal output out;
   out <== in;
}
component main = Minimal();
