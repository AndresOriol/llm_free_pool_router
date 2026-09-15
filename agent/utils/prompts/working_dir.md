### Current Working Directory

The project is rooted at `/`, and `/` is all you can reach.

**Path handling:**
- Paths are absolute *within the project*: `/pkg/module.py`.
- There is no filesystem above `/`. A host path such as `C:\Users\...` or `/home/...` does not exist here and `..` cannot escape.
- When you delegate with `task`, the subagent sees the same `/`.
