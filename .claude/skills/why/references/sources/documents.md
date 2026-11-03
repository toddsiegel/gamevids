# Long-form Documents (Google Drive + in-repo docs)

## What this source contains

- **In-repo docs.** `docs/plans/<topic>/` holds requirements, implementation plans, rollout plans, and review findings written before or alongside the code. `docs/agents/` and `.devnotes/` hold working notes. `CLAUDE.md`, `AGENTS.md`, and `test/CLAUDE.md` record conventions and the reasons behind them.
- **Deleted in-repo docs.** Plans are often removed once the work ships. Their full text survives in git history.
- **Google Drive.** Specs, PRDs, meeting notes, and slide decks that never entered the repo, plus their comment threads.

This is where "why" is written out in long form before it becomes code. A significant feature usually has a plan in `docs/plans/` or a doc in Drive.

## How to search it

In-repo docs are always available. Google Drive needs the Google Drive MCP. If it isn't connected, search the repo only and record Drive as a gap. Drive is read-only here: use only `search_files`, `read_file_content`, `get_file_metadata`, and `download_file_content`.

### In-repo docs

```bash
# Current docs mentioning the feature, symbol, or term
rg -n -i '<term>' docs .devnotes CLAUDE.md AGENTS.md test/CLAUDE.md

# Docs that were deleted, with the commit that removed them
git log --diff-filter=D --name-only --format='%h %ad %s' --date=short -- docs .devnotes

# Read a deleted doc as it was just before deletion
git show <hash>^:<path>

# Every version of a doc, to see how the plan changed
git log --follow -p -- <doc_path>

# Docs that once mentioned a term, even if since removed
git log -S '<term>' --format='%h %ad %s' --date=short -- docs .devnotes
```

### Google Drive

1. **Keyword search with `search_files`.** Use `fullText contains '<term>'` for the feature name, key symbols, product names (SciPi, SciPoll, Pings, Standing Panels, SciTrust), and user-visible terms. Try several phrasings.
2. **Bound by time.** Add `modifiedTime` or `createdTime` clauses around the PR dates. Design docs usually predate the code by days to weeks.
3. **Search by author.** `owner = '<email>'` for the PR author or issue author.
4. **Read candidates with `read_file_content`**, passing `includeComments: true`. Read the full content, not the snippet. Decisions are often settled in comment threads.
5. **Walk folders.** When a hit sits in a project folder, search `parentId = '<folder id>'` for sibling docs (alternatives, meeting notes, appendices).

## What good evidence looks like here

- A requirements or plan doc with a "Problem", "Motivation", or "Why" section matching the target's purpose
- An "Alternatives considered" or "Rejected approaches" section
- A plan that names the target file, model, or feature and was committed before or alongside it
- A deleted plan whose removal commit is the same PR that shipped the target
- A Drive comment thread recording "we decided X because Y"

## Common pitfalls

- **Plan vs. reality drift.** Plans are written before implementation and rarely updated. Compare the plan with the PR that shipped. Flag differences instead of reconciling them.
- **Multiple drafts.** `docs/plans/` topics often have an initial draft, requirements, and an overview. Drive often has copies. Check dates and say which version you are citing.
- **Unlinked docs.** The most relevant doc may not be linked from any PR or issue. Broad keyword searches help.
- **Access-restricted files.** If a Drive file can't be read, note it as a gap.

## What to return

For each relevant doc:
- Path (with the commit hash for deleted or historical versions) or Drive title and URL
- Author and created/last-modified dates
- The motivation text (verbatim quote), with section location
- Related docs (so the synthesizer can cite them)
- Whether it is current, superseded, or deleted
