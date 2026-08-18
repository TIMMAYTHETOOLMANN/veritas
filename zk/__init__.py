# zk/ — T4/T5: ZK Differential Fuzzer + Economic Impact Simulator
# Layer 1: extract   — VK & circuit config extraction (on-chain bytecode, .r1cs)
# Layer 2: witness   — adversarial witness generator (boundary/corpus mutations)
# Layer 3: core      — proof compute core (BN254 native; CUDA/Icicle slot)
# Layer 4: differential — malformed-proof -> target verifier (eth_call / anvil)
# Layer 5: impact    — EVM state-divergence economic simulator
