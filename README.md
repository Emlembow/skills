# Emlembow Skills

Portable [Agent Skills](https://agentskills.io/) for Codex, Claude Code, and other compatible coding agents. The repository also publishes a full Ponytail Review Gate plugin for Codex and Claude Code.

## Install with `npx skills`

Inspect the available portable skills before installing:

```bash
npx skills add Emlembow/skills --list
```

Install one skill into the current project:

```bash
npx skills add Emlembow/skills --skill frontend-aesthetics
npx skills add Emlembow/skills --skill skillopt-improve-skill
npx skills add Emlembow/skills --skill ponytail-review-gate
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

### [Ponytail Review Gate](skills/ponytail-review-gate/)

Adds or runs a lean-code completion gate. The portable skill works on its own; the full plugin below adds the companion Ponytail modes and review skills.

The repository also exposes the plugin's five companion `SKILL.md` entries through `npx skills add Emlembow/skills --list`, so they can be installed individually without plugin-specific integration.

## Full Ponytail plugin

The plugin at [`plugins/ponytail-review-gate`](plugins/ponytail-review-gate/) bundles Ponytail mode, diff review, whole-repository audit, help, and the adversarial completion gate.

### Codex

```bash
codex plugin marketplace add Emlembow/skills
codex plugin add ponytail-review-gate@emlembow-skills
```

Start a new task after installation so Codex loads the plugin's skills.

### Claude Code

```bash
claude plugin marketplace add Emlembow/skills
claude plugin install ponytail-review-gate@emlembow-skills
```

To install only the plugin's five portable skills through the open `skills` CLI, without plugin-specific integration:

```bash
npx skills add https://github.com/Emlembow/skills/tree/main/plugins/ponytail-review-gate --skill '*' --agent codex
```

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

This checks repository structure, skill metadata, portable discovery for both skill collections, and the Claude marketplace. Pull requests and pushes to `main` run the same validation in GitHub Actions.

## Sources

- [OpenAI: Build skills](https://developers.openai.com/codex/skills/)
- [Agent Skills specification](https://agentskills.io/specification)
- [`skills` CLI reference](https://www.skills.sh/docs/cli)
- [Claude Code skills](https://code.claude.com/docs/en/slash-commands)

## License

MIT. See [LICENSE](LICENSE).
