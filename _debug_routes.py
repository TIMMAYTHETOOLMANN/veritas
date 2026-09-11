#!/usr/bin/env python3
"""Debug route generation."""
from core.external_apis import AlchemyRPC
from core.pool import PoolRegistry, PoolId, PoolMetadata
from core.route_generator import RouteGenerator

rpc = AlchemyRPC()
reg = PoolRegistry()
reg.load_from_db(42161)

print(f"Loaded {reg.count()} pools")

# Check pools with reserves
for p in reg.get_all_pools():
    print(f"  {p.pool_id.venue}: {p.token0[:6]}/{p.token1[:6]} fee={p.fee} kind={p.kind} r0={p.reserve0} r1={p.reserve1}")

# Generate routes
gen = RouteGenerator(reg)
tokens = [
    "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
    "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
]

routes = gen.generate_all_routes(tokens, max_hops=2)
print(f"\nGenerated {len(routes)} routes")

for r in routes[:5]:
    print(f"  {r}")
    for s in r.steps:
        print(f"    {s.venue}: {s.token_in[:6]} -> {s.token_out[:6]} fee={s.fee}")
