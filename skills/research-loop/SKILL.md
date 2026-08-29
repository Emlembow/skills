---
name: research-loop
description: Run autonomous, metric-driven experiments on a version-controlled implementation against a fixed trusted evaluation harness. Use when the user asks to improve eval pass rate, benchmark score, prompt or policy quality, performance, cost, or another measurable outcome through repeated hypothesis, change, evaluate, and keep-or-discard cycles. Protect generalization with holdout gates, immutable evidence provenance, and replay-safe recovery. Reject hardcoded cases, benchmark leakage, scenario-specific patches, and harness gaming. Do not use for one-off debugging, ordinary test fixing, or Agent Skill artifact optimization.
---

# Research Loop

Improve the user-selected implementation through controlled experiments. Treat evaluator integrity, comparable measurements, immutable evidence identity, recoverable changes, and an auditable decision trail as hard requirements.

Before creating or resuming run state, read [references/run-state-protocol.md](references/run-state-protocol.md). It defines the canonical artifacts, identifiers, lifecycle, evidence schema, and atomic completion boundary. Use [scripts/validate_run_state.py](scripts/validate_run_state.py) at every resume and completion boundary. The validator proves structural integrity, not scientific merit; independently apply the sealed acceptance rule before recording `decided`.

## Preflight the workspace

Inspect the repository, harness, documentation, and Git state before writing files or asking questions.

- Require a Git repository and recoverable baseline commit. Do not proceed in an unversioned workspace.
- Identify pre-existing changes. If they overlap the writable scope, stop and ask the user to resolve or relocate them. If unrelated, create a branch-backed worktree without altering the original worktree.
- Infer the evaluation command, metric, mutable and immutable paths, constraints, generated state, environment, and expected resource use from repository sources when possible.
- Reject a request whose purpose is to improve the score by changing tests, fixtures, scoring logic, evaluation data, or protected feedback. Treat any candidate that changes those surfaces as invalid regardless of score.

Do not stash, overwrite, stage, clean, or destructively reset user changes.

## Establish and seal the run contract

Resolve every material field before starting:

- Run tag, goal, optional target, and an exact control policy: bounded or run-until-interrupted authorization, user-authorized stop conditions, plateau count and action, stable IDs and immutable rules for required counterexamples, cleanup grace, actual-usage receipt sources, and one immutable rule for every hard constraint. A finite deadline or finite `evaluator_calls` total is the only automatic run bound; without either, explicitly confirm `run_until_interrupted` authorization.
- Primary metric, direction, exact extraction method, acceptance split, positive minimum meaningful delta, reporting-only secondary metrics, and hard constraints.
- Evaluation command, canonical absolute executable path and byte digest, extractor, working directory, timeout, resources, exact nonsecret environment values, broker-injected secret names, dependency fingerprints, and secret-redaction rules. Clear the inherited parent environment at launch. Forbid `PATH`, module search paths, loader/preload variables, language startup hooks, runtime option injectors, and other execution-control variables; use direct absolute commands and fingerprinted dependencies.
- Writable implementation paths; immutable harness, test, fixture, and evaluation-data paths; and expected runtime-generated paths.
- Intended behavior and future-input population. Treat this population, not observed eval cases, as the optimization target.
- Feedback mode: `sealed_gate` when a disjoint supervisor-only gate exists, otherwise `visible_only`; development feedback; an optional one-time final-test stage distinct from every promotion stage; each access policy; and validation-query budget. Give the final stage its own absolute acceptance threshold. Use a cross-split regression rule only when comparability was independently established and sealed. A configured final test is mandatory for ordinary resolved closure after a confirmed incumbent exists. An explicit interruption may skip it, but supports no final-verification claim. Limit a `visible_only` run's final claim accordingly.
- Distinct sealed resource-manifest, capability, access, and effective-configuration identities for every stage; every identity class must be individually unique across stages. Make only the first development stage visible; keep later promotion stages and the final test protected. Charge a validation query for every protected-stage launch, not merely for the acceptance split.
- Determinism policy. For noise, predeclare paired incumbent/candidate arms on identical seeds or inputs, exploration and fresh-confirmation repetitions, aggregation, and uncertainty rule.
- Generated-state policy for every cache, database, checkpoint, or metric file. Use the validator-supported `reset_each_launch` policy: bind the canonical empty-state artifact, require every generated path absent before launch, and remove it after owned-process cleanup. Do not share caches or runtime state across arms or repetitions.
- Budget totals and units, deadline, per-attempt maximum, actual-usage sources, and separate operational and final-test reserves. Require `evaluator_calls`, and require `validation_queries` whenever any stage is protected. Before sealing, prove the per-attempt caps cover the largest minimum plan, the final reserve covers the complete final plan, and each finite total covers a baseline, one candidate, the operational reserve, and the full final reserve. A final attempt may consume only the final-test reserve and must preserve the operational reserve.
- Task-owned evidence stages, required telemetry, and promotion rule. Missing or non-finite required telemetry is `unknown`, never an inferred pass.
- Whether ideation is single-context or an optional independent cohort. Keep implementation and evaluation serial; use separately tagged isolated runs if concurrent execution is necessary.

