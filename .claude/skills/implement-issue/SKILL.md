---
name: implement-issue
description: "Implement an issue supplied by the user."
disable-model-invocation: true
---

Implement the issue supplied by the user, either by an issue number or URL.

If the issue is blocked by an open issue, or is an Umbrella issue, do not
implement it and notify the user.

If the issue can be implemented, do the following before implementing it:

- Assign it to the user
- Set the issue to the "In Progress" state, if it is in a project
- Create a new branch, if not already created. The branch should either based on main, or another branch if this issue depends on it.

Implement the issue. Commit when done and open a PR.

If there is no data in the database that the developer can test with create it, for all the cases they need to check. Ensure it is in a consistent state as it would be in production. Then tell the developer how to get started, e.g. give them a URL, and a list of steps to follow, or a list of statements to run in the Rails console, etc.
