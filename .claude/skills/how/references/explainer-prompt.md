# Explainer Prompt Template

Build the explainer subagent's prompt from this template. Fill in the placeholders. For simple questions with no explorers, drop the Explorer Findings section and the first two paragraphs of Instructions, and tell the explainer to explore the code itself following `.claude/skills/how/references/explorer-prompt.md`.

---

You are writing an architectural explanation of part of the SciPinion Rails codebase for a senior engineer. Multiple explorer agents have traced different slices of the codebase in parallel and gathered findings. Synthesize their findings into one coherent, well-structured explanation.

This is a read-only task. Do not edit or create files, run migrations, or run anything that writes to a database or external service.

## Original Question

> {QUESTION}

## Explorer Findings

{EXPLORER_FINDINGS_ALL}

## Instructions

The explorers each investigated a different angle of the same subsystem. Their findings will overlap in places and may occasionally contradict. Reconcile them. Merge overlapping descriptions, resolve contradictions by checking the code yourself, and combine the separate slices into a unified picture.

You have read-only access to the codebase to check anything, clarify a detail, or fill a gap. The explorers did the work, so you shouldn't need to re-explore from scratch.

Write an explanation a senior engineer unfamiliar with this area could read and walk away with a solid mental model, understanding the architecture well enough to start working in it confidently.

Use the domain vocabulary from `CONTEXT.md` if it exists. Prefer `Submission` over `AnswerGroup` when describing the concept, but name the actual class or table when pointing at code.

For placement questions ("where should this live", "is this the right layer"), read `Archspec.rb` and `Archspec_todo.rb` for the enforced layering rules, and the Deprecations section of `CLAUDE.md`. Ground the recommendation in those rules and in how neighbouring code is already organized, and don't recommend a deprecated pattern (FactoryBot, new ViewComponents).

## Output Format

Use this structure, adapted to what makes sense for the question. Not every section is needed for every question.

### Overview
1-2 paragraphs. What is this thing, what does it do, why does it exist. Someone should be able to read just this and decide whether to keep reading.

### Key Concepts
The important models, concerns, services, or abstractions needed to follow the rest. Brief definitions, not exhaustive.

### How It Works
The core of the explanation, and the longest section. Walk through the flow: what triggers it, what happens step by step, where data goes, what the decision points are. Call out behaviour that happens outside the obvious call chain (callbacks, enqueued jobs, ability checks, Turbo Stream broadcasts).

Use prose, not pseudocode. Reference specific files and methods as `path:line` so the reader knows where to look, but don't dump large code blocks unless a snippet is essential to a point.

When the flow involves multiple components talking to each other, or data transforming through stages, include a diagram. Use mermaid (```mermaid) for structured flows (sequence diagrams, flowcharts, component graphs) or ASCII art for simpler relationships where mermaid would be overkill. Use your judgment. A diagram should clarify, not decorate. If prose covers the flow, skip the diagram.

### Where Things Live
A brief file/directory map. Just the ones someone would need to start working here, including the most useful fixtures and tests.

### Recommendation
Placement questions only. Where the code should go, which layer and directory, and why, citing the Archspec rule or existing precedent.

### Gotchas
Non-obvious things, surprising behavior, historical context, pitfalls. Skip this section if there's nothing worth calling out.

## Communication Style

- Use concrete language, not abstractions-about-abstractions
- Say "`UsersController#update` calls `User#confirm!`, which enqueues `WelcomeJob`" not "the controller delegates to the model"
- When something is complex, explain why it's complex. Don't just describe the complexity
- When something is simple, don't pad it out
- If there's a helpful analogy, use it. If there isn't, don't force one
- If the explorers flagged open questions or gaps, acknowledge them rather than hiding them
