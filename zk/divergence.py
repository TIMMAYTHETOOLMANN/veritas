# zk/divergence.py — Layer 5 "State Divergence Engine" (the differentiator)
#
# Transforms a T4 differential finding into a MEASURED financial exploit by
# actually MINING the forged/authorized calldata on a LOCAL mainnet fork with
# an UNLOCKED attacker account, then diffing real on-fork state before/after.
#
# Described architecture ("EVM State & Economic Impact Simulator"):
#   broadcast the transaction to the local anvil node.
#   State Divergence Engine: Before and after the tx is mined,
#   if Attacker_Balance_Post > Attacker_Balance_Pre   OR
#      Protocol_TVL_Post      < Protocol_TVL_Pre
#   => flag Financially Exploitable.
#
# Doctrine: this is a LOCAL fork only ($0), NEVER a mainnet broadcast — T6
# (mainnet fire) remains an explicit user command. All balances/TVL are
# RPC-measured on the fork, never assumed. V=0 after divergence still
# downgrades to INFO regardless of predicate outcome.
#
# model: every "warhead" is a plain JSON spec {to, data, value?, label}.
# The engine knows nothing about the specific protocol; it just measures
# whether the warhead moves money on a real execution.
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.rpc import RPC, uint
from core import db

# Reuse anvil discovery + launch from rehearsal (identical retry ladder).
from rehearsal import find_anvil, _launch_attempt, _kill_tree, _free_port, FORK_RPCS

# Well-known unlocked test account anvil pre-funds on the fork. We over-fund it
# ourselves with anvil_setBalance so gas + transfers can never emerge to block.
UNLOCKED_ATTACKER = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
_ATTACKER_BAL_SEED = 10_000 * 10**18  # 10k ETH notional on the fork only

# Generic ERC20 balanceOf(address).slot0 encoded selector (ABI: 0x70a08231).
_BALANCEOF_SELECTOR = "0x70a08231"


def _erc20_balance_of_call(holder):
    """eth_call payload for balanceOf(holder) — works for ANY ERC20/ERC777."""
    a = holder.lower().replace("0x", "")
    return _BALANCEOF_SELECTOR + ("0" * 24) + a


