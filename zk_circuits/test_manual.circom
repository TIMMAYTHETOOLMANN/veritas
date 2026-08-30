pragma circom 0.5.46;
template Test() {
 signal input in;
 signal output out;
 out <== in;
}
component main = Test();
