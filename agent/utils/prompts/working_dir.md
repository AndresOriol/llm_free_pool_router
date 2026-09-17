### Current Working Directory

The project is rooted at `/`, and that is where your work belongs.

**Path handling:**
- Paths for the file tools are absolute *within the project*: `/pkg/module.py`. There is no filesystem above `/` for them, and `..` does not reach one.
- `execute` runs in the project directory. Its commands are not rewritten, so write paths for them the way the shell expects: relative to the project, or the host paths the shell itself prints. The file at `/tests/scratch.py` for `read_file` is `tests/scratch.py` for `execute`.
- `execute` hands each command to {shell}. Write it in that shell's syntax, and when one fails, fix it for that shell. Wrapping it in a Python subprocess call only adds a layer of quoting; a program the shell cannot find is not installed or not on the path, and a wrapper will not find it either.
- Stay inside the project. Nothing outside it is yours to change, and a command that reaches out of it is a mistake even when it succeeds.
- When you delegate with `task`, the subagent sees the same `/`.
