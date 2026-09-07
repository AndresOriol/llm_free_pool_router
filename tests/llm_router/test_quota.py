"""Checks for the quota report: the vendor's windows, when they reset, how a
refused attempt is counted, and that two accounts never share either.

No framework: `python -m tests.llm_router.test_quota` (or run the file).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from llm_router.quota.report import build_report, declared_limits
from llm_router.quota.windows import (DAY_SECONDS, MINUTE_SECONDS, day_start,
                                      minute_start)

#: Midday, mid-minute: 2027-01-15 12:30:45 Pacific, which is 20:30:45 UTC. Both
#: matter -- the two platforms in this pool turn their day on different clocks,
#: and a `NOW` sitting on a boundary would hide which one a window came from.
NOW = 1_800_045_045
GEMINI_DAY = NOW - day_start("gemini", NOW)      # 12h30m45s into the Pacific day
GROQ_DAY = NOW - day_start("groq", NOW)          # 20h30m45s into the UTC day
INTO_MINUTE = NOW - minute_start(NOW)            # 45s

GROQ_LIMITS = {"rpm": 30, "tpm": 8000, "rpd": 1000, "tpd": 100000}
GEMINI_LIMITS = {"rpm": 5, "tpm": 250000, "rpd": 20}

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
        {"provider": "Gemini_3_5_Flash_gemini_1", "account": "gemini_1",
         "platform": "gemini", "model": "gemini-3.5-flash", "priority": 4,
         "max_input_tokens": 250000, "limits": GEMINI_LIMITS},
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
    assert row.day.requests == 2, "the 25h-old call fell before Groq's midnight"
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
    # A window is the vendor's bucket, not a span our first attempt opened: it
    # clears when the clock says so, however long we have been spending into it.
    report = build_report([call(ts=NOW - 40), call(ts=NOW - 5)], POOL, NOW)
    row = row_of(report, "GptOss120b_groq_1")

    assert gauge_of(row, "rpm").resets_in == MINUTE_SECONDS - INTO_MINUTE
    assert gauge_of(row, "tpm").resets_in == MINUTE_SECONDS - INTO_MINUTE, (
        "both halves of a minute are the same minute")
    assert gauge_of(row, "rpd").resets_in == DAY_SECONDS - GROQ_DAY, "Groq's UTC day"

    # An empty window has nothing to clear, so it is shown without a reset --
    # the renderers hide the clock on a gauge nobody has spent into.
    idle = row_of(build_report([], POOL, NOW), "GptOss120b_groq_1")
    assert all(gauge.resets_in is None for gauge in idle.gauges), idle.gauges

    # The edge is the clock's, not ours: the first instant of this minute is in,
    # the second before it is out.
    edge = row_of(build_report([call(ts=minute_start(NOW))], POOL, NOW),
                  "GptOss120b_groq_1")
    assert edge.minute.requests == 1, "the minute began at that instant"
    before = row_of(build_report([call(ts=minute_start(NOW) - 1)], POOL, NOW),
                    "GptOss120b_groq_1")
    assert before.minute.requests == 0, "one second earlier is the minute before"
    assert before.day.requests == 1, "still inside the day, though"

    # A refusal and a served call share the minute they landed in. What differs
    # is what each one counts, not when either clears.
    report = build_report([refusal(ts=NOW - 40), call(ts=NOW - 20)], POOL, NOW)
    row = row_of(report, "GptOss120b_groq_1")
    assert gauge_of(row, "rpm").resets_in == gauge_of(row, "tpm").resets_in
    assert (gauge_of(row, "rpm").used, gauge_of(row, "tpm").used) == (2, 1200)


def _check_calendar_day():
    """The daily budget turns over on the vendor's clock, not on ours.

    This is the one that stalls a run when it is wrong: a rolling day keeps
    counting yesterday evening into this morning, and the requests-per-day
    filter then skips a member Google has already forgiven.
    """
    gemini = dict(provider="Gemini_3_5_Flash_gemini_1", account="gemini_1",
                  platform="gemini", model="gemini-3.5-flash")

    # A call from just before midnight Pacific is yesterday's, however few hours
    # old it is.
    midnight = day_start("gemini", NOW)
    report = build_report([call(ts=midnight - 1, **gemini),
                           call(ts=midnight, **gemini),
                           call(ts=NOW - 30)], POOL, NOW)
    row = row_of(report, "Gemini_3_5_Flash_gemini_1")
    assert row.day.requests == 1, "only the one on this side of midnight"
    assert row.total.requests == 2, "both are still on record"
    assert gauge_of(row, "rpd").resets_in == DAY_SECONDS - GEMINI_DAY

    # The two platforms turn their days at different moments, so the pool never
    # has one day of its own. Groq's UTC midnight is 8 hours before Gemini's.
    groq = row_of(report, "GptOss120b_groq_1")
    assert gauge_of(groq, "rpd").resets_in == DAY_SECONDS - GROQ_DAY
    assert GROQ_DAY - GEMINI_DAY == 8 * 3600, "Pacific standard time is UTC-8"


def _check_token_metering():
    """Gemini publishes its tokens-per-minute over the prompt alone."""
    served = dict(tokens_in=1000, tokens_out=200)
    report = build_report([
        call(ts=NOW - 5, provider="Gemini_3_5_Flash_gemini_1", account="gemini_1",
             platform="gemini", model="gemini-3.5-flash", **served),
        call(ts=NOW - 5, **served),
    ], POOL, NOW)

    gemini = row_of(report, "Gemini_3_5_Flash_gemini_1")
    assert gauge_of(gemini, "tpm").used == 1000, "the reply is not charged to TPM"
    assert gemini.minute.tokens == 1200, "though it was spent, and is counted"

    groq = row_of(report, "GptOss120b_groq_1")
    assert gauge_of(groq, "tpm").used == 1200, "Groq meters the whole exchange"


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
    # Both keys are on one platform, so one clock turns for both: a fold has a
    # single reset, not the soonest of several.
    assert gauge_of(model, "rpm").resets_in == MINUTE_SECONDS - INTO_MINUTE
    assert gauge_of(model, "rpm").used == 2, "the -50s attempt was last minute" 

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


def _check_declared_columns():
    """A column nobody has a ceiling for should not be a column."""
    report = build_report([], POOL, NOW)
    groq = [row for row in report.rows if row.platform == "groq"]
    gemini = [row for row in report.rows if row.platform == "gemini"]

    assert declared_limits(groq) == ("rpm", "tpm", "rpd", "tpd"), declared_limits(groq)
    # Gemini publishes no tokens-per-day for anything in this pool, and Gemma no
    # tokens-per-minute either -- so the platform keeps TPM, which the flash
    # models declare, but never TPD.
    assert declared_limits(gemini) == ("rpm", "tpm", "rpd"), declared_limits(gemini)
    assert declared_limits([]) == ()


def _check_accounts_are_separate():
    report = build_report([
        call(ts=NOW - 30),
        call(ts=NOW - 20),
        call(ts=NOW - 50, provider="GptOss120b_groq_2", account="groq_2"),
    ], POOL, NOW)

    first = row_of(report, "GptOss120b_groq_1")
    second = row_of(report, "GptOss120b_groq_2")
    assert (first.day.requests, second.day.requests) == (2, 1), "one model, two budgets"
    # The clock is shared and the spending is not: what separates two accounts
    # is what each has used, never when either one clears.
    assert gauge_of(first, "rpm").used == 2 and gauge_of(second, "rpm").used == 0
    assert gauge_of(first, "rpm").resets_in == MINUTE_SECONDS - INTO_MINUTE
    assert gauge_of(second, "rpm").resets_in is None, "nothing in it to clear"

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
    assert tpm.ratio is None and tpm.used == 1000, "consumption is still counted"
    assert gemma.day.tokens == 1200, "the reply was spent, just not metered"
    assert gemma.tightest.name == "rpm", "only a gauge with a ceiling can be tightest"

    empty = build_report([], POOL, NOW)
    assert len(empty.rows) == 4 and empty.calls == 0
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
    _check_calendar_day()
    _check_token_metering()
    _check_refusals()
    _check_unanswered()
    _check_model_fold()
    _check_declared_columns()
    _check_accounts_are_separate()
    _check_limits_and_notes()
    print("quota: all checks passed")


if __name__ == "__main__":
    _run()