Ask only about fields that materially affect the run and cannot be inferred. Treat a request that supplies and authorizes the complete contract as confirmation; otherwise present the resolved draft once and obtain confirmation before creating the run. Do not renegotiate a scientific protocol after observing candidate results. Close the run and create a new tag and baseline if the metric, evaluator, split, feedback, repetitions, threshold, or acceptance rule must change.

Normalize the tag to a unique lowercase slug of letters, digits, and single hyphens. Create the Git branch `research-loop/<run-tag>` in the clean worktree or required isolated worktree, then create `.research-loop/<run-tag>/`. Keep the run directory uncommitted and outside the candidate-writable boundary.

Record the complete machine-readable contract in `contract.json` and a user-readable audit copy in `contract.md`. Use contract schema version 2, bind the installed protocol version and exact validator-byte digest, and seal the exact `contract.json` bytes in `contract.sha256`. A different validator may not resume or migrate the run; use the sealed validator or start a new tag. Include the baseline commit, boundaries, control policy, generated-state lifecycle, direct executable's canonical path and byte hash, resolved command, canonical sanitized environment preimage and digest, and a complete recursive manifest of content fingerprints for every file under the immutable extractor, harness, test, fixture, evaluation-data, and configuration scopes. List secret environment names without values and inject them only through the trusted supervisor; secrets may authorize access but may not alter evaluation selection or scoring. Paths or opaque claimed digests alone are not an immutable-state record. Recheck live fingerprints before and after every launch. Never record credentials or secrets.

Run evaluation from a trusted supervisor boundary that candidate code cannot modify, such as a read-only harness checkout, container mount, or separate supervisor process and permissions. Stop if candidate code can alter or forge the evaluator, contract, ledger, journal, prior evidence, or acceptance decision.

## Protect generalization

For metrics over enumerable cases, require evidence beyond the cases used to propose edits.

- Use visible development cases for diagnosis. Keep validation prompts, answers, IDs, and per-case results behind the supervisor; expose only the contracted aggregate or coarse failure categories.
- Seal the validation gate before editing. Source it from the user, an untouched split, or an independent fresh-context generator using only the behavior specification.
- In `sealed_gate` mode, charge every protected-stage launch to the fixed query budget, normally only after its integrity-clean candidate passes every preceding stage and remains promotion-eligible. Do not query protected stages for crashes, timeouts, invalid attempts, or preceding-stage failures. Keep feedback too coarse to reconstruct cases. When exhausted, stop or begin a new run with a new sealed gate and baseline.
- Use the final test once at ordinary completion when configured. If it fails, report and close; do not tune against it in the same run. If an explicit stop interrupts before it, record that it was not run and make no final-verification claim.
- If no credible disjoint gate exists, label the outcome as visible-harness optimization rather than demonstrated general improvement.
- For non-case benchmarks, use the equivalent boundary across representative workloads, datasets, environments, scales, or time windows.

