#!/usr/bin/env python3
"""
core/route_generator.py — VERITAS cross-venue and multi-hop route generation.

The core discovery problem is NOT "Is pool A profitable?"
It is "Can token X be bought cheaper on venue A and sold more expensively on venue B?"

Supports:
  - Cross-venue: Sushi V2 → Uniswap V2, Camelot → Sushi, V2 → V3, V3 → V2, etc.
  - Multi-hop: WETH → USDC → ARB → WETH (bounded by MAX_HOPS)

Every route is a sequence of (venue, pool, token_in, token_out) steps.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from core.pool import PoolId, PoolMetadata, PoolRegistry


# Maximum hops in a route
DEFAULT_MAX_HOPS = 3

# Maximum routes to generate per token
DEFAULT_MAX_ROUTES_PER_TOKEN = 50

# Supported venues
VENUES = ["uniswap_v2", "sushi", "camelot", "uniswap_v3", "sushi_v3", "pancake_v3", "ramses"]

# Core hub tokens (used for multi-hop routing)
HUB_TOKENS = [
    "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",  # WETH
    "0xaf88d065e77c8cc2239327c5edb3a432268e5831",  # USDC
    "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8",  # USDC.e
    "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",  # USDT
]


@dataclass
class RouteStep:
    """A single step in a route."""
    venue: str
    pool_address: str
    token_in: str
    token_out: str
    fee: int = 3000
    kind: str = "v2"

    def __str__(self) -> str:
        return f"{self.venue}({self.fee})"


@dataclass
class Route:
    """
    A complete arbitrage route.

    A route starts and ends with the same token (e.g., WETH → ... → WETH).
    Each step is a swap on a specific venue/pool.
    """
    steps: List[RouteStep] = field(default_factory=list)
    start_token: str = ""
    net_profit_usd: float = 0.0
    confidence: float = 0.0
    score: float = 0.0

    @property
    def num_hops(self) -> int:
        return len(self.steps)

    @property
    def venues(self) -> List[str]:
        return [step.venue for step in self.steps]

    @property
    def is_cross_venue(self) -> bool:
        """True if the route spans multiple venues."""
        return len(set(self.venues)) > 1

    @property
    def is_triangular(self) -> bool:
        """True if this is a triangular arb (start == end token)."""
        if not self.steps:
            return False
        return self.steps[0].token_in.lower() == self.steps[-1].token_out.lower()

    @property
    def route_id(self) -> str:
        """Unique identifier for this route."""
        parts = [f"{s.token_in[:6]}→{s.token_out[:6]}@{s.venue}" for s in self.steps]
        return "|".join(parts)

    def __str__(self) -> str:
        if not self.steps:
            return "empty"
        parts = [self.steps[0].token_in[:6]]
        for step in self.steps:
            parts.append(f"→ {step.venue}({step.fee}) → {step.token_out[:6]}")
        return " ".join(parts)

    def to_dict(self) -> dict:
        return {
            "route_id": self.route_id,
            "steps": [
                {
                    "venue": s.venue,
                    "pool": s.pool_address,
                    "token_in": s.token_in,
                    "token_out": s.token_out,
                    "fee": s.fee,
                    "kind": s.kind,
                }
                for s in self.steps
            ],
            "num_hops": self.num_hops,
            "is_cross_venue": self.is_cross_venue,
            "venues": self.venues,
        }


class RouteGenerator:
    """
    Generates cross-venue and multi-hop arbitrage routes.

    Usage:
        gen = RouteGenerator(pool_registry)
        routes = gen.generate_cross_venue_routes(WETH, USDC)
        triangular = gen.generate_triangular_routes(WETH, max_hops=3)
    """

    def __init__(
        self,
        pool_registry: PoolRegistry,
        max_hops: int = DEFAULT_MAX_HOPS,
        max_routes_per_token: int = DEFAULT_MAX_ROUTES_PER_TOKEN,
    ):
        self.pool_registry = pool_registry
        self.max_hops = max_hops
        self.max_routes_per_token = max_routes_per_token

    def generate_cross_venue_routes(
        self, token_a: str, token_b: str
    ) -> List[Route]:
        """
        Generate all cross-venue routes for a token pair.

        A cross-venue route buys token_b on venue X and sells on venue Y.
        """
        routes: List[Route] = []

        # Get all pools for this pair
        pools = self.pool_registry.get_pools_for_pair(token_a, token_b)
        if len(pools) < 2:
            return routes

        # Group pools by venue
        venue_pools: Dict[str, List[PoolMetadata]] = {}
        for pool in pools:
            venue = pool.pool_id.venue
            if venue not in venue_pools:
                venue_pools[venue] = []
            venue_pools[venue].append(pool)

        # Generate cross-venue routes: buy on venue X, sell on venue Y
        venues = list(venue_pools.keys())
        for i, buy_venue in enumerate(venues):
            for sell_venue in venues:
                if buy_venue == sell_venue:
                    continue

                for buy_pool in venue_pools[buy_venue]:
                    for sell_pool in venue_pools[sell_venue]:
                        # Route: token_a -> token_b on buy_venue, token_b -> token_a on sell_venue
                        route = Route(
                            start_token=token_a,
                            steps=[
                                RouteStep(
                                    venue=buy_venue,
                                    pool_address=buy_pool.pool_id.pool_address,
                                    token_in=token_a,
                                    token_out=token_b,
                                    fee=buy_pool.fee,
                                    kind=buy_pool.kind,
                                ),
                                RouteStep(
                                    venue=sell_venue,
                                    pool_address=sell_pool.pool_id.pool_address,
                                    token_in=token_b,
                                    token_out=token_a,
                                    fee=sell_pool.fee,
                                    kind=sell_pool.kind,
                                ),
                            ],
                        )
                        routes.append(route)

        return routes

    def generate_triangular_routes(
        self, start_token: str, max_hops: Optional[int] = None
    ) -> List[Route]:
        """
        Generate triangular arbitrage routes starting and ending with start_token.

        Example: WETH → USDC → ARB → WETH
        """
        max_hops = max_hops or self.max_hops
        routes: List[Route] = []
        visited: Set[str] = set()

        # BFS/DFS to find cycles
        self._dfs_routes(
            current_token=start_token,
            start_token=start_token,
            path=[],
            routes=routes,
            visited=visited,
            max_hops=max_hops,
            route_count=[0],
        )

        return routes[:self.max_routes_per_token]

    def _dfs_routes(
        self,
        current_token: str,
        start_token: str,
        path: List[RouteStep],
        routes: List[Route],
        visited: Set[str],
        max_hops: int,
        route_count: List[int],
    ) -> None:
        """Depth-first search for triangular routes."""
        if route_count[0] >= self.max_routes_per_token:
            return

        if len(path) >= max_hops:
            return

        # Get all pools containing current_token
        pools = self.pool_registry.get_pools_for_token(current_token)

        for pool in pools:
            # Determine the other token in the pool
            if pool.token0.lower() == current_token.lower():
                next_token = pool.token1
            elif pool.token1.lower() == current_token.lower():
                next_token = pool.token0
            else:
                continue

            # Avoid revisiting tokens (except start_token to close the cycle)
            if next_token.lower() in visited and next_token.lower() != start_token.lower():
                continue

            # Create step
            step = RouteStep(
                venue=pool.pool_id.venue,
                pool_address=pool.pool_id.pool_address,
                token_in=current_token,
                token_out=next_token,
                fee=pool.fee,
                kind=pool.kind,
            )

            new_path = path + [step]

            # Check if we can close the cycle
            if next_token.lower() == start_token.lower() and len(new_path) >= 2:
                # Found a cycle
                route = Route(start_token=start_token, steps=new_path)
                routes.append(route)
                route_count[0] += 1
                if route_count[0] >= self.max_routes_per_token:
                    return
            elif len(new_path) < max_hops:
                # Continue searching
                new_visited = visited | {current_token.lower()}
                self._dfs_routes(
                    current_token=next_token,
                    start_token=start_token,
                    path=new_path,
                    routes=routes,
                    visited=new_visited,
                    max_hops=max_hops,
                    route_count=route_count,
                )

    def generate_all_routes(
        self, tokens: List[str], max_hops: Optional[int] = None
    ) -> List[Route]:
        """
        Generate all routes (cross-venue + triangular) for a set of tokens.
        """
        all_routes: List[Route] = []
        seen_route_ids: Set[str] = set()

        # Cross-venue routes for each pair
        for i, token_a in enumerate(tokens):
            for token_b in tokens[i + 1:]:
                routes = self.generate_cross_venue_routes(token_a, token_b)
                for route in routes:
                    if route.route_id not in seen_route_ids:
                        all_routes.append(route)
                        seen_route_ids.add(route.route_id)

        # Triangular routes from each token
        for token in tokens:
            triangular = self.generate_triangular_routes(token, max_hops)
            for route in triangular:
                if route.route_id not in seen_route_ids:
                    all_routes.append(route)
                    seen_route_ids.add(route.route_id)

        return all_routes

    def rank_routes(self, routes: List[Route]) -> List[Route]:
        """
        Pre-rank routes before expensive simulation.

        Ranking factors:
        - Number of hops (fewer is better)
        - Venue diversity (cross-venue preferred)
        - Historical edge frequency
        """
        for route in routes:
            # Score: prefer fewer hops, cross-venue
            hop_penalty = 1.0 / (1.0 + route.num_hops)
            cross_venue_bonus = 1.5 if route.is_cross_venue else 1.0
            route.score = hop_penalty * cross_venue_bonus

        routes.sort(key=lambda r: r.score, reverse=True)
        return routes
