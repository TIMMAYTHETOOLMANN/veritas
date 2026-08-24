# quick validation: regenerated PoC must COMPILE under solc 0.8.20.
# Ground truth for checksum correctness = solc accepts the literal (it errors
# on any bad checksum), so we don't hand-assert a checksum string.
import glob, os, solcx, sys
sys.path.insert(0, r"C:/Users/timot/OneDrive/Documents/VERITAS")
os.chdir(r"C:/Users/timot/OneDrive/Documents/VERITAS")
from zk import pocgen

try:
    solcx.install_solc("0.8.20")
except Exception:
    pass

for f in glob.glob("artifacts/pocs/*.sol"):
    os.remove(f)

p = pocgen.generate_poc(
    "0x47ce0c6ed5b0ce3d3a51fdb1c52dc66a7c3c2936",
    label="zk_ZK-FIELD-OVERFLOW_0x47ce0c6e",
    calldata="0x2c4a6b93" + "11" * 32 + "22" * 32 * 4 + "33" * 32,
    attacker="0xf39fd6e51aad88f6f4ce6ab8827279cffb92266",
    taxonomy="FUND_DRAIN",
    pre_tvl="3947000000000000000000",
    atk_delta="3947000000000000000000",
)
print("written:", p)

out = solcx.compile_files([p], solc_version="0.8.20",
                          output_values=["bin"], allow_paths=".")
ok = [k.split(":")[-1] for k, v in out.items()
      if v and str(v.get("bin", "0x")) not in ("0x", "", "0x00")]
print("COMPILED:", ok)
assert ok, "PoC did not compile — checksum or syntax regression"
print("POC COMPILES OK — buildable artifact confirmed")