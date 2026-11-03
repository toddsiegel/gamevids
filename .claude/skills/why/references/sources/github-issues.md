# GitHub Issues

## What this source contains

- Issues describing features, bugs, and their motivation
- Parent/sub-issue relationships (Umbrella or Epic → Slice, Chore, Bug)
- Issue types that signal the kind of work. `Decision 🤔`, `Research 🔬`, and `🧪 Prototype` issues exist to record reasoning
- Comments on issues (clarifications, scope changes, "why we're doing this" rationale)
- `Area: *` labels naming the product area, plus `Followup` and `flakey-test`
- Blocking/blocked-by dependencies, project membership, milestones
- The PRs that closed each issue, and cross-references from other issues and PRs

This is where the product/business context lives: the "we're doing this because a customer asked" or "this is part of the SciTrust rollout" layer.

## How to search it

Use `gh`. Read-only: never comment, label, assign, or close.

1. **Start with linked issues.** For each seed PR, `gh pr view <number> --json closingIssuesReferences,body` gives the issues it closed and any `#123` mentions in the body. GitHub shares one number space across issues and PRs, so resolve a bare `#123` with `gh issue view`, falling back to `gh pr view`.
2. **Read each issue fully.**

   ```bash
   gh issue view <number> --comments
   gh issue view <number> --json issueType,labels,parent,subIssues,blockedBy,blocking,closedByPullRequestsReferences,projectItems,milestone,stateReason,createdAt,closedAt,author
   ```

3. **Walk the issue tree.** If you land on a sub-issue, fetch its `parent`. Slices are tactical. Umbrellas and Epics often carry the "why." Read sibling sub-issues too: the rationale for one slice is often stated in another.
4. **Search by keyword.** Try the feature name, key symbols, error strings, and business terms in several phrasings, across open and closed issues.

   ```bash
   gh search issues --repo SciPinion/scipinion --limit 50 --json number,title,state,createdAt,closedAt,labels "<query>"
   ```

5. **Find issues that mention the target PR.** Cross-references show discussion that the PR itself doesn't link back to.

   ```bash
   gh api graphql -f query='{repository(owner:"SciPinion",name:"scipinion"){pullRequest(number:<n>){timelineItems(first:50,itemTypes:[CROSS_REFERENCED_EVENT]){nodes{... on CrossReferencedEvent{source{... on Issue{number title}}}}}}}}'
   ```

6. **Check labels, types, and projects.** `Decision 🤔` issues are the strongest candidates for recorded rationale. Search for them by area: `gh issue list --state all --label "Area: SciPis" --search 'type:"Decision 🤔"'`.

## What good evidence looks like here

- An issue body stating the business problem: "Experts can't see X, which blocks Y"
- A comment recording a decision: "We decided to go with approach B because approach A would require touching the payments flow"
- A `Decision 🤔` issue naming the target
- An Umbrella or Epic whose body explains the initiative a slice belongs to
- A `Bug 🪳` issue with reproduction steps matching what the target code guards against

## Common pitfalls

- **Scope drift.** An issue may have been reopened or rescoped after the PR merged. Read the full comment history and check dates against the code's ship date.
- **Thin bodies.** Many slices only say what to build. Look at the parent for the why.
- **Stale plans.** Older issues often describe a plan that changed during implementation. Cross-check against the PR that closed them.
- **Closed-as-duplicate chains.** If `stateReason` is `DUPLICATE`, follow the comments to the canonical issue.
- **Transferred or deleted issues.** If a referenced number 404s, record it as a gap rather than guessing.

## What to return

For each relevant issue:
- Number, title, and URL
- The problem/motivation quoted from the body or comments (not paraphrased. The synthesizer needs the exact text to cite)
- Issue type, labels, parent, project, milestone
- Author, created date, closed date, and the PR(s) that closed it
