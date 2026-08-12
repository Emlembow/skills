# Emlembow Skills

Portable [Agent Skills](https://agentskills.io/) for Codex, Claude Code, and other compatible coding agents.

## Install with `npx skills`

Inspect the available portable skills before installing:

```bash
npx skills add Emlembow/skills --list
```

Install one skill into the current project:

```bash
npx skills add Emlembow/skills --skill frontend-aesthetics
npx skills add Emlembow/skills --skill skillopt-improve-skill
npx skills add Emlembow/skills --skill adversarial-review
```

Project scope is the default and is usually the safest choice for team repositories. To install a reviewed skill for a specific agent at user scope, be explicit:

```bash
npx skills add Emlembow/skills --skill frontend-aesthetics --agent codex --global --yes
npx skills add Emlembow/skills --skill frontend-aesthetics --agent claude-code --global --yes
```

Use `--copy` only when the target environment cannot use the CLI's recommended symlink installation. Update installed skills with `npx skills update -p` for project scope or `npx skills update -g` for user scope.

Skills can contain executable scripts. Review a skill and its bundled resources before granting it access to sensitive repositories, credentials, or external systems. The CLI supports `DISABLE_TELEMETRY=1` and `DO_NOT_TRACK=1` when telemetry must be disabled.

## Primary portable skills

### [Frontend Aesthetics](skills/frontend-aesthetics/)

Designs distinctive, responsive web interfaces while preserving product context, accessibility, and an existing design system when one is present.

### [SkillOpt Improve Skill](skills/skillopt-improve-skill/)

Evaluates and improves an existing skill against a measurable objective using rollouts, bounded edits, and validation gates.

### [Adversarial Review](skills/adversarial-review/)

Requires a completed task result to survive two consecutive independent attempts to disprove it. The skill keeps versioned, digest-checked candidate snapshots and reviewer findings in a durable workspace ledger.

The skill is also published through [skills.sh](https://skills.sh/Emlembow/skills/adversarial-review). Skills hosted on GitHub appear there after an install through the `skills` CLI with anonymous telemetry enabled.

## Create or contribute a skill

Use the creator built into your agent when available (`$skill-creator` in Codex), or initialize a portable skill with:

```bash
npx skills init my-skill
```

Keep `SKILL.md` focused on one job, put trigger conditions in the frontmatter `description`, write imperative steps with explicit inputs and outputs, and move optional detail into one-level-deep `references/`. Add `agents/openai.yaml` for Codex presentation metadata.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the repository requirements.

## Validate

The automated checks pin tool versions for reproducibility even though end-user install examples follow the official unversioned `npx skills` form.

```bash
npm run validate
```

This checks repository structure, skill metadata, top-level portable discovery, and the Claude marketplace. Pull requests and pushes to `main` run the same validation in GitHub Actions.

## Sources

- [OpenAI: Build skills](https://developers.openai.com/codex/skills/)
- [Agent Skills specification](https://agentskills.io/specification)
- [`skills` CLI reference](https://www.skills.sh/docs/cli)
- [Claude Code skills](https://code.claude.com/docs/en/slash-commands)

## License

MIT. See [LICENSE](LICENSE).
