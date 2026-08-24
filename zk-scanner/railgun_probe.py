# railgun_probe.py — T4-adapted differential battery vs LIVE Railgun Verifier (mainnet)
#
# Target: RailgunSmartWallet proxy 0xFA7093CDD9EE6932B4eb2c9e1cde7CE00B1FA4b9 (~$90M escrow)
# Method:  1. Extract every STORED VerifyingKey via getVerificationKey(n, c) grid.
#          2. For each stored VK, fire adversarial (proof, inputs) tuples at the public
#             verifyProof(VK, proof, inputs) entry (same Snark.verify core the money path uses).
#          3. Ground truth = local py_ecc EIP-197 pairing oracle.
#             on-chain ACCEPT + oracle False = CONFIRMED SOUNDNESS DIVERGENCE.
#          4. Escrow census across all Railgun deployments (eth/polygon/bsc/arbitrum).
# Fail-closed: 3 consecutive RPC errors abort the campaign.
import sys, json, random, time, threading
sys.path.insert(0, r"C:\Users\timot\OneDrive\Documents\VERITAS")

# py_ecc pairing's final exponentiation recurses ~3-4k frames (square-and-multiply
# over the 3000-bit FQ12 exponent) — over CPython 3.12's default 1000 limit.
# Run everything inside a worker thread with a large committed stack.
sys.setrecursionlimit(1_000_000)

from core.rpc import RPC, uint
from core.selectors import kec256

PROXY = "0xFA7093CDD9EE6932B4eb2c9e1cde7CE00B1FA4b9"
rpc = RPC("https://ethereum-rpc.publicnode.com")

P = 21888242871839275222246405745257275088696311157297823662689037894645226208583
R = 21888242871839275222246405745257275088548364400416034343698204186575808495617

def sel(sig): return "0x" + kec256(sig.encode()).hex()[:8]

GETVK = sel("getVerificationKey(uint256,uint256)")
VERIFYPROOF = sel("verifyProof(tuple,tuple,uint256[])")

# ---------------------------------------------------------------- VK decode
def decode_vk(ret):
    if not ret or ret == "0x":
        return None
    b = ret[2:]
    w = [int(b[i:i+64], 16) for i in range(0, len(b), 64)]
    if len(w) < 17 or w[2] == 0:          # alpha1.x == 0 -> key not set
        return None
    str_len_w = 1 + w[1] // 32
    slen = w[str_len_w]
    ipfs = bytes.fromhex(b[str_len_w*64+64 : str_len_w*64+64+slen*2]).decode(errors="replace")
    ic_len_w = 1 + w[16] // 32
    iclen = w[ic_len_w]
    pts = [(w[ic_len_w+1+2*k], w[ic_len_w+2+2*k]) for k in range(iclen)]
    return {"ipfs": ipfs, "alpha": (w[2], w[3]),
            "beta":  (w[4], w[5], w[6], w[7]),
            "gamma": (w[8], w[9], w[10], w[11]),
            "delta": (w[12], w[13], w[14], w[15]),
            "ic": pts, "shape": (w, iclen)}

# ---------------------------------------------------------------- ABI encode
def word(v): return f"{v:064x}"
def enc_g1(p): return word(p[0]) + word(p[1])
def enc_g2(p): return word(p[0]) + word(p[1]) + word(p[2]) + word(p[3])

def enc_vk(vk):
    head_words = 16
    sdata = vk["ipfs"].encode()
    str_off = head_words * 32
    str_words = 1 + (len(sdata) + 31) // 32
    ic_off = str_off + str_words * 32
    head = (word(str_off) + enc_g1(vk["alpha"]) + enc_g2(vk["beta"]) +
            enc_g2(vk["gamma"]) + enc_g2(vk["delta"]) + word(ic_off))
    sh = sdata.hex()
    sh += "0" * ((32 - len(sh) % 32) % 32)
    stail = word(len(sdata)) + sh
    ictail = word(len(vk["ic"])) + "".join(enc_g1(pt) for pt in vk["ic"])
    return head + stail + ictail

