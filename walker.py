# walker.py — harness: init → walk → persist → report
import sys, json, traceback
sys.path.insert(0, r"C:\Users\timot\OneDrive\Documents\VERITAS")
from core import walker
from core import config

if __name__ == "__main__":
    print("[walker] initializing fortified walker...")
    totals = walker.walk_all()
    print(f"[walker] completed: {totals}")
    pool = walker.report()
    print(f"[walker] candidate pool: {len(pool)} targets")
    for p in pool[:10]:
        print(f"  {p['address'][:12]}... {p['chain']} sim={p['similarity']} "
              f"t={p['template_id']} denom={p['denom']} code={p['code_size']}")
    if len(pool) > 10:
        print(f"  ... +{len(pool)-10} more (see DB)")
    print("[walker] fortified walker complete")
