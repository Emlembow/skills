---
name: adversarial-review
description: Run a file-audited completion loop that requires two consecutive fresh-context subagents to independently try to disprove the same versioned, digest-checked task result. Use when the user invokes $adversarial-review, asks for two independent adversarial approvals, or requires a result to survive repeated attempts to find correctness, completeness, requirement, or verification gaps before completion. Do not trigger for an ordinary one-pass review, and never self-certify when fresh subagents are unavailable.
---

# Adversarial Review

Complete the task, then withhold completion until two independent reviewers return `DONE` for the same unchanged candidate. Preserve an inspectable record without exposing earlier work or findings to either reviewer.

## Enforce the invariants

- Let the main agent perform and revise the task. Let reviewers inspect and report only.
- Require two consecutive `DONE` verdicts from distinct, newly created subagents for one candidate digest.
- Run reviewer A first. Start reviewer B only after A returns a valid `DONE`.
- Reset the pass streak to zero after any `FAIL`, invalid verdict, reviewer error, unverifiable result, contaminated review, or candidate mutation.
- Never reuse a reviewer, carry a pass across candidate versions, or substitute the main agent's judgment for a reviewer.
- Treat only supported task-relevant gaps as failures. Keep taste, optional improvements, and non-blocking suggestions from turning a valid result into `FAIL`.

## Create the durable ledger

Create `.adversarial-review/<run-id>/` in the task workspace. Derive a short unique run ID from the task and time; add a numeric suffix on collision. Keep this structure:

```text
.adversarial-review/<run-id>/
├── task.md
├── state.md
├── v0/
│   ├── result.md
│   ├── artifacts/
│   └── manifest.sha256
├── f0a.md
├── f0b.md
└── complete.md
```

Create later versions as `v1`, `v2`, and so on. Align findings with their candidate: `v0` receives `f0a` and `f0b`; `v1` receives `f1a` and `f1b`.

Write `task.md` once with:

- The user's task, preserving its material wording.
- Explicit acceptance criteria derived from that task.
- The inputs and safe verification actions available to reviewers.
- Any environmental limitation that affects verification.

Write `state.md` with the current version and digest, pass streak, isolation mode, reviewer identities already used, and a short chronological event log. Record `context-only` when the host cannot enforce a filesystem sandbox; do not present procedural isolation as OS-enforced isolation.

## Record each candidate snapshot

Record the first completed result as `v0/`. Put the exact proposed answer or outcome in `result.md`. Copy every output file and the minimum supporting context needed to assess it into `artifacts/`; do not make mutable workspace paths the reviewer's only evidence. Include raw verification evidence when the result depends on checks the reviewer cannot safely repeat. Exclude credentials and unrelated private data.

Generate `manifest.sha256` over `result.md` and every file in `artifacts/` with an available SHA-256 utility. Hash `manifest.sha256` itself and use that value as the candidate digest. Record the digest in `state.md`.

Treat the recorded `vN/` as read-only during its review. The files remain ordinary writable files; the digest only detects changes. If any recorded bytes change, invalidate every pass for that version, create `vN+1/`, reset the streak, and start again with two new reviewers. If the task or acceptance criteria change, start a new run with a new `task.md`. Rehash the candidate before and after every review; a mismatch invalidates the review.

If the result cannot be represented in a self-contained packet, record the limitation and do not claim the gate passed.

## Isolate each reviewer

For every review, create a new random temporary directory outside the ledger. Copy only `task.md` and the current recorded `vN/` snapshot into it. Do not include:

- Earlier versions.
- `state.md` or `complete.md`.
- Another reviewer's findings.
- Main-agent reasoning, suspected defects, proposed fixes, or prior conclusions.
- Conversation history beyond the task itself.

Spawn a new subagent without inherited turns. Use `fork_turns="none"` when that control exists. Give it only the packet path, the adversarial assignment below, and the required findings filename. Use the strongest filesystem scoping the host provides. Under context-only isolation, explicitly prohibit reading the task workspace, parent directories, the ledger, chat history, or any path outside the packet.

Give reviewer A and reviewer B separate clean copies of identical task and candidate bytes. Run them sequentially and never tell B how A voted. Archive the exact reviewer-created findings file into the ledger only after that reviewer exits, then remove its temporary packet.

## Assign the adversarial review

Direct each reviewer to:

1. Read only the supplied `task.md` and candidate directory.
2. Try to disprove the result against every acceptance criterion.
3. Look for missing requirements, incorrect claims, counterexamples, incomplete outputs, broken edge cases, and insufficient verification.
4. Run safe checks only inside its disposable candidate copy and report the commands or reasoning used.
5. Avoid repairing or rewriting the candidate.
6. Create only the assigned `fNa.md` or `fNb.md` with this shape:

```markdown
# Adversarial finding fNa

- Reviewer: <fresh agent identity>
- Candidate: vN
- Candidate digest: <SHA-256 manifest digest>
- Verdict: DONE | FAIL

## Disproof attempts
<checks, counterexamples, and reasoning actually attempted>

## Task-relevant failures
<supported failures, or "None">

## Evidence
<evidence for the verdict>

## Unresolved claims
<anything material that could not be verified, or "None">

## Optional observations
<non-blocking suggestions, or "None">
```

Require exactly one verdict:

- `DONE`: The reviewer made a substantive attempt to disprove the candidate and found no supported task-relevant failure or unresolved material claim.
- `FAIL`: The reviewer demonstrated a correctness, completeness, requirement, or verification gap, or could not verify a material claim.

Do not accept a bare `DONE` without disproof attempts and evidence.

## Run the loop

1. Complete the task and record `v0`; set the pass streak to `0`.
2. Launch fresh reviewer A for `vN` and validate its identity, candidate label, digest, evidence, and verdict.
3. On A's valid `DONE`, archive `fNa.md`, rehash `vN`, and set the streak to `1`.
4. On A's `FAIL` or any invalid review, archive its output when available, record an error file when it produced no valid findings, reset to `0`, and do not launch B.
5. Launch fresh reviewer B only while the streak is `1`, using an uncontaminated packet for the unchanged `vN`.
6. On B's valid `DONE`, archive `fNb.md`, rehash `vN`, and set the streak to `2`.
7. On B's `FAIL` or any invalid review, archive the result, reset to `0`, and invalidate A's pass.
8. After any reset, address the candidate, evidence, packet, or task-definition failure. Publish `vN+1`, even if only the reviewability evidence changed, and restart with a new reviewer A.
9. If either reviewer reports `FAIL`, do not dismiss it while retaining a pass. Resolve it in the next version or add evidence that lets a new reviewer independently disprove the finding.

Treat a reviewer crash, timeout, missing file, malformed verdict, digest mismatch, access outside the packet, or communication with another reviewer as an invalid review and a reset.

## Certify completion

Before certifying, confirm that:

- `fNa.md` and `fNb.md` were created by distinct fresh agents.
- Both contain valid `DONE` verdicts for the current `vN` and identical digest.
- The candidate still matches `manifest.sha256`.
- The result being delivered is exactly the approved candidate.

Create `complete.md` only then. Record the final version and digest, both reviewer identities, links to both findings, isolation mode, and the final manifest verification. Report the result, ledger path, approved version, and the two-review outcome to the user.

If the host cannot create fresh subagents, the task cannot be packetized safely, or required verification remains unavailable, leave `complete.md` absent and report the gate as blocked.