Reject exact eval phrases, case IDs, expected answers, fixture values, benchmark names, seeds, lookup tables, score-threshold branches, harness detection, and scenario-specific instructions derived from observed cases. A narrow rule is acceptable only when it is an explicit product requirement and passes unseen paraphrase and counterfactual checks.

Before evaluation, audit the diff for protected-data access, suspicious overlap, answer encoding, generated or ignored files, symlinks, external targets, and changes narrower than the behavior specification. Direct leakage or compromised comparability is `invalid`; a plausible general rule that fails the gate is ordinary overfit and `discard`.

## Initialize or recover durable state

Acquire one supervisor lock scoped to the run. Use an OS lock or atomic lock directory plus verified process identity; a stale-prone PID text file alone is insufficient. Refuse a second live controller.

Treat `contract.json`, `contract.sha256`, `journal.jsonl`, `results.jsonl`, finalized evidence files, attempt boundaries, `run.finalized.json`, and Git history as canonical. Treat `contract.md`, live status, summaries, reports, candidate rankings, and reviewer memos as derived or audit-only; they cannot override canonical evidence or acceptance.

Create supervisor-owned `logs/`, `evidence/`, `designs/`, `snapshots/`, `boundaries/`, and `quarantine/` directories. Persist complete sanitized output without exposing secrets. Write in-flight output to `.partial` or `.tmp` names and publish finalized artifacts with atomic replacement. Ignore partial output for decisions. Move late or ambiguous output to `quarantine/`; never let it promote a candidate. Require every finalized canonical artifact to be referenced; reject orphans and seal the exact sorted artifact manifest at run closure.

At startup or resume, run:

```bash
python3 <skill-root>/scripts/validate_run_state.py <run-dir> --allow-incomplete
```

Exit `0` means a clean boundary; exit `3` means one structurally recoverable trailing attempt and identifies its next append-only recovery phase; any other nonzero result is an integrity failure. The recovery phase never infers process liveness. Reconcile the validated journal, ledger, Git HEAD, fingerprints, and durable process identity. Resume only a process proven to be the recorded launch. Requeue only an intent proven never to have launched. If ownership, provenance, or completion is uncertain, preserve the state and stop; never guess that work completed. Finish or recover the one trailing attempt before creating another.

## Establish the baseline

Verify tools, credentials, services, and inputs without changing the contract. Make at most three low-risk setup-only recovery attempts unless another bound is contracted. If setup remains unavailable, stop without claiming a baseline.

Initialize generated state according to the contract by proving every generated path absent and binding the canonical empty-state artifact. Snapshot the complete writable tree and effective evaluation configuration. Verify the contract, executable bytes, canonical environment digest, evaluator, immutable fingerprints, command, extractor, clean index and worktree, absence of untracked or ignored files, and state snapshot immediately before launch. Run the unchanged implementation through the exact executable with the parent environment cleared and only the sealed nonsecret values plus named broker-injected secrets, drain its complete process tree and external jobs, remove generated paths, then recheck the same state before extracting metrics.

Finalize the baseline through the same evidence and boundary protocol as a candidate. A valid baseline is `status: keep`, `lane: confirmed`, and `evidence_maturity: complete` under the contracted stages. A crash, timeout, malformed metric, mutation, or integrity failure must be recorded and stops the run. Measure development and any contracted validation baseline separately, charge gate use, and do not run the final test.

## Select a falsifiable experiment

Maintain one official confirmed incumbent. Default to one candidate implementation and evaluation at a time.

Before the first edit and whenever the contracted plateau trigger fires, inspect a frozen snapshot of the incumbent and authorized evidence, then form a small read-only pool of materially distinct candidate cards. Prefer three to five mechanism families or intervention surfaces plus a cheap falsifier; for a localized obvious change, one card and its falsifier is sufficient. Rank by expected information gain, supporting evidence, cost, risk, and generality—not by rhetorical confidence.

