"""Checks for the quota report: the windows, the gauges, and which of the two
sources a figure came from.

No framework: `python -m tests.llm_router.test_quota` (or run the file).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from llm_router.quota.probe import reset_seconds
from llm_router.quota.report import build_report

NOW = 1_800_000_000

POOL = {
    "generated": NOW,
    "config": "llm_router/config.yaml",
    "pool": [
        {"provider": "GptOss120b_groq_1", "account": "groq_1", "platform": "groq",
         "model": "openai/gpt-oss-120b", "priority": 2, "max_input_tokens": 8000,
         "limits": {"rpm": 30, "tpm": 8000, "rpd": 1000, "tpd": 100000}},
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
    return base


def row_of(report, provider):
    return next(row for row in report.rows if row.provider == provider)


def gauge_of(row, name):
    return next(gauge for gauge in row.gauges if gauge.name == name)


def _check_windows():
    report = build_report(
        [call(ts=NOW - 30), call(ts=NOW - 3600), call(ts=NOW - 90_000)], POOL, {}, NOW)
    row = row_of(report, "GptOss120b_groq_1")

    assert row.minute.requests == 1, row.minute
    assert row.day.requests == 2, "the 25h-old call is outside the rolling day"
    assert row.total.requests == 3, "but it is still on record"

    # Three calls in a minute: 3 requests against 30 RPM (10%), 3600 tokens
    # against 8000 TPM (45%). Tokens are the binding constraint, and the
    # tightest gauge has to be the one that will actually stop the member.
    report = build_report([call(ts=NOW - 5), call(ts=NOW - 6), call(ts=NOW - 7)],
                          POOL, {}, NOW)
    row = row_of(report, "GptOss120b_groq_1")
    assert row.tightest.name == "tpm", row.tightest
    assert (row.tightest.used, row.tightest.limit) == (3600, 8000), row.tightest


def _check_local_gauges():
    report = build_report([call(provider="Gemma4_31b_gemini_1")], POOL, {}, NOW)
    gemma = row_of(report, "Gemma4_31b_gemini_1")

    assert [g.name for g in gemma.gauges] == ["rpm", "tpm", "rpd", "tpd"], gemma.gauges
    tpm = gauge_of(gemma, "tpm")
    assert tpm.limit is None, "Gemma's TPM is unlimited, not zero"
    assert tpm.ratio is None and tpm.used == 1200, "consumption is still counted"
    assert gemma.tightest.name == "rpm", "only a gauge with a ceiling can be tightest"

    # A configured member that has spent nothing is still a row: untouched
    # budget is what someone deciding whether to start a long run needs to see.
    empty = build_report([], POOL, {}, NOW)
    assert len(empty.rows) == 2 and empty.calls == 0
    assert all(row.configured and row.last_call is None for row in empty.rows)

    report = build_report([
        call(),
        call(outcome="rate_limited", tokens_in=None, tokens_out=None),
        call(outcome="error", tokens_in=None, tokens_out=None),
    ], POOL, {}, NOW)
    row = row_of(report, "GptOss120b_groq_1")
    assert row.day.requests == 3, "a refused attempt still spent a request"
    assert (row.day.rate_limited, row.day.errors) == (1, 1), row.day
    assert row.day.tokens_in == 1000, "only the served call reported tokens"


def _check_vendor_readings():
    # Groq's `limit_requests` is its *daily* budget and `limit_tokens` its
    # per-minute one. Nothing in the header says so, so the reading is matched
    # against the declared limits rather than assumed per platform.
    vendor = {"GptOss120b_groq_1": {
        "ts": NOW - 5, "ok": True, "reports": True, "status": 200,
        "limit_requests": 1000, "remaining_requests": 940,
        "limit_tokens": 8000, "remaining_tokens": 6000,
        "reset_requests": 86.4, "reset_tokens": 0.547}}
    report = build_report([call()], POOL, vendor, NOW)
    row = row_of(report, "GptOss120b_groq_1")

    rpd = gauge_of(row, "rpd")
    assert (rpd.source, rpd.used, rpd.limit) == ("vendor", 60, 1000), rpd
    assert rpd.resets_in == 86.4, rpd
    tpm = gauge_of(row, "tpm")
    assert (tpm.source, tpm.used) == ("vendor", 2000), tpm
    # The vendor said nothing about the other two, so they stay local -- and the
    # local count is visible next to the vendor's, not overwritten by it.
    assert gauge_of(row, "rpm").source == "local"
    assert gauge_of(row, "rpm").used == 1, "the ledger still counts what we sent"
    assert report.probed == NOW - 5

    # A ceiling that matches no declared limit is still reported, under the
    # header's own name -- the vendor knows about budgets the config may not.
    vendor = {"GptOss120b_groq_1": {
        "ts": NOW, "ok": True, "reports": True,
        "limit_requests": 7, "remaining_requests": 5}}
    row = row_of(build_report([], POOL, vendor, NOW), "GptOss120b_groq_1")
    unmatched = gauge_of(row, "requests")
    assert (unmatched.used, unmatched.limit) == (2, 7), unmatched
    assert gauge_of(row, "rpd").source == "local", "the declared limits are untouched"

    # A probe that failed changes nothing: better the local count than a gap.
    vendor = {"GptOss120b_groq_1": {"ts": NOW, "ok": False, "reports": True,
                                    "status": 404, "error": "HTTP 404"}}
    report = build_report([call()], POOL, vendor, NOW)
    row = row_of(report, "GptOss120b_groq_1")
    assert all(gauge.source == "local" for gauge in row.gauges), row.gauges
    assert any("failed to answer" in note for note in report.notes), report.notes

    # A platform that cannot report is said to be silent, not left looking
    # un-probed -- they read differently to someone deciding what to trust.
    vendor = {"Gemma4_31b_gemini_1": {"ts": NOW, "reports": False,
                                      "detail": "gemini returns no rate-limit headers"}}
    report = build_report([], POOL, vendor, NOW)
    assert any("publishes no usage figures" in note for note in report.notes), report.notes


def _check_notes_and_rollup():
    report = build_report([call(), call(), call(provider="Gemma4_31b_gemini_1")],
                          POOL, {}, NOW)
    groq = next(a for a in report.accounts if a.account == "groq_1")
    assert groq.day.requests == 2 and groq.day.tokens == 2400, groq
    assert len(report.accounts) == 2

    # A member that has left the config is reported, not dropped.
    report = build_report([call(provider="Llama3_70b_groq_1",
                                model="llama-3.3-70b-versatile")], POOL, {}, NOW)
    row = row_of(report, "Llama3_70b_groq_1")
    assert row.configured is False and row.tightest is None
    assert any("Llama3_70b_groq_1" in note for note in report.notes), report.notes

    report = build_report([call()], None, {}, NOW)
    assert report.rows[0].tightest is None
    assert report.rows[0].model == "openai/gpt-oss-120b", "read off the ledger line"
    assert any("python -m llm_router" in note for note in report.notes), report.notes

    # A stale reading is worse than none if it is presented as current.
    vendor = {"GptOss120b_groq_1": {"ts": NOW - 7200, "ok": True, "reports": True,
                                    "limit_requests": 1000, "remaining_requests": 900}}
    report = build_report([], POOL, vendor, NOW)
    assert any("2.0h old" in note for note in report.notes), report.notes


def _check_reset_parsing():
    # Groq quotes resets in compound units; the panel wants seconds.
    assert reset_seconds("1m26.4s") == 86.4
    assert reset_seconds("547ms") == 0.547
    assert reset_seconds("2h30m") == 9000
    assert reset_seconds("7.66s") == 7.66
    assert reset_seconds(None) is None and reset_seconds("") is None
    assert reset_seconds("soon") is None


def _run():
    _check_windows()
    _check_local_gauges()
    _check_vendor_readings()
    _check_notes_and_rollup()
    _check_reset_parsing()
    print("quota: all checks passed")


if __name__ == "__main__":
    _run()
