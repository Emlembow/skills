---
name: research-loop
description: Run autonomous, metric-driven experiments on a version-controlled implementation against a fixed evaluation harness. Use when the user asks to improve eval pass rate, benchmark score, prompt or policy quality, performance, cost, or another measurable outcome through repeated hypothesis, change, evaluate, keep-or-discard cycles. Protect generalization with holdout gates and reject hardcoded cases, benchmark leakage, scenario-specific prompt patches, or harness gaming. Do not use for one-off debugging, ordinary test fixing, or improving an Agent Skill artifact itself; use $skillopt-improve-skill for skill optimization.
---

# Research Loop

Improve the user-selected implementation through controlled experiments while keeping the evaluation harness fixed. Treat comparable measurements, recoverable changes, and an auditable decision trail as hard requirements.

## Preflight the workspace

Inspect the repository, harness, documentation, and Git state before writing files or asking questions.

- Require a Git repository and a recoverable baseline commit. Do not proceed in an unversioned workspace.
- Identify pre-existing changes. If they overlap the proposed writable scope, stop and ask the user to resolve or relocate them. If they are unrelated, require a new branch-backed worktree without altering the original worktree.
- Infer the evaluation command, metric, mutable and immutable paths, constraints, and required environment from repository sources when possible.
- Reject a request whose purpose is to improve the score by changing tests, fixtures, scoring logic, or evaluation data. During a legitimate run, treat any candidate that changes those surfaces as invalid regardless of its score.

Do not stash, overwrite, stage, or clean user changes during preflight.

## Establish the run contract

Resolve every field below before starting:

- Run tag, goal, and optional target value. If there is no target or total-run limit, explicitly confirm and record the user's authorization to run until interrupted.
- Primary metric, whether higher or lower is better, its exact extraction method, and which split determines target attainment.
- Evaluation command, resolved executable and extractor, working directory, timeout, per-evaluation resources, total-run limits or open-ended authorization, and expected runtime-generated paths.
- Writable implementation paths and immutable harness, test, fixture, and evaluation-data paths.
- Hard constraints that every candidate must satisfy.
- Acceptance rule and any ordered secondary or simplicity tie-breakers.
- Intended behavior or task specification and the population of future inputs the result must generalize to. Treat this, not the observed eval cases, as the optimization target.
- Feedback partitions, access policy, and query budget: development cases available for iteration, a disjoint supervisor-only validation gate, and a final test used once at completion when available.
- Determinism policy: use one run for a known-deterministic harness. For a noisy harness, predeclare candidate-versus-incumbent paired arms on identical seeds or inputs, exploration and fresh-confirmation repetitions, aggregation, and a minimum meaningful delta or uncertainty rule.
- Generated-state policy for every cache, database, checkpoint, or metric file: reset between arms, isolate per arm and repetition, or intentionally persist from an identical initialized snapshot. Never let earlier candidates warm later measurements asymmetrically.
- Required environment, secret-redaction rules, stop conditions, and user-authorized exceptions.

Ask only about material fields that cannot be inferred. Treat an explicit request that supplies and authorizes the complete contract as confirmation. Otherwise present the resolved draft once and obtain confirmation before creating the run. Do not renegotiate a contract after seeing candidate results.

Normalize the run tag to a unique lowercase slug containing only letters, digits, and single hyphens before using it in a path or branch. After confirmation, create `research-loop/<run-tag>` in the current clean worktree or in the required separate worktree from the agreed baseline. Only then create `.research-loop/<run-tag>/contract.md` inside the isolated run. Record the baseline commit; exact writable and generated paths; generated-state lifecycle; the evaluation protocol; and immutable-file SHA-256 digests, Git blob IDs, or an equivalent content fingerprint. Seal its SHA-256 before evaluation and recheck it before and after every launch. Paths alone are not an immutable-state record. Never record credentials or secrets.

Run evaluation from a trusted boundary that candidate code cannot modify, such as a read-only harness checkout, container mount, or supervisor outside the writable worktree. Freeze and fingerprint the resolved command, extractor, harness, tests, fixtures, and evaluation data there. If the environment cannot prevent candidate code from changing or forging the evaluator during a run, stop and report that evaluation integrity cannot be guaranteed.

## Protect generalization