Optional fresh-context ideators may create cards independently from the same frozen snapshot. Do not show them protected cases or sibling proposals, and do not spend gate queries on speculative cards. Parallel ideation does not authorize parallel code changes or evaluation.

For the selected card, record a stable `experiment_id`, mechanism family, intervention surface, intent, `implementation_parent` (baseline or a confirmed attempt only), `evidence_sources` (which may cite any lane without inheriting its code), hypothesis, supporting and weakening metric signature, predicted unseen behavior, invariant or counterexample, cheapest falsifier or ablation, fail-fast condition, allowed files, forbidden changes, cost estimate, and rejected alternatives. Retries that preserve this design reuse `experiment_id` but receive a new `attempt_id`; a material mechanism or code-scope change creates a new experiment or explicit pre-evaluation amendment. A plan is not evidence.

## Admit, launch, and evaluate one attempt

Before every attempt, freeze the complete ordinal launch plan, including the sealed timeout for each launch, and calculate remaining budget from recorded actual use. Atomically publish a prelaunch admission artifact whose requested totals exactly equal the plan. Admit only if the time window covers the sum of serial launch timeouts and worst-case time and resource use fit the deadline, per-attempt caps, remaining totals, and typed reserves. Bind the exact plan and admission file hashes in `prepared` and every launch intent. A denied admission is terminal and may not produce a launch intent. A cheap probe may reject or shortlist an idea but never satisfy acceptance.

Run one transaction:

1. Freeze the selected design, accepted incumbent, exact actual pre-attempt HEAD, contract digest, evaluator digest, effective configuration, initial generated-state snapshot, seed or input, and allocated resources. Require the HEAD tree to equal the incumbent tree and the object ID to continue the recorded keep-or-inverse-commit history. Assign a unique `attempt_id` and one immutable `result_id` per planned arm and repetition.
2. Bind the candidate design path and digest in `prepared`, then modify only writable paths. Audit the complete diff, commit explicit candidate paths with the general hypothesis in the message, then append and sync `committed`. Never use broad staging.
3. For each planned launch, create a supervisor-owned `evaluated_tree_snapshot` that binds the evaluated commit's tracked Git tree and attests that untracked writable and unexpected paths are empty; bind the canonical empty generated state separately. Recheck the live workspace HEAD, index, tracked tree, immutable hashes, unexpected untracked or ignored paths, and absent generated paths. If they mismatch, do not launch; decide `invalid`, recover safely, and finalize without repetitions.
4. Before each arm or repetition, recheck the sealed executable bytes and reconstruct the cleared launch environment from the contract, then append and sync `launch_intent` with a unique launch ID, result ID, invocation digest, sealed timeout, effective-configuration, initial-state and evaluated-tree digests, the exact pre-spawn admission digest, allocation, intended partial log, and launch ordinal. Do not create it until admission is affirmatively allowed and deadline-fitting, remaining admitted time covers this launch and the full unlaunched timeout suffix, and the preceding launch is terminal and cleanup-resolved. Immediately after spawn, append and sync `launched` with either PID plus process-group and start identity, or only the external job's durable identity; recheck at that receipt that spawn time plus its timeout still fits the admission and deadline. If an intent is proven never to have launched, append the protocol's terminal `launch_aborted` receipt and make the attempt non-promotional. Never launch work that cannot be uniquely reclaimed or terminated.
5. For deterministic evaluation, run once. For noise, use live dedicated incumbent and candidate Git worktrees in the same repository; bind canonical absolute roots plus filesystem and per-worktree Git-directory identities, require distinct nonnested roots, and retain the clean worktrees for validation. Pair identical seeds or inputs, require equal normalized configuration and initialized-state digests, counterbalance first-arm order, and retain every repetition. A noisy confirmed baseline or final test uses the sealed single-arm exploration and confirmation counts with fresh identities. Never switch one worktree between opposing arms.
6. After each launch, verify the entire owned process tree and external jobs ended, append and sync `launch_finished`, remove runtime-generated paths, then recheck the live clean workspace, contract, evaluator, immutable paths, and unexpected files. Elapsed launch time beyond the sealed timeout, or actual use beyond an allocation, admitted worst case, cap, or total, forces the non-promotional `timeout` status while preserving the truthful usage receipts. If cleanup is not yet verified, append `cleanup_confirmed` only after independent verification and do not evaluate sooner. If ownership remains unknowable, append the fenced unresolved receipt, admit no evidence, mark budget accounting incomplete, and preserve and stop. After successful cleanup, deterministically normalize the canonical evaluator JSON, atomically publish matching evidence and log artifacts, and append `evidence_published`. A completed launch without that receipt is not evidence. Then append `stage_completed` with its exact published result IDs and rule outcome. Do not enter a later protected stage until every earlier stage has a durable pass. Append `evaluated` only after every entered stage has a checkpoint and every intent is terminal.
7. Run every sealed counterexample and record exactly one `true`, `false`, or `null` outcome under each sealed ID; promotion requires all `true`. Before the first candidate intent into any protected stage, append `integrity_cleared`, bound to the candidate commit and latest visible-stage tree snapshot, with passing scope/leakage audits and literal `true` for every hard constraint. Enter each protected stage only after the prior stage checkpoint passed; charge every protected launch to `validation_queries`, give it its own launch receipt and aggregate result artifact, and expose no protected cases. Decide against the pre-attempt incumbent and append `decided` before changing branch state. For noise, require contemporaneous paired exploration and fresh confirmation. A deterministic tie is not a promotion.
8. Promote only when all constraints and leakage checks pass, the primary contracted comparison succeeds, and every required evidence stage is complete. A disjoint gate must also pass before claiming general improvement; a `visible_only` contract may promote against its visible acceptance rule but supports only a visible-harness claim.
9. Make a promoted candidate the incumbent. For any safely recoverable non-promotion, create an inverse commit such as `git revert`, record its hash, append `rolled_back`, and verify the complete writable tree matches the prior incumbent. Never destructively roll back user state.
10. Verify every referenced canonical artifact is finalized, append and sync exactly one result record, append and sync `finalized`, then atomically publish the nonce-bearing attempt boundary described in the protocol. Bind that exact prior boundary in the next `prepared` event. The boundary—not a log, metric, process exit, result row, journal event, Git state, or status file—is the completion fact. Run the state validator before selecting another idea.

