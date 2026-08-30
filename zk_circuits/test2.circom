template Test() {
    signal input a;
    signal output b;
    b <== a * a;
}

component main = Test();
