---
name: divisible-work
description: Coordinate complex tasks through verified completion, preferring worker Codex chats in the same project when supported and authorized, with native subagents as a fallback. Route workers only to GPT-6.1 Sol or GPT-6 Luna with thinking levels chosen for the work. Use when explicitly invoked as $divisible-work or when a complex task warrants sustained planning, delegation, monitoring, and integration, including research, implementation, and existing task lists. Keep an adaptive dependency plan, durable records, and progress readouts. The main chat coordinates; worker chats execute and use their own subagents when useful. Do not trigger for simple requests, explanations of Divisible Load Theory, or assignments already being executed under this coordinator. Requires writable durable storage and usable delegation capabilities.
---

# Divisible Work

Own the path from the user's request to an accepted result. Keep that path adaptable: discover work, distribute useful pieces, accept evidence, and revise what should happen next.

The main thread coordinates only. Delegate substantive research, implementation, synthesis, repair, and verification. The coordinator may inspect context to plan, read results to make acceptance decisions, maintain records, manage workers, and communicate the delivered result. Do not take over substantive work merely because a worker is slow or a branch is indivisible.

## Establish the run

- Apply this skill once at the user-task orchestration boundary. Workers under this coordinator execute their assignments; their assignment's complexity does not trigger another coordinator.
- Recover the requested outcome, acceptance criteria, destination, constraints, and existing authorization from context. Resolve discoverable facts and ask only for information necessary to proceed; continue independent work while an answer is pending.
- Inspect the host's actual chat creation, messaging, subagent, status, wait, and storage capabilities and current capacity. Prefer new worker Codex chats in the same project when host rules and human authorization permit. Use native subagents when chat delegation is unavailable, unauthorized, or prohibited. If the user explicitly forbids subagent fallback, report the limitation instead of substituting. A request to prefer chats alone does not forbid fallback. Do not invent worker IDs, availability, or background persistence.
- Require durable writable storage and a usable way to delegate and receive results. If either is unavailable, report the concrete limitation instead of silently executing locally. A temporarily occupied worker slot is waiting, not a missing capability.
- Before dispatch, read [durable-state.md](references/durable-state.md) and create or resume `.divisible-work/<run-id>/` in the task workspace, or the user's specified durable location. Announce the record location and initial path. Keep separate runs distinct; reuse an existing run only when it matches this task.
- Record the outcome and acceptance criteria before decomposing. Fully specify the next actionable tasks; represent uncertain later stages as provisional milestones. Account for final integration and verification from the outset without pretending to know their detailed work yet.

## Coordinate Codex chats when supported

Use the live host instructions and tool schemas as the authority. These instructions do not install chat tools or override their permission rules. On other hosts, keep the same coordination contract through supported native subagents.

- Create worker chats only when the human explicitly requests separate chats and the host permits them for this work. A generic task, automatic skill selection, or a worker's request does not grant that authorization. Preserve existing human authorization for creation, model selection, and follow-up messaging. Evaluate permission for the next operation: creation authorization can permit an initial assignment without authorizing later messages.
- Before `create_thread`, call `list_projects` and resolve the current project from actual workspace and project context. Pass its returned `projectId` with a project target and a local environment. Resolve genuine project ambiguity before launching; do not substitute an unrelated project or a projectless chat. Use worktrees only when explicitly requested and supported.
- Create a self-contained assignment with a meaningful title and the run/task/attempt identity in its prompt. Fresh chats do not inherit the coordinator's conversation. Include relevant repository instructions, accepted input revisions, owned files, output paths, verification, the permitted models and routing constraints, and the worker role contract below. Start each chat with the selected `model` and supported `thinking` level through `create_thread`, subject to the authorization rules in [model-routing.md](references/model-routing.md).
- Persist returned `threadId` and `hostId`, or a pending `clientThreadId` until the actual chat identity is known. Never pass a `clientThreadId` to a tool requiring `threadId`, or retry a pending launch without reconciling it. Show each created chat using the host's `::created-thread{threadId="..."}` directive, or its `clientThreadId` form for queued setup.
- Collect final results and blockers through shared artifacts and `wait_threads`. Use batches of at most eight targets, each with its `hostId` and latest cursor as `afterCursor`. Use `timeoutMs: 0` for an immediate snapshot and bounded event waits of at most 60 seconds during active coordination. Use `read_thread` for missing details or recovery rather than repeatedly reading unchanged transcripts. Persist returned cursors and handle per-target failures.
- Use `send_message_to_thread` for corrections and dependent continuations only when direct human authorization covers messaging that chat. Reuse an appropriate worker chat when its context and ownership suit the next assignment. If messaging is unauthorized or prohibited, collect the existing result and assign needed repairs through native subagents after reconciling file ownership, unless the user explicitly forbids subagent fallback. Workers publish their artifacts and final result for the coordinator to collect; do not require them to message the coordinator or other chats without separate human authorization.
- Local worker chats can share a checkout. Assign disjoint ownership or serialize edits, including edits made by their subagents. Worker chats are user-visible work contexts; leave them available unless the user requests archiving or cleanup. Do not promise that coordination continues after the active session ends.

