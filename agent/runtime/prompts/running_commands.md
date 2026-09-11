## Running commands

`execute` is not a shell. It launches one program directly, so:

- Only these run, named bare with no path: {programs}. Nothing else runs at all -- there is no wrapper, no interpreter and no escape hatch around that list.
- No `&&`, `||`, `;`, `|`, `>` or heredocs -- there is nothing to interpret them. Run one command per call, or make several calls in one response when they do not depend on each other.
- No `cd`. Paths are relative to the project root.