Every repetition must bind the evaluated commit and `evaluated_tree_snapshot` to the raw result, sanitized log, resolved effective configuration, generated-state snapshot, evaluator fingerprint, seed or input, and byte hashes. Cite prior evidence by its existing `result_id` without copying it into a new attempt. Give every genuinely new launch a new `result_id`, even when its output bytes match an earlier run; content-address shared artifact bytes by digest, never by filename, label, or metric value.

Separate raw results from interpretation. Record one finding as `positive`, `negative`, `diagnostic`, `uncertain`, or `procedural`, with scope, caveats, next action, and `revive_if` when relevant. Assign exactly one supervisor-owned lane:

- `confirmed`: complete contracted evidence and accepted; the only lane that may replace or seed the incumbent.
- `incubator`: promising but required evidence is incomplete or unknown; validate it, but do not use it as an implementation parent.
- `candidate`: protocol-clean and interpretable but not accepted; retain for comparison, falsification, or a separately contracted follow-up.
- `diagnostic`: invalid, crashed, timed out, cancelled, interrupted, or otherwise non-promotional evidence.

Use `status: keep` only with `confirmed`; use `deferred` for `incubator`; use `discard` for a completed non-promotion; and use `crash`, `timeout`, `invalid`, `cancelled`, or `interrupted` precisely. A finding count, preliminary score, or reviewer vote never establishes maturity or promotion.

## Synthesize without erasing evidence

Maintain a bounded derived summary citing exact experiment, attempt, and result IDs: active mechanism, accepted rules, dead ends, negative evidence, open questions, and explicit revive conditions. Do not copy protected feedback into it.

