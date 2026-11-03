---
name: why
description: "Use for 'why does X work this way', 'why we picked Y', design rationale, regressions, postmortems, or data-backed thresholds. Queries each evidence category (git and PRs, GitHub Issues, docs and Google Drive, Slack, Rollbar) in parallel, then returns a cited read on decisions and tradeoffs."
disable-model-invocation: true
---

# Why

Investigate the motivation and intent behind code: not what it does, but what forces led to its shape.

Reference paths below are relative to this skill's directory, `.claude/skills/why/`. Subagents start at the repo root, so give them the full path.

## Operating Posture

Operate as a **careful, cautious, and precise investigator**. Be honest about what you know vs what you're inferring. Read `references/epistemics.md` for the full confidence framework and phrasing guide. The synthesizer must follow it.

## Step 1. Understand the Target and the Question

Parse what the user is asking. The **target** is usually a chunk of code, a pattern, a feature, or a named design decision. The **question** is usually a design rationale, a tradeoff, a motivating edge case, an external constraint, dead code, or a broad history sweep.

If the target is vague ("why do we do it this way?" with no clear referent), make your best guess from conversation context (the current branch, recent edits, what was just discussed). State your interpretation briefly so the user can redirect if you're off, then proceed.

## Step 2. Establish the Code Anchor

Before spawning investigators, anchor the investigation in concrete code. You need:

- The relevant file path(s) and line range(s)
- The key symbols (function names, class names, constants)
- An initial commit list. The last few commits touching the target.
- The PR that landed each commit. PRs are usually rebase-merged, so most subjects carry no `(#1234)`. Look it up from the commit SHA

Build this inline.

```bash
# Blame target lines for last-touch commits
git blame -L <start>,<end> <file>

# Full file history, with patches, through renames
git log --follow -p -- <file>

# Last N commits touching the file
git log --oneline -20 -- <file>

# PR(s) that landed a commit
gh api repos/{owner}/{repo}/commits/<sha>/pulls --jq '.[] | "#\(.number) \(.title)"'
```

Pull PR bodies and discussion via `gh` for any substantive commits:

```bash
gh pr view <number> --json title,body,author,createdAt,mergedAt,labels,closingIssuesReferences,comments,reviews
```

Capture this as seed context (file paths, symbols, commits, PR numbers, linked issue numbers). Pass it to the investigators.

## Step 3. Spawn Parallel Investigators (default posture)

**Default to the full parallel investigation.**

### Discovery

Source control, GitHub Issues, and in-repo docs are always available through git, `gh`, and the working tree. Before spawning investigators, check which of these MCPs are connected in this session:

- Slack (`mcp__claude_ai_Slack__*`)
- Google Drive (`mcp__claude_ai_Google_Drive__*`)
- Rollbar (`mcp__rollbar__*`)

A server exposing only `authenticate`/`complete_authentication` tools, or missing entirely, is not connected. Record it as a gap. Skip the Slack or Rollbar investigator when its MCP is missing; without Drive, the documents investigator still searches in-repo docs.

Aim for a complete **coverage map**, not a minimal one. Document the null, don't skip the search.

Launch all matching investigators in a single message so they run concurrently. Don't ask one agent to cover multiple categories.

Subagent config (each):

- `subagent_type`: `general-purpose`
- `model`: `sonnet`

Investigators must not edit files, post comments, or otherwise write anything. `general-purpose` can, so say so in each prompt. Don't use `Explore` instead: it reads excerpts rather than whole sources.

Each investigator gets:

1. The base prompt from `references/investigator-prompt.md`
2. The category playbook from `references/sources/` (see `references/source-playbook.md` for the mapping)
3. The cross-cutting `references/sources/incident-postmortem.md` **if the target code looks defensive** (null checks, retry logic, timeout handling, rate limiting, feature flags, rescue blocks)
4. The code anchor from Step 2 (file paths, symbols, commit hashes, PR numbers, issue numbers)
5. The user's original question

### Investigator roster. One per available evidence category

Spawn one investigator per available category. Each owns exactly one category.

Each entry names the category and the kind of "why" it uniquely surfaces. Use it to know what to expect back, how to name a gap when a category returns empty, and (only in the rare provably-irrelevant case) to justify a skip.

1. **Source control investigator**. Git history, `gh` for PRs, code comments, tests. Always spawn. Best at surfacing _implementation-time rationale captured during review_.

2. **GitHub Issues investigator**. GitHub Issues via `gh`. Always spawn. Best at surfacing _the product or business forcing function_. Strongest when the why is external to engineering.

3. **Long-form documents investigator**. In-repo `docs/` (including deleted plans in git history) and Google Drive when its MCP is connected. Always spawn. Best at surfacing _long-form design rationale_. Where the why is written out before it becomes code.

4. **Slack investigator**. Best at surfacing _real-time deliberation that never reached a doc_. Especially important when the source control, issue, and doc paper trail is thin.

5. **Rollbar investigator**. Best at surfacing _the specific exceptions and error trajectories that motivated defensive or corrective code_. Strongest for rescue blocks, nil guards, type checks, retries, and other defenses.

### When to skip an investigator

Only skip with an **explicit, written justification** that goes in the final "Sources Consulted" section. Two valid reasons:

- **The MCP for that category is not connected** in this session. Flag this as a gap, not a choice. Example: "Slack skipped. Slack MCP not connected, so the conversational record was not searchable."
- **The source is provably irrelevant**, not just "probably irrelevant." A high bar. Example: "Rollbar skipped. Target is a build-time script with no runtime code path."

If your scope assessment suggests a single-commit trivial target where the PR description already contains the complete answer, you may answer inline **only after** confirming all five category searches would be redundant. Say so explicitly. This should be rare.

## Step 4. Synthesize

Spawn one synthesizer subagent:

- `subagent_type`: `general-purpose`
- `model`: `opus`

The synthesizer gets:

1. The investigator findings, including any null results and any categories skipped with justification
2. The code anchor from Step 2 (file paths, symbols, commit hashes, PR numbers, issue numbers)
3. The user's original question
4. The epistemics framework from `references/epistemics.md`
5. The synthesizer prompt template from `references/synthesizer-prompt.md`

## Step 5. Present

Take the synthesizer's output and present it to the user. You may lightly edit for clarity or add context from the conversation, but **do not rewrite the confidence language**.

## Output Format

The output structure is the one in `references/synthesizer-prompt.md`: The Question, The Code in Question, What We Found, What We Can Reasonably Infer, Competing Hypotheses, What We Don't Know, Sources Consulted, Confidence Summary. Adapt as needed, but keep the confidence separation intact, and keep Sources Consulted as one line per investigator, including the ones that returned nothing or were skipped, with the reason.

After the Sources Consulted block, if the user's `why` question is a precursor to actually changing this code, convert the lineage findings into a Preserve / Change / Avoid / Risk constraint set suitable for planning the change.

## Common Failure Modes to Avoid

- **Recency bias**. Assuming the most recent commit is authoritative. The current shape is often the accretion of many earlier decisions. Trace back.

## Reference Files

- `references/epistemics.md`. Confidence tiers and phrasing guide. The synthesizer must follow it.
- `references/investigator-prompt.md`. Base prompt template for investigator subagents.
- `references/source-playbook.md`. Index pointing at the category playbooks below.
- `references/sources/*.md`. One playbook per category, plus cross-cutting `incident-postmortem.md`. Give an investigator the single file that matches its category.
- `references/synthesizer-prompt.md`. Prompt template for the synthesizer subagent, including the output format.
