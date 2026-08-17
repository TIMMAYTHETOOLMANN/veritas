# lineage_run.py — CLI runner over core/lineage.py
# Fetch bytecode for targets -> cluster fork lineage -> localize deltas -> persist.
# Usage:
#   python lineage_run.py --seed      # (re)populate targets w/ reference set; phantoms verified zero-code via RPC
#   python lineage_run.py --fetch     # targets -> eth_getCode -> cache/lineage_codes.json (skip if cached)
#   python lineage_run.py --cluster   # cluster live targets, print + persist to lineage table
#   python lineage_run.py --delta REF FORK   # delta_regions as byte ranges w/ % of code length
#   python lineage_run.py --selftest  # synthetic validation of similarity() + delta_regions()
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import db, lineage
from core.rpc import RPC
from core.config import config

ROOT = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(ROOT, "cache", "lineage_codes.json")
RPC_URL = config.rpc_endpoints[0]  # https://ethereum-rpc.publicnode.com (free/public, read-only)
REFERENCE = "0x12D66f87A04A9E220743712cE6d9bB1B5616B8Fc"  # TC 0.1 ETH pool

# Reference-scan set (mirrors validate.py known TC pools; phantoms are verified 0B via RPC in --seed)
SEED_POOLS = {
    "0x12D66f87A04A9E220743712cE6d9bB1B5616B8Fc": "TC 0.1 ETH",
    "0x47CE0C6eD5B0Ce3d3A51fdb1C52DC66a7c3b29D1": "TC 1 ETH",
}
SEED_PHANTOMS = [
    "0x000000000000000000000000000000000000dEaD",
    "0x0000000000000000000000000000000000000001",
    "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
]


def load_manifest():
    if os.path.exists(MANIFEST):
        with open(MANIFEST) as f:
            return json.load(f)
    return {}


def save_manifest(m):
    os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
    with open(MANIFEST, "w") as f:
        json.dump(m, f, indent=1)


def cmd_seed():
    """Reconstruct the reference scan set in targets. Phantoms are only inserted
    after a live eth_getCode confirms they carry 0 bytes of code."""
    rpc = RPC(RPC_URL)
    db.init()
    c = db.conn()
    for addr, label in SEED_POOLS.items():
        code = rpc.get_code(addr)
        size = len(code) // 2 - 1 if code and code != "0x" else 0
        db.put(c, "INSERT OR REPLACE INTO targets VALUES(?,?,?,?,?,?,?)",
               (addr, "ethereum", size, None, None, None, db.now()))
        print(f"[seed] pool   {label:12s} {addr} code={size}B")
    for addr in SEED_PHANTOMS:
        code = rpc.get_code(addr)
        if code not in ("0x", "", None):
            print(f"[seed] SKIP phantom {addr} has code ({len(code)//2-1}B) — not a phantom")
            continue
        db.put(c, "INSERT OR REPLACE INTO targets VALUES(?,?,?,?,?,?,?)",
               (addr, "ethereum", 0, None, None, None, db.now()))
        print(f"[seed] phantom {addr} code=0B (verified via eth_getCode)")
    c.close()


def cmd_fetch():
    """Fetch bytecode for every targets address into the manifest cache.
    Idempotent: cached addresses are skipped (bytecode is immutable for
    non-proxy contracts). Zero-code phantoms are cached as '' and excluded
    from clustering — fuzzy_hash('') is meaningless."""
    db.init()
    c = db.conn()
    addrs = [r["address"] for r in c.execute("SELECT address FROM targets ORDER BY address").fetchall()]
    c.close()
    if not addrs:
        sys.exit("[fetch] targets table is empty — run `python lineage_run.py --seed` first")
    manifest, rpc = load_manifest(), RPC(RPC_URL)
    fetched = skipped = empty = 0
    for a in addrs:
        if a in manifest:
            skipped += 1
            print(f"[fetch] cached  {a} code={len(manifest[a])//2}B")
            continue
        code = rpc.get_code(a)
        if code in ("0x", "", None):
            manifest[a], empty = "", empty + 1
            print(f"[fetch] PHANTOM {a} code=0B (cached as empty; excluded from clustering)")
        else:
            manifest[a], fetched = code, fetched + 1
            print(f"[fetch] fetched {a} code={len(code)//2-1}B")
    save_manifest(manifest)
    print(f"[fetch] manifest={MANIFEST} fetched={fetched} cached_skip={skipped} phantom_empty={empty}")


