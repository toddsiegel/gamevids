# Rollbar Error History

## What this source contains

Rollbar is the archive of things that went wrong in production. For defensive, corrective, or error-handling code, it often holds the direct motivation: the specific exceptions, stack traces, and frequencies that pushed someone to add a check, rescue, retry, or fallback.

- **Items.** Grouped errors with a counter (the `#123456` shown in the Rollbar UI), title, level, status, environment, `framework` (`rails` or `browser-js`), and first/last occurrence timestamps
- **GitHub links.** Items filed as GitHub issues carry the issue number in `integrations_data.github.issue`
- **Occurrences.** Individual instances of an item, each with its own timestamp, stack trace, request data, and person
- **Deploys.** Every Heroku production deploy, with the full git SHA as `revision` and the Heroku release number in `comment`
- **Versions.** How a code version performed in an environment, if occurrences carry one
- **Explicit reports.** The app calls `Rollbar.error` / `Rollbar.warning` directly in places (jobs, mailboxes). Those items are titled by the message passed in, not by an exception class

The most valuable thing Rollbar provides is **temporal correlation**: "item #4521 first occurred 2024-01-02, peaked at 500 occurrences/day, and stopped after the deploy on 2024-01-15 that shipped the defensive check."

## How to search it

Use the Rollbar MCP (`mcp__rollbar__*`).

1. **Orient.** Run `list-projects` to confirm which project(s) the token reaches. Production errors are in environment `production`.

2. **Search for items related to the target.**

   ```
   list-items(query: "<text>", environment: "production", status: <see below>)
   ```

   `status` defaults to `active`, which hides the resolved items that matter most here. Search `resolved` and `muted` too. Server and browser errors share one project, so check each item's `framework` and discard the side the target doesn't run on. Good query terms: exception classes the target rescues, the class or method name of the target, error message strings the target checks for, and any message the target passes to `Rollbar.error`.

3. **Pull the full item for each candidate.**

   ```
   get-item-details(counter)
   ```

   Does the stack trace pass through the target code? Does the request data or person match the conditions the target defends against?

   If `integrations_data.github.issue` is set, record the issue number under Additional Leads for the GitHub Issues investigator. Older items name `seanhays/scipinion`, this repo's previous name; the issue numbers still resolve here.

4. **Walk the occurrence history.**

   ```
   list-occurrences(counter, limit: 100, last_id: <id>)
   ```

   Page with `last_id` to find the first and last occurrence and the frequency trajectory. Line these up against the target commit's date. Group items return `group_item_not_supported`; record that as a gap.

5. **Find the deploy that shipped the target.**

   ```
   get-deployments(limit)
   ```

   Deploys come back newest first with no date filter, so raise `limit` until you reach the target commit's date. The first deploy whose `revision` contains the target commit is the one that shipped it:

   ```bash
   git merge-base --is-ancestor <target-sha> <revision> && echo shipped
   ```

   Compare that deploy's `finish_time` with the item's occurrence history. An item's `resolved_in_version` is also a git SHA; check whether it contains the target commit the same way.

## What good evidence looks like here

- An item whose **first occurrence** is shortly before the target's PR and **last occurrence** shortly after, suggesting the target addressed this error
- Stack traces that pass through or land on the target method, showing the exact failure mode being defended against
- The target's PR description, commit message, or GitHub issue referencing a Rollbar item URL or counter
- An item with high occurrence counts that stops after the deploy containing the target
- An item titled with the exact message the target passes to `Rollbar.error`, showing what the author wanted to watch

## Common pitfalls

- **Grouping drift.** Rollbar groups occurrences by fingerprint. Refactors or renamed classes can track the "same" error under a new item. If an item ends abruptly, check for a new item starting right after.
- **Deploy correlation is noisy.** A deploy contains many commits. An item stopping after a deploy doesn't prove the target fixed it. Another change in the same deploy might have.
- **Silent fixes.** Sometimes the error stops because upstream changed, not because of the defensive code. The correlation suggests the fix. It doesn't prove authorship.
- **Resolved != fixed.** Items can be resolved by hand without any code change, and resolved items reopen automatically on a new occurrence. Treat `status` as a human marker, not evidence.
- **Retention.** Old occurrences may be gone. If the target predates the earliest data you can find, that's a gap, not a null result.
- **Non-production noise.** Development and test are disabled (`config/initializers/rollbar.rb`), but staging may report under its own environment. Don't mix environments when comparing counts.

## What to return

For each relevant item:
- Counter, title, and URL
- Environment, level, and status
- First / last occurrence timestamps and total occurrence count
- A representative stack trace snippet showing relevance to the target (verbatim excerpt, not summary)
- First/last-occurrence correlation with the target's ship date
- The deploy that first shipped the target (revision SHA and `finish_time`)
- Any linked GitHub issue number
