// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title VerifierVulnerable — BROKEN: accepts any proof without pairing check
/// @dev Positive control: T4 differential loop MUST flag this as CONFIRMED_VULNERABLE
contract VerifierVulnerable {
    /// @notice Returns true unconditionally — no pairing, no VK check
    function verifyProof(
        uint[2] calldata a,
        uint[2][2] calldata b,
        uint[2] calldata c,
        uint[2] calldata input
    ) external pure returns (bool) {
        // INTENTIONAL VULNERABILITY: no pairing precompile call at all
        // This is the "under-constrained / caller-supplied VK" class
        return true;
    }

    /// @notice A withdraw function gated only by the broken verifier
    function withdraw(
        uint[2] calldata a,
        uint[2][2] calldata b,
        uint[2] calldata c,
        uint[2] calldata input,
        bytes32 root,
        bytes32 nullifierHash,
        address recipient
    ) external {
        require(this.verifyProof(a, b, c, input), "Invalid proof");
        // In a real pool this would transfer ETH; here it just records the nullifier
        // The vulnerability is that ANY proof passes, so funds are drainable
    }
}
