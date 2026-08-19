# zk/config.py — Protocol configuration management
# Manages target protocols, circuit configs, VK registries, and campaign parameters.
# Pure config layer: no RPC, no proving, no side effects. Importable standalone.

import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db
from core.rpc import RPC, uint
from core.selectors import selectors_map

# Default RPC endpoints per chain
DEFAULT_RPC = {
    1: "https://ethereum-rpc.publicnode.com",
    11155111: "https://ethereum-sepolia-rpc.publicnode.com",
}

# Known ZK protocol templates with their characteristic selectors
ZK_TEMPLATES = {
    "tornado_v2": {
        "description": "Tornado Cash-style privacy pool",
        "selectors": ["deposit", "withdraw", "verify", "getroot", "nullif", "roots", "denom", "levels"],
        "proof_system": "groth16",
        "curve": "bn254",
        "typical_pub_inputs": 2,
    },
    "zk_upgradable": {
        "description": "Upgradable verifier pattern (dangerous)",
        "selectors": ["withdraw", "verify", "setver"],
        "proof_system": "groth16",
        "curve": "bn254",
        "typical_pub_inputs": 2,
    },
    "generic_zk": {
        "description": "Generic ZK verifier (no template match)",
        "selectors": ["verify"],
        "proof_system": "groth16",
        "curve": "bn254",
        "typical_pub_inputs": 2,
    },
}

# Exploit class definitions with their attack parameters
EXPLOIT_CLASSES = {
    "ZK-FIELD-OVERFLOW": {
        "description": "Missing range constraint allows field-modulus boundary values",
        "severity": "CRITICAL",
        "financial_ceiling": "L0+L1 (entire pool)",
        "attack_vectors": ["p-1", "p-2", "2^256-1", "2^128-1", "negative values"],
    },
    "ZK-UNDER-CONSTRAINED": {
        "description": "Output wire not constrained to public input",
        "severity": "CRITICAL",
        "financial_ceiling": "L0+L1 (entire pool)",
        "attack_vectors": ["garbage witnesses", "unconstrained wire injection"],
    },
    "ZK-NULLIFIER-COLLISION": {
        "description": "Weak nullifier derivation enables double-spend",
        "severity": "HIGH",
        "financial_ceiling": "L0 (balance-limited)",
        "attack_vectors": ["secret pairs", "hash collisions", "near-identical secrets"],
    },
    "ZK-VERIFIER-CONFIG-MISMATCH": {
        "description": "On-chain verifier expects different config than circuit",
        "severity": "MEDIUM/HIGH",
        "financial_ceiling": "L0+L1 (entire pool)",
        "attack_vectors": ["cross-circuit replay", "public input truncation", "VK domain separator mismatch"],
    },
    "ZK-PROOF-MALLEABILITY": {
        "description": "Proof points can be mathematically mutated",
        "severity": "HIGH",
        "financial_ceiling": "L0 (replay-limited)",
        "attack_vectors": ["negation of A/C", "B coordinate swap", "point sign flip"],
    },
}


