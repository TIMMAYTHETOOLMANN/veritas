# core/lineage.py — fork-lineage clustering via rolling fuzzy hash of bytecode
# Groups forked deployments, localizes delta regions vs. template reference.
# Pure Python (no ssdeep dep): 7-byte window rolling hash over nibble-normalized code.

WIN = 7

def fuzzy_hash(code_hex: str, block=64) -> str:
    """Rolling-hash chunk signature. Normalizes PUSH-data noise lightly by
    hashing raw byte stream; chunks emit base36 tokens joined by ':'."""
    code = bytes.fromhex(code_hex[2:] if code_hex.startswith("0x") else code_hex)
    parts, h, count = [], 0, 0
    for b in code:
        h = (h * 31 + b) & 0xFFFFFFFF
        count += 1
        if count >= block:
            parts.append(_b36(h)); h, count = 0, 0
    if count:
        parts.append(_b36(h))
    return ":".join(parts)

def _b36(n):
    alpha = "0123456789abcdefghijklmnopqrstuvwxyz"
    s = ""
    while n:
        s = alpha[n % 36] + s; n //= 36
    return s or "0"

def similarity(h1: str, h2: str) -> float:
    """Jaccard over chunk-token multisets — robust to insertions/deletions."""
    from collections import Counter
    a, b = Counter(h1.split(":")), Counter(h2.split(":"))
    inter = sum((a & b).values())
    union = sum((a | b).values())
    return inter / union if union else 0.0

def cluster(targets: dict, reference_hex: str = None, threshold=0.55):
    """targets: {addr: code_hex}. Returns clusters + per-target best-match sim
    vs. reference when provided."""
    ref = fuzzy_hash(reference_hex) if reference_hex else None
    hashes = {a: fuzzy_hash(c) for a, c in targets.items()}
    # union-find clustering
    parent = {a: a for a in targets}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    addrs = list(targets)
    for i, a in enumerate(addrs):
        for b in addrs[i+1:]:
            if similarity(hashes[a], hashes[b]) >= threshold:
                parent[find(a)] = find(b)
    clusters = {}
    for a in addrs:
        clusters.setdefault(find(a), []).append(a)
    out = {"clusters": list(clusters.values())}
    if ref:
        out["vs_reference"] = {a: round(similarity(hashes[a], ref), 3) for a in addrs}
    return out

def delta_regions(ref_hex: str, fork_hex: str, window=48) -> list:
    """Locate differing byte regions between reference and fork bytecode —
    pinpoints WHERE a fork's modification introduced divergence."""
    r = ref_hex[2:] if ref_hex.startswith("0x") else ref_hex
    f = fork_hex[2:] if fork_hex.startswith("0x") else fork_hex
    deltas, i = [], 0
    n = min(len(r), len(f))
    while i < n:
        if r[i] != f[i]:
            j = i
            while j < n and not (r[j] == f[j] and r[j:j+window] == f[j:j+window]):
                j += 1
            deltas.append((i // 2, j // 2))  # byte offsets
            i = j
        else:
            i += 1
    if len(r) != len(f):
        deltas.append((n // 2, max(len(r), len(f)) // 2))
    return deltas
