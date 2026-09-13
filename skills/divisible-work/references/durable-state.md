# Durable records and worked examples

Read when creating or resuming a run. Use these records to recover the coordinator's decisions without depending on conversation memory. The examples illustrate possible paths, not required stages or worker counts.

## Run directory

```text
.divisible-work/<run-id>/
  TASKS.md
  JOURNAL.md
  outputs/
    T001/
      A01/
```

Choose a unique run ID from the task and a timestamp or other collision-resistant suffix. Use paths relative to the run directory in its records where possible. Keep the user's deliverable at its requested destination; `outputs/` retains task evidence and intermediate artifacts. Before accepting evidence or closing the run, resolve recorded artifact paths from the run directory and confirm they point to the actual files. Do not overwrite another run or alter project ignore settings without a task-specific reason.

`TASKS.md` is authoritative current state. `JOURNAL.md` explains how it changed. Only the coordinator writes these two files; workers write their assigned artifacts or work products. A journal entry alone cannot override the current ledger.

## TASKS.md shape

Keep a compact index and enough task detail to resume. Include:

```markdown
# Run: <run-id>

Goal: <requested outcome and deliverable destination>
Acceptance: <observable criteria for the integrated result>
Constraints: <scope, authorization, resource limits, relevant user choices>
Routing: <available routine/reasoning models and efforts, user overrides, or host-default limitation>
Goal revision: G1
Run status: active | blocked | complete | cancelled
Updated: <timestamp>

## Current path

<Known dependencies and provisional milestones; identify what remains uncertain.>

## Tasks

| ID | Objective | Depends on | Status | Attempt / worker | Next action |
| --- | --- | --- | --- | --- | --- |
| T001 | <bounded output> | none | pending | A01 / unassigned | dispatch |

## T001

Goal revision: G1
Output / ownership: <format, location, owned files or resources>
Acceptance criteria: <checks needed to accept this task>
Inputs: <accepted prerequisites and their attempts/revisions; separately identify any candidate under review>
Dispatch intent: <attempt ID, assignment identity, timestamp, reconciliation note>
Attempts:
- A01: <worker handle, started/finished timestamps, requested model/effort or host-default, selection reason, context handoff, host-confirmed settings or unknown, outcome, artifact references>
Accepted evidence: <accepted attempt, output revision, verification result, timestamp>
Blocker / next action: <actionable detail, or none>
Replaces / replaced by: <task IDs and reason, if applicable>

## Next coordinator action

<What to do next; include active workers and uncertain launches to reconcile.>

## Latest readout

<Timestamp, accepted/current task counts, scope changes, ETA and its basis if used.>

## Completion decision

<Leave pending until the integrated outcome is verified; then link evidence and deliverables.>
```

Adapt presentation to task size; retain the information rather than mechanically copying unused fields. Keep detailed attempts under their task record, with a concise index. Stable task IDs describe obligations; attempt IDs distinguish executions. Include the task/attempt identity in every worker assignment so an interrupted launch can be found later.

Record routing per attempt, including fallbacks and escalations, so a resumed coordinator does not infer a model from a role name. Keep requested settings separate from host-confirmed settings; a successful launch or worker self-report alone does not prove which model ran. Record `unknown` when confirmation is unavailable. For older records without routing fields, recover what the host exposes and leave the rest unknown. Recheck model availability before new dispatches after a resume; do not replace valid running workers solely because defaults changed.

## Status meanings

| Status | Meaning and next action |
| --- | --- |
| `pending` | Not executing; ready when adequately specified and its prerequisites are accepted. A verifier may read a pinned, unaccepted candidate as its review subject. Distinguish waiting for capacity from waiting for a dependency. |
| `running` | A known worker owns the current attempt. Record its actual handle and assigned inputs. |
| `review` | A candidate result or verification report has arrived; the coordinator must decide acceptance or the next check. |
| `complete` | The coordinator accepted evidence against the current criteria and inputs. |
| `blocked` | A concrete obstacle currently prevents this task from advancing; record what resolves it. |
| `superseded` | Replaced by identified tasks or explicitly removed by a recorded scope change; retain its history and obligation coverage. |

