---
name: how
description: "Use for \"how does X work\", code walkthroughs before changing something, and placement / ownership / layering questions (\"where should this live\", \"is this a model, service, or form\", \"is this the right layer\"). Explains subsystem architecture, request/job flow, onboarding mental models. Use why for motivation."
disable-model-invocation: true
---

# How

Explore the codebase to answer "how does X work?" questions. Produce architectural explanations at the level of a senior engineer onboarding onto a subsystem, enough to build a working mental model, not so much that it reads like annotated source code.

Reference paths below are relative to this skill's directory, `.claude/skills/how/`. Subagents start at the repo root, so give them the full path.

Subagents must not edit files, run migrations, or otherwise write anything. `general-purpose` can, so say so in each prompt (the templates already do). Don't use `Explore` instead: it reads excerpts rather than whole files, and this skill depends on reading the actual implementation.

## Step 1. Assess Complexity

If the scope is ambiguous, state your interpretation and explore. The user can redirect.

- **Simple** (a single model, concern, controller action, or job; a narrow question such as "how does `Invite#approved?` work"): no explorers. One explainer explores and explains in a single pass. Go to Step 2b.
- **Complex** (a subsystem spanning models, controllers, jobs, and views; a cross-cutting feature such as invites or moderation; a full product module overview such as how a SciPi moves from draft to published results): spawn parallel explorers first, then hand off to the explainer. Go to Step 2a.

**Placement questions** ("where should this live", "is this the right layer") also read `Archspec.rb` and `Archspec_todo.rb`, which define the enforced layering (`architecture :vanilla_rails`), and the Deprecations section of `CLAUDE.md`. Treat these as simple unless answering requires tracing how the neighbouring code is organized across several layers.

When in doubt, take the simple path.

## Step 2a. Explore (complex questions only)

Decompose the question into 2 to 4 exploration angles, each a distinct slice of the subsystem. Good slices in this app usually follow the Rails layers or the lifecycle: the request path (routes, controller, form object, views and Stimulus controllers), the domain (models, concerns, callbacks, branding-specific behaviour on `QuestionGroup`), the async side (Delayed Job jobs, mailers, scheduled rake tasks), and integrations (Elasticsearch, SignNow, ORCID, external gateways). Spawn all explorers in a single message:

- `subagent_type`: `general-purpose`
- `model`: `sonnet`

Each explorer gets the prompt in `references/explorer-prompt.md` with its angle filled in. Then go to Step 3.

## Step 2b. Direct Explain (simple questions)

Spawn one subagent that explores and explains in one pass:

- `subagent_type`: `general-purpose`
- `model`: `opus`

Build its prompt from `references/explainer-prompt.md` without the explorer-findings section. Go to Step 4.

## Step 3. Synthesize (complex questions only)

Once all explorers have returned, spawn one subagent to synthesize their findings into one explanation:

- `subagent_type`: `general-purpose`
- `model`: `opus`

Build its prompt from `references/explainer-prompt.md` with every explorer's findings filled in.

## Step 4. Present

Present the explainer's output to the user. Light edits for clarity or context from the conversation are fine. Do not substantially rewrite it.

## Output Format

The explanation uses the sections defined in `references/explainer-prompt.md`, dropping any that do not apply: Overview, Key Concepts, How It Works, Where Things Live, Gotchas. Placement questions add a Recommendation section.