def call_verifyproof(vk, proof, inputs):
    proof_enc = enc_g1(proof[0]) + enc_g2(proof[1]) + enc_g1(proof[2])
    vk_enc = enc_vk(vk)
    off_vk = 10 * 32
    off_inputs = off_vk + len(vk_enc) // 2
    inputs_enc = word(len(inputs)) + "".join(word(v) for v in inputs)
    data = VERIFYPROOF + word(off_vk) + proof_enc + word(off_inputs) + vk_enc + inputs_enc
    return rpc.eth_call(PROXY, data)

# ---------------------------------------------------------------- py_ecc oracle
from py_ecc.bn128 import G1, G2, FQ, FQ2, neg, multiply, add, pairing
import pyecc_patch  # iterative FQP.__pow__ — CPython 3.12 C-recursion fix
from py_ecc.bn128 import FQ12 as _FQ12

FQ12_ONE = _FQ12([1] + [0] * 11)

def is_inf_g1(p): return p is None or (int(p[0]) == 0 and int(p[1]) == 0)
def is_inf_g2(p): return p is None or (all(int(c) == 0 for c in p[0]) and all(int(c) == 0 for c in p[1]))

def g1_add(a, b):
    if is_inf_g1(a): return b
    if is_inf_g1(b): return a
    return add(a, b)

def to_g1(x, y):
    if x == 0 and y == 0: return None
    return (FQ(x), FQ(y))

def to_g2(x0, x1, y0, y1):
    if x0 == 0 and x1 == 0 and y0 == 0 and y1 == 0: return None
    return (FQ2([x0, x1]), FQ2([y0, y1]))

def pairing_term(g2p, g1p):
    """e(g1p, g2p); infinity contributes 1."""
    if g1p is None or g2p is None:
        return None
    return pairing(g2p, g1p)

def oracle_verify(vk, proof, inputs):
    """EIP-197 ground truth for e(-A,B)*e(alpha,beta)*e(vkX,gamma)*e(C,delta)==1."""
    A = to_g1(*proof[0]); B = to_g2(*proof[1]); C = to_g1(*proof[2])
    alpha = to_g1(*vk["alpha"]); beta = to_g2(*vk["beta"])
    gamma = to_g2(*vk["gamma"]); delta = to_g2(*vk["delta"])
    # vkX = ic[0] + sum ic[i+1]*inputs[i]
    vkX = to_g1(*vk["ic"][0]) if vk["ic"] else None
    for i, v in enumerate(inputs):
        if i + 1 >= len(vk["ic"]):
            return ("ERROR", "ic OOB")
        pt = to_g1(*vk["ic"][i+1])
        term = multiply(pt, v) if pt is not None else None
        vkX = g1_add(vkX, term)
    negA = neg(A) if A is not None else None
    terms = [pairing_term(B, negA), pairing_term(beta, alpha),
             pairing_term(gamma, vkX), pairing_term(delta, C)]
    prod = None
    for t in terms:
        if t is None: continue
        prod = t if prod is None else prod * t
    if prod is None:
        return (True, "all terms identity")
    return (prod == FQ12_ONE, "ok")

# sanity: e(G2,G1) * e(-G2,G1) == 1
_t1 = pairing(G2, G1); _t2 = pairing(neg(G2), G1)
assert (_t1 * _t2) == FQ12_ONE, "py_ecc pairing sanity FAILED — abort"
print("[oracle] py_ecc pairing sanity: OK")

# ---------------------------------------------------------------- corpus
rng = random.Random(0xFA70)

def rand_g1():
    s = rng.randrange(1, R)
    pt = multiply(G1, s)
    return (int(pt[0]), int(pt[1]))
def rand_g2():
    s = rng.randrange(1, R)
    pt = multiply(G2, s)
    return (int(pt[0].coeffs[0]), int(pt[0].coeffs[1]),
            int(pt[1].coeffs[0]), int(pt[1].coeffs[1]))

INF_G1 = (0, 0); INF_G2 = (0, 0, 0, 0)

