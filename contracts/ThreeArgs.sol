// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract ThreeArgs {
    address public immutable A;
    address public immutable B;
    address public immutable W;

    constructor(address _a, address _b, address _w) {
        A = _a;
        B = _b;
        W = _w;
    }

    function get() external view returns (address, address, address) {
        return (A, B, W);
    }
}