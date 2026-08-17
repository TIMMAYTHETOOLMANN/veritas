# core/scoring.py — invariant -> money path -> EV
# Class ceilings: which value layer the class unlocks if confirmed.
CEILINGS = {
    "caller_supplied_vk": {"layer": "L1", "recipe":
        "deploy own circuit+ceremony -> mint proof for arbitrary note -> withdraw(pool_balance)"},
    "ungated_nullifier": {"layer": "L0", "recipe":
        "observe withdrawal tx -> replay with altered recipient -> repeat per captured nullifier"},
    "zero_root_binding": {"layer": "L0+L3", "recipe":
        "submit any message under zero/stale root -> arbitrary execute/withdraw"},
    "sig_malleability": {"layer": "L0", "recipe":
        "reconstruct (r, n-s) from observed withdrawal sig -> second payout per event"},
    "missing_domain": {"layer": "L1*N", "recipe":
        "replay same proof/sig across N chains where contract is deployed"},
    "weak_threshold": {"layer": "L3", "recipe":
        "forge quorum via compromised/stale guardians -> arbitrary execute()"},
}

def reachable_value(inventory_list, layer_key):
    """Pick the measured value for the layer this class can reach."""
    total = 0
    for inv in inventory_list:
        if layer_key in ("L0+L3", "L1*N"):  # systemic: use max L1 observed
            v = inv.get("balance_wei") or inv.get("eth_wei") or 0
        elif inv.get("layer") == layer_key:
            v = inv.get("balance_wei") or inv.get("eth_wei") or 0
        else:
            continue
        total = max(total, v)
    return total

def score(vclass, inventory_list, p_success=0.9, competition=0.5,
          gas_wei=50_000_000_000_000, confirmed=True):
    """EV in wei. V measured from chain. Unconfirmed or V=0 => not actionable."""
    spec = CEILINGS.get(vclass)
    if not spec:
        return None
    V = reachable_value(inventory_list, spec["layer"])
    conf_mult = 1.0 if confirmed else 0.3   # suspicion discounts hard
    ev = int(p_success * conf_mult * V * (1 - competition)) - gas_wei
    return {"vclass": vclass, "layer": spec["layer"], "recipe": spec["recipe"],
            "value_wei": V, "p_success": p_success, "competition": competition,
            "ev_wei": ev, "actionable": ev > 0 and confirmed,
            "severity": ("CRITICAL" if ev > 10**18 and confirmed else
                          "HIGH" if ev > 10**16 and confirmed else
                          "INFO" if V == 0 else "LOW")}