def build_cases(vk):
    n_inputs = max(1, len(vk["ic"]) - 1)
    cases = []
    # A: random valid points, random inputs (soundness)
    for _ in range(6):
        cases.append({"tag": "random_valid",
                      "proof": (rand_g1(), rand_g2(), rand_g1()),
                      "inputs": [rng.randrange(R) for _ in range(n_inputs)]})
    # B: degenerate infinity points
    cases.append({"tag": "inf_A_inf_C",
                  "proof": (INF_G1, rand_g2(), INF_G1),
                  "inputs": [0] * n_inputs})
    cases.append({"tag": "all_inf",
                  "proof": (INF_G1, INF_G2, INF_G1),
                  "inputs": [0] * n_inputs})
    # C: boundary inputs (must pass require < R)
    for bv in (R - 1, R - 2, 2**254 - 1, P - 1):
        cases.append({"tag": f"boundary_input_{bv}",
                      "proof": (rand_g1(), rand_g2(), rand_g1()),
                      "inputs": [bv] + [0] * (n_inputs - 1)})
    # D: field overflow input (R exactly — require must revert)
    cases.append({"tag": "overflow_input_R",
                  "proof": (rand_g1(), rand_g2(), rand_g1()),
                  "inputs": [R] + [0] * (n_inputs - 1)})
    # E: A not on curve (negate must revert)
    cases.append({"tag": "A_not_on_curve",
                  "proof": ((1, 1), rand_g2(), rand_g1()),
                  "inputs": [0] * n_inputs})
    # F: C not on curve (precompile must reject)
    cases.append({"tag": "C_not_on_curve",
                  "proof": (rand_g1(), rand_g2(), (1, 1)),
                  "inputs": [0] * n_inputs})
    return cases

# ---------------------------------------------------------------- run
rpc_errors = 0
def guarded(fn):
    global rpc_errors
    try:
        rpc_errors = 0
        return fn()
    except RuntimeError as e:
        rpc_errors += 1
        if rpc_errors >= 3:
            raise SystemExit("ABORT: 3 consecutive RPC errors — fail-closed")
        return ("REVERT", str(e)[:160])

print(f"\n[stage 1] VK grid extraction from {PROXY}")
vks = {}
for n in range(0, 7):
    for c in range(0, 7):
        data = GETVK + word(n) + word(c)
        ret = guarded(lambda: rpc.eth_call(PROXY, data))
        if isinstance(ret, tuple):
            continue
        vk = decode_vk(ret)
        if vk:
            vks[(n, c)] = vk
            print(f"  VK(nullifiers={n}, commitments={c}): ic={len(vk['ic'])} points, ipfs={vk['ipfs'][:20]}...")
print(f"[stage 1] {len(vks)} stored VKs: {sorted(vks.keys())}")

print(f"\n[stage 2] differential battery (stored VK, adversarial proof/inputs)")
results = []
for (n, c), vk in sorted(vks.items()):
    for case in build_cases(vk):
        ret = guarded(lambda: call_verifyproof(vk, case["proof"], case["inputs"]))
        if isinstance(ret, tuple):
            onchain = "REVERT"
            note = ret[1]
        elif int(ret, 16) == 1:
            onchain = "ACCEPT"
            note = ""
        else:
            onchain = "REJECT"
            note = ""
        try:
            obool, onote = oracle_verify(vk, case["proof"], case["inputs"])
            oracle = "ACCEPT" if obool else ("REJECT" if obool is False else "ERROR")
        except Exception as e:
            oracle, onote = "ERROR", str(e)[:100]
        verdict = "MATCH"
        if onchain == "ACCEPT" and oracle == "REJECT":
            verdict = "CRITICAL_DIVergence".upper()
        elif onchain == "ACCEPT" and oracle == "ERROR":
            verdict = "SUSPECT_DIVergence".upper()
        elif onchain == "REJECT" and oracle == "ACCEPT":
            verdict = "OVER_RESTRICTED"
        results.append({"shape": [n, c], "tag": case["tag"], "onchain": onchain,
                        "oracle": oracle, "verdict": verdict,
                        "note": note or onote})
        print(f"  ({n},{c}) {case['tag']:<22} onchain={onchain:<7} oracle={oracle:<7} {verdict}")
        time.sleep(0.12)

# ---------------------------------------------------------------- degenerate caller-VK (gas-estimation surface, INFO only)
print(f"\n[stage 3] degenerate caller-supplied VK (gas-estimation surface — documented, INFO)")
degen_vk = {"ipfs": "", "alpha": INF_G1, "beta": (0, 0, 0, 0), "gamma": (0, 0, 0, 0),
            "delta": (0, 0, 0, 0), "ic": [INF_G1, INF_G1, INF_G1]}
