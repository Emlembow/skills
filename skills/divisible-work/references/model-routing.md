# Route models by assignment

Read before the first dispatch. Revisit when an assignment changes, a model is unavailable, or a run resumes on a different host. Routing changes who executes an attempt; it does not change acceptance criteria, authorization, or the coordinator-only role.

## Resolve choices

- Inspect the actual spawn and continuation controls, available models and supported efforts, context inheritance rules, and any configured roles. Do not invent model identifiers or assume a model available to the parent is available to workers.
- Honor explicit user or project model choices and resource limits before these defaults. A choice of parent model alone does not pin worker models. Preserve the parent model; do not rewrite global settings or project configuration as part of running this skill.
- Resolve an efficient model for routine work and a stronger model for demanding reasoning from the host's advertised capabilities. If the host cannot select models, use its default and record that routing is unavailable. If a preferred model is unavailable, use a suitable available model or the host default and record the fallback. If this would violate an explicit user requirement, block only affected work and report the exact limitation.

## Choose the route and effort

| Assignment | Default route | Effort when selectable |
| --- | --- | --- |
| Clear, bounded exploration, source lookup, implementation, reproduction, or test execution | Efficient model | Low for mechanical work; medium otherwise |
| Ambiguous diagnosis, architectural reasoning, conflicting-source synthesis, or consequential integration | Stronger model | Medium or high according to uncertainty |
| Independent review warranted by correctness, integration, or consequence | Stronger model | Medium; raise for difficult reasoning |

Judge the actual assignment, not just its role name. A researcher resolving contradictory evidence may need the stronger model immediately; a deterministic integration command may fit the efficient model. Do not force a weak first attempt when the difficulty is already clear. Do not create review tasks solely to use a model tier.

Select reasoning effort separately from the model and use only values supported by that model. Explicit effort choices take precedence. If effort selection is unavailable, leave it unset and record the host default. Avoid maximum effort as a blanket default; consider observed quality, elapsed time, context transfer, and repair costs when adjusting routes. Record usage only when the host exposes it; do not infer savings from model names or raw task counts.

## Dispatch with sufficient context

- Use native model/effort parameters or an actually available configured role. A model name in a worker prompt is not a model switch. Check whether a role's configuration overrides the requested model or effort.
- Give fresh workers a self-contained assignment with the user constraints, acceptance criteria, owned outputs, and paths to the required artifact revisions. Include relevant repository instructions even when conversation history is omitted. Do not send the whole transcript merely to compensate for a missing brief.
- Reuse a worker when its retained context and current route suit the continuation. Do not assume a follow-up changes its model. When a different route is necessary, use a supported change mechanism or launch a replacement attempt after reconciling the existing worker and preventing conflicting writes.
- Keep the requested route distinct from the host-confirmed model and effort. If the host exposes no confirmation, record `unknown` and describe the route as requested, not verified.

## Codex mapping when supported

Use the live tool schema as the authority. When advertised, choose `gpt-5.6-luna` for the efficient route and `gpt-6-astra` for the stronger route. Use `xhigh` reasoning for every Luna dispatch or continuation; this overrides the generic low/medium effort guidance above. Preserve an explicit user request for a different effort. If the host cannot select or support Luna at `xhigh`, choose another permitted model or report the limitation rather than silently lowering Luna's effort. These are defaults for Codex, not requirements for other hosts. Respect explicit overrides and resolve alternatives from the host when either identifier is unavailable.

For a `collaboration.spawn_agent` surface exposing `model`, `reasoning_effort`, and `fork_turns`, pass the model and supported effort explicitly. Where full-history forks inherit the parent model and reject overrides, set `fork_turns: "none"` for a self-contained assignment, or use a supported positive history count when that context is necessary. Do not combine overrides with an omitted `fork_turns` when omission means a full-history fork. Check the schema rather than assuming these parameter names or rules exist on every Codex client.

Before continuing an existing Luna worker, compare its recorded requested effort and any host-confirmed overrides with the required effort (`xhigh` unless the user explicitly chose otherwise). If the request is unknown or either setting conflicts, use a supported effort change or a replacement attempt with the required effort; a plain follow-up must not be assumed to change effort.

Other Codex clients may expose named agents or configured defaults instead. Use them only when present. Custom role files can pin model and effort even when a spawn requests different settings; verify the effective route where possible. See the [official subagent configuration documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents) when configuring a host is explicitly part of the user's task. Merely installing this skill does not install agent TOML files or change host defaults.

## Escalate from evidence

Escalate when a worker reports a concrete reasoning limitation or the coordinator finds an unsupported conclusion, unresolved contradiction, or repair that needs broader reasoning. Prefer narrowing the task or supplying missing context when that addresses the cause. Missing access, tools, source data, and worker capacity need their actual dependency resolved, not a more expensive model.

Persist the new attempt, route, reason, accepted inputs, and useful prior artifacts before relaunching. Reconcile uncertain launches before retrying, including launches rejected with ambiguous status. Keep independent branches moving. Preserve the normal verification and acceptance checks after an escalation; a stronger model is not evidence of correctness.
