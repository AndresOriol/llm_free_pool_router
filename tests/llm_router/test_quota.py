"""Checks for the quota report: the windows, when they reset, how a refused
attempt is counted, and that two accounts never share either.

No framework: `python -m tests.llm_router.test_quota` (or run the file).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from llm_router.quota.report import DAY_SECONDS, MINUTE_SECONDS, build_report

NOW = 1_800_000_000

GROQ_LIMITS = {"rpm": 30, "tpm": 8000, "rpd": 1000, "tpd": 100000}

POOL = {
    "generated": NOW,
    "config": "llm_router/config.yaml",
    "pool": [
        {"provider": "GptOss120b_groq_1", "account": "groq_1", "platform": "groq",
         "model": "openai/gpt-oss-120b", "priority": 2, "max_input_tokens": 8000,
         "limits": GROQ_LIMITS},
        # The same model on a second account: one model, two budgets.
        {"provider": "GptOss120b_groq_2", "account": "groq_2", "platform": "groq",
         "model": "openai/gpt-oss-120b", "priority": 2, "max_input_tokens": 8000,
         "limits": GROQ_LIMITS},
        {"provider": "Gemma4_31b_gemini_1", "account": "gemini_1", "platform": "gemini",
         "model": "gemma-4-31b-it", "priority": 31, "max_input_tokens": 128000,
         "limits": {"rpm": 15, "rpd": 1500}},
    ],
}


def call(**overrides):
    base = {"ts": NOW - 10, "provider": "GptOss120b_groq_1", "account": "groq_1",
            "platform": "groq", "model": "openai/gpt-oss-120b", "outcome": "ok",
            "tokens_in": 1000, "tokens_out": 200}
    base.update(overrides)
    if base.get("tokens_in") is None:
        base.pop("tokens_in"), base.pop("tokens_out")
    return base


def refusal(**overrides):
    return call(outcome="rate_limited", tokens_in=None, tokens_out=None, **overrides)


def row_of(report, provider):
    return next(row for row in report.rows if row.provider == provider)


def gauge_of(row, name):
    return next(gauge for gauge in row.gauges if gauge.name == name)


def _check_windows():
    report = build_report(
        [call(ts=NOW - 30), call(ts=NOW - 3600), call(ts=NOW - 90_000)], POOL, NOW)
    row = row_of(report, "GptOss120b_groq_1")

    assert row.minute.requests == 1, row.minute
    assert row.day.requests == 2, "the 25h-old call is outside the rolling day"
    assert row.total.requests == 3, "but it is still on record"

    # Three calls in a minute: 3 requests against 30 RPM (10%), 3600 tokens
    # against 8000 TPM (45%). Tokens bind first, and the tightest gauge has to
    # be the one that will actually stop the member.
    report = build_report([call(ts=NOW - 5), call(ts=NOW - 6), call(ts=NOW - 7)],
                          POOL, NOW)
    row = row_of(report, "GptOss120b_groq_1")
    assert row.tightest.name == "tpm", row.tightest
    assert (row.tightest.used, row.tightest.limit) == (3600, 8000), row.tightest


def _check_resets():
    # The window belongs to the attempt that opened it: the oldest one still
    # inside it. Here that is 40s ago, so the minute clears in 20s -- not in 60.
    report = build_report([call(ts=NOW - 40), call(ts=NOW - 5)], POOL, NOW)
    row = row_of(report, "GptOss120b_groq_1")

    assert gauge_of(row, "rpm").resets_in == 20, gauge_of(row, "rpm")
    assert gauge_of(row, "tpm").resets_in == 20, "tokens were spent at the same moment"
    assert gauge_of(row, "rpd").resets_in == DAY_SECONDS - 40, gauge_of(row, "rpd")

    # An empty window has no reset, because it has not started. It will start
    # whenever the next attempt is made.
    idle = row_of(build_report([], POOL, NOW), "GptOss120b_groq_1")
    assert all(gauge.resets_in is None for gauge in idle.gauges), idle.gauges

    # A call exactly one window old is still inside it, and clears now.
    edge = row_of(build_report([call(ts=NOW - MINUTE_SECONDS)], POOL, NOW),
                  "GptOss120b_groq_1")
    assert edge.minute.requests == 1 and gauge_of(edge, "rpm").resets_in == 0

    # A refusal opens the request window -- it spent a request -- but not the
    # token window, which no refusal ever touches. So the two clocks differ.
    report = build_report([refusal(ts=NOW - 50), call(ts=NOW - 20)], POOL, NOW)
    row = row_of(report, "GptOss120b_groq_1")
    assert gauge_of(row, "rpm").resets_in == 10, "the refusal at -50s opened it"
    assert gauge_of(row, "tpm").resets_in == 40, "the served call at -20s opened it"


def _check_refusals():
    report = build_report([
        call(),
        refusal(),
        call(outcome="error", tokens_in=None, tokens_out=None),
    ], POOL, NOW)
    row = row_of(report, "GptOss120b_groq_1")

    assert row.day.requests == 3, "a refused attempt still spent a request"
    assert (row.day.rate_limited, row.day.errors) == (1, 1), row.day
    assert row.day.tokens_in == 1000, "only the served call reported tokens"

    # A refusal counts inside the request gauges and is called out there, and
    # counts nowhere in the token gauges: no tokens were spent being refused.
    assert gauge_of(row, "rpd").used == 3 and gauge_of(row, "rpd").refused == 1
    assert gauge_of(row, "rpm").refused == 1
    assert gauge_of(row, "tpm").used == 1200 and gauge_of(row, "tpm").refused == 0
    assert any("refused for quota" in note for note in report.notes), report.notes

    # When the provider said when to come back, that beats our window model.
    report = build_report([refusal(ts=NOW - 10, retry_after=45)], POOL, NOW)
    assert row_of(report, "GptOss120b_groq_1").blocked_for == 35

    # An expired Retry-After is not a block any more.
    report = build_report([refusal(ts=NOW - 100, retry_after=45)], POOL, NOW)
    assert row_of(report, "GptOss120b_groq_1").blocked_for is None

    # And a refusal without one leaves the question to the window model.
    report = build_report([refusal(ts=NOW - 10)], POOL, NOW)
    assert row_of(report, "GptOss120b_groq_1").blocked_for is None


def _check_unanswered():
    # An attempt that never got an answer cost the account nothing, so it is not
    # a request. It is still an error, because something did go wrong.
    report = build_report([call(), call(outcome="error", tokens_in=None,
                                       tokens_out=None, reached=False)], POOL, NOW)
    row = row_of(report, "GptOss120b_groq_1")

    assert row.day.requests == 1, "only the answered attempt spent one"
    assert (row.day.unanswered, row.day.errors) == (1, 1), row.day
    assert gauge_of(row, "rpd").used == 1, gauge_of(row, "rpd")

    # And it opens no window: a budget cannot start on a request the vendor
    # never saw.
    report = build_report([call(ts=NOW - 50, outcome="error", tokens_in=None,
                                tokens_out=None, reached=False)], POOL, NOW)
    row = row_of(report, "GptOss120b_groq_1")
    assert row.day.requests == 0
    assert all(gauge.resets_in is None for gauge in row.gauges), row.gauges


def _check_model_fold():
    """The default view: one model, every account that serves it."""
    report = build_report([
        call(ts=NOW - 30),
        call(ts=NOW - 20),
        call(ts=NOW - 50, provider="GptOss120b_groq_2", account="groq_2"),
    ], POOL, NOW)
    model = next(m for m in report.models if m.model == "openai/gpt-oss-120b")

    assert model.accounts == ["groq_1", "groq_2"], model.accounts
    assert model.day.requests == 3, "three attempts over two keys"
    # Two accounts at 1000 a day really are 2000 a day: separate budgets add.
    assert gauge_of(model, "rpd").limit == 2000, gauge_of(model, "rpd")
    assert gauge_of(model, "rpd").used == 3
    # The sum keeps its parts, so the panel can show 2 x 1000 rather than a
    # ceiling nobody recognises from the config.
    assert gauge_of(model, "rpd").sources == 2
    assert model.max_input_tokens == 8000, "the window both accounts offer"
    # The soonest window to clear is the one that frees capacity first, whichever
    # key it sits on: groq_2 opened its minute at -50s.
    assert gauge_of(model, "rpm").resets_in == 10, gauge_of(model, "rpm")

    # Gemma declares no TPM on either account, so the total has no ceiling --
    # summing what is known with what is not would invent a number.
    gemma = next(m for m in report.models if m.model == "gemma-4-31b-it")
    assert gemma.gauges and gauge_of(gemma, "tpm").limit is None
    assert gauge_of(gemma, "tpm").sources == 0, "nothing was summed into it"
    assert gauge_of(gemma, "rpd").limit == 1500, "one account serves it, so one limit"

    # A platform is summed for what it spent and never given a ceiling: Groq
    # shares one request budget across models, so adding them would be fiction.
    groq = next(p for p in report.platforms if p.platform == "groq")
    assert groq.models == 1 and groq.accounts == ["groq_1", "groq_2"], groq
    assert groq.day.requests == 3
    assert not hasattr(groq, "gauges"), "a platform has no ceiling of its own"
    assert [p.platform for p in report.platforms] == ["gemini", "groq"]


def _check_accounts_are_separate():
    report = build_report([
        call(ts=NOW - 30),
        call(ts=NOW - 20),
        call(ts=NOW - 50, provider="GptOss120b_groq_2", account="groq_2"),
    ], POOL, NOW)

    first = row_of(report, "GptOss120b_groq_1")
    second = row_of(report, "GptOss120b_groq_2")
    assert (first.minute.requests, second.minute.requests) == (2, 1), "one model, two budgets"
    assert gauge_of(first, "rpm").resets_in == 30, "opened by its own oldest call"
    assert gauge_of(second, "rpm").resets_in == 10, "and so was the other account's"

    # Rollups are per account, never per platform: both Groq accounts are Groq,
    # and neither lends the other any budget.
    assert [(a.account, a.day.requests) for a in report.accounts] == [
        ("gemini_1", 0), ("groq_1", 2), ("groq_2", 1)], report.accounts
    blocked = build_report([refusal(ts=NOW - 5, retry_after=30)], POOL, NOW)
    groq_1 = next(a for a in blocked.accounts if a.account == "groq_1")
    groq_2 = next(a for a in blocked.accounts if a.account == "groq_2")
    assert groq_1.blocked_for == 25 and groq_2.blocked_for is None


def _check_limits_and_notes():
    report = build_report([call(provider="Gemma4_31b_gemini_1")], POOL, NOW)
    gemma = row_of(report, "Gemma4_31b_gemini_1")

    assert [g.name for g in gemma.gauges] == ["rpm", "tpm", "rpd", "tpd"], gemma.gauges
    tpm = gauge_of(gemma, "tpm")
    assert tpm.limit is None, "Gemma's TPM is unlimited, not zero"
    assert tpm.ratio is None and tpm.used == 1200, "consumption is still counted"
    assert gemma.tightest.name == "rpm", "only a gauge with a ceiling can be tightest"

    empty = build_report([], POOL, NOW)
    assert len(empty.rows) == 3 and empty.calls == 0
    assert all(row.configured and row.last_call is None for row in empty.rows)

    # A member that has left the config is reported, not dropped.
    report = build_report([call(provider="Llama3_70b_groq_1",
                                model="llama-3.3-70b-versatile")], POOL, NOW)
    row = row_of(report, "Llama3_70b_groq_1")
    assert row.configured is False and row.tightest is None
    assert any("Llama3_70b_groq_1" in note for note in report.notes), report.notes

    report = build_report([call()], None, NOW)
    assert report.rows[0].tightest is None
    assert report.rows[0].model == "openai/gpt-oss-120b", "read off the ledger line"
    assert any("python -m llm_router" in note for note in report.notes), report.notes


def _run():
    _check_windows()
    _check_resets()
    _check_refusals()
    _check_unanswered()
    _check_model_fold()
    _check_accounts_are_separate()
    _check_limits_and_notes()
    print("quota: all checks passed")


if __name__ == "__main__":
    _run()
