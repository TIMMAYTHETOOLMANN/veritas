// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IERC20 {
    function approve(address spender, uint256 amount) external returns (bool);
}

contract CtorTest2 {
    address public immutable A;
    address public immutable B;
    address public immutable W;

    constructor(address _a, address _b, address _w) {
        A = _a;
        B = _b;
        W = _w;
        // use a concrete large number instead of type(uint256).max
        IERC20(_w).approve(_b, 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff);
    }
}