def cmd_cluster():
    manifest = load_manifest()
    if not manifest:
        sys.exit("[cluster] manifest empty — run `python lineage_run.py --fetch` first")
    live = {a: h for a, h in manifest.items() if h}   # graceful phantom exclusion
    phantoms = [a for a, h in manifest.items() if not h]
    for a in sorted(phantoms):
        print(f"[cluster] EXCLUDED phantom (0B code): {a}")
    ref_hex = manifest.get(REFERENCE)
    if not ref_hex:
        print(f"[cluster] reference {REFERENCE} not in manifest — clustering without vs_reference")
    out = lineage.cluster(live, reference_hex=ref_hex)

    # deterministic ordering: size desc, then lowest address
    clusters = sorted(out["clusters"], key=lambda ms: (-len(ms), min(ms)))
    def root_of(members):
        if ref_hex and REFERENCE in members:
            return REFERENCE
        return max(members, key=lambda a: (len(manifest[a]), a))  # largest code = closest to template

    db.init()
    c = db.conn()
    print(f"[cluster] {len(clusters)} cluster(s) over {len(live)} live targets (threshold=0.55)")
    for n, members in enumerate(clusters, 1):
        members = sorted(members)
        root = root_of(members)
        tid = f"cluster_{root}"
        print(f"\n  {tid} (cluster #{n})  root={root}  size={len(members)}")
        for a in members:
            dr = lineage.delta_regions(ref_hex, manifest[a]) if ref_hex and a != REFERENCE else []
            pct = f" ({sum(e - s for s, e in dr) / max(1, len(manifest[a]) // 2) * 100:.2f}% of code)" if dr else ""
            db.put(c, "INSERT OR REPLACE INTO lineage VALUES(?,?,?)",
                   (tid, a, json.dumps(dr)))
            print(f"    {a}  delta_regions={len(dr)}{pct}")
    c.close()
    if "vs_reference" in out:
        print("\n  vs_reference (TC 0.1 pool):")
        for a, s in sorted(out["vs_reference"].items(), key=lambda kv: -kv[1]):
            print(f"    {a}  sim={s}")
    print("\n[cluster] memberships persisted to lineage table in veritas.db")


def cmd_delta(ref, fork):
    manifest, rpc = load_manifest(), RPC(RPC_URL)
    def code_of(a):
        if a in manifest and manifest[a]:
            return manifest[a]
        code = rpc.get_code(a)
        if code in ("0x", "", None):
            sys.exit(f"[delta] {a} has no code")
        manifest[a] = code
        save_manifest(manifest)
        return code
    r, f = code_of(ref), code_of(fork)
    deltas = lineage.delta_regions(r, f)
    rl, fl = len(r) // 2 - 1, len(f) // 2 - 1
    print(f"[delta] ref={ref} ({rl}B)  fork={fork} ({fl}B)  size_delta={fl - rl:+d}B")
    total = 0
    for i, (s, e) in enumerate(deltas, 1):
        total += e - s
        print(f"  region {i:2d}: bytes [{s:6d}, {e:6d})  len={e - s:5d}B  "
              f"{(e - s) / max(1, fl) * 100:.2f}% of fork code")
    print(f"[delta] {len(deltas)} region(s), {total}B total, "
          f"{total / max(1, fl) * 100:.2f}% of fork code differs")
    if not deltas:
        print("[delta] bytecode identical")


def cmd_selftest():
    """Synthetic validation: flip 100 bytes of TC 0.1 code at a known offset,
    verify delta_regions() localizes within ±64B and similarity() stays >0.9."""
    manifest = load_manifest()
    code = manifest.get(REFERENCE)
    if not code:
        sys.exit("[selftest] TC 0.1 bytecode not in manifest — run --fetch first")
    body = code[2:] if code.startswith("0x") else code
    nbytes = len(body) // 2
    OFF, N = 1000, 100
    ba = bytearray(bytes.fromhex(body))
    for i in range(OFF, OFF + N):
        ba[i] ^= 0xFF                      # guaranteed per-byte difference
    fork = "0x" + bytes(ba).hex()
    ref_hex, fork_hex = code, fork

    sim = lineage.similarity(lineage.fuzzy_hash(ref_hex), lineage.fuzzy_hash(fork_hex))
    deltas = lineage.delta_regions(ref_hex, fork_hex)
    print(f"[selftest] TC 0.1 code={nbytes}B; flipped {N} bytes at offset {OFF} (XOR 0xFF)")
    hit = False
    for s, e in deltas:
        covers = s <= OFF and e >= OFF + N
        loc_ok = abs(s - OFF) <= 64 and abs(e - (OFF + N)) <= 64
        print(f"  region bytes [{s}, {e}) len={e - s}B ({(e - s) / nbytes * 100:.2f}% of code)  "
              f"start_err={s - OFF:+d}  end_err={e - (OFF + N):+d}  "
              f"covers_flipped={covers} within_±64B={loc_ok}")
        if covers:
            hit = loc_ok
    chunks = len(lineage.fuzzy_hash(ref_hex).split(":"))
    print(f"[selftest] similarity={sim:.4f} (chunks={chunks}, ~3 corrupted by 100B flip)")
    ok_sim, ok_delta = sim > 0.9, hit
    print(f"[selftest] similarity>0.9: {'PASS' if ok_sim else 'FAIL'} | "
          f"delta localized ±64B: {'PASS' if ok_delta else 'FAIL'}")
    if not (ok_sim and ok_delta):
        sys.exit(1)


def main():
    p = argparse.ArgumentParser(description="VERITAS lineage clustering runner")
    p.add_argument("--seed", action="store_true", help="seed targets with reference set")
    p.add_argument("--fetch", action="store_true", help="fetch bytecode into manifest cache")
    p.add_argument("--cluster", action="store_true", help="cluster targets, persist lineage")
    p.add_argument("--selftest", action="store_true", help="synthetic similarity/delta validation")
    p.add_argument("--delta", nargs=2, metavar=("REF", "FORK"), help="delta regions REF vs FORK")
    a = p.parse_args()
    if a.seed:
        cmd_seed()
    if a.fetch:
        cmd_fetch()
    if a.cluster:
        cmd_cluster()
    if a.selftest:
        cmd_selftest()
    if a.delta:
        cmd_delta(a.delta[0], a.delta[1])
    if not any((a.seed, a.fetch, a.cluster, a.selftest, a.delta)):
        p.print_help()


if __name__ == "__main__":
    main()
