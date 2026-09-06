# ShadowPath Paper Review (arXiv:2608.19937v1)

## Paper Metadata

- **Title**: ShadowPath: Lookup-Private Credential Status Verification over Authenticated State
- **Authors**: Patrick Herbke, Wolf Rieder, Christian René Sechting, Huaning Yang, Sid Lamichhane, Philip Raschke, Axel Küpper
- **Affiliations**: Technische Universität Berlin, Humboldt-Universität zu Berlin
- **Venue**: Network and Distributed System Security (NDSS) Symposium 2027
- **Date**: August 20, 2026
- **DOI**: https://doi.org/10.48550/arXiv.2608.19937

---

## 1. Executive Summary

ShadowPath is a privacy-preserving credential revocation verification system that enables holders to prove their credentials remain valid **without revealing**:
- Which specific credential is being presented
- The holder's identity
- The registry position of the credential
- Any metadata that could link separate presentations

The core innovation is moving the credential status lookup to the **holder**, who proves in zero-knowledge that their credential is not in the revocation registry under the verifier-selected root.

---

## 2. Problem Statement (Section IV)

### 2.1 The Privacy Challenge in Revocation

Traditional verifiable credentials allow holders to present digitally signed claims without requiring the issuer to participate in every presentation. However, **revocation complicates this privacy model**:

1. **Status Check Exposure**: Verifiers must determine if a credential is still valid, but existing status checks may expose:
   - Recurring identifiers (credential IDs)
   - Registry positions (index in revocation list)
   - Request metadata (timing, frequency)

2. **Linkability**: Exposed metadata can serve as stable handles to link separate presentations, defeating the privacy model.

3. **Trust Assumptions**: Current systems require either:
   - Online issuer participation (latency, availability)
   - Transparent revocation registries (privacy loss)
   - Accumulator-based schemes (limited scalability)

### 2.2 ShadowPath's Solution

ShadowPath moves the credential status lookup to the holder. For each presentation, the holder proves, in zero-knowledge, that the credential has not been revoked under the verifier-selected registry root. The verifier learns the status result but not observable metadata.


## 3. Cryptographic Architecture

### 3.1 Authenticated Registry Trees (Section II-A)

ShadowPath supports two authenticated data structures for the revocation registry:

#### Sparse Merkle Tree (SMT) Backend
- **Structure**: Binary tree, depth 50
- **Hash Function**: Poseidon (ZK-friendly)
- **Path**: 50 sibling values (one per level)
- **In-Circuit Check**: Recompute hash root from path
- **Setup**: None (transparent)

#### Verkle Tree Backend
- **Structure**: k-ary tree, width k=1024, depth 5
- **Commitment Scheme**: KZG vector commitments over BLS12-377
- **Path**: 5 node openings (one per level)
- **In-Circuit Check**: Verify and link KZG openings
- **Setup**: KZG trusted setup required

**Key Trade-off**: Verkle trees reduce path depth (5 vs 50) but each opening requires expensive KZG pairing operations.

### 3.2 Zero-Knowledge Proofs (Section II-B)

#### Groth16 (Primary)
- **Curve**: BW6-761 (outer circuit)
- **Proof Size**: ~200 bytes
- **Verification Gas**: ~250k gas
- **Proving Time**: 371.6 ms (SMT) / 2.11 s (Verkle) on desktop
- **Verification Time**: 3.70 ms (SMT) / 7.55 ms (Verkle)

#### PLONK (Alternative)
- **Advantage**: No trusted setup required
- **Proving Time**: ~3s (Verkle, mobile)

### 3.3 Protocol Relation

The core relation R_D proves knowledge of a tuple (VC_id, fp, idx, rho_D) where:
- VC_id: Credential identifier (private)
- fp: Fingerprint derived from credential (private)
- idx: Registry index (private)
- rho_D: Authentication path (private)

Subject to:
1. **Lookup Derivation**: idx is correctly derived from VC_id
2. **Session Binding**: Proof is bound to verifier's session randomness
3. **Fingerprint Check**: fp matches the registry entry at idx (or entry is absent)


## 4. Protocol Execution Flow (Section VI-A)

### 4.1 Step-by-Step Protocol

```
VERIFIER                    HOLDER
    |                          |
    |--- session_randomness -->|
    |                          |
    |    1. Derive idx from VC_id
    |    2. Retrieve auth path rho_D
    |    3. Check registry entry
    |    4. Generate Groth16 proof
    |                          |
    |<-------- proof ----------|
    |                          |
    |    5. Verify proof
    |    6. Check session freshness
    |    7. Accept/reject
```

### 4.2 Session Binding (Anti-Replay)

Each presentation uses **fresh session randomness** from the verifier. The proof is bound to this randomness, ensuring:
- Proofs cannot be replayed across sessions
- Even if the same credential is presented multiple times, the verifier cannot link the presentations

### 4.3 Fingerprint Derivation

The fingerprint `fp` is derived from the credential using a Poseidon hash:
```
fp = Poseidon(VC_id, issuer_pubkey, issuance_timestamp)
```

This serves as a commitment to the credential that can be checked against the registry without revealing VC_id.

### 4.4 Absent-Entry Semantics

For non-revoked credentials, the registry entry at `idx` is either:
1. **Absent**: The leaf at idx is empty (default state)
2. **Fingerprint Mismatch**: The leaf contains a different fp (credential rotated)

