<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="Emlembow Skills: portable Agent Skills distributed through npx skills to Codex, Claude Code, and other compatible agents">
</p>

<p align="center">
  <a href="https://agentskills.io/">Agent Skills</a> ·
  <a href="#choose-a-skill">Browse the collection</a> ·
  <a href="CONTRIBUTING.md">Contribute</a> ·
  <a href="LICENSE">MIT licensed</a>
</p>

Focused, portable instructions for Codex, Claude Code, and other coding agents that support the Agent Skills format. Each skill does one job, keeps its behavior in a standard `SKILL.md`, and includes host metadata only where a host needs it.

## Start with the catalog

Inspect every available portable skill before installing anything:

```bash
npx skills add Emlembow/skills --list
```

Project scope is the default and is usually the safest choice for a team repository. This collection is installed from GitHub with `npx skills add Emlembow/skills`; it is not an npm package.

## Choose a skill

| Skill | Best for |
| --- | --- |
| [`research-loop`](skills/research-loop/) | Metric-driven experiments with holdouts, immutable evidence, and replay-safe recovery |
| [`adversarial-review`](skills/adversarial-review/) | Two independent attempts to disprove a versioned, digest-checked result |
| [`divisible-work`](skills/divisible-work/) | Same-project Codex chat coordination with native subagent fallback, durable state, verification, and progress readouts |

`adversarial-review` is also indexed on [skills.sh](https://skills.sh/Emlembow/skills/adversarial-review). GitHub-hosted skills appear there after an install through the `skills` CLI with anonymous telemetry enabled.

`divisible-work` keeps the main chat focused on coordination and prefers new worker Codex chats in the same project when host rules and human authorization permit. Worker chats execute their assignments and use their own native subagents when useful; only the main chat updates the shared task ledger and accepts results. Native subagents provide the fallback on other hosts or where chat delegation is unavailable or prohibited. Installing the Claude plugin does not provide Codex chat tools.

The skill preserves the parent model and routes workers only to GPT-6.1 Sol (`gpt-6.1-sol`) or GPT-6 Luna (`gpt-6-luna`). The main chat chooses a model and a supported thinking level for each assignment, usually Luna for clear bounded work and Sol for demanding reasoning, synthesis, or integration. There is no fixed thinking level. Worker subagents follow the same model restriction. The skill passes model and thinking choices at startup through permitted host controls and records them; an unavailable permitted route cannot fall back to a third model or unknown defaults.

To explicitly request worker chat creation and follow-up messaging in Codex:

```text
Use $divisible-work to create worker Codex chats in this project, send follow-up assignments, and choose GPT-6.1 Sol or GPT-6 Luna plus a thinking level for each. Workers should use their own subagents when useful; use native subagents as the fallback.
```

Automatic skill selection alone does not authorize creating or messaging chats or overriding their model. The example explicitly requests chat creation, follow-up messaging, and model selection. The skill follows the host's permission rules and uses the native subagent fallback where those rules require it and an allowed model is available.

## Install exactly what you need

Every portable skill can be selected independently:

```bash
npx skills add Emlembow/skills --skill research-loop
npx skills add Emlembow/skills --skill adversarial-review
npx skills add Emlembow/skills --skill divisible-work
```

To install a reviewed skill for a specific agent at user scope, be explicit:

```bash
npx skills add Emlembow/skills --skill research-loop --agent codex --global --yes
npx skills add Emlembow/skills --skill research-loop --agent claude-code --global --yes
npx skills add Emlembow/skills --skill divisible-work --agent codex --global --yes
npx skills add Emlembow/skills --skill divisible-work --agent claude-code --global --yes
```

The CLI recommends symlink installation. Use `--copy` only when the target environment cannot use symlinks. Update project-scoped skills with `npx skills update -p`, or user-scoped skills with `npx skills update -g`.

> **Review before granting access.** Skills can contain executable scripts. Read a skill and its bundled resources before giving it access to sensitive repositories, credentials, or external systems. Set `DISABLE_TELEMETRY=1` or `DO_NOT_TRACK=1` when telemetry must be disabled.

## Portable at the core

The repository keeps reusable behavior separate from host presentation and distribution:

```text
skills/<skill-name>/
├── SKILL.md              # portable behavior and trigger rules
├── agents/openai.yaml    # Codex display metadata
├── references/           # optional focused detail
├── scripts/              # deterministic helpers, only when needed
└── .claude-plugin/       # Claude plugin metadata, when distributed there
```

- The top-level `skills/` folders are the source discovered by `npx skills`.
- `agents/openai.yaml` adds Codex presentation without changing portable behavior.
- Claude plugin manifests and marketplaces point back to the same skill folders.
- Repository validation checks structure, metadata, discovery, and marketplace paths together.

## Create or contribute a skill

Use the creator built into your agent when available (`$skill-creator` in Codex), or initialize a portable skill with:

```bash
npx skills init my-skill
```

Keep `SKILL.md` focused on one job. Put trigger and non-trigger conditions in its frontmatter `description`, write imperative steps with explicit inputs and outputs, and move optional detail into one-level-deep `references/`. Add `agents/openai.yaml` for Codex presentation metadata.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the complete repository requirements.

## Validate the collection

The automated checks pin tool versions for reproducibility even though end-user examples use the official unversioned `npx skills` form.

```bash
npm run validate
```

This checks repository structure, skill metadata, top-level portable discovery, discovery at every local plugin source registered in either marketplace, the Claude marketplace, and each distributed Claude plugin. Pull requests and pushes to `main` run the same validation in GitHub Actions.

## References

- [OpenAI: Build skills](https://developers.openai.com/codex/skills/)
- [Agent Skills specification](https://agentskills.io/specification)
- [`skills` CLI reference](https://www.skills.sh/docs/cli)
- [Claude Code skills](https://code.claude.com/docs/en/slash-commands)

## License

MIT. See [LICENSE](LICENSE).
