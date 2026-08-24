// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title FlashloanArbV2 — cross-venue executor (V2 pairs + Uniswap V3).
 *
 * Legs are venue-typed:
 *   kind 0 = Uniswap-V2-style pair (direct pair.swap, 0.3% fee baked in)
 *   kind 1 = Uniswap V3 pool via SwapRouter02 exactInputSingle (fee tier
 *            supplied per leg; router must be approved)
 *
 * Flow: Aave V3 flashLoanSimple(borrow WETH) -> sell WETH on buyLeg for
 * quote -> sell quote on sellLeg back to WETH -> approve-repay Aave
 * (principal + 0.05%) -> NoProfit() guard -> profit stays for owner sweep.
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

contract FlashloanArbV2 {
    address public immutable owner;
    IAavePool public immutable aavePool;
    IV3Router public immutable v3Router;
    address public immutable WETH;

    event ArbExecuted(
        uint256 profitAmount,
        uint256 principal,
        uint8 buyKind,
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

    /// A venue leg. kind: 0 = V2 pair (venue = pair address, fee ignored),
    /// 1 = V3 (venue = pool address for reference, fee = tier; router used).
    struct Leg {
        uint8 kind;
        address venue;
        uint24 fee;
    }

    /**
     * @param principal  WETH to borrow
     * @param buyLeg     venue where we SELL the borrowed WETH for quote
     * @param sellLeg    venue where we SELL quote back for WETH
     * @param quoteToken intermediate token
     */
    function execute(
        uint256 principal,
        Leg calldata buyLeg,
        Leg calldata sellLeg,
        address quoteToken
    ) external onlyOwner {
        // quote approval for the router (per-quote-token; cheap and bounded)
        IERC20(quoteToken).approve(address(v3Router), type(uint256).max);
        bytes memory params = abi.encode(buyLeg, sellLeg, quoteToken);
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

        (Leg memory buyLeg, Leg memory sellLeg, address quoteToken) =
            abi.decode(params, (Leg, Leg, address));

        uint256 wethBefore = IERC20(WETH).balanceOf(address(this));

        // ---- leg 1: WETH -> quote on buyLeg --------------------------
        if (buyLeg.kind == 0) {
            (uint256 r0, uint256 r1,) = IPair(buyLeg.venue).getReserves();
            bool wethIsToken0 = IPair(buyLeg.venue).token0() == WETH;
            (uint256 wethReserve, uint256 quoteReserve) =
                wethIsToken0 ? (r0, r1) : (r1, r0);
            uint256 quoteOut = (quoteReserve * amount * 997)
                / (wethReserve * 1000 + amount * 997);
            IERC20(WETH).transfer(buyLeg.venue, amount);
            (uint256 out0, uint256 out1) =
                IPair(buyLeg.venue).token0() == quoteToken
                    ? (quoteOut, uint256(0)) : (uint256(0), quoteOut);
            IPair(buyLeg.venue).swap(out0, out1, address(this), new bytes(0));
        } else {
            IV3Router.ExactInputSingleParams memory p =
                IV3Router.ExactInputSingleParams({
                    tokenIn: WETH,
                    tokenOut: quoteToken,
                    fee: buyLeg.fee,
                    recipient: address(this),
                    amountIn: amount,
                    amountOutMinimum: 0,
                    sqrtPriceLimitX96: 0
                });
            v3Router.exactInputSingle(p);
        }

        // ---- leg 2: quote -> WETH on sellLeg -------------------------
        uint256 quoteBal = IERC20(quoteToken).balanceOf(address(this));
        if (sellLeg.kind == 0) {
            (uint256 s0, uint256 s1,) = IPair(sellLeg.venue).getReserves();
            bool quoteIsToken0 = IPair(sellLeg.venue).token0() == quoteToken;
            (uint256 quoteReserve, uint256 wethReserve) =
                quoteIsToken0 ? (s0, s1) : (s1, s0);
            uint256 wethOut = (wethReserve * quoteBal * 997)
                / (quoteReserve * 1000 + quoteBal * 997);
            IERC20(quoteToken).transfer(sellLeg.venue, quoteBal);
            (uint256 sout0, uint256 sout1) =
                IPair(sellLeg.venue).token0() == WETH
                    ? (wethOut, uint256(0)) : (uint256(0), wethOut);
            IPair(sellLeg.venue).swap(sout0, sout1, address(this), new bytes(0));
        } else {
            IV3Router.ExactInputSingleParams memory p =
                IV3Router.ExactInputSingleParams({
                    tokenIn: quoteToken,
                    tokenOut: WETH,
                    fee: sellLeg.fee,
                    recipient: address(this),
                    amountIn: quoteBal,
                    amountOutMinimum: 0,
                    sqrtPriceLimitX96: 0
                });
            v3Router.exactInputSingle(p);
        }

        // ---- repay Aave: principal + premium (APPROVE, not transfer) --
        uint256 repay = amount + premium;
        uint256 wethFinal = IERC20(WETH).balanceOf(address(this));
        // Preserve any WETH the executor already held (prior profit) and
        // ensure the new post-trade balance can cover repayment.
        require(wethFinal >= wethBefore + repay, "insufficient WETH to repay");
        IERC20(WETH).approve(address(aavePool), repay);

        // Net profit is what remains after preserving prior balance and repaying.
        uint256 profit = wethFinal - wethBefore - repay;
        if (profit == 0) revert NoProfit();
        emit ArbExecuted(profit, amount, buyLeg.kind, sellLeg.kind);
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
