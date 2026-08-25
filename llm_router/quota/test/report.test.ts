import assert from "node:assert/strict";
import { test } from "node:test";

import type { Call, PoolSnapshot } from "../src/ledger.ts";
import { buildReport } from "../src/report.ts";

const NOW = 1_800_000_000;

const POOL: PoolSnapshot = {
  generated: NOW,
  config: "llm_router/config.yaml",
  pool: [
    {
      provider: "GptOss120b_groq_1", account: "groq_1", platform: "groq",
      model: "openai/gpt-oss-120b", priority: 2, max_input_tokens: 8000,
      limits: { rpm: 30, tpm: 8000, rpd: 1000, tpd: 100000 },
    },
    {
      provider: "Gemma4_31b_gemini_1", account: "gemini_1", platform: "gemini",
      model: "gemma-4-31b-it", priority: 31, max_input_tokens: 128000,
      limits: { rpm: 15, rpd: 1500 },
    },
  ],
};

function call(overrides: Partial<Call> = {}): Call {
  return {
    ts: NOW - 10,
    provider: "GptOss120b_groq_1",
    account: "groq_1",
    platform: "groq",
    model: "openai/gpt-oss-120b",
    outcome: "ok",
    tokens_in: 1000,
    tokens_out: 200,
    ...overrides,
  };
}

test("a call outside a window is left out of it", () => {
  const report = buildReport(
    [call({ ts: NOW - 30 }), call({ ts: NOW - 3600 }), call({ ts: NOW - 90_000 })],
    POOL, NOW);
  const row = report.rows.find((candidate) => candidate.provider === "GptOss120b_groq_1")!;

  assert.equal(row.minute.requests, 1);
  assert.equal(row.day.requests, 2, "the 25h-old call is outside the rolling day");
  assert.equal(row.total.requests, 3, "but it is still on record");
});

test("the tightest gauge is the one that will stop the member first", () => {
  // One minute's worth of calls: 3 requests against 30 RPM (10%), but 3600
  // tokens against 8000 TPM (45%) -- tokens are the binding constraint.
  const report = buildReport(
    [call({ ts: NOW - 5 }), call({ ts: NOW - 6 }), call({ ts: NOW - 7 })], POOL, NOW);
  const row = report.rows.find((candidate) => candidate.provider === "GptOss120b_groq_1")!;

  assert.equal(row.tightest?.name, "tpm");
  assert.equal(row.tightest?.used, 3600);
  assert.equal(row.tightest?.limit, 8000);
});

test("an undeclared limit is no ceiling, not a zero one", () => {
  const report = buildReport(
    [call({ provider: "Gemma4_31b_gemini_1", model: "gemma-4-31b-it" })], POOL, NOW);
  const gemma = report.rows.find((candidate) => candidate.provider === "Gemma4_31b_gemini_1")!;
  const tpm = gemma.gauges.find((gauge) => gauge.name === "tpm")!;

  assert.deepEqual(gemma.gauges.map((gauge) => gauge.name), ["rpm", "tpm", "rpd", "tpd"],
    "every metered quantity gets a gauge, declared or not");
  assert.equal(tpm.limit, null, "Gemma's TPM is unlimited, not zero");
  assert.equal(tpm.ratio, null);
  assert.equal(tpm.used, 1200, "and its consumption is still counted");
  assert.equal(gemma.tightest?.name, "rpm", "only a gauge with a ceiling can be tightest");
});

test("a configured member with nothing spent still gets a row", () => {
  const report = buildReport([], POOL, NOW);

  assert.equal(report.rows.length, 2);
  assert.equal(report.calls, 0);
  assert.ok(report.rows.every((row) => row.configured && row.lastCall === null));
});

test("refused and failed attempts are counted apart from served ones", () => {
  const report = buildReport([
    call(),
    call({ outcome: "rate_limited", tokens_in: undefined, tokens_out: undefined }),
    call({ outcome: "error", tokens_in: undefined, tokens_out: undefined }),
  ], POOL, NOW);
  const row = report.rows.find((candidate) => candidate.provider === "GptOss120b_groq_1")!;

  assert.equal(row.day.requests, 3, "a refused attempt still spent a request");
  assert.equal(row.day.rateLimited, 1);
  assert.equal(row.day.errors, 1);
  assert.equal(row.day.tokensIn, 1000, "only the served call reported tokens");
});

test("a member that has left the config is reported, not dropped", () => {
  const report = buildReport(
    [call({ provider: "Llama3_70b_groq_1", model: "llama-3.3-70b-versatile" })],
    POOL, NOW);
  const row = report.rows.find((candidate) => candidate.provider === "Llama3_70b_groq_1")!;

  assert.equal(row.configured, false);
  assert.equal(row.tightest, null, "no config entry, so no limits to measure against");
  assert.ok(report.notes.some((note) => note.includes("Llama3_70b_groq_1")));
});

test("accounts are rolled up, because that is where the budget lives", () => {
  const report = buildReport([call(), call(), call({ provider: "Gemma4_31b_gemini_1" })],
    POOL, NOW);
  const groq = report.accounts.find((account) => account.account === "groq_1")!;

  assert.equal(groq.day.requests, 2);
  assert.equal(groq.day.tokensIn + groq.day.tokensOut, 2400);
  assert.equal(report.accounts.length, 2);
});

test("no pool snapshot means no limits, and says so", () => {
  const report = buildReport([call()], null, NOW);

  assert.equal(report.rows[0].tightest, null);
  assert.equal(report.rows[0].model, "openai/gpt-oss-120b", "read off the ledger line");
  assert.ok(report.notes.some((note) => note.includes("python -m llm_router")));
});
