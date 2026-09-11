#!/usr/bin/env python3
"""Replace lines 225-227 with discovery logic."""
import pathlib

lines = pathlib.Path('veritas_engine.py').read_text().split('\n')

# Find the line with "Phase 1: Discovery"
phase1_idx = None
for i, line in enumerate(lines):
    if 'Phase 1: Discovery' in line and 'get pools to scan' in line:
        phase1_idx = i
        break

if phase1_idx is None:
    print("ERROR: Could not find 'Phase 1: Discovery' line")
else:
    print(f"Found Phase 1 at line {phase1_idx + 1}")
    
    # Replace lines phase1_idx, phase1_idx+1, phase1_idx+2 with new content
    new_lines = [
        '            # Phase 1: Discovery — query chain for pools',
        '            # Run discovery if no pools have actual reserves',
        '            pools_with_reserves = self.pool_registry.get_pools_with_reserves()',
        '            needs_discovery = len(pools_with_reserves) < 2',
        '            ',
        '            if needs_discovery:',
        '                # Discover pools with RPC failover',
        '                discovered = []',
        '                discovery_stats = {"rate_limits": 0, "rpc_errors": 0}',
        '                healthy_urls = self.rpc_health.get_healthy_urls()',
        '                ',
        '                for url in healthy_urls:',
        '                    from core.rpc import RPC',
        '                    rpc_disc = RPC(url, timeout=30, retries=1)',
        '                    self.market_discovery = MarketDiscovery(',
        '                        rpc_disc, self.pool_registry, self.chain_id',
        '                    )',
        '                    discovered = self.market_discovery.discover_and_refresh(',
        '                        self._hot_tokens,',
        '                        venues=["uniswap_v3", "sushi", "camelot", "uniswap_v2"],',
        '                    )',
        '                    discovery_stats = self.market_discovery.stats()',
        '                    if discovered:',
        '                        break',
        '                ',
        '                stats["pools_discovered"] = len(discovered)',
        '                stats["discovery_rate_limits"] = discovery_stats.get("rate_limits", 0)',
        '            else:',
        '                stats["pools_discovered"] = 0',
        '            ',
        '            # Get all pools for scanning',
        '            pools = self.pool_registry.get_live_pools(min_usd_depth=0)',
        '            stats["pools_seen"] = len(pools)',
    ]
    
    # Replace the 3 old lines with the new lines
    lines[phase1_idx:phase1_idx+3] = new_lines
    
    pathlib.Path('veritas_engine.py').write_text('\n'.join(lines))
    print(f"Replaced 3 lines with {len(new_lines)} lines of discovery logic")
    
    # Verify
    verify = pathlib.Path('veritas_engine.py').read_text()
    if 'discover_and_refresh' in verify:
        print("SUCCESS: Discovery logic applied")
    else:
        print("FAILED")