For metrics computed over prompts, examples, requests, fixtures, workloads, or other enumerable cases, require evidence beyond the cases used to propose edits.

- Use visible development cases for diagnosis. Keep validation prompts, expected answers, case IDs, and per-case results hidden behind the trusted supervisor; expose only the predeclared aggregate or coarse failure categories.
- Seal the validation gate before candidate editing. Source it from the user, an existing untouched split, or an independent fresh-context generator working only from the behavior specification. Do not let the candidate-authoring context inspect its exact cases or answers.
- Limit validation queries in the contract, normally to one gate query per finalized candidate with no adaptive retries. Keep the budget and feedback resolution well below what could reconstruct individual outcomes; for a small gate, return only pass/fail or a coarse bucket rather than an exact score. When the budget is exhausted, stop or start a new run with a newly sealed gate and baseline.
- Reserve a final test for one completion-time measurement when available. If it fails, report the result and close the run; do not tune against that test in the same run.
- If the existing harness has no split, treat it as development feedback. Before autonomous optimization, create a disjoint gate of representative paraphrases, counterfactuals, edge cases, and regression cases from the intended behavior—not from candidate failures. If no credible disjoint gate can be established, stop or explicitly label the result as optimization of the visible harness rather than demonstrated general improvement.
- For non-case benchmarks, define the equivalent generalization boundary: multiple representative workloads, datasets, environments, scales, or time windows. Do not optimize a single configuration and claim universal improvement.

Reject shortcuts even when they raise the score:

- Do not add exact eval phrases, case IDs, expected answers, fixture values, benchmark names, seeds, score thresholds, lookup tables, or branches that detect the harness or a known case.
- Do not change a prompt or policy to say, in effect, “when asked this observed scenario, produce this answer.” Express a general principle, decision rule, or workflow that applies to unseen variants. A scenario-specific rule is allowed only when that scenario is an explicit product requirement and the rule also passes unseen paraphrase and counterfactual checks.
- Do not read protected cases or outputs from candidate code, scrape supervisor artifacts, infer answers from filenames or ordering, or weaken the intended task so more cases appear to pass.
- Do not accept a patch justified by one anecdote alone. State the broader failure class, the mechanism of improvement, at least one predicted unseen variant, and a counterexample that should remain unchanged.

Before evaluating, audit the candidate diff for suspicious overlap with development or protected cases and for new conditionals, constants, examples, or instructions narrower than the behavior specification. Classify direct leakage, case detection, answer encoding, or feedback-policy violations as `invalid`. Classify a plausible general rule that improves development but fails the disjoint gate as overfit and `discard` it.

## Initialize the ledger

Create `.research-loop/<run-tag>/logs/`, `.research-loop/<run-tag>/results.jsonl`, and append-only `journal.jsonl` as supervisor-owned artifacts outside the candidate-writable boundary. Keep their required repository-relative location for discovery, but use a read-only mount, separate supervisor process and permissions, or equivalent isolation so evaluated code cannot alter the contract, journal, ledger, or current and prior logs. If that isolation is unavailable, stop. Keep the run directory uncommitted. Stage candidate files by explicit path; never use broad staging that could include logs, harness files, or unrelated work. Persist complete sanitized output: redact known credentials, tokens, and user-designated secrets before writing logs, and do not run a harness that may emit secrets without a safe redaction method.

Use the journal for interruption recovery. Append and durably sync phase events for `prepared`, `committed`, `started`, `evaluated`, `decided`, `rolled_back` when applicable, and `finalized`. Include the iteration, pre-attempt `incumbent_commit`, and the candidate commit as soon as it exists. Make `decided` contain the final status, metrics, acceptance reason, intended incumbent, and required rollback action before changing branch state. At startup or resume, reconcile the journal, ledger, Git HEAD, process state, and fingerprints; execute the recorded decision rather than deciding again. Do not begin another attempt until an incomplete one is safely finalized or rolled back to the recorded incumbent.

Append one valid JSON object per baseline or candidate attempt. Include:

- Unique `iteration`, `role` (`baseline` or `candidate`), `commit`, `incumbent_commit`, nullable `rollback_commit`, `started_at`, `ended_at`, and `contract_sha256`.
- `status` (`keep`, `discard`, `crash`, `timeout`, or `invalid`) and `acceptance_reason`.
- `primary_metric`, aggregate `primary_value`, split-specific development and gate metrics, `aggregation`, and `secondary_metrics`.
- `repetitions`, each with pair ID, arm (`incumbent` or `candidate`), phase (`exploration`, `confirmation`, or `deterministic`), evaluated commit, seed or input identifier, metric values, duration, and log path; use an empty array when a pre-run integrity failure prevents evaluation.
- Total `duration_seconds`, `hypothesis`, `change_summary`, validation-query count, leakage-audit result, counterexample results, `constraint_results`, and nullable `failure` details.

For example:

```json
{"iteration":0,"role":"baseline","commit":"abc1234","incumbent_commit":"abc1234","rollback_commit":null,"started_at":"2026-01-01T00:00:00Z","ended_at":"2026-01-01T00:00:12Z","contract_sha256":"...","status":"keep","acceptance_reason":"valid baseline","primary_metric":"pass_rate","primary_value":0.72,"split_metrics":{"development":0.75,"validation":0.69},"aggregation":"single deterministic run","secondary_metrics":{},"repetitions":[{"pair_id":null,"arm":"candidate","phase":"deterministic","evaluated_commit":"abc1234","seed":null,"primary_value":0.72,"duration_seconds":12.4,"log":"logs/000-baseline.log"}],"duration_seconds":12.4,"hypothesis":"unchanged baseline","change_summary":"none","validation_queries":1,"leakage_audit":"pass","counterexample_results":{},"constraint_results":{"harness_unchanged":true,"writable_tree_unchanged":true,"generalization_gate":true},"failure":null}
```

A valid baseline uses `role: "baseline"` and `status: "keep"`. A failed baseline uses the same role with `crash`, `timeout`, or `invalid`; do not count the baseline among candidate status totals in the final report. Use `null` for unavailable metrics, retain every repetition value, and explain parsing, state-integrity, or constraint failures. Keep sanitized output in the referenced logs rather than flooding conversation context.

## Establish the baseline

Before iteration 0, verify that required tools, credentials, services, and inputs are available without changing the contract. Make at most three low-risk setup-only recovery attempts unless the contract states another bound. If setup remains unavailable, stop without claiming a baseline. Measure development and sealed-validation baselines separately and charge the latter to the validation-query budget; do not run the final test during baseline setup.

Initialize generated state according to the contract. Snapshot the complete writable tree and verify the sealed contract, trusted evaluator, immutable fingerprints, command, extractor, and generated-state snapshot immediately before running the unchanged implementation. Do not execute on a mismatch. Run the exact contracted protocol, redirect sanitized output to numbered baseline logs, verify that every launched process or external job has ended, then recheck all state before extracting metrics and appending the finalized baseline result.

Verify that metric extraction is unambiguous, repetitions are comparable, immutable fingerprints still match, the evaluation did not mutate implementation state or unexpected paths, and all baseline validity constraints pass. If the baseline crashes, times out, yields malformed metrics, mutates protected state, or exposes an invalid contract, record the appropriate `crash`, `timeout`, or `invalid` result and stop. After an evaluation has begun, any setup or contract repair requires a new tag and fresh baseline.

## Run experiments

Repeat without approval checkpoints until a stop condition is met:

