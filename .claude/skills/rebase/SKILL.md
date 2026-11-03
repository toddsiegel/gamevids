---
name: rebase
description: "Use when you need to rebase a branch."
---

Rebase the current branch onto a base branch. The base branch should be determined in this order:

1. If a base branch name is specified, rebase onto that branch.
2. If a base branch name is not specified, look for local candidate branches. If one or more candidate branches are found, ask the user to choose one. List them from most likely to least likely using the AskUserQuestion tool. Always include main as the final, default option.
3. If no base branch was specified, and no candidate base branches exist, rebase onto `main`.

If any conflicts are detected, use the `resolve-git-conflicts` skill to resolve them.
