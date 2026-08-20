# core/config.py — chain seed, RPC fleets, topics (full 32-byte), backoff policy
# Event topics for eth_getLogs MUST be the full 32-byte keccak256 of the event
# signature. The old 4-byte values (e.g. "0x7735a7e9") are function selectors
# and match NOTHING in the topic0 position. Fabricated topics with unknown
# signatures ("ProofVerified", "NullifierHash") are dropped entirely.
import time
from core.selectors import kec256

def topic0(sig: str) -> str:
    """Full 32-byte keccak256 event topic for an event signature."""
    return "0x" + kec256(sig.encode()).hex()

# Verified-correct topics (identical to core/discovery.py TOPICS, computed
# from the only event shapes with known signatures).
EVENT_TOPICS = {
    # Tornado family
    "Deposit":    topic0("Deposit(bytes32,uint32,uint256)"),
    "Withdrawal": topic0("Withdrawal(address,bytes32,address,uint256)"),
    # Railgun family
    "RailgunDeposit":  topic0("Deposit(bytes32,uint256,address)"),
    "RailgunWithdraw": topic0("Withdraw(bytes32,uint256,address,bytes)"),
    # Aztec family
    "AztecDeposit":  topic0("Deposit(bytes32,uint256,address)"),
    "AztecWithdraw": topic0("Withdraw(bytes32,uint256,address)"),
    # Generic ZK privacy pool
    "ZkDeposit":  topic0("Deposit(bytes32,uint256)"),
    "ZkWithdraw": topic0("Withdraw(bytes32,uint256,address)"),
}

class Config:
    def __init__(self):
        # Chain seed: (chain_id, chain_name, evm_rpc, event_topics, start_block)
        self.chains = [
            (1, "ethereum",
             "https://ethereum-rpc.publicnode.com", dict(EVENT_TOPICS), 0),
            (11155111, "sepolia",
             "https://ethereum-sepolia-rpc.publicnode.com", dict(EVENT_TOPICS), 0),
            # L2s — ZK protocol activity centers
            (42161, "arbitrum",
             "https://arb1.arbitrum.io/rpc", dict(EVENT_TOPICS), 0),
            (10, "optimism",
             "https://mainnet.optimism.io", dict(EVENT_TOPICS), 0),
            (324, "zksync",
             "https://mainnet.era.zksync.io", dict(EVENT_TOPICS), 0),
            (534352, "scroll",
             "https://rpc.scroll.io", dict(EVENT_TOPICS), 0),
            (59144, "linea",
             "https://rpc.linea.build", dict(EVENT_TOPICS), 0),
            (8453, "base",
             "https://mainnet.base.org", dict(EVENT_TOPICS), 0),
        ]
        # Per-chain RPC fleets — rotation must NEVER cross chains.
        # ankr endpoints (rpc.ankr.com/eth, rpc.ankr.com/eth_sepolia) were
        # REMOVED 2026-08: the free public endpoints are now key-walled
        # ("Unauthorized: You must authenticate with an API key", -32000),
        # which poisoned fleet rotation and silently skipped windows.
        #
        # Endpoint intel (verified live 2026-08, mainnet getLogs on the
        # known-active window [25700000..25700019]):
        #   WORKS:  gateway.tenderly.co/public/mainnet, eth.api.onfinality.io/public,
        #           ethereum-rpc.publicnode.com, eth.drpc.org
        #   POISON — never add to a fleet:
        #     rpc.flashbots.net  -> FALSE-EMPTY getLogs (0 logs where 3
        #            providers confirm a real Deposit) — silently lying.
        #     eth.merkle.io      -> Cloudflare-1015 ban.
        #     rpc.nodies.app     -> DNS dead.
        #     1rpc.io/eth        -> 50-block range cap (too narrow).
        #     blastapi.io        -> 403.
        #     rpc.mevblocker.io  -> flaky-intermittent (works with retries,
        #            but flakiness invites skip-churn; excluded from the
        #            default fleet, fine for narrow manual windows).
        # publicnode/drpc only failed at ~780K-deep ARCHIVE reads, not
        # recent-block getLogs — safe for walker sweeps.
        self.rpc_fleet = {
            1: [
                "https://gateway.tenderly.co/public/mainnet",
                "https://eth.api.onfinality.io/public",
                "https://ethereum-rpc.publicnode.com",
                "https://eth.drpc.org",
            ],
            11155111: [
                "https://ethereum-sepolia-rpc.publicnode.com",
                "https://gateway.tenderly.co/public/sepolia",
            ],
            # L2 fleets — public endpoints, no API keys required
            42161: [  # Arbitrum
                "https://arb1.arbitrum.io/rpc",
                "https://arbitrum.drpc.org",
                "https://arbitrum.publicnode.com",
            ],
            10: [  # Optimism
                "https://mainnet.optimism.io",
                "https://optimism.drpc.org",
                "https://optimism.publicnode.com",
            ],
            324: [  # zkSync Era
                "https://mainnet.era.zksync.io",
                "https://zksync.drpc.org",
                "https://zksync.publicnode.com",
            ],
            534352: [  # Scroll
                "https://rpc.scroll.io",
                "https://scroll.drpc.org",
                "https://scroll.publicnode.com",
            ],
            59144: [  # Linea
                "https://rpc.linea.build",
                "https://linea.drpc.org",
                "https://linea.publicnode.com",
            ],
            8453: [  # Base
                "https://mainnet.base.org",
                "https://base.drpc.org",
                "https://base.publicnode.com",
            ],
        }
        # Back-compat alias (mainnet fleet).
        self.rpc_endpoints = self.rpc_fleet[1]

        # walker tuning
        self.blocks_per_page = 1000        # initial getLogs chunk (blocks)
        self.min_chunk_blocks = 250        # hard floor when halving on error
        self.log_cap = 1500                # response-size suspicion threshold
        self.rpc_timeout = 30
        self.max_cursors = 3               # concurrent cursors per chain
        self.backoff_initial = 0.5         # seconds
        self.backoff_max = 30.0
        self.backoff_multiplier = 2.0
        self.backoff_jitter = 0.5
        self.max_retries = 5
        # T0 signal floor
        self.template_sim_floor = 0.6
        self.min_bytecode_size = 150
        self.max_candidates_per_chain = 5000
        self.idempotent = True

    def backoff(self, attempt):
        base = min(self.backoff_initial * (self.backoff_multiplier ** attempt),
                   self.backoff_max)
        return base + (time.time() % self.backoff_jitter)

config = Config()
