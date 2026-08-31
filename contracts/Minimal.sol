// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Minimal {
    address public immutable OWNER;

    constructor() {
        OWNER = msg.sender;
    }

    function get() external view returns (address) {
        return OWNER;
    }
}