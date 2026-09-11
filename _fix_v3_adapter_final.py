#!/usr/bin/env python3
"""Fix V3 quote adapter to use resilient RPC for all calls."""
import pathlib

content = pathlib.Path('core/quote_engine_v2.py').read_text()

# Replace the entire V3 quote method
old_method = '''    def quote(self, token_in: str, token_out: str, amount_in: int, pool: PoolMetadata) -> QuoteResult:
        start = time.time()
        result = QuoteResult(
            amount_in=amount_in,
            token_in=token_in,
            token_out=token_out,
            pool=pool.pool_id,
            venue=pool.pool_id.venue,
            fee=pool.fee,
        )

        # Try with current RPC, then failover to health monitor
        rpcs_to_try = [self.rpc]
        if self.health_monitor:
            for url in self.health_monitor.get_healthy_urls():
                from core.rpc import RPC
                rpcs_to_try.append(RPC(url, timeout=30, retries=1))
        
        print(f"[QUOTE] Trying {len(rpcs_to_try)} RPCs for {token_in[:6]}->{token_out[:6]}", flush=True)
        
        try:
            # Get slot0 from pool (try multiple RPCs if needed)
            slot0 = None
            for i, rpc in enumerate(rpcs_to_try):
                try:
                    print(f"[QUOTE]   RPC {i}: {rpc.url[:40]}...", flush=True)
                    slot0 = rpc.eth_call(pool.pool_id.pool_address, "0x3850c7bd")
                    if slot0 and len(slot0) >= 66:
                        print(f"[QUOTE]   SUCCESS!", flush=True)
                        break
                    else:
                        print(f"[QUOTE]   Failed: short result", flush=True)
                except Exception as e:
                    print(f"[QUOTE]   Error: {str(e)[:60]}", flush=True)
                    continue
            if not slot0 or len(slot0) < 66:
                result.error = "failed to get slot0"
                return result

            sqrt_price_x96 = int(slot0[2:66], 16)
            if sqrt_price_x96 <= 0:
                result.error = "invalid sqrtPriceX96"
                return result

            # Get pool state
            liquidity = self.rpc.eth_call(pool.pool_id.pool_address, "0x1a686502")
            liquidity_val = int(liquidity[2:66], 16) if liquidity and len(liquidity) >= 66 else 0

            # Get token order
            token0 = self.rpc.eth_call(pool.pool_id.pool_address, "0x0dfe1681")
            token0_addr = "0x" + token0[2:][-40:].lower() if token0 and len(token0) >= 66 else ""

            # Determine direction
            zero_for_one = token_in.lower() == token0_addr

            # Get decimals
            decimals_in = pool.decimals0 if zero_for_one else pool.decimals1
            decimals_out = pool.decimals1 if zero_for_one else pool.decimals0

            # Calculate output using concentrated liquidity math
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
            result.block_number = self.rpc.eth_blockNumber()
        except Exception:
            result.block_number = 0

        return result'''

new_method = '''    def quote(self, token_in: str, token_out: str, amount_in: int, pool: PoolMetadata) -> QuoteResult:
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

        return result'''

content = content.replace(old_method, new_method)

pathlib.Path('core/quote_engine_v2.py').write_text(content)
print("Fixed V3 quote adapter to use resilient RPC")
