#!/usr/bin/env python3
"""Integrate Alchemy + RapidAPI into the engine."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# 1. Add import for external APIs
content = content.replace(
    'from core.rpc import RPC',
    'from core.rpc import RPC\nfrom core.external_apis import AlchemyRPC, CryptoArbitrageAPI, DeFiLlamaAPI'
)

# 2. Add Alchemy URL to default config RPC URLs (as primary)
content = content.replace(
    '    "rpc_urls": [\n        "https://gateway.tenderly.co/public/arbitrum",',
    '    "rpc_urls": [\n        AlchemyRPC().url,'
)

# 3. Add external API initialization in __init__
content = content.replace(
    '        self.market_discovery: Optional[MarketDiscovery] = None',
    '        self.market_discovery: Optional[MarketDiscovery] = None\n        self.alchemy: Optional[AlchemyRPC] = None\n        self.arbitrage_api: Optional[CryptoArbitrageAPI] = None\n        self.defillama_api: Optional[DeFiLlamaAPI] = None'
)

# 4. Add external API initialization in initialize()
content = content.replace(
    '        self.market_discovery = MarketDiscovery(rpc, self.pool_registry, self.chain_id)',
    '        self.market_discovery = MarketDiscovery(rpc, self.pool_registry, self.chain_id)\n        self.alchemy = AlchemyRPC()\n        self.arbitrage_api = CryptoArbitrageAPI()\n        self.defillama_api = DeFiLlamaAPI()'
)

# 5. Update status() to include external API stats
content = content.replace(
    '            "accounting": self.accounting.summary(),\n            "config": {k: v for k, v in self.config.items() if "secret" not in k.lower()},',
    '            "accounting": self.accounting.summary(),\n            "alchemy": self.alchemy.stats() if self.alchemy else None,\n            "arbitrage_api": self.arbitrage_api.stats() if self.arbitrage_api else None,\n            "defillama_api": self.defillama_api.stats() if self.defillama_api else None,\n            "config": {k: v for k, v in self.config.items() if "secret" not in k.lower()},'
)

pathlib.Path('veritas_engine.py').write_text(content)
print("Integrated external APIs into engine")