class ZKConfig:
    """Protocol configuration manager — loads/saves target configs, tracks VKs."""

    def __init__(self, db_path=None):
        self.db_path = db_path
        self._cache = {}

    def get_template(self, template_id):
        return ZK_TEMPLATES.get(template_id, ZK_TEMPLATES["generic_zk"])

    def get_exploit_class(self, vclass):
        return EXPLOIT_CLASSES.get(vclass, {})

    def get_chain_rpc(self, chain_id):
        return DEFAULT_RPC.get(chain_id, DEFAULT_RPC[1])

    def load_target_config(self, address, chain_id=1):
        """Load full configuration for a target from the database."""
        address = address.lower()
        c = db.conn()
        try:
            # Target info
            trow = c.execute(
                "SELECT * FROM targets WHERE address=?", (address,)
            ).fetchone()

            # VK info
            vrow = c.execute(
                "SELECT * FROM vk_registry WHERE address=?", (address,)
            ).fetchone()

            # Latest probes
            probes = c.execute(
                "SELECT * FROM probes WHERE address=? ORDER BY ts DESC LIMIT 20",
                (address,)
            ).fetchall()

            # Latest campaign
            camp = c.execute(
                "SELECT * FROM fuzz_campaigns WHERE address=? ORDER BY ts DESC LIMIT 1",
                (address,)
            ).fetchone()

            # Findings
            findings = c.execute(
                "SELECT * FROM findings WHERE address=? ORDER BY id DESC",
                (address,)
            ).fetchall()

            # Inventory
            inv = c.execute(
                "SELECT * FROM inventory WHERE address=?", (address,)
            ).fetchall()
        finally:
            c.close()

        return {
            "address": address,
            "chain_id": chain_id,
            "target": dict(trow) if trow else None,
            "vk": dict(vrow) if vrow else None,
            "probes": [dict(p) for p in probes],
            "campaign": dict(camp) if camp else None,
            "findings": [dict(f) for f in findings],
            "inventory": [dict(i) for i in inv],
        }

    def build_campaign_spec(self, address, chain_id=1, corpus_size=64, seed=0x5EED):
        """Build a complete campaign specification for a target."""
        cfg = self.load_target_config(address, chain_id)
        template_id = cfg["target"]["template_id"] if cfg["target"] else "generic_zk"
        tmpl = self.get_template(template_id)

        vk = cfg["vk"]
        n_pub = tmpl["typical_pub_inputs"]
        if vk and vk.get("ic_count"):
            n_pub = max(1, vk["ic_count"] - 1)

        return {
            "address": address,
            "chain_id": chain_id,
            "template": template_id,
            "template_desc": tmpl["description"],
            "proof_system": tmpl["proof_system"],
            "curve": tmpl["curve"],
            "n_pub_inputs": n_pub,
            "corpus_size": corpus_size,
            "seed": seed,
            "vk_hash": vk["vk_hash"] if vk else None,
            "vk_extracted": bool(vk),
            "exploit_classes": list(EXPLOIT_CLASSES.keys()),
            "rpc_url": self.get_chain_rpc(chain_id),
        }

    def validate_config(self, spec):
        """Validate a campaign spec — returns list of issues."""
        issues = []
        if not spec.get("address"):
            issues.append("missing address")
        if not spec.get("rpc_url"):
            issues.append("missing rpc_url")
        if spec.get("corpus_size", 0) < 4:
            issues.append("corpus_size too small (min 4)")
        if spec.get("n_pub_inputs", 0) < 1:
            issues.append("n_pub_inputs must be >= 1")
        return issues

    def list_targets(self, chain_id=None, status=None):
        """List all known targets, optionally filtered."""
        c = db.conn()
        try:
            query = "SELECT * FROM targets WHERE 1=1"
            params = []
            if chain_id:
                query += " AND chain_id=?"
                params.append(chain_id)
            if status:
                query += " AND status=?"
                params.append(status)
            query += " ORDER BY address"
            rows = c.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            c.close()

    def list_campaigns(self, address=None):
        """List recent campaigns, optionally for one address."""
        c = db.conn()
        try:
            if address:
                rows = c.execute(
                    "SELECT * FROM fuzz_campaigns WHERE address=? ORDER BY ts DESC",
                    (address.lower(),)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM fuzz_campaigns ORDER BY ts DESC LIMIT 50"
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            c.close()


# Module-level singleton
config = ZKConfig()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="ZK protocol configuration manager")
    ap.add_argument("--list-targets", action="store_true")
    ap.add_argument("--chain", type=int, default=None)
    ap.add_argument("--status", default=None)
    ap.add_argument("--target", help="show config for address")
    ap.add_argument("--spec", help="build campaign spec for address (JSON)")
    args = ap.parse_args()

    if args.list_targets:
        targets = config.list_targets(chain_id=args.chain, status=args.status)
        for t in targets:
            print(f"  {t['address']}  template={t['template_id']}  sim={t['similarity']}  status={t['status']}")
    elif args.target:
        if args.spec:
            spec = config.build_campaign_spec(args.target)
            print(json.dumps(spec, indent=2, default=str))
        else:
            cfg = config.load_target_config(args.target)
            print(json.dumps(cfg, indent=2, default=str))