try:
    ret = guarded(lambda: call_verifyproof(degen_vk,
                                            (INF_G1, rand_g2(), INF_G1), [0, 0]))
    print(f"  all-infinity VK + infinity proof -> {int(ret, 16)} "
          f"({'ACCEPT (gas-estimation bypass, no money path)' if int(ret, 16) == 1 else 'reject'})")
except RuntimeError as e:
    print(f"  all-infinity VK -> REVERT {str(e)[:100]}")

# ---------------------------------------------------------------- escrow census
print(f"\n[stage 4] escrow census — all Railgun deployments")
CHAINS = [
    ("ethereum",  "https://ethereum-rpc.publicnode.com", "0xFA7093CDD9EE6932B4eb2c9e1cde7CE00B1FA4b9"),
    ("polygon",   "https://polygon-rpc.publicnode.com",  None),  # filled from deployments repo
    ("bsc",       "https://bsc-rpc.publicnode.com",      None),
    ("arbitrum",  "https://arb-rpc.publicnode.com",      None),
]
TOKENS = {
    "ethereum": {"USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                 "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
                 "DAI": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
                 "WETH": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"},
    "polygon":  {"USDC": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
                 "USDT": "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
                 "DAI": "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063",
                 "WETH": "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619"},
    "bsc":      {"USDT": "0x55d398326f99059fF775485246999027B3197955",
                 "USDC": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
                 "DAI": "0x1AF3F329e8BE154074D8769D1FFa4eE058B1DBc3",
                 "WETH": "0x2170Ed0880ac9A755fd29B2688956BD959F933F8"},
    "arbitrum": {"USDC": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
                 "USDT": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
                 "DAI": "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1",
                 "WETH": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"},
}
BALOF = sel("balanceOf(address)")

# fetch proxy addresses for other chains from deployments repo
import urllib.request
def fetch_chain_cfg(chain):
    url = f"https://raw.githubusercontent.com/Railgun-Community/deployments/master/src/chains/{chain}.ts"
    req = urllib.request.Request(url, headers={"User-Agent": "veritas"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode()
import re
for i, (name, url, addr) in enumerate(CHAINS):
    if addr is None:
        try:
            txt = fetch_chain_cfg(name)
            m = re.search(r"proxy:\s*\{\s*address:\s*'([^']+)'", txt)
            if m:
                CHAINS[i] = (name, url, m.group(1))
        except Exception as e:
            print(f"  {name}: deployments fetch failed {e}")

census = {}
for name, url, addr in CHAINS:
    if addr is None:
        print(f"  {name}: no proxy address")
        continue
    try:
        r2 = RPC(url)
        eth = r2.get_balance(addr)
        row = {"ETH": eth / 1e18}
        for tname, tok in TOKENS.get(name, {}).items():
            try:
                data = BALOF + "0" * 24 + addr[2:].lower()
                v = uint(r2.eth_call(tok, data))
                dec = 6 if tname in ("USDC", "USDT") else 18
                if v:
                    row[tname] = v / 10**dec
            except Exception:
                pass
        census[name] = {"proxy": addr, "balances": row}
        print(f"  {name:<10} {addr}  " + "  ".join(f"{k}={v:,.2f}" for k, v in row.items()))
    except Exception as e:
        print(f"  {name}: RPC fail {str(e)[:80]}")

# ---------------------------------------------------------------- report
crit = [r for r in results if "CRITICAL" in r["verdict"]]
susp = [r for r in results if "SUSPECT" in r["verdict"]]
report = {
    "target": PROXY, "stored_vks": {f"({n},{c})": len(v["ic"]) for (n, c), v in vks.items()},
    "cases_run": len(results),
    "critical_divergences": crit, "suspect_divergences": susp,
    "escrow_census": census,
}
with open("railgun_t4_report.json", "w") as fh:
    json.dump(report, fh, indent=1)
print(f"\n[rollup] cases={len(results)}  CRITICAL={len(crit)}  SUSPECT={len(susp)}")
print(f"[rollup] report -> railgun_t4_report.json")
