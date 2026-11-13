---
name: next-step
description: Implement the next task in a specified plan file
disable-model-invocation: true
---

Read the next step the specified plan file. If the file is missing, or there is no next step, say so and stop.

For obvious or trivial changes, just make the change. Otherwise, before making changes:

- Check the codebase, CLAUDE.md files, and other documentation for conventions, established patterns, and rules to follow.
- Make sure the to-do item is clear. If it isn't, use the `grilling` skill to clarify it with the developer.
- Consider any refactorings that would make the change easier, in the spirit of Kent Beck's "make the change easy, then make the easy change". Each refactoring should be driven by a specific code smell and go in its own commit, with the test suite passing. Use the `commit-messages` skill, and cite the code smell in the commit message.

Once you understand the change and any refactorings are committed, implement the change. Add tests as needed to maintain good test coverage.

Once the change is complete and verified (the test suite passes, and UI changes are checked in the browser), mark the step as completed in the plan file and commit all of the changes, including the plan file. Use the `commit-messages` skill to write the commit message.
