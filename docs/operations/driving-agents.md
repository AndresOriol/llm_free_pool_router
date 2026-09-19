[← Wiki index](../README.md)

# Driving the free agents

*Handing an agent a brief from the command line — how this project's sibling
workloads get built, and how any one-off task does.*

Every agent is one command: a workdir as the argument, the task on `--task` or
on stdin, and the process exits when the task is done
([cli.py](../../agent/utils/cli.py)).

1. **Write a scoped brief** — goal, the change, constraints, how to tell it is
   done — into a file **inside the target workdir**. The agent's file tools are
   rooted there, so `/` in the brief means the workdir
   ([The blast radius](../agents/code.md#the-blast-radius)).
2. **Put the workdir on a branch of its own.** The agent runs a real shell in
   the workdir, including `git`: it commits as it goes, and commits whatever is
   on disk when its step budget runs out ([The step budget](../agents/code.md#the-step-budget)).
   It never creates the branch for you.
3. **Launch it:**

   ```bash
   python -m agent.code ../my-project --task "Read /tasks/my-brief.md and do it"
   ```

   Piping works too (`< brief.md`), and is what makes it a background job: EOF
   ends the input, the run ends the process. `agent.explore` and
   `agent.improve` take the same shape.
4. **Read what it says moved, then the account.** The command prints the final
   message and then what git says changed, verdict first — trust the second
   over the first. With `code-account` promoted, the agent also writes its
   account into the project's `NOTES.md`.
5. **Review the branch and merge it yourself.** The human gate is the merge,
   not the commit.

The `closet_ai` sibling project exists partly as this loop's real workload —
every failure mode found while building it is input to this repo's roadmap
([Related repos](../overview.md#related-repos)).