The proof verifies either condition, proving non-revocation without revealing which case holds.

---

## 5. Security Properties (Section VI-C)

### 5.1 Completeness
An honestly generated proof for a non-revoked credential is accepted by the verifier.

### 5.2 Knowledge Soundness
Any accepted proof corresponds to a valid witness (VC_id, fp, idx, rho_D) that authenticates non-revocation under R_e.

### 5.3 Zero-Knowledge
The proof hides the private witness (including rho_D) beyond what follows from the public statement.

### 5.4 Anti-Linkability


## 7. Limitations & Attack Vectors (Section IX)

### 7.1 Excluded Threats

The paper explicitly excludes:
1. **Issuer-Verifier Collusion**: If issuer and verifier collude, they can correlate presentations
2. **Synchronization Traffic**: State distribution metadata can leak information
3. **Registry Completeness**: Security guarantees are relative to the authenticated root

### 7.2 Potential Attack Vectors

#### A. Timing Attacks
- **Description**: Verifier measures proof generation time to infer registry position
- **Mitigation**: Constant-time proof generation, padding

#### B. Stale State Attacks
- **Description**: Prover uses old state root to present revoked credential as valid
- **Mitigation**: Proofs must reject state roots older than N blocks (VERITAS uses 2 blocks)

#### C. Front-Running
- **Description**: Attacker observes pending proof and submits competing transaction
- **Mitigation**: Session binding, nullifier derivation

#### D. Cryptographic Assumptions
- **KZG Binding**: Relies on soundness of KZG commitments
- **Poseidon Collision**: Relies on collision resistance of Poseidon hash
- **Curve Security**: Relies on security of BLS12-377 and BW6-761 curves

### 7.3 Deployment Considerations

1. **Trusted Setup**: Verkle backend requires KZG trusted setup ceremony
2. **State Distribution**: Efficient distribution of registry state to holders
3. **Freshness**: Mechanism to ensure state roots are recent
4. **Revocation Latency**: Time between revocation and registry update

---

## 8. Application to Blockchain Privacy

### 8.1 VERITAS Integration

The VERITAS system applies ShadowPath's Verkle+Groth16 pipeline to flash loan arbitrage:
- **Private Arb Proving**: Prove profitability without revealing pools/sizes/paths
- **MEV Resistance**: Mempool sees only verifyProof(), no sensitive data
- **Nullifier Replay Protection**: Each proof bound to block hash

### 8.2 Anonymous Reputation Proofs

ShadowPath's protocol can be adapted for:
- **Anonymous KYC**: Prove credential validity without revealing identity
- **Sybil Resistance**: Prove unique humanity without doxxing
- **Compliance**: Prove regulatory eligibility without exposing wallet history

### 8.3 Off-Ramp Privacy

For black-hat cash-out anonymization:
- Generate anonymous reputation proofs to pass KYC/AML checks
- ShadowPath enables private credential verification at centralized exchanges
- Prevents linking deposit addresses to attack addresses

---

## 9. Conclusion

ShadowPath provides a practical solution for private credential status verification with:
- Strong privacy guarantees (zero-knowledge, anti-linkability)
- Efficient verification (milliseconds, ~250k gas)
- Mobile-friendly proving (~3s on modern smartphones)
- Flexible backend choice (SMT or Verkle)

The key insight is that **lookup privacy** can be achieved by moving the status check to the holder and proving correctness in zero-knowledge, eliminating the need for online issuer participation or transparent registries.

---

## References

1. Herbke, P., et al. "ShadowPath: Lookup-Private Credential Status Verification over Authenticated State." arXiv:2608.19937v1 [cs.CR], 2026.
2. Grassi, L., et al. "Poseidon: A New Hash Function for Zero-Knowledge Proof Systems." USENIX Security 2021.
3. Kate, A., Zaverucha, G.M., Goldberg, I. "Constant-Size Commitments to Polynomials and Their Applications." ASIACRYPT 2010.
4. Groth, J. "On the Size of Pairing-Based Non-interactive Arguments." EUROCRYPT 2016.
5. Gabizon, A., Williamson, Z.J., Ciobotaru, O. "PLONK: Permutations over Lagrange-bases for Oecumenical Noninteractive arguments of Knowledge." 2019.
With fresh session randomness, verifier-visible status data do not reveal whether two presentations use the same credential.

---

## 6. Performance Benchmarks (Section VIII)

### 6.1 Desktop Performance

| Metric | SMT (Groth16) | Verkle (Groth16) | Verkle (PLONK) |
|--------|---------------|------------------|----------------|
| Proving Time | 371.6 ms | 2.11 s | ~2.5 s |
| Verification Time | 3.70 ms | 7.55 ms | ~10 ms |
| Proof Size | ~200 bytes | ~200 bytes | ~400 bytes |
| Verify Gas | ~250k | ~250k | ~350k |

### 6.2 Mobile Performance

| Device | Verkle Proving Time |
|--------|---------------------|
| iPhone 15 Pro | ~3.0 s |
| Pixel 8 | ~3.2 s |

### 6.3 Key Finding

**Shorter authenticated paths do NOT necessarily yield cheaper zero-knowledge proofs.**

The Verkle tree's 5 KZG openings are more expensive than the SMT's 50 Poseidon hashes because KZG operations require pairing-friendly curve arithmetic.
4. **Path Authentication**: rho_D authenticates the lookup under root R_e