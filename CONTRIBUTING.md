# Contributing

Contributions should remain portable across agents unless a skill or plugin explicitly declares a narrower scope.

## Add or update a skill

1. Create `skills/<skill-name>/SKILL.md` with `$skill-creator` or `npx skills init <skill-name>`.
2. Use a lowercase, hyphenated directory name of at most 64 characters and make the frontmatter `name` match it exactly.
3. Keep frontmatter to `name` and `description` for maximum portability. Make the description state both what the skill does and when it should or should not trigger.
4. Write the body as concise imperative instructions. Keep it under 500 lines and preferably under 5,000 tokens.
5. Put optional detail in focused `references/`, deterministic helpers in `scripts/`, and output resources in `assets/`. Link resources directly from `SKILL.md`; avoid deep reference chains.
6. Add `agents/openai.yaml` with quoted strings for `display_name`, a 25-64 character `short_description`, and a `default_prompt` that explicitly mentions `$skill-name`.
7. Do not add a skill-local README, changelog, or installation guide. Put human-facing collection documentation in the root README.
8. If the skill includes scripts, make dependencies and failure messages clear and run every new or changed script.
9. Update the root README and any marketplace entry that distributes the skill.
10. Run `npm run validate`.

## Trigger and workflow checks

Test at least these cases before opening a pull request:

- A direct invocation of the skill.
- A prompt that should trigger the skill implicitly.
- A nearby prompt that should not trigger it.
- A realistic success path.
- A missing-input, tool-failure, or edge-case path when relevant.

Record the prompts and outcomes in the pull request description rather than adding test notes inside the skill.

## Plugin changes

Keep plugin manifests, marketplace entries, and bundled skill paths in sync. The full Ponytail plugin must remain valid from both:

- `.agents/plugins/marketplace.json` for Codex.
- `.claude-plugin/marketplace.json` for Claude Code.

Do not claim a capability that the target host does not load. Keep host-specific behavior inside the corresponding plugin surface, while leaving the top-level `skills/` collection portable.

## Pull requests

Explain the user-visible behavior, list validation commands and results, and call out any new script, dependency, network access, or credential requirement.

By contributing, you agree that your changes are licensed under the repository's MIT license.
