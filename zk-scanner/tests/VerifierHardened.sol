// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title VerifierHardened — correctly calls BN254 pairing precompile
/// @dev Negative control: T4 differential loop MUST report this as HEALTHY
contract VerifierHardened {
    // BN254 base field prime
    uint256 constant P = 21888242871839275222246405745257275088696311157297823662689037894645226208384;

    /// @notice Negate a G1 point (x, p - y)
    function _negG1(uint256 ax, uint256 ay) internal pure returns (uint256, uint256) {
        if (ay == 0) return (ax, 0);
        return (ax, P - ay);
    }

    /// @notice Hardened: calls BN254 pairing precompile (0x08)
    function verifyProof(
        uint[2] calldata a,
        uint[2][2] calldata b,
        uint[2] calldata c,
        uint[2] calldata input
    ) external view returns (bool) {
        // G1 generator (1, 2)
        // G2 generator of bn128
        // Build a pairing check with a dummy VK:
        // e(-A, B) * e(G1, G2) * e(-C, G2) * e(-G1, G2) == 1
        // For random/forged proofs this will NOT verify — the pairing
        // equation only holds for valid proofs of the dummy circuit.

        bytes memory input_data = new bytes(4 * 6 * 32);

        // Pair 1: (-A, B)
        (uint256 na_x, uint256 na_y) = _negG1(a[0], a[1]);
        _writeG1(input_data, 0, na_x, na_y);
        _writeG2(input_data, 2 * 32, b[0][0], b[0][1], b[1][0], b[1][1]);

        // Pair 2: (G1, G2) — dummy IC point (generator)
        _writeG1(input_data, 6 * 32, 1, 2);
        _writeG2(input_data, 8 * 32,
            10857046999023057135944570762232829481370756359578518086990519993285655852781,
            1155973203298638710771204864021347466587347143268668415181183168730242144363,
            84956539231234314176049732474892724353992562132110485804350875571407461735,
            1530139339420677371072966269898987503244980063547352995959333115479463259772
        );

        // Pair 3: (-C, G2)
        (uint256 nc_x, uint256 nc_y) = _negG1(c[0], c[1]);
        _writeG1(input_data, 12 * 32, nc_x, nc_y);
        _writeG2(input_data, 14 * 32,
            10857046999023057135944570762232829481370756359578518086990519993285655852781,
            1155973203298638710771204864021347466587347143268668415181183168730242144363,
            84956539231234314176049732474892724353992562132110485804350875571407461735,
            1530139339420677371072966269898987503244980063547352995959333115479463259772
        );

        // Pair 4: (-G1, G2)
        (uint256 nga_x, uint256 nga_y) = _negG1(1, 2);
        _writeG1(input_data, 18 * 32, nga_x, nga_y);
        _writeG2(input_data, 20 * 32,
            10857046999023057135944570762232829481370756359578518086990519993285655852781,
            1155973203298638710771204864021347466587347143268668415181183168730242144363,
            84956539231234314176049732474892724353992562132110485804350875571407461735,
            1530139339420677371072966269898987503244980063547352995959333115479463259772
        );

        (bool success, bytes memory result) = address(0x08).staticcall(input_data);
        if (!success) return false;
        return result.length == 32 && uint256(bytes32(result)) == 1;
    }

    function _writeG1(bytes memory data, uint256 offset, uint256 x, uint256 y) internal pure {
        assembly {
            mstore(add(data, add(32, offset)), x)
            mstore(add(data, add(64, offset)), y)
        }
    }

    function _writeG2(bytes memory data, uint256 offset,
        uint256 x0, uint256 x1, uint256 y0, uint256 y1) internal pure {
        assembly {
            mstore(add(data, add(32, offset)), x0)
            mstore(add(data, add(64, offset)), x1)
            mstore(add(data, add(96, offset)), y0)
            mstore(add(data, add(128, offset)), y1)
        }
    }
}