1. Inspect the incumbent, prior results, and only the feedback authorized for development. Never inspect protected validation or final-test cases.
2. State one coherent, falsifiable hypothesis: the broader failure class, general mechanism, predicted unseen variant, and behavior that must not change.
3. Modify only writable paths. Prefer general instructions, algorithms, or invariants over enumerated examples. Keep the change small enough to attribute the result, while allowing multiple edits that implement one inseparable idea.
4. Review the diff and repository status for case-specific text or constants, harness detection, answer encoding, protected-data access, dependency, generated-file, symlink, ignored-file, external-target, or unrelated changes. Append the `prepared` journal event, then commit only explicit candidate paths with a message that names the general hypothesis and append `committed`.
5. Snapshot the committed candidate's complete writable tree. Verify the sealed contract, trusted evaluator, immutable fingerprints, command, extractor, generated-state policy, and unexpected paths immediately before evaluation. On a mismatch, do not run: set the status to `invalid` and skip to rollback. If recovery is uncertain, preserve the state, append the finalized attempt, and stop.
6. For deterministic evaluation, launch the candidate once and journal `started`. For noisy evaluation, materialize the exact incumbent and candidate commits in separate isolated arm worktrees or equivalent immutable snapshots; never switch the main experiment worktree between arms. Immediately snapshot each arm before its launch, then run both arms in randomized or predeclared order for every paired exploration and fresh-confirmation seed or input, reinitializing or isolating generated state identically for each arm. Log each arm separately.
7. After every arm, verify that its complete process tree and external jobs ended, journal `evaluated`, and compare that arm with its own immediate pre-launch snapshot while rechecking the sealed contract, evaluator, immutable fingerprints, generated-state policy, and unexpected paths. Any violation makes the attempt `invalid`; skip metric-based acceptance and proceed to rollback. If recovery is uncertain, preserve state, append the result, and stop.
8. When state checks pass, extract metrics and run counterexamples plus the sealed generalization gate within its query budget. Decide against the pre-attempt `incumbent_commit`; append `decided`. Keep only a candidate that passes the leakage audit, improves under the primary comparison, passes the disjoint gate, and causes no prohibited regression. For noisy evaluations, also require the contemporaneous paired comparison and meaningful-delta or uncertainty rule in both phases. On a deterministic tie, keep only when a predeclared secondary or simplicity rule wins without regression. Map an ordinary constraint or generalization failure to `discard`; reserve `invalid` for leakage, case detection, protected-feedback access, or compromised comparability or state integrity.
9. Make an accepted candidate the incumbent. For a rejected, crashed, timed-out, or otherwise safely recoverable invalid candidate, create a recoverable inverse commit such as `git revert`, record its hash, append `rolled_back`, and verify the full writable tree matches the prior incumbent. Never use destructive rollback against user state.
10. Append and durably sync exactly one finalized ledger object after the decision and any rollback. Only then append and sync `finalized` to the journal and choose the next idea.

Do not change evaluation data, partitions, feedback granularity, extraction, query budget, timeout, repetitions, acceptance thresholds, or tie-breakers to rescue a candidate. Do not rotate the gate because a candidate failed it. If any protocol change becomes necessary, close the run and establish a separately tagged contract and baseline.

## Handle failures

- Treat missing or malformed metric output as `crash`; record the parser failure and roll back a candidate after state and process cleanup succeeds.
- Treat an over-budget evaluation as `timeout`; terminate only its process tree and external evaluation jobs, then roll back. After every launch—not only a timeout—verify cleanup before continuing or rerunning; stop if cleanup cannot be verified.
- If the agent invoked the contracted command incorrectly, journal the operational error, perform the same process cleanup and post-state validation, preserve the candidate, then rerun the exact contracted command. Do not score the mistaken invocation as an experiment.
- If a trivial code typo leaves the hypothesis intact, finalize and roll back the crashed candidate, then create a new candidate that reapplies the complete hypothesis with the correction. Reject an idea that fundamentally violates constraints or cannot fit the budget.
- If a pre-run fingerprint or tree check fails, do not evaluate. If a post-run check fails, invalidate the score. Preserve unexpected content for diagnosis; resume only after user-authorized safe recovery or in a new isolated worktree and run.
- Never modify immutable tests, fixtures, scoring logic, or evaluation data to manufacture an improvement.
- Never use validation or final-test failures as candidate-specific instructions. Feed only the allowed summary back into the next hypothesis, and stop when the query budget is exhausted.

Continue through failed ideas and plateaus. Revisit evidence, simplify, combine compatible prior findings, or inspect relevant project references when the initial idea queue is exhausted; do not turn a plateau into an approval checkpoint.

## Finish the run

Stop when the target is reached, the user interrupts, or a contracted, integrity, or safety condition fires. Reconcile any incomplete journaled attempt and restore the best validated incumbent when safe; otherwise preserve the state and report exactly what remains unresolved. Leave the dedicated branch or worktree on the best validated incumbent state whenever recovery succeeds.

Report the run tag, intended generalization population, frozen evaluation command, artifact directory, development, validation, and one-time final-test metrics separately, validation-query usage, candidate status counts, best accepted candidate commit, final branch HEAD, accepted general rules, rejected overfit or invalid shortcuts, stop reason, and remaining uncertainty. Distinguish “visible-harness score improved” from “general improvement demonstrated.” Make the stronger claim only when the ledger contains a passing disjoint generalization gate.
