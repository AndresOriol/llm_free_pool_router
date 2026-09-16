"""Post-mortem summaries over ledger attempts; no provider calls."""

from collections import Counter, defaultdict

from .windows import day_start, minute_start


def build_diagnostics(calls, pool, now):
    members = {item["provider"]: item for item in (pool or {}).get("pool", [])}
    by_member = defaultdict(Counter)
    first_refusal = {}
    quota_ids = Counter()
    chains = defaultdict(list)
    tpm_refusals = []

    ordered = sorted((call for call in calls if call.get("ts", 0) <= now),
                     key=lambda call: call.get("ts", 0))
    for call in ordered:
        member = members.get(call.get("provider"), {})
        key = (call.get("account") or member.get("account") or "unknown",
               call.get("model") or member.get("model") or "unknown",
               call.get("provider") or "unknown")
        by_member[key][call.get("outcome", "unknown")] += 1
        quota_id = call.get("quota_id")
        if quota_id:
            quota_ids[quota_id] += 1
        if call.get("request_id"):
            chains[call["request_id"]].append(call)
        if call.get("outcome") == "rate_limited":
            platform = call.get("platform") or member.get("platform", "")
            if call.get("ts", 0) >= day_start(platform, now):
                first_refusal.setdefault(key, call.get("ts"))
            if call.get("quota_window") == "tpm":
                start = minute_start(call.get("ts", 0))
                accepted = sum(previous.get("tokens_in", 0) or 0 for previous in ordered
                               if previous.get("provider") == call.get("provider")
                               and previous.get("outcome") == "ok"
                               and start <= previous.get("ts", 0) <= call.get("ts", 0))
                tpm_refusals.append({"provider": key[2], "ts": call.get("ts"),
                                     "accepted_tokens_before": accepted,
                                     "estimated_tokens": call.get("estimated_tokens")})

    affected_by_model = defaultdict(set)
    affected_by_account = defaultdict(set)
    rows = []
    for (account, model, provider), outcomes in sorted(by_member.items()):
        bad = outcomes["rate_limited"] + outcomes["error"]
        if bad:
            affected_by_model[model].add(account)
            affected_by_account[account].add(model)
        rows.append({"account": account, "model": model, "provider": provider,
                     "outcomes": dict(outcomes),
                     "first_refusal": first_refusal.get((account, model, provider))})

    return {
        "by_account_model": rows,
        "quota_ids": dict(quota_ids),
        "failover_chains": [
            {"request_id": request_id,
             "attempts": [{key: call.get(key) for key in
                           ("attempt", "provider", "outcome", "quota_id", "duration_ms")}
                          for call in attempts]}
            for request_id, attempts in chains.items() if len(attempts) > 1
        ],
        "tpm_refusals": tpm_refusals,
        "patterns": {
            "model_wide": {model: sorted(accounts) for model, accounts in
                           affected_by_model.items() if len(accounts) > 1},
            "account_wide": {account: sorted(models) for account, models in
                             affected_by_account.items() if len(models) > 1},
        },
    }
