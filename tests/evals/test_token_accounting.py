from evals.metrics import from_trace


def test_router_usage_does_not_double_provider_tokens():
    # Two concurrent calls: the wrapper returns the same usage as its provider.
    events = []
    for i, count in enumerate((100, 200)):
        events.extend([
            {"event": "llm_start", "run_id": f"r{i}", "model": "RouterChatModel"},
            {"event": "llm_start", "run_id": f"p{i}", "model": "gemini"},
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