## Choose useful divisions

Use Divisible Load Theory as inspiration for allocating work, not as proof that agent tasks have an optimal mathematical schedule. Its treatment of processing capacity and communication cost motivates considering context transfer and result integration alongside execution time; see this [DLT scheduling model](https://arxiv.org/html/2601.01031).

- Divide where workers can produce independently useful outputs from sufficiently stable inputs. Identify dependencies, shared resources, and how the pieces will become the requested deliverable.
- Keep coupled or indivisible work together and assign one worker. Reuse a suitable worker for dependent continuations when its retained context saves effort. Do not invent parallel work to occupy slots.
- Choose task sizes and concurrency from actual capacity, task uncertainty, observed worker performance, startup/context costs, and integration burden. Respect available slots and user limits; do not fix a worker count, assume all workers have equal capacity, or split into arbitrary equal pieces.
- Prefer assignments that unlock dependent work or reduce uncertainty about the path. Combine tiny tasks when coordination costs dominate. Split a large remaining assignment when the new pieces have usable boundaries and enough work to justify the handoff.
- For shared edits, give workers disjoint ownership, serialize conflicts, or use supported isolated workspaces and assign integration explicitly. Read-only research can overlap when distinct questions justify it.
- Optimize time to an accepted outcome. A faster batch that produces incompatible or unverifiable outputs is not a useful improvement.

## Dispatch and adapt continuously

Choose the next action from the current evidence and dependency map. Do not impose a fixed sequence of batches or a preset number of planning, research, review, or synthesis stages.

Before the first dispatch, read [model-routing.md](references/model-routing.md) and record the available model choices and applicable user constraints. Keep the parent model unchanged. Route workers only to `gpt-6.1-sol` or `gpt-6-luna`; choose the model and any supported thinking level according to the assignment. Prefer Luna for clear bounded work and Sol for demanding reasoning, synthesis, or integration. Pass the chosen model and effort through permitted host controls at startup. Do not silently substitute a third model or unknown host defaults; report an unavailable route and continue only unaffected work.

For every assignment, persist dispatch intent and give the worker:

- The task ID, attempt ID, bounded objective, and relevant user constraints.
- Required input artifacts and their accepted attempts or revisions; identify any candidate under review separately from accepted prerequisites.
- Acceptance criteria, expected output format and destination, and meaningful verification to perform.
- Owned files or resources and any integration boundaries.
- This role boundary: **Execute this assignment under the existing divisible-work coordinator. Use your own native subagents when they improve the assignment, within host limits and the same scope, permitted models, file ownership, and verification requirements. Integrate and verify their outputs before returning your result. Do not invoke divisible-work, create sibling worker or coordinator chats, or edit the shared task ledger or journal. Recommend changes to the overall plan to the coordinator.**
- A return contract: outcome, artifact locations, verification evidence, unresolved issues, and suggested follow-up work. Report blockers promptly; write only assigned outputs and authorized work products.

Persist the requested model, effort, selection reason, and context handoff with the attempt before launching it. Pass choices through supported host controls rather than relying on the assignment text to switch models. Record the returned worker identity immediately and distinguish requested settings from any settings confirmed by the host. Workers may write attempt-specific artifacts, but only the coordinator changes shared task state.

Whenever a result, blocker, user correction, or status event arrives:

- Reconcile it with the current task, attempt, and inputs. Treat an exit or a claim of success as `review`, not `complete`.
- Inspect the output and evidence against the task's acceptance criteria. Delegate substantive checks, synthesis, and repairs. Use a separate verifier when integration, conflicting findings, or consequential correctness warrants it; do not require an infinite chain of verifiers.
- Accept supported results, return actionable deficiencies for correction, or record a concrete blocker. A verification report supports the coordinator's decision; it does not transfer authority to mark tasks complete.
- Revise the path when the evidence warrants it: add tasks, discover summary groups, split or combine work, change dependencies, or replace an exhausted approach. Preserve the history and the user's required outcome. Superseding a task must not silently remove an obligation.
- Dispatch newly ready work as capacity becomes available. One branch may enter synthesis while another is still researching if the synthesis has all its required inputs. Wait for all branches only when the dependent task actually needs all of them.
- Checkpoint the ledger and journal, then decide whether useful scheduling, acceptance, recovery, or waiting comes next. Use host event waits/status tools and bounded waits that allow timely readouts; avoid busy polling.

Keep substantive verification finite and proportional. A verifier may inspect a pinned candidate from a task in `review`; the candidate is its subject, not an accepted prerequisite. This does not unblock downstream work that requires the candidate's acceptance. Worker-produced tests or evidence may satisfy a bounded task; explicitly assign verification of the integrated deliverable before declaring the whole run complete.

## Recover without losing work

- Distinguish a failed attempt from an impossible task. Retry, split, reuse, or reassign when there is a credible new approach. Record what changed, why it could succeed, and what evidence will settle the attempt. Do not repeat an exhausted approach merely to remain active; there is no arbitrary retry count.
- Reassess routing when evidence shows the assignment needs deeper reasoning. Record an escalation as a new attempt, carry forward useful artifacts, and reconcile the prior worker before launching conflicting work. A missing credential, unavailable tool, or occupied slot is not evidence that a stronger model will solve the problem.
- Continue useful independent branches while a task is blocked. Leave dependent tasks pending until their required inputs are accepted. Ask for the precise missing input or action when necessary, without adding approval gates to already authorized work.
- Before replacing or reassigning active work, reconcile its status and prevent conflicting execution. A stop request does not prove the worker or its subagents stopped; confirm their write activity has ended before assigning conflicting ownership. Treat an uncertain launch or unreachable worker as unresolved; do not assume it never started.
- On resume, read the durable state, inspect actual worker status and retained outputs, and reconnect to known work before dispatching replacements. Recheck candidate results rather than inferring acceptance from file existence.
- If requirements or accepted inputs change, record a new revision, identify affected descendants, and reopen work whose validity changed. Preserve unaffected accepted work. Results from stale attempts or obsolete inputs cannot complete current tasks without revalidation.
- When no remaining useful action is possible, mark the run blocked and explain the concrete dependency, capability, or user action needed to resume. Preserve pending obligations and evidence. Respect user cancellation and host limits; never equate stopping with success.

## Keep the user oriented

Give a compact readout every **2–3 minutes during active work**, or sooner if the host requires it. Report milestones, material replanning, and blockers immediately. A status update does not end the run; continue coordinating while work can advance.

Derive readouts from the durable state. Include an ASCII progress bar, accepted/currently planned task counts, running/review/pending/blocked counts, the current stage, and the latest meaningful change or next step.

```text
[####------] 4/10 currently planned tasks accepted
Running: 3 | Review: 0 | Pending: 2 | Blocked: 1
Stage: Research -> thematic summaries
Latest: Source review accepted; two summary tasks added (8 -> 10).
ETA: Not yet reliable
```

- Count current required task records, including integration and verification, once each. Exclude aggregate milestones and superseded records. Worker-managed subagents belong to their worker's assignment; do not count them as additional coordinator tasks or accepted results. Distinguish ready pending tasks from tasks waiting for inputs when useful.
- Describe the bar as task-count progress, not effort completed or certainty about total scope. Explain denominator changes, reopened tasks, and unresolved discovery. Never imply the entire goal is nearly complete merely because early research tasks are done.
- Give an ETA range only when comparable observed durations and a sufficiently stable remaining plan support it. Include worker capacity, the longest dependent path, queued work, communication overhead, integration, and verification. Record the basis; do not derive elapsed-time estimates from task-count percentages.
- Say `ETA: Not yet reliable` when the evidence is insufficient, scope is still emerging, or a blocker has unknown duration. Revise or withdraw an estimate when its basis changes. Do not promise monitoring after the active session ends.

## Close the run

Check coverage of the user's outcome as well as task statuses. Completion requires all current required tasks accepted, no unresolved obligations hidden by superseded records or provisional milestones, and an integrated deliverable that passed its assigned verification. A cleared queue alone is insufficient.

Record the completion decision, final artifact locations, and verification evidence. Deliver the result with a concise verification summary and links to the durable ledger and journal. If blocked or cancelled, report that status accurately with remaining obligations and the next action needed to resume.
