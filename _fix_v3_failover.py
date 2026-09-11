#!/usr/bin/env python3
"""Update V3 quote adapter to use health monitor for failover."""
import pathlib

content = pathlib.Path('core/quote_engine_v2.py').read_text()

# Update the quote method to use health monitor
old_quote = '''    def quote(self, token_in: str, token_out: str, amount_in: int, pool: PoolMetadata) -> QuoteResult:
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
            # Get slot0 from pool
            slot0 = self.rpc.eth_call(pool.pool_id.pool_address, "0x3850c7bd")'''

new_quote = '''    def quote(self, token_in: str, token_out: str, amount_in: int, pool: PoolMetadata) -> QuoteResult:
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
        
        try:
            # Get slot0 from pool (try multiple RPCs if needed)
            slot0 = None
            for rpc in rpcs_to_try:
                try:
                    slot0 = rpc.eth_call(pool.pool_id.pool_address, "0x3850c7bd")
                    if slot0 and len(slot0) >= 66:
                        break
                except Exception:
                    continue'''

content = content.replace(old_quote, new_quote)

pathlib.Path('core/quote_engine_v2.py').write_text(content)
print("Updated V3 quote adapter to use health monitor")
