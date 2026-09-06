# Oracle Manipulation Vulnerability Analysis

## Overview

Oracle manipulation is the most common attack vector in DeFi flash loan attacks. This analysis covers the mechanics, vulnerable protocols, and mitigation strategies.

---

## 1. How Oracle Manipulation Works

### 1.1 The Core Vulnerability

Protocols that use **spot prices** from DEXs as their price oracle are vulnerable to manipulation:

```
Spot Price = Reserve_A / Reserve_B

Attack: Swap massive amount → Skew reserves → Price changes dramatically
```

### 1.2 Attack Flow

```
ORACLE MANIPULATION ATTACK FLOW:

1. FLASH LOAN
   Borrow $100M of Token A

2. MANIPULATE PRICE
   Swap $50M Token A → Token B on DEX
   Result: Token B price spikes 300%

3. EXPLOIT PROTOCOL
   Lending protocol now thinks Token B is worth 300% more
   Borrow $150M against "inflated" Token B collateral

4. REVERSE MANIPULATION
   Swap back Token B → Token A
   Price returns to normal

5. REPAY FLASH LOAN
   Repay $100M + 0.09% fee

6. PROFIT
   Keep $50M borrowed assets (minus manipulation costs)
```

---

## 2. Vulnerable Protocol Types

### 2.1 Spot Price Oracles

**Vulnerability**: Uses current DEX reserves as price feed

**Examples**:
- Early versions of Compound (before Chainlink integration)
- Venus Protocol (BSC) - $150M+ exploit
- Harvest Finance - $24M exploit

**Why Vulnerable**: Single-block price manipulation is free with flash loans

### 2.2 Time-Weighted Average Price (TWAP)

**Vulnerability**: TWAP can be manipulated over multiple blocks

**Examples**:
- Uniswap V2 TWAP (short window)
- SushiSwap TWAP

**Why Vulnerable**: Attacker can maintain manipulated price for N blocks

### 2.3 Custom Oracles

**Vulnerability**: Proprietary oracle implementations with edge cases

**Examples**:
- Mango Markets - $114M exploit
- Rari Capital - $80M exploit

---

## 3. Famous Exploits

### 3.1 bZx Attack (Feb 2020) - $350K

**Vector**: Oracle manipulation via Kyber swap

**Steps**:
1. Flash loan 10,000 ETH
2. Swap on Kyder to manipulate WBTC/ETH price
3. Use inflated price on bZx to borrow more than allowed
4. Repay flash loan, keep profit

### 3.2 Pancake Bunny (May 2021) - $200M

**Vector**: WBNB/BUNNY price manipulation

**Steps**:
1. Flash loan massive WBNB
2. Dump BUNNY on DEX to crash price
3. Buy back BUNNY cheaply
4. Repay flash loan

### 3.3 Mango Markets (Oct 2022) - $114M

**Vector**: MNGO spot price manipulation


---

## 4. Mathematical Analysis

### 4.1 Price Impact Calculation

For Uniswap V2 (constant product AMM):

```
x * y = k (constant product)

Price of Token A in terms of Token B:
P = y / x

After swapping Δx of Token A:
New reserves: (x + Δx, y')
New price: P' = y' / (x + Δx)

Where y' = k / (x + Δx) = (x * y) / (x + Δx)

Price change: ΔP = P' - P
```

### 4.2 Profit Calculation

```
Profit = Borrowed_Amount - Flash_Loan_Fee - Slippage_Cost - Gas

Where:
- Flash_Loan_Fee = 0.09% of borrowed amount
- Slippage_Cost = Price impact of manipulation
- Gas = Transaction gas cost
```

---

## 5. Mitigation Strategies

### 5.1 Use Decentralized Oracles

**Chainlink Price Feeds**:
- Aggregated from multiple sources
- Resistant to single-source manipulation
- Recommended for production protocols

**Band Protocol**:
- Cross-chain data oracle
- Community-curated data sources

### 5.2 Time-Weighted Average Price (TWAP)

**Uniswap V3 TWAP**:
- Cumulative price tracking
- Manipulation requires maintaining price over many blocks
- Cost increases with TWAP window length

**Implementation**:
```solidity
// Read cumulative prices
(uint56 cumulativePrice0, uint56 cumulativePrice1,) = oracle.observe(
    secondsAgos  // [3600, 0] for 1-hour TWAP
);

// Calculate TWAP
uint256 twapPrice0 = (cumulativePrice0[1] - cumulativePrice0[0]) / 3600;
uint256 twapPrice1 = (cumulativePrice1[1] - cumulativePrice1[0]) / 3600;
```

### 5.3 Circuit Breakers

**Price Change Limits**:
```solidity
uint256 public constant MAX_PRICE_CHANGE_BPS = 1000; // 10% max change

function updatePrice(uint256 newPrice) external {
    uint256 lastPrice = getLastPrice();
    uint256 change = newPrice > lastPrice
        ? (newPrice - lastPrice) * 10000 / lastPrice
        : (lastPrice - newPrice) * 10000 / lastPrice;
    require(change <= MAX_PRICE_CHANGE_BPS, "Price change too large");
    _updatePrice(newPrice);
}
```

### 5.4 Multi-Oracle Aggregation

**Median of Multiple Sources**:
```solidity
function getAggregatedPrice() external view returns (uint256) {
    uint256[] memory prices = new uint256[](3);
    prices[0] = chainlinkOracle.getPrice();
    prices[1] = twapOracle.getPrice();
    prices[2] = backupOracle.getPrice();
    return median(prices);
}
```

### 5.5 Flash Loan Resistance

**Block-Based Delays**:
```solidity
mapping(address => uint256) public lastDepositBlock;

function deposit(uint256 amount) external {
    require(
        block.number > lastDepositBlock[msg.sender] + 1,
        "Flash loan detected"
    );
    lastDepositBlock[msg.sender] = block.number;
    _deposit(amount);
}
```

---

## 6. Detection and Monitoring

### 6.1 On-Chain Monitoring

**Indicators of Oracle Attack**:
1. Large single-block swaps (>20% of pool reserves)
2. Price deviation from multiple sources
3. Unusual borrowing activity after price spike

### 6.2 Alerting Systems

**Real-Time Monitoring**:
- Monitor large swaps on DEXs
- Track oracle price deviations
- Alert on unusual borrowing patterns

---

## 7. Conclusion

Oracle manipulation remains one of the most critical vulnerabilities in DeFi. Key takeaways:

1. **Never use spot prices** as oracle feeds
2. **Use decentralized oracles** (Chainlink) for production
3. **Implement TWAP** with sufficient window length
4. **Add circuit breakers** for extreme price movements
5. **Monitor on-chain activity** for attack patterns

The evolution of flash loan attacks shows that **oracle security is paramount** for DeFi protocol safety.

**Steps**:
1. Flash loan $150M
2. Pump MNGO price on spot markets
3. Borrow against inflated collateral
4. Price crashes, attacker keeps borrowed funds

### 3.4 Cream Finance (Aug 2021) - $33M

**Vector**: sUSD price manipulation via Curve

**Steps**:
1. Flash loan $1.3B
2. Manipulate Curve pool to skew sUSD price
3. Borrow against inflated collateral
4. Repay flash loan