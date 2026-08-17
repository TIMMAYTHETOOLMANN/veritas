# core/config.py — chain seed, RPC fleet, thresholds, backoff policy
import time

class Config:
    def __init__(self):
        # Chain seed: list of (chain_id, chain_name, evm_rpc, event_topics, start_block)
        self.chains = [
            # Mainnet — active ZK verifier / zero-knowledge deployment surface
            (1, "ethereum", "https://ethereum-rpc.publicnode.com",
             {"ProofVerified": "0x0aefcf6a", "Deposit": "0x7735a7e9",
              "Withdrawal": "0x946728f8", "NullifierHash": "0x8c0b1c99"},
             0),
            # Sepolia — testnet (watch for deployment drift, lower yield)
            (11155111, "sepolia", "https://ethereum-sepolia-rpc.publicnode.com",
             {"ProofVerified": "0x0aefcf6a", "Deposit": "0x7735a7e9", "NullifierHash": "0x8c0b1c99"},
             0),
        ]
        # RPC fleet rotation on throttle (add endpoints as needed — all free/public)
        self.rpc_endpoints = [
            "https://ethereum-rpc.publicnode.com",
            "https://eth.drpc.org",
            "https://rpc.ankr.com/eth",
        ]
        # walker configuration
        self.max_cursors = 3                # concurrent cursors per chain (multi-threaded cursor)
        self.blocks_per_page = 1000         # log-scan page size
        self.backoff_initial = 0.5          # seconds
        self.backoff_max = 30.0             # seconds
        self.backoff_multiplier = 2.0
        self.backoff_jitter = 0.5           # max random jitter
        self.max_retries = 5                # per-call retry before skip
        # T0 signal floor — candidates below these never reach T1
        self.template_sim_floor = 0.6
        self.min_bytecode_size = 150        # bytes (reject deploy debris)
        self.max_candidates_per_chain = 5000  # cap to bound T1 budget
        self.idempotent = True              # re-runs produce identical results

    def backoff(self, attempt):
        base = min(self.backoff_initial * (self.backoff_multiplier ** attempt), self.backoff_max)
        return base + (time.time() % self.backoff_jitter)

config = Config()
