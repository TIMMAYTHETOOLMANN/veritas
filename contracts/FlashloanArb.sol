// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title FlashloanArb
 * @notice VERITAS executor: atomic two-pool flash-loan arbitrage on Arbitrum.
 *
 * Flow: Aave V3 flashLoanSimple (borrow WETH) -> swap on pool A (V2 direct
 * pair.swap, 0.3% fee) -> swap on pool B -> repay Aave (principal + 0.05%)
 * -> keep profit in the contract; owner sweeps.
 *
 * Any leg failing reverts the WHOLE transaction — principal is never at
 * risk; the only cost of a failed attempt is gas.
 *
 * The executor is deliberately owner-gated: only the owner (hot wallet)
 * can call execute(), and profit can only be swept to the owner.
 */
interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
    function approve(address spender, uint256 amount) external returns (bool);
}

interface IPair {
    function getReserves() external view returns (uint112, uint112, uint32);
    function token0() external view returns (address);
    function swap(uint256 amount0Out, uint256 amount1Out, address to, bytes calldata data) external;
}

interface IAavePool {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

contract FlashloanArb {
    address public immutable owner;
    IAavePool public immutable aavePool;
    address public immutable WETH;

    // profit accounting for the sim gate and forensics
    event ArbExecuted(
        address indexed tokenProfit,
        uint256 profitAmount,
        uint256 principal,
        address poolBuy,
        address poolSell
    );

    error NotOwner();
    error RepayFailed();
    error NoProfit();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor(address _aavePool, address _weth) {
        owner = msg.sender;
        aavePool = IAavePool(_aavePool);
        WETH = _weth;
    }

    /**
     * @param principal  WETH amount to borrow from Aave
     * @param poolBuy    V2 pair where we SELL WETH for quote (buy the quote side)
     * @param poolSell   V2 pair where we SELL quote back for WETH
     * @param quoteToken the intermediate token
     *
     * params encoding for executeOperation (packed by this contract):
     *   abi.encode(poolBuy, poolSell, quoteToken)
     */
    function execute(
        uint256 principal,
        address poolBuy,
        address poolSell,
        address quoteToken
    ) external onlyOwner {
        bytes memory params = abi.encode(poolBuy, poolSell, quoteToken);
        aavePool.flashLoanSimple(address(this), WETH, principal, params, 0);
    }

    /**
     * @dev Aave callback. Receives `principal` WETH, must return
     *      principal + premium (0.05%) by the end of this call.
     */
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool) {
        require(msg.sender == address(aavePool), "bad caller");
        require(initiator == address(this), "bad initiator");

        (address poolBuy, address poolSell, address quoteToken) =
            abi.decode(params, (address, address, address));

        uint256 wethBefore = IERC20(WETH).balanceOf(address(this));

        // ---- leg 1: sell WETH -> quote on poolBuy --------------------
        // We transfer WETH into the pair, then call swap() asking for the
        // quote side out. (Classic V2 direct interaction — router-free,
        // saves router gas and avoids approval phishing surface.)
        (uint256 r0, uint256 r1,) = IPair(poolBuy).getReserves();
        bool wethIsToken0 = IPair(poolBuy).token0() == WETH;
        (uint256 wethReserveBuy, uint256 quoteReserveBuy) =
            wethIsToken0 ? (r0, r1) : (r1, r0);
        // V2 out amount with 0.3% fee: out = quoteR * in*997 / (wethR*1000 + in*997)
        uint256 quoteOut = (quoteReserveBuy * amount * 997)
            / (wethReserveBuy * 1000 + amount * 997);
        IERC20(WETH).transfer(poolBuy, amount);
        (uint256 out0, uint256 out1) =
            IPair(poolBuy).token0() == quoteToken ? (quoteOut, uint256(0)) : (uint256(0), quoteOut);
        IPair(poolBuy).swap(out0, out1, address(this), new bytes(0));

        // ---- leg 2: sell quote -> WETH on poolSell -------------------
        uint256 quoteBal = IERC20(quoteToken).balanceOf(address(this));
        (uint256 s0, uint256 s1,) = IPair(poolSell).getReserves();
        bool quoteIsToken0 = IPair(poolSell).token0() == quoteToken;
        (uint256 quoteReserveSell, uint256 wethReserveSell) =
            quoteIsToken0 ? (s0, s1) : (s1, s0);
        uint256 wethOut = (wethReserveSell * quoteBal * 997)
            / (quoteReserveSell * 1000 + quoteBal * 997);
        IERC20(quoteToken).transfer(poolSell, quoteBal);
        (uint256 sout0, uint256 sout1) =
            IPair(poolSell).token0() == WETH ? (wethOut, uint256(0)) : (uint256(0), wethOut);
        IPair(poolSell).swap(sout0, sout1, address(this), new bytes(0));

        // ---- repay Aave: principal + 0.05% premium --------------------
        // Aave V3 PULLS repayment via transferFrom — we must approve, not
        // transfer. (Direct transfer leaves the pool's accounting blind and
        // its pull reverts.)
        uint256 repay = amount + premium;
        uint256 wethFinal = IERC20(WETH).balanceOf(address(this));
        require(wethFinal >= repay, "insufficient WETH to repay"); // atomic guard
        IERC20(WETH).approve(address(aavePool), repay);

        // profit = WETH held after repayment minus what we held pre-loan
        uint256 profit = IERC20(WETH).balanceOf(address(this)) - wethBefore;
        if (profit == 0) revert NoProfit();
        emit ArbExecuted(WETH, profit, amount, poolBuy, poolSell);
        return true;
    }

    /// @notice sweep accumulated profit (WETH) to the owner.
    function sweepProfit(address token) external onlyOwner {
        uint256 bal = IERC20(token).balanceOf(address(this));
        require(bal > 0, "nothing to sweep");
        IERC20(token).transfer(owner, bal);
    }

    /// @notice sweep accidental ETH donations (WETH unwrapped by others etc.)
    function sweepETH() external onlyOwner {
        uint256 bal = address(this).balance;
        require(bal > 0, "no ETH");
        (bool ok,) = payable(owner).call{value: bal}("");
        require(ok, "eth send failed");
    }

    receive() external payable {}
}
