# Slack Conversations

## What this source contains

- Real-time discussions of problems and decisions
- Design discussion threads where tradeoffs were debated
- Questions answered by senior engineers that didn't make it into docs
- Post-merge discussions that explain why something was revisited
- DMs and group DMs (searchable only when the connected user is a participant)

Slack is frequently where the *real* decisions got made, especially for smaller changes that didn't warrant a doc. It's also the most ephemeral source. Threads get deleted, channels get archived, and search quality degrades over time.

## How to search it

Load the Slack tool schemas with `ToolSearch` (`+slack`) first. If the server exposes only `authenticate`/`complete_authentication`, it isn't connected. Stop and report the gap.

Read-only: use only `slack_search_public_and_private`, `slack_search_channels`, `slack_search_users`, `slack_read_channel`, `slack_read_thread`, and `slack_read_user_profile`. Never send, schedule, or draft messages, add reactions, or touch canvases and lists.

1. **Author-bounded search.** Messages from the PR author around the PR merge date. Look up their Slack user ID with `slack_search_users`, then filter `from:<@U…> after:YYYY-MM-DD before:YYYY-MM-DD`. Limits scope dramatically and often hits gold.
2. **Keyword search for the feature name and key symbols.** Include misspellings and casual phrasings.
3. **PR URL search.** Slack often links PRs when they're reviewed or discussed. Search for the PR URL (or just `/pull/<number>`).
4. **Error string search.** If the code handles a specific error, search for the error string.
5. **Channel-scoped search.** Engineering discussion lives in `#engineering`, `#development`, and `#dev-chat`. There are no dedicated incident channels. Use `slack_search_channels` to find others relevant to the target's product area.
6. **Thread traversal.** When you find a relevant message, fetch the whole thread with `slack_read_thread`. The decision often lives in the replies.

## What good evidence looks like here

- A thread where tradeoffs were explicitly debated ("I was going to use A but B is better because...")
- An incident channel message describing the bug the code prevents
- A question from a reviewer and an authoritative answer from the author or lead
- A reference to a meeting where a decision was made
- A message from a product manager or customer-facing engineer explaining a customer ask

## Common pitfalls

- **Channel archaeology limits.** Very old messages may be gone due to retention policies. If you can't find anything before a certain date, note the retention cliff.
- **Unsearched DMs.** Many decisions happen in DMs that aren't searchable. You'll miss them. That's a known limitation.
- **Speculative jokes as "decisions."** Slack is casual. "Lol just do the thing" isn't a decision, even if it preceded the commit. Look for considered discussion.
- **Context collapse in single messages.** Without the thread, a single message often reads differently than in context. Always fetch threads.
- **Auth failures.** If the MCP isn't authenticated, stop. Don't make up findings. Report that Slack wasn't searchable.

## What to return

For each relevant thread:
- Channel name
- Permalink or thread ID
- Participants
- Date range of the discussion
- The key quotes (verbatim) with attribution
- Context: what thread/incident/discussion this was part of
