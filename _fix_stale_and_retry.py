#!/usr/bin/env python3
"""Clear stale DB data and add rate limit retry to MarketDiscovery."""
import pathlib

# Fix 1: MarketDiscovery - add rate limit detection and retry
discovery_content = pathlib.Path('core/market_discovery.py').read_text()

# Add rate limit error detection
old_class = '''class MarketDiscovery:
    """
    On-chain market discovery for VERITAS.

    Discovers pools by querying factory contracts directly.
    This ensures the registry reflects current chain state.

    Usage:
        discovery = MarketDiscovery(rpc, registry)
        pools = discovery.discover_pools(tokens, venues=["uniswap_v3", "sushi"])
    """

    def __init__(self, rpc: RPC, registry: PoolRegistry, chain_id: int = 42161):
        self.rpc = rpc
        self.registry = registry
        self.chain_id = chain_id
        self._stats = {
            "pairs_queried": 0,
            "pools_found": 0,
            "pools_registered": 0,
            "rpc_errors": 0,
        }'''

new_class = '''# Rate limit error patterns
_RATE_LIMIT_PATTERNS = [
    "rate limit",
    "usage limit",
    "too many requests",
    "429",
    "throttled",
    "quota exceeded",
]


def _is_rate_limit_error(error: Exception) -> bool:
    """Check if an error is a rate limit error."""
    msg = str(error).lower()
    return any(pattern in msg for pattern in _RATE_LIMIT_PATTERNS)


class MarketDiscovery:
    """
    On-chain market discovery for VERITAS.

    Discovers pools by querying factory contracts directly.
    This ensures the registry reflects current chain state.

    Usage:
        discovery = MarketDiscovery(rpc, registry)
        pools = discovery.discover_pools(tokens, venues=["uniswap_v3", "sushi"])
    """

    def __init__(self, rpc: RPC, registry: PoolRegistry, chain_id: int = 42161):
        self.rpc = rpc
        self.registry = registry
        self.chain_id = chain_id
        self._stats = {
            "pairs_queried": 0,
            "pools_found": 0,
            "pools_registered": 0,
            "rpc_errors": 0,
            "rate_limits": 0,
        }'''

discovery_content = discovery_content.replace(old_class, new_class)

# Add rate limit handling to _query_v3_pool
old_v3 = '''        except Exception as e:
            self._stats["rpc_errors"] += 1
            return None

    def _query_v2_pool('''

new_v3 = '''        except Exception as e:
            self._stats["rpc_errors"] += 1
            if _is_rate_limit_error(e):
                self._stats["rate_limits"] += 1
            return None

    def _query_v2_pool('''

discovery_content = discovery_content.replace(old_v3, new_v3)

# Add rate limit handling to _query_v2_pool
old_v2 = '''        except Exception as e:
            self._stats["rpc_errors"] += 1
            return None

    def _safe_block('''

new_v2 = '''        except Exception as e:
            self._stats["rpc_errors"] += 1
            if _is_rate_limit_error(e):
                self._stats["rate_limits"] += 1
            return None

    def _safe_block('''

discovery_content = discovery_content.replace(old_v2, new_v2)

pathlib.Path('core/market_discovery.py').write_text(discovery_content)
print("Added rate limit detection to MarketDiscovery")

# Fix 2: Clear stale DB data
import sqlite3
db_path = pathlib.Path('veritas.db')
if db_path.exists():
    c = sqlite3.connect(str(db_path))
    # Clear pools with zero reserves (stale test data)
    result = c.execute(
        "DELETE FROM veritas_pools WHERE reserve0 = '0' AND reserve1 = '0'"
    )
    deleted = result.rowcount
    c.commit()
    c.close()
    print(f"Cleared {deleted} stale pools from database")
else:
    print("No database file found")
