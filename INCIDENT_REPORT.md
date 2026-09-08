# VERITAS — Secret Exposure Incident Report

**Status:** ACTIVE — remediation in progress
**Detected:** 2026-09-07T07:33:56Z (agent-assisted security audit)
**Repository:** https://github.com/TIMMAYTHETOOLMANN/veritas (PUBLIC)
**Host:** LUCY

## 1. Summary
Commit `442b1bf` ("100") committed live secret material to the public GitHub
repository and was pushed to `origin/master`. Anyone who cloned/forked the repo
before remediation holds these secrets. Fund exposure is LOW (see §4), but the
Hyperliquid API credentials and one funded private key are technically compromised.

## 2. Affected assets & secrets
| file                | type                      | content hint             | risk |
|---------------------|---------------------------|--------------------------|------|
| .fresh_private_key  | 64-byte hex private key   | `ad2af411...`            | HIGH (key compromised) |
| .fresh_address      | ETH address               | `0x3B488AA2...`          | MED (address for key) |
| .hl_api_key         | Hyperliquid api key       | `0x3c1A5A8E...`          | MED |
| .hl_api_secret      | Hyperliquid L1 api secret | `0xa8b85198...`          | HIGH (can sign HL txns) |
| .hl_master_address  | hot wallet address        | `0x1a0d4679...`          | LOW (public already) |

Note: `.hot_secret` and `.hl_secret` are currently gitignored (not in history); the
above five files ARE in public history.

## 3. Fund exposure (read-only check, 2026-09-07)
| address                                | native ETH        | USDC / WETH |
|----------------------------------------|-------------------|-------------|
| 0x3B488AA2364B35feBF1947A61056f17C3EF35803 (fresh) | 0 ETH | 0 |
| 0x1a0d467974E70e3c1a2b7b84Fec21183Fc4eB60f (hot)    | ~0.000821 ETH     | 0 |

→ No material funds at risk on Arbitrum at detection time. Still rotate: the
Hyperliquid API secret may have off-Arbitrum (HL native / perp) authority.

## 4. Forensic evidence (pre-rewrite snapshot)
- artifact: archive/forensics/veritas-full-bundle-20260907T073729Z.bundle (277 MB)
- sha256:   3a4c41c22805440ecb03f1f624c5df2a2a00a8625adc1e5311b9a77677abe76a
- chain-of-custody: archive/forensics/chain-of-custody.md

## 5. Remediation status
- [x] Forensic git bundle + sha256 captured
- [x] Chain-of-custody log created
- [x] Read-only balance check performed
- [x] New operational wallet generated (0x842ea0d9... — NOT committed)
- [x] git-filter-repo tooling staged (tools/git-filter-repo)
- [x] Purge script written (tools/purge_secrets.sh) — REVIEWED, not yet run
- [ ] Rotate Hyperliquid API credentials
- [ ] Move any funds out of 0x3B488AA2... / 0x1a0d4679...
- [ ] RUN tools/purge_secrets.sh (history rewrite)
- [ ] Force-push rewritten history (coordinate team first)
- [ ] Add pre-commit secrets scan (detect-secrets / truffleHog)
- [ ] Move secrets to env vars / secret manager; stop committing key files
- [ ] Notify exchanges/custodians/insurer as applicable

## 6. Purge commands (REVIEW before running)
```bash
# 1. Fresh mirror + rewrite history (strips secret files from all commits)
git clone --mirror https://github.com/TIMMAYTHETOOLMANN/veritas.git veritas-mirror.git
cd veritas-mirror.git
python3 tools/git-filter-repo --invert-paths \
    --path .fresh_private_key --path .fresh_address \
    --path .hl_api_key --path .hl_api_secret --path .hl_master_address \
    --path .hot_secret --path .hl_secret

# 2. Verify gone
git log --all --oneline -- .fresh_private_key .hl_api_key .hl_api_secret

# 3. Force-push (coordinate team first — breaks forks/clones)
git push --force --tags origin 'refs/heads/*'
```

## 7. Collaborator guidance after rewrite
- Backup local branches (`git format-patch`), then `git clone` fresh from origin.
- Do NOT reuse the old secret values.
- Do NOT force-push old local clones.

## 8. New operational wallet (generated during remediation)
- address: 0x842ea0d938f3de251aeEDa7FA3397f9fa11fE2E3
- This key is NOT committed anywhere; treat as minimal-balance operational hot key.
- For reserve funds, use a hardware wallet / multisig.