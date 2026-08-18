# Purge test-fixture pollution from veritas.db.
# The 20 findings id 7-26 + their exploitability/impact_sims/probes/fuzz_campaigns
# all reference 0x5fbdb231... = the deterministic fresh-anvil VerifierVulnerable
# fixture deployed during test harness runs. It is a LOCAL TEST, not a live
# mainnet contract; per doctrine V must be MEASURED from chain, never assumed —
# its ~45.4 ETH "TVL" is fabricated anvil balance. Keeping it would report a
# false financially-actionable on a nonexistent target.
import sqlite3

FIXTURE = "0x5fbdb2315678afecb367f032d93f642f64180aa3"

c = sqlite3.connect("veritas.db")
c.row_factory = sqlite3.Row

# 1. finding ids that reference the fixture
fids = [r["id"] for r in c.execute(
    "SELECT id FROM findings WHERE address=? AND confidence='differential_confirmed'",
    (FIXTURE,)).fetchall()]
print("purging fixture finding ids:", fids)

# 2. delete dependent rows first (FK conceptual order)
for t in ["impact_sims", "exploitability"]:
    ph = ",".join("?" * len(fids))
    n = c.execute(f"DELETE FROM {t} WHERE finding_id IN ({ph})", fids).rowcount
    print(f"  {t}: deleted {n}")

# 3. delete the findings
n = c.execute(
    "DELETE FROM findings WHERE address=? AND confidence='differential_confirmed'",
    (FIXTURE,)).rowcount
print(f"  findings: deleted {n}")

# 4. purge fixture probes + campaigns
n = c.execute("DELETE FROM probes WHERE address=? AND battery='t4_differential'",
              (FIXTURE,)).rowcount
print(f"  probes(t4_differential): deleted {n}")
n = c.execute("DELETE FROM fuzz_campaigns WHERE address=?", (FIXTURE,)).rowcount
print(f"  fuzz_campaigns: deleted {n}")

c.commit()

print("\n=== after-purge state ===")
for t in ["findings", "exploitability", "impact_sims", "probes", "fuzz_campaigns"]:
    print(f"  {t}: {c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
print("=== remaining findings ===")
for r in c.execute("SELECT id,address,vclass,tier,confidence,status FROM findings ORDER BY id"):
    print(" ", r["id"], r["vclass"], r["confidence"], r["status"], r["address"][:14])
c.close()