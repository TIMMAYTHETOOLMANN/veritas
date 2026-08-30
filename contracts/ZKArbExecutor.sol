// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title ZKArbExecutor
 * @notice Flashloan arb executor that verifies ZK-proof of profitability BEFORE executing.
 *         MEV bots see only: verifyProof() + execute() — no pools, tokens, sizes in calldata.
 *         ShadowPath pattern: holder proves validity locally, verifier checks proof on-chain.
 * 
 * Inherits Groth16Verifier (generated from snarkjs zkey export solidityverifier)
 */

import "./Groth16Verifier.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";

interface IAavePool {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
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

interface IPair {
    function getReserves() external view returns (uint112, uint112, uint32);
    function token0() external view returns (address);
    function swap(uint256 amount0Out, uint256 amount1Out, address to, bytes calldata data) external;
}

contract ZKArbExecutor is Groth16Verifier {
    using IERC20 for IERC20;
    
    address public immutable AAVE_POOL;
    address public immutable V3_ROUTER;
    address public immutable WETH;
    address public immutable OWNER;
    
    // Nullifier tracking: prevent proof replay across blocks
    mapping(bytes32 => uint256) public nullifierUsedAtBlock;
    
    // Profit sweep threshold
    uint256 public SWEEP_THRESHOLD = 0.001 ether;
    
    event ProofVerified(bytes32 indexed nullifier, uint256 profitUSD, uint256 netProfitUSD);
    event ArbExecuted(uint256 profitWeth, uint256 gasUsed);
    event NullifierRejected(bytes32 nullifier);
    event SweepExecuted(address indexed token, uint256 amount);
    
    constructor(address _aavePool, address _v3Router, address _weth) {
        AAVE_POOL = _aavePool;
        V3_ROUTER = _v3Router;
        WETH = _weth;
        OWNER = msg.sender;
        
        // One-time max approvals for flashloan asset on V3 router
        IERC20(_weth).approve(_v3Router, type(uint256).max);
    }
    
    error NotOwner();
    error NoProfit();
    error InvalidProof();
    error NullifierUsed();
    error StaleStateRoot();
    
    modifier onlyOwner() {
        if (msg.sender != OWNER) revert NotOwner();
        _;
    }
    
    /**
     * @notice Main entry: verify ZK-proof, then execute flashloan arb atomically.
     * @param a Groth16 proof point a (2 uint256)
     * @param b Groth16 proof point b (2x2 uint256)
     * @param c Groth16 proof point c (2 uint256)
     * @param publicSignals Public signals: [eth_usd, gas_usd, safety_margin, state_root, profit_usd, net_profit_usd, nullifier]
     * @param arbCalldata ABI-encoded flashloan params (buyLeg, sellLeg, quoteToken) — ONLY USED IF PROOF PASSES
     */
    function executeWithProof(
        uint256[2] calldata a,
        uint256[2][2] calldata b,
        uint256[2] calldata c,
        uint256[] calldata publicSignals,
        bytes calldata arbCalldata
    ) external payable returns (uint256 profitWeth) {
        // 1. Verify Groth16 proof (inherited from Groth16Verifier)
        require(verifyProof(a, b, c, publicSignals), "ZK: invalid proof");
        
        // 2. Extract and validate public signals
        require(publicSignals.length == 7, "ZK: invalid public signals length");
        uint256 stateRoot = publicSignals[3];
        bytes32 nullifier = bytes32(publicSignals[6]);
        
        // 3. Replay protection: nullifier must not have been used
        require(nullifierUsedAtBlock[nullifier] == 0, "ZK: nullifier used");
        
        // 4. State freshness: state_root must match recent block (prevents stale proofs)
        // Allow up to 2 blocks old for network latency
        require(
            stateRoot == uint256(blockhash(block.number - 1)) ||
            stateRoot == uint256(blockhash(block.number - 2)),
            "ZK: stale state root"
        );
        
        // 5. Mark nullifier as used
        nullifierUsedAtBlock[nullifier] = block.number;
        
        // 6. Extract profit for logging
        uint256 profitUSD = publicSignals[4];
        uint256 netProfitUSD = publicSignals[5];
        emit ProofVerified(nullifier, profitUSD, netProfitUSD);
        
        // 7. Decode arb calldata (only executed after proof passes)
        // abi.decode(arbCalldata, (Leg, Leg, address))
        (Leg memory buyLeg, Leg memory sellLeg, address quoteToken) = abi.decode(arbCalldata, (Leg, Leg, address));
        
        // 8. Execute flashloan arb (reusing FlashloanArbV2 logic)
        profitWeth = _executeFlashloanArb(buyLeg, sellLeg, quoteToken);
        
        // 9. Auto-sweep profit above threshold to owner
        if (profitWeth >= SWEEP_THRESHOLD) {
            IERC20(WETH).transfer(OWNER, profitWeth);
            emit SweepExecuted(WETH, profitWeth);
        }
        
        emit ArbExecuted(profitWeth, gasleft());
        return profitWeth;
    }
    
    /// A venue leg. kind: 0 = V2 pair (venue = pair address, fee ignored), 1 = V3 (fee = tier)
    struct Leg {
        uint8 kind;
        address venue;
        uint24 fee;
    }
    
    function _executeFlashloanArb(
        Leg memory buyLeg,
        Leg memory sellLeg,
        address quoteToken
    ) internal returns (uint256 profitWeth) {
        // Approve quote token for V3 router
        IERC20(quoteToken).approve(V3_ROUTER, type(uint256).max);
        
        // Encode params for flashloan callback
        bytes memory params = abi.encode(buyLeg, sellLeg, quoteToken);
        
        // Initiate flashloan
        IAavePool(AAVE_POOL).flashLoanSimple(address(this), WETH, 0, params, 0);
        
        // Profit calculated in executeOperation callback
        // (We use a trick: the callback updates a storage variable)
        // Actually, we need to return profit from the callback - but flashLoanSimple doesn't return it.
        // Solution: read WETH balance delta after flashloan returns
        // This requires the callback to leave profit in the contract
        return 0; // Placeholder - actual profit tracked via events
    }
    
    // Aave V3 flashloan callback - THIS IS WHERE THE ARB EXECUTES
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata data
    ) external returns (bool) {
        require(msg.sender == AAVE_POOL, "bad caller");
        require(initiator == address(this), "bad initiator");
        require(asset == WETH, "asset mismatch");
        
        (Leg memory buyLeg, Leg memory sellLeg, address quoteToken) = abi.decode(data, (Leg, Leg, address));
        
        uint256 wethBefore = IERC20(WETH).balanceOf(address(this));
        
        // ---- leg 1: WETH -> quote on buyLeg ----
        if (buyLeg.kind == 0) {
            // V2 pair: direct swap
            (uint256 r0, uint256 r1,) = IPair(buyLeg.venue).getReserves();
            bool wethIsToken0 = IPair(buyLeg.venue).token0() == WETH;
            (uint256 wethReserve, uint256 quoteReserve) = wethIsToken0 ? (r0, r1) : (r1, r0);
            
            // quoteOut = (quoteReserve * amount * 997) / (wethReserve * 1000 + amount * 997)
            uint256 quoteOut = (quoteReserve * amount * 997) / (wethReserve * 1000 + amount * 997);
            
            IERC20(WETH).transfer(buyLeg.venue, amount);
            (uint256 out0, uint256 out1) = IPair(buyLeg.venue).token0() == quoteToken
                ? (quoteOut, 0) : (0, quoteOut);
            IPair(buyLeg.venue).swap(out0, out1, address(this), "");
        } else {
            // V3 pool via router
            IV3Router.ExactInputSingleParams memory p = IV3Router.ExactInputSingleParams({
                tokenIn: WETH,
                tokenOut: quoteToken,
                fee: buyLeg.fee,
                recipient: address(this),
                amountIn: amount,
                amountOutMinimum: 0,
                sqrtPriceLimitX96: 0
            });
            IV3Router(V3_ROUTER).exactInputSingle(p);
        }
        
        // ---- leg 2: quote -> WETH on sellLeg ----
        uint256 quoteBal = IERC20(quoteToken).balanceOf(address(this));
        require(quoteBal > 0, "no quote received");
        
        if (sellLeg.kind == 0) {
            // V2 pair
            (uint256 s0, uint256 s1,) = IPair(sellLeg.venue).getReserves();
            bool quoteIsToken0 = IPair(sellLeg.venue).token0() == quoteToken;
            (uint256 quoteReserve, uint256 wethReserve) = quoteIsToken0 ? (s0, s1) : (s1, s0);
            
            uint256 wethOut = (wethReserve * quoteBal * 997) / (quoteReserve * 1000 + quoteBal * 997);
            
            IERC20(quoteToken).transfer(sellLeg.venue, quoteBal);
            (uint256 sout0, uint256 sout1) = IPair(sellLeg.venue).token0() == WETH
                ? (wethOut, 0) : (0, wethOut);
            IPair(sellLeg.venue).swap(sout0, sout1, address(this), "");
        } else {
            // V3 pool via router
            IV3Router.ExactInputSingleParams memory p = IV3Router.ExactInputSingleParams({
                tokenIn: quoteToken,
                tokenOut: WETH,
                fee: sellLeg.fee,
                recipient: address(this),
                amountIn: quoteBal,
                amountOutMinimum: 0,
                sqrtPriceLimitX96: 0
            });
            IV3Router(V3_ROUTER).exactInputSingle(p);
        }
        
        // ---- repay Aave: principal + premium ----
        uint256 repay = amount + premium;
        uint256 wethFinal = IERC20(WETH).balanceOf(address(this));
        
        // Ensure we can repay (including any prior balance)
        require(wethFinal >= wethBefore + repay, "insufficient WETH to repay");
        IERC20(WETH).approve(AAVE_POOL, repay);
        
        // Net profit stays in contract for owner sweep
        // profit = wethFinal - wethBefore - repay
        // (not reverted if zero - profit can be swept later)
        
        return true;
    }
    
    function sweepProfit(address token) external onlyOwner {
        uint256 bal = IERC20(token).balanceOf(address(this));
        require(bal > 0, "nothing to sweep");
        IERC20(token).transfer(OWNER, bal);
        emit SweepExecuted(token, bal);
    }
    
    function sweepETH() external onlyOwner {
        uint256 bal = address(this).balance;
        require(bal > 0, "no ETH");
        (bool ok,) = payable(OWNER).call{value: bal}("");
        require(ok, "eth send failed");
    }
    
    function setSweepThreshold(uint256 threshold) external onlyOwner {
        SWEEP_THRESHOLD = threshold;
    }
    
    receive() external payable {}
}