At a plateau, high-impact provisional result, or final claim, use an independent proposer and skeptic when available and budgeted. Freeze their authorized evidence cutoff. Ask what evidence supports continuing, what would justify pivoting or simplifying, and what cheapest test could falsify the leading explanation. Reviewers may propose tests or downgrade claims; they cannot inspect holdouts, change the evaluator, or override acceptance.

Use plateau signals as exploration prompts, not proof of exhaustion: repeated valid non-improvements, no meaningful secondary movement, dominance by one mechanism family, or repeated failures in the same family. Respond with the cheapest falsifier, simplification or ablation, a compatible evidence-backed combination, an underexplored surface, then a fresh candidate pool. Preserve the immutable ledger throughout.

## Handle failures and stopping

- Missing or malformed metrics are `crash`; an evaluation whose actual use exceeds its launch allocation, admitted worst case, or per-attempt cap is non-promotional `timeout`. Terminate only owned work, verify cleanup, recover the incumbent, and finalize the attempt.
- A mistaken invocation is operational evidence, not a scientific result. Close it without promotion and create a new `attempt_id` under the same unchanged `experiment_id` for the exact contracted rerun.
- A trivial implementation typo still closes as its own failed attempt. Reapply the full unchanged design in a new attempt; a material fix is a new experiment or amendment.
- A pre-launch fingerprint mismatch prevents execution. A post-launch mismatch invalidates the score. Preserve unexpected content for diagnosis and stop if safe recovery is uncertain.
- Never change evaluation data, feedback granularity, extraction, query budget, timeout, repetitions, or thresholds to rescue a candidate. Never rotate a gate because it failed.
- Never use validation or final-test failures as candidate-specific instructions.

Check for an external stop before candidate creation, before each repetition, and during long waits. On stop, first append `stop_requested` with nonempty `source`, `reason`, and `rule_evaluation`, a `stop_kind` of `user_interruption`, `ordinary`, `integrity`, or `resource_exhaustion`, and boolean `final_test_skip_authorized`; then fence baseline and candidate launches and drain active work through the sealed cleanup grace. Set that authorization true only for `source: user` with `stop_kind: user_interruption`; when true, no later launch is legal. Otherwise, if a configured final verification is required, the sole post-stop launch exception is exactly one terminal `role: final` attempt using only the final reserve. Cancel only with verified ownership and the sealed rule. If the frozen plan has an unlaunched suffix, append `plan_tail_cancelled` naming its exact ordinals and checkpoint the interrupted stage as `unknown`. Record `cancelled` or `interrupted`, not `crash`, when that is the cause.

## Finish the run

Stop when the target is reached, the user interrupts, the budget cannot admit another useful attempt while preserving reserves, or a contracted integrity or safety condition fires. Reconcile incomplete work and restore the best confirmed incumbent when safe. If safe recovery or launch provenance remains uncertain, preserve the divergent state, record `unresolved_recovery: true` separately from process-cleanup uncertainty, and make no resolved-completion or final-verification claim.

Run the optional final-test stage once as a serial `role: final` verification attempt under the same launch, evidence, and boundary protocol. It cites only fresh local final-test evidence, never re-runs promotion stages, does not modify code or become an implementation parent, and does not return feedback for tuning. Append `run_stopped`, generate derived final status and report files, then atomically publish `run.finalized.json` only after all canonical bytes and the final HEAD are fixed. Verify with:

```bash
python3 <skill-root>/scripts/validate_run_state.py <run-dir> --require-run-finalized
```

Report the run tag, intended population, frozen command and evaluator digest, artifact directory, development, validation, and final-test metrics separately, actual and reserved budget use, whether usage is exact or only a receipted lower bound, validation queries, status and lane counts, best confirmed commit, final HEAD, accepted rules, negative evidence and revive conditions, rejected overfit or invalid shortcuts, stop reason, unresolved cleanup, unresolved recovery, quarantined work, and remaining uncertainty. Claim general improvement only when confirmed evidence includes the disjoint gate; otherwise say that the visible harness improved.