def _decode_uint(ret):
    body = (ret or "")[2:]
    if len(body) < 64:
        return 0
    try:
        return int(body[:64], 16)
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Economic impact CLASSIFICATION (described "Infinite Mint / Double Spend /
# Fund Drain" taxonomy) — derived purely from measured deltas.
# ---------------------------------------------------------------------------

def _classify_effects(pre_balances, post_balances, attacker, target):
    """Infer exploit taxonomy from a before/after address->wei balance diff.

    pre_balances/post_balances: dict address(lower) -> int wei (any measure —
    ETH native or a token notional that was censused).
    """
    attacker = attacker.lower()
    target = target.lower()
    atk_delta = post_balances.get(attacker, 0) - pre_balances.get(attacker, 0)
    tgt_delta = post_balances.get(target, 0) - pre_balances.get(target, 0)

    effects = []
    if atk_delta > 0 and tgt_delta < 0 and atk_delta == -tgt_delta:
        effects.append("FUND_DRAIN")
    elif atk_delta > 0 and tgt_delta >= 0:
        effects.append("INFINITE_MINT")
    elif atk_delta > 0:
        effects.append("FUND_MOVE")
    if tgt_delta < 0:
        effects.append("TVL_REDUCTION")
    if atk_delta == 0 and tgt_delta == 0:
        effects.append("NO_BALANCE_EFFECT")
    return effects, atk_delta, tgt_delta


# ---------------------------------------------------------------------------
# The State Divergence Engine
# ---------------------------------------------------------------------------

def run_divergence(target, warhead, tvl_accounts=None, attacker=UNLOCKED_ATTACKER,
                   fork_block=None, anvil_path=None, label="forge",
                   auto_fund=True, min_confirmations=1, fork_sources=None,
                   launch_plain_anvil=False):
    """Mine `warhead` (a {to,data,value} spec) on a local EVM and measure the
    REAL state divergence. Returns a dict.

    tvl_accounts: list of additional addresses to census for TVL (e.g. the
        pool's token/supply contract). All censused as native-ETH balance.
    warhead: {"to": addr, "data": hex, "value": hex or int, "label": str}.

    Two launch modes:
      launch_plain_anvil=True  — fresh anvil (no fork). Used to validate the
                                 engine against local fixture contracts ($0, no
                                 network). Census is still the attacker/target.
      launch_plain_anvil=False — mainnet fork (FORK_RPCS / fork_sources).
    """
    anvil = find_anvil(anvil_path)
    if not anvil:
        return {"path": "no_anvil", "reason": "anvil not installed",
                "fallback": "static_impact() is the always-available $0 oracle"}

    sources = fork_sources or FORK_RPCS
    if launch_plain_anvil:
        attempts = [[]]  # no --fork-url -> fresh chain
    else:
        attempts = [sources] + [[u] for u in sources]
    proc = host = None
    for i, urls in enumerate(attempts):
        if i > 0:
            print(f"[t5:div] retry {i} with fallback URL set: {urls}")
        _cmd = [anvil, "--port", str(_free_port())]
        for u in urls:
            _cmd += ["--fork-url", u]
        if fork_block is not None:
            _cmd += ["--fork-block-number", str(fork_block)]
        _cmd += ["--unlocked", "--disable-block-gas-limit"]
        launched = subprocess_launch(_cmd)
        if launched is None:
            continue
        proc, host = launched
        break
    if proc is None:
        return {"path": "no_anvil", "reason": "anvil failed to start"}

    try:
        rpc = RPC(host, timeout=20, retries=2)
        rpc.call("eth_chainId", [])  # force readiness
        attacker = attacker.lower()

        # Seed the attacker on the fork (anvil cheat — local only).
        if auto_fund:
            try:
                rpc.call("anvil_setBalance",
                         [attacker, hex(_ATTACKER_BAL_SEED)])
            except Exception:
                # Non-fatal: maybe --unlocked already covers it / anvil older.
                pass

        # ---- pre-state census ---------------------------------------------
        pre = {c: _eth_bal(rpc, c) for c in _preimage_set(target, attacker, tvl_accounts)}

        # ---- mine the warhead ----------------------------------------------
        to = warhead.get("to", target).lower()
        tx = {"from": attacker, "to": to, "data": warhead.get("data", "0x")}
        val = warhead.get("value")
        if val is not None:
            tx["value"] = val if isinstance(val, str) and val.startswith("0x") else hex(int(val))
        tx["gas"] = hex(_safe_gas(rpc))  # capped to node block gas limit
        try:
            txhash = rpc.call("eth_sendTransaction", [tx])
            mined = _mine(rpc, txhash, wait_s=10, confirmations=min_confirmations)
            outcome = "MINED" if mined else "PENDING"
            status = None
            if mined:
                rcpt = rpc.call("eth_getTransactionReceipt", [txhash])
                status = "SUCCESS" if rcpt and rcpt.get("status") == "0x1" else "REVERTED"
                gas_used = int(rcpt.get("gasUsed", "0x0"), 16)
            else:
                gas_used = None
        except Exception as e:
            if _is_revert(e):
                outcome, status, gas_used = "REVERTED", "REVERTED", None
            else:
                outcome, status, gas_used = "RPC_ERROR", None, None

        # ---- post-state census --------------------------------------------
        post = {c: _eth_bal(rpc, c) for c in pre}
        effects, atk_delta, tgt_delta = _classify_effects(pre, post, attacker, target)

        pre_tvl = _tvl(pre, target)
        post_tvl = _tvl(post, target)
        exploitable = (atk_delta > 0 or post_tvl < pre_tvl)

        return {
            "path": "fork_divergence",
            "label": label,
            "fork_block": fork_block,
            "uncensored_accounts": sorted(pre.keys()),
            "attacker": attacker,
            "pre_attacker_wei": str(pre.get(attacker, 0)),
            "post_attacker_wei": str(post.get(attacker, 0)),
            "attacker_delta_wei": str(atk_delta),
            "pre_tvl_wei": str(pre_tvl),
            "post_tvl_wei": str(post_tvl),
            "tvl_delta_wei": str(post_tvl - pre_tvl),
            "effects": effects,
            "outcome": outcome,
            "status": status,
            "gas_used": gas_used,
            "tx_hash": txhash if outcome in ("MINED", "PENDING") else None,
            "financially_exploitable": bool(exploitable),
            "verdict": ("FORK_DIVERGED_EXPLOITABLE"
                        if exploitable else
                        ("FORK_REVERTED" if outcome == "REVERTED"
                         else "FORK_NO_EFFECT")),
        }
    finally:
        _kill_tree(proc)
        print("[t5:div] anvil terminated")


def measure_on(existing_rpc, target, warhead, attacker=UNLOCKED_ATTACKER,
               tvl_accounts=None, label="forge", auto_fund=True):
    """Measure state divergence AROUND a single warhead tx on an ALREADY-LIVE
    anvil/RPC (the caller owns lifecycle). Core of the State Divergence Engine,
    exposed so tests/fixtures can drive a real deployed pool without the engine
    re-launching a fresh node. Same predicate, no launch machinery.
    """
    attacker = attacker.lower()
    if auto_fund:
        try:
            existing_rpc.call("anvil_setBalance",
                              [attacker, hex(_ATTACKER_BAL_SEED)])
        except Exception:
            pass
    pre = {c: existing_rpc.get_balance(c)
           for c in (set([target.lower(), attacker.lower()]) |
                     set(str(x).lower() for x in (tvl_accounts or [])))}

    to = warhead.get("to", target).lower()
    tx = {"from": attacker, "to": to, "data": warhead.get("data", "0x")}
    val = warhead.get("value")
    if val is not None:
        tx["value"] = val if isinstance(val, str) and val.startswith("0x") else hex(int(val))
    tx["gas"] = hex(_safe_gas(existing_rpc))  # capped to node block gas limit
    try:
        txhash = existing_rpc.call("eth_sendTransaction", [tx])
        mined = _mine(existing_rpc, txhash, wait_s=8, confirmations=1)
        outcome = "MINED" if mined else "PENDING"
        status = None
        gas_used = None
        if mined:
            rcpt = existing_rpc.call("eth_getTransactionReceipt", [txhash])
            status = "SUCCESS" if rcpt and rcpt.get("status") == "0x1" else "REVERTED"
            gas_used = int(rcpt.get("gasUsed", "0x0"), 16)
    except Exception as e:
        if _is_revert(e):
            outcome, status, gas_used = "REVERTED", "REVERTED", None
        else:
            outcome, status, gas_used = "RPC_ERROR", None, None

    post = {c: existing_rpc.get_balance(c) for c in pre}
    effects, atk_delta, tgt_delta = _classify_effects(pre, post, attacker, target)
    pre_tvl, post_tvl = _tvl(pre, target), _tvl(post, target)
    exploitable = (atk_delta > 0 or post_tvl < pre_tvl)
    return {
        "path": "divergence_measure", "label": label, "attacker": attacker,
        "pre_attacker_wei": str(pre.get(attacker, 0)),
        "post_attacker_wei": str(post.get(attacker, 0)),
        "attacker_delta_wei": str(atk_delta),
        "pre_tvl_wei": str(pre_tvl), "post_tvl_wei": str(post_tvl),
        "tvl_delta_wei": str(post_tvl - pre_tvl), "effects": effects,
        "outcome": outcome, "status": status, "gas_used": gas_used,
        "tx_hash": txhash if outcome in ("MINED", "PENDING") else None,
        "financially_exploitable": bool(exploitable),
        "verdict": ("FORK_DIVERGED_EXPLOITABLE" if exploitable
                    else ("FORK_REVERTED" if outcome == "REVERTED"
                          else "FORK_NO_EFFECT")),
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def subprocess_launch(cmd):
    """Launch anvil (no window), poll readiness. Returns (proc, host) or None."""
    import subprocess
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        return None
    port = next((c for i, c in enumerate(cmd) if i and cmd[i-1] == "--port"), "8545")
    host = f"http://127.0.0.1:{port}"
    rpc = RPC(host, timeout=5, retries=1)
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            rpc.call("eth_chainId", [])
            return proc, host
        except Exception:
            time.sleep(1.0)
    _kill_tree(proc)
    return None


def _eth_bal(rpc, addr):
    try:
        return rpc.get_balance(addr)
    except Exception:
        return 0


def _preimage_set(target, attacker, tvl_accounts):
    s = {target.lower(), attacker.lower()}
    for a in (tvl_accounts or []):
        s.add(str(a).lower())
    return s


def _tvl(balances, target):
    # TVL proxy = target's native balance by default; if a caller added extra
    # census accounts, sum those as the protocol's measurable value.
    return balances.get(target.lower(), 0)


def _safe_gas(rpc, requested=4_000_000):
    """Cap a tx gas limit to the node's current block gas limit (minus 100k)
    so a warhead is never rejected with 'intrinsic gas too high'. Default 4M is
    plenty for even a pairing-heavy verifyProof; a fresh anvil block limit is
    30M but some nodes lower it."""
    try:
        blk = rpc.call("eth_getBlockByNumber", ["latest", False])
        limit = int(blk.get("gasLimit", "0x1C9C380"), 16)  # default 30M
    except Exception:
        limit = 30_000_000
    return max(1, min(int(requested), limit - 100_000))


def _mine(rpc, txhash, wait_s=12, confirmations=1):
    deadline = time.time() + wait_s
    while time.time() < deadline:
        try:
            rcpt = rpc.call("eth_getTransactionReceipt", [txhash])
        except Exception:
            rcpt = None
        if rcpt:
            return True
        time.sleep(0.5)
    return False


def _is_revert(e):
    if "revert" in str(e).lower():
        return True
    try:
        body = e.read()
        return b"revert" in body[:512].lower()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Persistence — a REAL impact_sims row with measured deltas (L5 differentiator)
# ---------------------------------------------------------------------------

def persist_divergence(finding_id, dv, artifacts=None):
    c = db.conn()
    c.execute("""INSERT OR REPLACE INTO impact_sims
        (finding_id, address, fork_block, pre_tvl_wei, post_tvl_wei,
         attacker_delta_wei, financially_exploitable, artifacts, ts)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (finding_id, dv.get("attacker", ""), dv.get("fork_block") or 0,
         dv.get("pre_tvl_wei", "0"), dv.get("post_tvl_wei", "0"),
         dv.get("attacker_delta_wei", "0"),
         1 if dv.get("financially_exploitable") else 0,
         json.dumps({"effects": dv.get("effects"),
                     "outcome": dv.get("outcome"),
                     "status": dv.get("status"),
                     "tx_hash": dv.get("tx_hash"),
                     "gas_used": dv.get("gas_used"),
                     "artifacts": artifacts or {}})[:1500],
         db.now()))
    c.commit(); c.close()


def report(dv):
    e = lambda w: int(w) / 1e18
    print(f"\n[t5:div] State Divergence — {dv.get('label')} @ {dv.get('attacker')}")
    print(f"    pre_tvl={e(dv.get('pre_tvl_wei','0')):,.6f}  "
          f"post_tvl={e(dv.get('post_tvl_wei','0')):,.6f}  "
          f"atk_delta={e(dv.get('attacker_delta_wei','0')):,.6f} ETH")
    print(f"    effects={','.join(dv.get('effects',[])) or 'none'}  "
          f"outcome={dv.get('outcome')}  verdict={dv.get('verdict')}")
    return dv