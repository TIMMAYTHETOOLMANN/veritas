# zk/ — T4/T5: ZK Differential Fuzzer + Economic Impact Simulator
# Layer 1: extract     — VK & circuit config extraction (on-chain bytecode, .r1cs)
# Layer 2: witness     — adversarial witness generator (boundary/corpus mutations)
# Layer 3: core        — proof compute core (BN254 native; CUDA/Icicle slot)
# Layer 4: differential — malformed-proof -> target verifier (eth_call / anvil)
# Layer 5: impact      — static EV/measured-census oracle
# Layer 5b: divergence — State Divergence Engine (mine on unlocked local fork,
#                        measure real pre/post TVL + attacker deltas)
# Layer 6: config      — protocol configuration management
# Layer 7: report      — comprehensive ZK audit report generator
