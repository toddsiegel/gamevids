# Incident & Postmortem Context

Not a separate source, a **cross-cutting angle**. Incidents often motivate defensive code ("we added this check after the X outage"), so if the target looks defensive (null checks, retry logic, timeout handling, rate limiting, feature flags, rescue blocks), specifically hunt for incident history across every available source:

- **Documents**: search in-repo `docs/` and Google Drive for postmortems mentioning the target file, feature, or error string
- **GitHub Issues**: `Bug 🪳` and `Followup` issues opened around the dates the target code was added
- **Slack**: search `#engineering` and `#development` for outage or error reports around the dates the target code was added
- **Git**: commits with messages like "fix for incident", "add defensive check", "revert" followed by "re-apply with..." are strong signals
- **Rollbar**: items whose first/last occurrence window aligns with the target's PR ship date, stack traces through the target

If you find an incident link, fetch the full postmortem. Postmortems typically have an "Action Items" section that ties directly to code changes. When multiple sources corroborate (a Rollbar item appears in a GitHub issue, which appears in a postmortem doc, which appears in a Slack thread that links to the target PR, and the item stops occurring after the fix), the evidence is especially strong.

Worth spending time on when the code's defensive character makes an incident-driven origin plausible. Skip it for code that doesn't look defensive.
