from evals.metrics import from_trace


def test_router_usage_does_not_double_provider_tokens():
    # Two concurrent calls: the wrapper returns the same usage as its provider.
    events = []
    for i, count in enumerate((100, 200)):
        events.extend([
            {"event": "llm_start", "run_id": f"r{i}", "model": "RouterChatModel",
             "parent_run_id": f"node{i}"},
            {"event": "llm_start", "run_id": f"p{i}", "model": "gemini",
             "parent_run_id": f"node{i}" if i == 0 else f"r{i}"},
            {"event": "llm_end", "run_id": f"p{i}", "tokens_in": count,
             "tokens_out": 10},
            {"event": "llm_end", "run_id": f"r{i}", "tokens_in": count,
             "tokens_out": 10},
        ])
    result = from_trace(events)
    assert result["provider_calls"] == 2
    assert result["tokens_in"] == 300
    assert result["tokens_out"] == 20
    assert result["tokens_per_call"] == 150


def test_wrapper_only_usage_survives_even_alongside_an_unrelated_provider():
    events = [
        {"event": "llm_start", "run_id": "legacy", "model": "RouterChatModel"},
        {"event": "llm_end", "run_id": "legacy", "tokens_in": 80, "tokens_out": 5},
        {"event": "llm_start", "run_id": "other", "model": "gemini"},
        {"event": "llm_end", "run_id": "other", "tokens_in": 60, "tokens_out": 7},
    ]
    assert from_trace(events[:2])["tokens_in"] == 80
    result = from_trace(events)
    assert (result["tokens_in"], result["tokens_out"]) == (140, 12)
