# Claude Skills Collection

A curated collection of custom skills for Claude AI. Skills are composable, portable extensions that enhance Claude's capabilities for specific tasks.

## What Are Skills?

Skills are folders containing instructions, scripts, and resources that Claude can load when needed. They make Claude more powerful by providing:

- **Composable** - Stack together automatically; Claude identifies and coordinates needed skills
- **Portable** - Use the same format across Claude apps, Claude Code, and API
- **Efficient** - Load only what's necessary when needed
- **Powerful** - Can include executable code for reliable task execution

Learn more: [Anthropic Skills Announcement](https://www.anthropic.com/news/skills)

## Repository Structure

```
claude-skills/
├── .claude-plugin/
│   └── marketplace.json     # Claude Code plugin marketplace catalog
├── skills/           # Individual skill folders
│   ├── skill-name/
│   │   ├── .claude-plugin/
│   │   │   └── plugin.json  # Claude Code plugin manifest
│   │   ├── SKILL.md         # Skill definition and instructions
│   │   └── ...       # Additional resources/scripts
├── templates/        # Templates for creating new skills
└── README.md
```

Each skill folder is both an Agent Skills package (`SKILL.md`) and a single-skill Claude Code plugin (`.claude-plugin/plugin.json`). The root marketplace catalog lets Claude Code install skills through `/plugin marketplace add`.

## Using These Skills

### With `npx skills`

The easiest cross-agent install path is the `skills` CLI:

```bash
# List available skills
npx skills@latest add Emlembow/claude-skills --list

# Install a specific skill for Claude Code
npx skills@latest add Emlembow/claude-skills --skill skillopt-improve-skill --agent claude-code --global

# Install all skills for all detected agents
npx skills@latest add Emlembow/claude-skills --all
```

### In Claude Code Via Plugin Marketplace

Add this repository as a Claude Code marketplace:

```bash
claude plugin marketplace add Emlembow/claude-skills
```

Then install individual skills as namespaced plugins:

```bash
claude plugin install frontend-aesthetics@emlembow-skills
claude plugin install skillopt-improve-skill@emlembow-skills
```

Inside an interactive Claude Code session, the equivalent commands are:

```text
/plugin marketplace add Emlembow/claude-skills
/plugin install frontend-aesthetics@emlembow-skills
/plugin install skillopt-improve-skill@emlembow-skills
```

For local development, load a skill folder directly as a plugin:

```bash
claude --plugin-dir ./skills/skillopt-improve-skill
```

### Repo Management

Validate the marketplace and npx discovery paths:

```bash
npm run validate
```

Or run individual checks:

```bash
npm run skills:list
npm run validate:marketplace
npm run validate:plugins
```

### In Claude Apps (Pro, Max, Team, Enterprise)

1. Custom skills can be imported through the Claude interface
2. Follow the [Claude user guide](https://support.anthropic.com/) for detailed instructions

### Via API

Use the `/v1/skills` endpoint to add skills to your API requests. See [API documentation](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview) for details.

## Available Skills

### [Frontend Aesthetics](skills/frontend-aesthetics/)
Ensures websites and web applications are built with distinctive, creative aesthetics that avoid generic "AI slop" design patterns. This skill guides Claude to create intentional, context-specific designs with:

- **Unique typography** - Distinctive font pairings beyond overused defaults
- **Cohesive color themes** - Mood-driven palettes inspired by IDE themes, art movements, and nature
- **Orchestrated animations** - Meaningful motion that enhances the experience
- **Atmospheric backgrounds** - Layered gradients, patterns, and contextual effects
- **Strategic layouts** - Breaking the grid when appropriate for visual impact

Perfect for any HTML, React, or web-based artifact creation.

### [SkillOpt Improve Skill](skills/skillopt-improve-skill/)
Optimizes existing Claude/Codex skills toward measurable improvement goals using Microsoft SkillOpt-style methodology. This skill helps Claude:

- **Design evals** - Turn an improvement goal into train/validation cases and scoring criteria
- **Run rollouts** - Capture baseline and candidate behavior with structured result files
- **Reflect on evidence** - Propose bounded, general SkillOpt-style edit patches
- **Gate updates** - Accept only candidates that improve validation results without regressions
- **Use SkillOpt proper** - Decide when to run Microsoft SkillOpt or a lightweight local loop

Ideal when you have a skill and a concrete goal like improving trigger precision, tool-use reliability, task success rate, or validation behavior.

### [Ponytail Review Gate](skills/ponytail-review-gate/)
Adds a reusable Ponytail adversarial review gate for code changes. The package also includes a standalone Codex plugin at [plugins/ponytail-review-gate](plugins/ponytail-review-gate/) with Ponytail mode, diff review, optional repo audit, hooks, and the mandatory completion gate wrapper.

Use it when you want another project to require a lean-code review before an agent finishes code-writing work.

## Creating Your Own Skills

### Quick Start

1. Copy the template from `templates/skill-template/`
2. Customize the `SKILL.md` file with your instructions
3. Add a `.claude-plugin/plugin.json` manifest so Claude Code can install it as a plugin
4. Add any necessary scripts or resources
5. Test with `npm run validate`
6. Update `.claude-plugin/marketplace.json`
7. (Optional) Submit a PR to share with the community

### Skill Structure

Each skill folder should contain:

- `SKILL.md` - Main skill definition with instructions for Claude
- `.claude-plugin/plugin.json` - Claude Code plugin manifest
- `README.md` (optional) - Human-readable documentation
- Scripts/resources (optional) - Any code or files the skill needs

### Best Practices

- **Clear instructions**: Write precise, actionable instructions for Claude
- **Scope**: Keep skills focused on specific tasks
- **Examples**: Include examples of when/how to use the skill
- **Testing**: Test thoroughly before sharing
- **Documentation**: Document dependencies and requirements
- **Modularity**: Design skills to work independently or compose with others
- **Marketplace compatibility**: Keep `.claude-plugin/marketplace.json` in sync with `skills/`

## Contributing

Contributions are welcome! To add a skill:

1. Fork this repository
2. Create a new skill folder under `skills/`
3. Follow the structure and best practices above
4. Submit a pull request with a description of your skill

## Resources

- [Anthropic Skills Documentation](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview)
- [Official Anthropic Skills Examples](https://github.com/anthropics/skills)
- [Claude Code Documentation](https://docs.claude.com/en/docs/claude-code)

## License

MIT License - feel free to use and modify these skills for your needs.

---

**Note**: This is a personal collection of skills. For official Anthropic skills, visit [github.com/anthropics/skills](https://github.com/anthropics/skills).