These are current task states, not a fixed workflow. An unsuccessful attempt can return its task to `pending` for a changed approach or `blocked` for missing input. A completed task can reopen when its inputs or criteria cease to be valid. Dependencies must remain acyclic; repair a cycle or an unresolved replacement before scheduling dependent work.

For verification, record the candidate task/attempt/revision separately from accepted source prerequisites. Do not require the subject to be complete before its verifier can run. A supported negative verification report can complete the checking task while leaving its subject unaccepted; a repaired candidate needs a new check against its current revision.

Store each dependency's accepted attempt or output revision in the consuming assignment. When replacing T001 with T004 and T005, redirect affected dependencies to the replacements and explain how they cover the original obligation. If an old attempt later returns, retain its artifacts but do not accept it as the current result without revalidation. Prevent replaced workers from continuing conflicting edits.

Use task records for known required integration and verification work even if their specifications are provisional. Provisional tasks remain pending and cannot dispatch until adequately specified. Use aggregate milestones only for grouping; never count both a group and its member tasks. Adding, splitting, superseding, or reopening tasks can change the progress denominator or numerator; state that openly.

## Journal and checkpointing

Append concise timestamped entries containing task/attempt IDs, what happened, the evidence or reason, the resulting decision, and the next action. Record dispatches, model choices and routing changes, accepted or rejected results, retries, blockers, changed dependencies or scope, resumed workers, ETA basis changes, and closure. Link artifacts instead of copying worker transcripts.

Before a launch, persist an identifiable dispatch intent in the ledger and journal. After the launch, promptly record the returned handle. After other meaningful events, update the authoritative ledger and append the journal entry. Use atomic file replacement for ledger updates where supported. If a write fails, recover durable recording before dispatching more work.

The two Markdown files are not a transactional database. If interrupted between writes, reconcile actual workers and artifacts with the authoritative ledger, append a recovery note for any history gap, and checkpoint the corrected state. Do not silently infer a successful launch, failed launch, or accepted output from an incomplete entry.

Before repeating an uncertain launch, search the available worker inventory or history for its task/attempt identity. If its existence or write activity cannot be established, hold conflicting work and record the uncertainty. Resume independent work when safe. A worker's absence from a limited status listing is not proof that it never ran.

## Worked paths

### Research that reveals new groupings

A user requests an evidence-backed comparison. Record the desired comparison criteria, research questions, synthesis obligation, and final verification. Send distinct questions to available workers without assuming the number of themes the results will reveal.

One worker returns evidence that identifies several useful themes. Accept its supported findings and create theme-summary tasks with the relevant accepted sources as inputs. Dispatch a theme summary as soon as its own inputs are ready, even while an unrelated research question is still running. If another result reveals a missing theme or contradicts a source, add or revise the affected tasks and explain the changed task count.

Once the necessary summaries are accepted, assign a worker to produce the requested comparison. Assign bounded verification of coverage, citations, and contradictions. If verification exposes a gap, route that gap back into the plan, then recheck the affected result. Close only after the integrated comparison is accepted. The coordinator presents the produced comparison without doing the substantive synthesis itself.

### Execute an existing task list

A user supplies changes to a parser, a report exporter, and their shared schema. Preserve all three obligations. Determine which need a stable schema before implementation; delegate schema work first if necessary. Allow independent preparation or unrelated fixes to proceed meanwhile.

After the schema is accepted, dispatch parser and exporter tasks with distinct ownership and the same accepted schema revision. Assign integration and verification explicitly. If the exporter fails a compatibility check, keep the accepted parser work, create a changed repair attempt, and rerun affected integration checks. Reopen parser work only if the fix changes an input on which its acceptance depended.

### Indivisible work

A task needs one coherent line of reasoning or a tightly coupled edit. Assign it to one worker instead of inventing parallel subtasks. Reuse that worker for a suitable continuation, and delegate a bounded check when needed. Keep the durable record and normal readouts; idle capacity is acceptable.

### Interrupted run with a blocked branch

On resume, the ledger shows a launched worker, an uncertain dispatch, and a task blocked on a missing source. Reconnect to the known worker, reconcile the uncertain dispatch before replacing it, and keep independent ready tasks moving. Request the missing source when necessary. If all remaining required tasks ultimately depend on it, mark the run blocked with a precise resumption action; do not mark it complete or silently omit that branch.
