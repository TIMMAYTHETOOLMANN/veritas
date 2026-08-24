// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title MockV2Pair — fork-sim only. Minimal constant-product-style pair
 * for the sim gate's controlled-dislocation selftest. NOT for production.
 *
 * Reserves = live token balances (auto-updates on transfer-in). swap()
 * transfers requested outputs without a k-invariant check — the harness
 * computes outs with exact V2 math, so behavior matches a real pair for
 * the executor's calling pattern.
 */
interface IERC20M {
    function balanceOf(address) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
}

contract MockV2Pair {
    address public immutable token0;
    address public immutable token1;

    constructor(address _t0, address _t1) {
        token0 = _t0;
        token1 = _t1;
    }

    function getReserves() external view returns (uint112, uint112, uint32) {
        return (
            uint112(IERC20M(token0).balanceOf(address(this))),
            uint112(IERC20M(token1).balanceOf(address(this))),
            0
        );
    }

    function swap(uint256 amount0Out, uint256 amount1Out, address to, bytes calldata) external {
        if (amount0Out > 0) IERC20M(token0).transfer(to, amount0Out);
        if (amount1Out > 0) IERC20M(token1).transfer(to, amount1Out);
    }
}
