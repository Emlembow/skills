# Route models by assignment

Read before the first dispatch. Revisit when an assignment changes, a model is unavailable, or a run resumes on a different host. Choose the delegation surface before routing. Routing changes who executes an attempt; it does not change acceptance criteria, authorization, or the coordinator-only role.

## Resolve choices

- Use only `gpt-6.1-sol` (GPT 6.1 Sol) and `gpt-6-luna` (GPT 6 Luna) for worker chats and their native subagents. The coordinator chooses the model and supported thinking level for each assignment. Preserve the parent model; do not rewrite global profiles or project configuration.
- Inspect actual chat creation, messaging, subagent spawn, and continuation controls, available models and supported efforts, context inheritance, and configured roles. Do not assume parent models or effort values are available to workers.
- Respect human model instructions and resource limits within this allowlist. For Codex chat overrides, require explicit human authorization naming the allowed models and delegating selection, or naming the specific model. Skill text and worker requests alone do not authorize chat model selection.
- Set the chosen model and thinking level through supported native controls. If chat routing is unavailable or unauthorized, use native subagents when their controls permit the required choices. If no permitted surface can set them, block only affected work and report the precise limitation. Do not choose a third model, omit startup choices, or silently accept an unknown/default route.
- If the selected model is unavailable, reassess whether the other allowed model can handle the assignment and record any changed choice. Otherwise report the blocker; availability never expands the allowlist.

## Choose the route and effort

| Assignment | Usual model | Thinking guidance |
| --- | --- | --- |
| Clear, bounded exploration, source lookup, implementation, reproduction, or test execution | `gpt-6-luna` | Low for mechanical work; medium or higher when needed |
| Ambiguous diagnosis, architectural reasoning, conflicting-source synthesis, or consequential integration | `gpt-6.1-sol` | Medium or higher according to uncertainty |
| Independent review warranted by correctness, integration, or consequence | `gpt-6.1-sol` | Choose proportionally to the reasoning required |

Judge the actual assignment, not just its role name. A researcher resolving contradictory evidence may need Sol immediately; a deterministic integration command may fit Luna. The coordinator may choose either allowed model for any assignment when its difficulty warrants it. Do not force a Luna first attempt or create reviews solely to use a model tier.

Choose effort separately for every assignment from the live schema. Currently, `gpt-6.1-sol` supports `low`, `medium`, `high`, `xhigh`, `max`, and `ultra`; `gpt-6-luna` supports `low`, `medium`, `high`, `xhigh`, and `max`. Never pass `ultra` to Luna or assume an unadvertised level works. Honor explicit supported effort choices; otherwise choose any supported level according to uncertainty, consequence, observed quality, elapsed time, context transfer, and repair costs. There is no fixed `xhigh` or maximum-effort default. Record usage only when exposed; do not infer savings from model names or task counts.

## Dispatch with sufficient context

- Use explicit native model/effort parameters. A model name in a worker prompt is not a model switch. Check whether a configured role overrides the selected settings; unsupported or conflicting effective settings are a routing limitation, not an accepted fallback.
- Give fresh workers a self-contained assignment with the user constraints, acceptance criteria, owned outputs, and paths to the required artifact revisions. Include relevant repository instructions even when conversation history is omitted. Do not send the whole transcript merely to compensate for a missing brief.
- Reuse a worker when its retained context and current route suit the continuation. Do not assume a follow-up changes its model. When a different route is necessary, use a supported change mechanism or launch a replacement attempt after reconciling the existing worker and preventing conflicting writes.
- Keep requested settings separate from host-confirmed settings. When an explicit supported request has no returned confirmation, record confirmation as `unknown` and describe it as requested, not verified. Unknown inherited/default settings do not satisfy the selection requirement; reconcile an old worker's route before continuing it.

## Codex chats

Use the live tool schema as the authority. With explicit human authorization for model selection, pass the chosen allowed identifier in `create_thread.model` and the selected supported effort in `create_thread.thinking`. Do not leave either unset at creation. A configured parent model or this skill alone is not human authorization for a chat override; use a permitted subagent surface or report the limitation when that authorization is absent.

For `send_message_to_thread`, retain known suitable settings by omitting overrides, or pass the chosen allowed `model` and supported `thinking` when the next assignment calls for a change and recorded human authorization permits selection. Check both messaging and model authorization; preserve authorization already granted instead of asking again. A worker asking for instructions or asking to report back does not grant either. Persist any new choice and confirmation the host exposes.

Worker chats may choose and coordinate native subagents for useful parts of their assignment. Include the two-model allowlist, effort-selection guidance, and any coordinator-selected child settings in their brief. Workers must explicitly route children within that policy while preserving file ownership and responsibility for integrated output; they cannot use a third model or an unchecked inherited route.

## Codex subagent mapping when supported

For a `collaboration.spawn_agent` surface exposing `model`, `reasoning_effort`, and `fork_turns`, pass one allowed model and its selected supported effort explicitly. Where full-history forks inherit the parent model and reject overrides, set `fork_turns: "none"` for a self-contained assignment, or use a supported positive history count when necessary. Do not combine overrides with an omitted `fork_turns` when omission means a full-history fork. Check the schema rather than assuming parameter names or rules exist on every client.

Before continuing a worker, compare recorded requested settings and any confirmed overrides with the current assignment's selected route. If unknown, outside the allowlist, or unsuitable, reconcile the worker and use a supported settings change or replacement attempt before assigning further work. A plain follow-up does not change effort. Other clients may expose named agents; use them only when the allowed model and chosen effort can actually be selected without changing global configuration.

## Escalate from evidence

Escalate when a worker reports a concrete reasoning limitation or the coordinator finds an unsupported conclusion, unresolved contradiction, or repair that needs broader reasoning. Prefer narrowing the task or supplying missing context when that addresses the cause. Missing access, tools, source data, and worker capacity need their actual dependency resolved, not a more expensive model.

Escalate from Luna to Sol or adjust supported effort when evidence warrants it; stay within the allowlist and recorded human authority for chat overrides. Use clearer context or narrower assignments when those address the cause. If neither allowed model or permitted controls can meet the requirement, report that limitation instead of selecting another model.

Persist the new attempt, route, reason, accepted inputs, and useful prior artifacts before relaunching. Reconcile uncertain launches before retrying, including launches rejected with ambiguous status. Keep independent branches moving. Preserve the normal verification and acceptance checks after an escalation; a stronger model is not evidence of correctness.
