# Source playbooks

The why skill spawns one investigator per available evidence category, each reading the single playbook below for its category.

| Category | Playbook | Tooling |
|---|---|---|
| Source control history | [`code-archaeology.md`](./sources/code-archaeology.md) | git, `gh` |
| GitHub Issues | [`github-issues.md`](./sources/github-issues.md) | GitHub Issues via `gh` |
| Long-form documents | [`documents.md`](./sources/documents.md) | Google Drive and in-repo `docs/` |
| Slack | [`slack.md`](./sources/slack.md) | Slack |
| Rollbar | [`rollbar.md`](./sources/rollbar.md) | Rollbar |

Cross-cutting:

- [`incident-postmortem.md`](./sources/incident-postmortem.md). Add this if the target code looks defensive (null checks, retry, timeout, rate limit, feature flag, rescue block).
