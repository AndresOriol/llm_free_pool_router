---
name: delegate
description: >-
  {description}
---

# Delegating work to another agent

Each peer below is a command you run with `execute`. Running one starts a full
agent session: it spends the same free-tier pool your own turns do, it blocks
until that session ends — which can be an hour — and then prints a summary.

**Pass `timeout=3600` on that `execute` call.** The default is sized for a test
run, and a delegated session that hits it is killed partway through with its
quota already spent. An hour is the most `execute` accepts.

## The agents this run can reach

{roster}

Nothing else is reachable. An agent not listed here was either turned off for
this run or probed and found unusable; running it anyway wastes a step.

## When it is worth it

Delegate when the work is outside what you can reach yourself:

- a question that needs the open web, and you have no network tool;
- a change in a repository your own `/` is not bound to.

Do not delegate what you could finish in a few tool calls of your own. A
delegation costs a whole session of quota and wall-clock time, and it cannot be
interrupted once launched. Do not delegate the same brief twice hoping for a
better answer — if the first result was thin, read the files it left and work
from those.

## Writing the brief

The delegate cannot see your conversation, your files or your task. It gets one
string. Write it for a stranger:

1. **The question**, stated so it can be answered without guessing at context.
2. **What the answer is for** — the decision it feeds, so the delegate can tell
   a useful answer from a merely correct one.
3. **What would make it useless** — the shape you cannot work with: the wrong
   version, the wrong language, a summary where you need the source.

Ask once and ask specifically. A vague brief spends the same hour.

<good-example>
--task "Does httpx support HTTP/2 server push? We are choosing between httpx
and aiohttp for a client that must stream server-pushed events. Answer from
httpx's own docs, changelog or issue tracker — a blog post asserting it without
a citation is useless."
</good-example>

<bad-example>
--task "research httpx"
</bad-example>

## Reading what comes back

**The deliverable is on disk, not in the summary.** The closing message is one
agent's account of its own session; the files and commits are what it actually
did. The roster above says what each peer leaves behind — go and read it, with
your own file tools or `git diff`, before you act on anything the summary said.

If the command times out or exits non-zero, the session still did whatever it
did before it stopped. Check the files before deciding it produced nothing.
