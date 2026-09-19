# Driving the free agents

The agents that build this project's sibling workloads are the free agents
themselves. The loop:

1. Write a scoped brief (Goal / Change / Constraints / Acceptance) into a file
   **inside the target workdir** — the agent is jailed there
   ([The blast radius](../agents/code.md#the-blast-radius)).
2. Launch one-shot, pointing stdin at a one-line instruction:

   ```bash
   echo "Read /tasks/my-brief.md and do it" | python -m agent.code <workdir>
   ```

   EOF makes the process exit, so this works as a background job.
3. The brief should tell the agent to write at `/` (its virtual root) and to run
   `python -m pytest` to check its own work.
4. The agent has no git. Review the diff, then commit.

The `closet_ai` sibling project exists partly as this loop's real workload —
every failure mode found while building it is input to this repo's roadmap
([Related repos](../overview.md#related-repos)).
