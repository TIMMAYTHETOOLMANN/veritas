#!/usr/bin/env python3
"""Integrate QuoterV2 into the main quote engine."""
import pathlib

content = pathlib.Path('core/quote_engine_v2.py').read_text()

# Add import for QuoterV2
content = content.replace(
    'from core.rpc import RPC\nfrom core.rpc_resilience import ResilientRPC\nfrom core.error_taxonomy import ErrorTaxonomy',
    'from core.rpc import RPC\nfrom core.rpc_resilience import ResilientRPC\nfrom core.quote_engine_v3 import V3QuoterV2Adapter, V3SpotPriceAdapter\nfrom core.error_taxonomy import ErrorTaxonomy'
)

# Update V3QuoteAdapter to use QuoterV2
old_adapter = '''class V3QuoteAdapter:
    """V3-style quote adapter using pool slot0() for price."""

    def __init__(self, rpc: RPC, resilient_rpc: ResilientRPC = None):
        self.rpc = rpc
        self.resilient_rpc = resilient_rpc

    def quote(self, token_in: str, token_out: str, amount_in: int, pool: PoolMetadata) -> QuoteResult:
        start = time.time()
        result = QuoteResult(
            amount_in=amount_in,
            token_in=token_in,
            token_out=token_out,
            pool=pool.pool_id,
            venue=pool.pool_id.venue,
            fee=pool.fee,
        )

        # Use resilient RPC for all calls (with automatic failover)
        rpc = self.resilient_rpc if self.resilient_rpc else self.rpc
        
        try:
            # Get slot0
            slot0 = rpc.eth_call(pool.pool_id.pool_address, "0x3850c7bd")
            if not slot0 or len(slot0) < 66:
                result.error = "failed to get slot0"
                return result

            sqrt_price_x96 = int(slot0[2:66], 16)
            if sqrt_price_x96 <= 0:
                result.error = "invalid sqrtPriceX96"
                return result

            # Get liquidity
            liquidity = rpc.eth_call(pool.pool_id.pool_address, "0x1a686502")
            liquidity_val = int(liquidity[2:66], 16) if liquidity and len(liquidity) >= 66 else 0

            # Get token0
            token0 = rpc.eth_call(pool.pool_id.pool_address, "0x0dfe1681")
            token0_addr = "0x" + token0[2:][-40:].lower() if token0 and len(token0) >= 66 else ""

            # Determine direction
            zero_for_one = token_in.lower() == token0_addr

            # Get decimals
            decimals_in = pool.decimals0 if zero_for_one else pool.decimals1
            decimals_out = pool.decimals1 if zero_for_one else pool.decimals0

            # Calculate output
            amount_out = self._calculate_v3_output(
                amount_in, sqrt_price_x96, liquidity_val, zero_for_one, decimals_in, decimals_out
            )

            if amount_out <= 0:
                result.error = "zero output calculated"
                return result

            result.amount_out = amount_out
            result.gas_estimate = 250_000
            result.success = True
            result.confidence = 0.85

        except Exception as e:
            err = ErrorTaxonomy.classify(e)
            result.error = f"{err.code.value}: {err.message}"

        result.quote_latency_ms = (time.time() - start) * 1000
        result.timestamp = time.time()
        try:
            result.block_number = rpc.eth_blockNumber()
        except Exception:
            result.block_number = 0

        return result

    def _calculate_v3_output(
        self, amount_in: int, sqrt_price_x96: int, liquidity: int,
        zero_for_one: bool, decimals_in: int, decimals_out: int
    ) -> int:
        """Calculate V3 output using simplified concentrated liquidity math."""
        # Convert sqrtPriceX96 to price
        price = (sqrt_price_x96 / (2**96))**2

        # Adjust for decimals
        if zero_for_one:
            # token0 -> token1
            price_adjusted = price * 10**(decimals_in - decimals_out)
        else:
            # token1 -> token0
            price_adjusted = (1 / price) * 10**(decimals_out - decimals_in)

        # Simple estimate: output = input * price * (1 - fee)
        fee_bps = 500  # 0.05% default
        amount_out_float = (amount_in / 10**decimals_in) * price_adjusted * (1 - fee_bps / 10000)
        amount_out = int(amount_out_float * 10**decimals_out)

        return amount_out'''

new_adapter = '''class V3QuoteAdapter:
    """V3-style quote adapter using on-chain QuoterV2 for exact quotes."""
    
    def __init__(self, rpc: RPC, resilient_rpc: ResilientRPC = None):
        self.rpc = rpc
        self.resilient_rpc = resilient_rpc
        self.quoterv2 = V3QuoterV2Adapter(rpc, resilient_rpc)
        self.spot_price = V3SpotPriceAdapter(rpc, resilient_rpc)

    def quote(self, token_in: str, token_out: str, amount_in: int, pool: PoolMetadata) -> QuoteResult:
        start = time.time()
        result = QuoteResult(
            amount_in=amount_in,
            token_in=token_in,
            token_out=token_out,
            pool=pool.pool_id,
            venue=pool.pool_id.venue,
            fee=pool.fee,
        )

        try:
            # Try QuoterV2 first (authoritative)
            amount_out = self.quoterv2.quote(token_in, token_out, amount_in, pool)
            
            if amount_out and amount_out > 0:
                result.amount_out = amount_out
                result.gas_estimate = 250_000
                result.success = True
                result.confidence = 0.95  # High confidence - on-chain quote
            else:
                # Fallback to spot price estimation
                amount_out = self.spot_price.quote(token_in, token_out, amount_in, pool)
                if amount_out and amount_out > 0:
                    result.amount_out = amount_out
                    result.gas_estimate = 250_000
                    result.success = True
                    result.confidence = 0.70  # Lower confidence - estimation
                else:
                    result.error = "quoter_v2_and_spot_price_both_failed"
                    return result

        except Exception as e:
            err = ErrorTaxonomy.classify(e)
            result.error = f"{err.code.value}: {err.message}"
            return result

        result.quote_latency_ms = (time.time() - start) * 1000
        result.timestamp = time.time()
        try:
            result.block_number = self.resilient_rpc.eth_blockNumber() if self.resilient_rpc else self.rpc.eth_blockNumber()
        except Exception:
            result.block_number = 0

        return result'''

content = content.replace(old_adapter, new_adapter)

pathlib.Path('core/quote_engine_v2.py').write_text(content)
print("Integrated QuoterV2 into quote engine")
