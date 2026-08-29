// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title FlashloanArbV3 — three-leg cross-venue executor (V2 + V3 pairs).
 * @notice Enables triangular arbitrage: WETH → quote1 → quote2 → WETH
 *
 * Flow: Aave V3 flashLoanSimple(borrow WETH)
 *        -> sell WETH on buyLeg1 for quote1
 *        -> sell quote1 on buyLeg2 for quote2   <-- extra leg
 *        -> sell quote2 on sellLeg back to WETH
 *        -> approve-repay Aave (principal + 0.05%)
 *        -> NoProfit() guard -> profit stays for owner sweep.
 *
 * Any failure reverts the whole tx — principal never at risk.
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

interface IV3Router {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata params)
        external payable returns (uint256 amountOut);
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

contract FlashloanArbV3 {
    address public immutable owner;
    IAavePool public immutable aavePool;
    IV3Router public immutable v3Router;
    address public immutable WETH;

    event ArbExecuted(
        uint256 profitAmount,
        uint256 principal,
        uint8 buy1Kind,
        uint8 buy2Kind,
        uint8 sellKind
    );

    error NotOwner();
    error NoProfit();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor(address _aavePool, address _v3Router, address _weth) {
        owner = msg.sender;
        aavePool = IAavePool(_aavePool);
        v3Router = IV3Router(_v3Router);
        WETH = _weth;
        // one-time max approvals for the flashloan asset on the V3 router
        IERC20(_weth).approve(_v3Router, type(uint256).max);
    }

    /// @notice A venue leg. kind: 0 = V2 pair, 1 = V3 pool.
    struct Leg {
        uint8 kind;
        address venue;
        uint24 fee;
    }

    /**
     * @dev Execute a three-leg arbitrage: WETH -> quote1 -> quote2 -> WETH
     * @param principal  WETH to borrow from Aave
     * @param buyLeg1    venue where we SELL the borrowed WETH for quote1
     * @param buyLeg2    venue where we SELL quote1 for quote2
     * @param sellLeg    venue where we SELL quote2 back for WETH
     * @param quote1     first intermediate token
     * @param quote2     second intermediate token
     */
    function execute(
        uint256 principal,
        Leg calldata buyLeg1,
        Leg calldata buyLeg2,
        Leg calldata sellLeg,
        address quote1,
        address quote2
    ) external onlyOwner {
        // approve quote tokens for the router (cheap, bounded)
        IERC20(quote1).approve(address(v3Router), type(uint256).max);
        IERC20(quote2).approve(address(v3Router), type(uint256).max);

        bytes memory params = abi.encode(
            buyLeg1, buyLeg2, sellLeg, quote1, quote2
        );
        aavePool.flashLoanSimple(address(this), WETH, principal, params, 0);
    }

    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool) {
        require(msg.sender == address(aavePool), "bad caller");
        require(initiator == address(this), "bad initiator");

        (
            Leg memory buyLeg1,
            Leg memory buyLeg2,
            Leg memory sellLeg,
            address quote1,
            address quote2
        ) = abi.decode(params, (Leg, Leg, Leg, address, address));

        uint256 wethBefore = IERC20(WETH).balanceOf(address(this));

        // ---- leg 1: WETH -> quote1 on buyLeg1 --------------------------
        if (buyLeg1.kind == 0) {
            (uint256 r0, uint256 r1,) = IPair(buyLeg1.venue).getReserves();
            bool wethIsToken0 = IPair(buyLeg1.venue).token0() == WETH;
            (uint256 wethReserve, uint256 quote1Reserve) =
                wethIsToken0 ? (r0, r1) : (r1, r0);
            uint256 quote1Out = (quote1Reserve * amount * 997)
                / (wethReserve * 1000 + amount * 997);
            IERC20(WETH).transfer(buyLeg1.venue, amount);
            (uint256 out0, uint256 out1) =
                IPair(buyLeg1.venue).token0() == quote1
                    ? (quote1Out, uint256(0)) : (uint256(0), quote1Out);
            IPair(buyLeg1.venue).swap(out0, out1, address(this), new bytes(0));
        } else {
            IV3Router.ExactInputSingleParams memory p =
                IV3Router.ExactInputSingleParams({
                    tokenIn: WETH,
                    tokenOut: quote1,
                    fee: buyLeg1.fee,
                    recipient: address(this),
                    amountIn: amount,
                    amountOutMinimum: 0,
                    sqrtPriceLimitX96: 0
                });
            v3Router.exactInputSingle(p);
        }

        // ---- leg 2: quote1 -> quote2 on buyLeg2 ------------------------
        uint256 quote1Bal = IERC20(quote1).balanceOf(address(this));
        if (buyLeg2.kind == 0) {
            (uint256 s0, uint256 s1,) = IPair(buyLeg2.venue).getReserves();
            bool quote1IsToken0 = IPair(buyLeg2.venue).token0() == quote1;
            (uint256 quote1Reserve, uint256 quote2Reserve) =
                quote1IsToken0 ? (s0, s1) : (s1, s0);
            uint256 quote2Out = (quote2Reserve * quote1Bal * 997)
                / (quote1Reserve * 1000 + quote1Bal * 997);
            IERC20(quote1).transfer(buyLeg2.venue, quote1Bal);
            (uint256 out0, uint256 out1) =
                IPair(buyLeg2.venue).token0() == quote2
                    ? (quote2Out, uint256(0)) : (uint256(0), quote2Out);
            IPair(buyLeg2.venue).swap(out0, out1, address(this), new bytes(0));
        } else {
            IV3Router.ExactInputSingleParams memory p =
                IV3Router.ExactInputSingleParams({
                    tokenIn: quote1,
                    tokenOut: quote2,
                    fee: buyLeg2.fee,
                    recipient: address(this),
                    amountIn: quote1Bal,
                    amountOutMinimum: 0,
                    sqrtPriceLimitX96: 0
                });
            v3Router.exactInputSingle(p);
        }

        // ---- leg 3: quote2 -> WETH on sellLeg --------------------------
        uint256 quote2Bal = IERC20(quote2).balanceOf(address(this));
        if (sellLeg.kind == 0) {
            (uint256 t0, uint256 t1,) = IPair(sellLeg.venue).getReserves();
            bool quote2IsToken0 = IPair(sellLeg.venue).token0() == quote2;
            (uint256 wethReserve, uint256 quote2Reserve) =
                            quote2IsToken0 ? (t1, t0) : (t0, t1); // note: we want WETH out
                        uint256 wethOut = (wethReserve * quote2Bal * 997)
                            / (quote2Reserve * 1000 + quote2Bal * 997);
                        IERC20(quote2).transfer(sellLeg.venue, quote2Bal);
                        (uint256 out0, uint256 out1) =
                            IPair(sellLeg.venue).token0() == WETH
                                ? (wethOut, uint256(0)) : (uint256(0), wethOut);
                        IPair(sellLeg.venue).swap(out0, out1, address(this), new bytes(0));
        } else {
            IV3Router.ExactInputSingleParams memory p =
                IV3Router.ExactInputSingleParams({
                    tokenIn: quote2,
                    tokenOut: WETH,
                    fee: sellLeg.fee,
                    recipient: address(this),
                    amountIn: quote2Bal,
                    amountOutMinimum: 0,
                    sqrtPriceLimitX96: 0
                });
            v3Router.exactInputSingle(p);
        }

        // ---- repay Aave: principal + premium (APPROVE, not transfer) --
        uint256 repay = amount + premium;
        uint256 wethFinal = IERC20(WETH).balanceOf(address(this));
        require(wethFinal >= wethBefore + repay, "insufficient WETH to repay");
        IERC20(WETH).approve(address(aavePool), repay);

        // Net profit is what remains after preserving prior balance and repaying.
        uint256 profit = wethFinal - wethBefore - repay;
        if (profit == 0) revert NoProfit();

        emit ArbExecuted(profit, amount,
            buyLeg1.kind, buyLeg2.kind, sellLeg.kind);
        return true;
    }

    function sweepProfit(address token) external onlyOwner {
        uint256 bal = IERC20(token).balanceOf(address(this));
        require(bal > 0, "nothing to sweep");
        IERC20(token).transfer(owner, bal);
    }

    function sweepETH() external onlyOwner {
        uint256 bal = address(this).balance;
        require(bal > 0, "no ETH");
        (bool ok,) = payable(owner).call{value: bal}("");
        require(ok, "eth send failed");
    }

    receive() external payable {}
}