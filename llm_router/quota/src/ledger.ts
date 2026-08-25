/**
 * Reading what the router wrote down.
 *
 * Both files come from `llm_router/usage.py` and are the panel's only input: no
 * API is called and no key is read here. Free tiers publish no usage endpoint
 * worth trusting, and the router already knows every call it made -- so the
 * question "how much is left" is answered from our own record, not the vendor's.
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

/** `ok` served the call, `rate_limited` was refused for quota, `error` failed. */
export type Outcome = "ok" | "rate_limited" | "error";

/** One line of `ledger.jsonl`: one attempt against one pool member. */
export interface Call {
  ts: number;
  provider: string;
  account: string;
  platform: string;
  model: string;
  outcome: Outcome;
  tokens_in?: number;
  tokens_out?: number;
}

/**
 * A model's published free-tier quota, as declared in `config.yaml`. A missing
 * key is a limit the vendor does not publish or does not enforce (Gemma's TPM),
 * and is shown as no ceiling rather than as zero.
 */
export interface Limits {
  rpm?: number;
  tpm?: number;
  rpd?: number;
  tpd?: number;
}

/** One account x model pair, as the router last built it. */
export interface PoolMember {
  provider: string;
  account: string;
  platform: string;
  model: string;
  priority: number;
  max_input_tokens: number | null;
  limits: Limits;
}

export interface PoolSnapshot {
  generated: number;
  config: string | null;
  pool: PoolMember[];
}

/**
 * Where the ledger lives. Mirrors `usage.usage_dir()` on the Python side --
 * `llm_router/.usage/` unless `LLM_ROUTER_USAGE_DIR` says otherwise -- so both
 * halves agree without either being configured.
 */
export function usageDir(override?: string): string {
  if (override) return override;
  const fromEnv = process.env.LLM_ROUTER_USAGE_DIR;
  if (fromEnv) return fromEnv;
  return fileURLToPath(new URL("../../.usage/", import.meta.url));
}

export function readLedger(dir: string): Call[] {
  const path = join(dir, "ledger.jsonl");
  if (!existsSync(path)) return [];

  const calls: Call[] = [];
  for (const line of readFileSync(path, "utf8").split("\n")) {
    if (!line.trim()) continue;
    try {
      calls.push(JSON.parse(line) as Call);
    } catch {
      // A half-written last line is expected, not exceptional: the eval runner
      // kills runs on a timeout, and the record of a killed run is exactly the
      // one worth keeping. Drop the torn line, keep the rest.
    }
  }
  return calls;
}

export function readPool(dir: string): PoolSnapshot | null {
  const path = join(dir, "pool.json");
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, "utf8")) as PoolSnapshot;
  } catch {
    return null;
  }
}
