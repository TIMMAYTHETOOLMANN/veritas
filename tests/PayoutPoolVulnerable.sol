// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title PayoutPoolVulnerable — a real-money pool whose verifier is a stub.
/// @dev Used by tests/test_divergence.py to prove the State Divergence Engine
///      mines a forged-withdraw tx and measures real attacker/TVL deltas.
contract PayoutPoolVulnerable {
    /// @notice Broken verifier: accepts ANY proof (no pairing check)
    function verifyProof(bytes calldata /*proof*/) external pure returns (bool) {
        return true; // INTENTIONAL VULNERABILITY — stubbed verifier
    }

    /// @notice Authorized by the (broken) verifier; pays caller from the pool.
    /// @param amount Number of wei to pay the caller (pool balance permitting).
    function withdraw(bytes calldata proof, uint256 amount) external returns (bool) {
        require(this.verifyProof(proof), "invalid proof");
        // No nullifier/spent tracking — the class of bug the T4 differential
        // loop flags. Any proof punches through and drains the balance.
        (bool ok,) = msg.sender.call{value: amount}("");
        require(ok, "payout failed");
        return true;
    }

    receive() external payable {}
}