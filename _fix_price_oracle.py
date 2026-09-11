#!/usr/bin/env python3
"""Fix price oracle to actually fetch on-chain prices using QuoterV2."""
import pathlib

content = pathlib.Path('core/price_oracle.py').read_text()

# Replace the stub _price_via_pair with real implementation
old_stub = '''    def _price_via_pair(
        self, token_addr: str, stable_addr: str
    ) -> Tuple[Optional[float], float]:
        """
        Compute token price via a stable pair using pool reserves.

        Returns (price_usd, usd_liquidity) or (None, 0).
        """
        from core.pool import PoolRegistry
        # We need to find pools for this pair — use a simple approach
        # In production, this would query the pool registry
        # For now, return None to fall through to fallback
        return None, 0.0'''

new_impl = '''    def _price_via_pair(
        self, token_addr: str, stable_addr: str
    ) -> Tuple[Optional[float], float]:
        """
        Compute token price via a stable pair using QuoterV2.

        Returns (price_usd, usd_liquidity) or (None, 0).
        """
        try:
            # Use QuoterV2 to get a quote for 1 token_in -> token_out
            # This gives us the current market price
            QUOTER_V2 = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
            SELECTOR = "c6a5026a"
            
            # Get decimals for both tokens
            decimals_in = TOKEN_DECIMALS.get(token_addr.lower(), 18)
            decimals_out = TOKEN_DECIMALS.get(stable_addr.lower(), 18)
            
            # Quote 1 token_in (in wei)
            amount_in = 10 ** decimals_in
            
            # Build QuoterV2 call: quoteExactInputSingle((tokenIn, tokenOut, fee, amountIn, sqrtPriceLimit))
            # Try multiple fee tiers
            fee_tiers = [500, 3000, 10000]  # 0.05%, 0.3%, 1%
            
            for fee in fee_tiers:
                # Encode the call
                t_in = token_addr.lower().replace("0x", "").rjust(64, "0")
                t_out = stable_addr.lower().replace("0x", "").rjust(64, "0")
                fee_hex = format(fee, "064x")
                amt_hex = format(amount_in, "064x")
                sqrt_limit = "0" * 64
                
                data = "0x" + SELECTOR + t_in + t_out + fee_hex + amt_hex + sqrt_limit
                
                try:
                    result = self.rpc.eth_call(QUOTER_V2, data)
                    if result and result != "0x" and len(result) >= 66:
                        amount_out = int(result[2:66], 16)
                        if amount_out > 0:
                            # Calculate price: amount_out / (10 ** decimals_out) per 1 token_in
                            price = amount_out / (10 ** decimals_out)
                            # Estimate liquidity from the quote size
                            usd_liquidity = price * (10 ** decimals_in)  # Rough estimate
                            return price, usd_liquidity
                except Exception:
                    continue
            
            return None, 0.0
            
        except Exception:
            return None, 0.0'''

content = content.replace(old_stub, new_impl)

pathlib.Path('core/price_oracle.py').write_text(content)
print("Fixed price oracle to use QuoterV2 for on-chain prices")
