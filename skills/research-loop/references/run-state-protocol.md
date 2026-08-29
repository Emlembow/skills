# Run-State Protocol

Use this protocol for every research-loop run. The trusted supervisor owns every artifact described here. Candidate code may not write, replace, or choose a canonical record.

## Artifact roles and layout

Use this run-local layout:

```text
.research-loop/<run-tag>/
  contract.json
  contract.sha256
  contract.md
  controller.lock/
  journal.jsonl
  results.jsonl
  designs/<experiment-id>.json
  evidence/<result-id>.json
  logs/<result-id>.log
  snapshots/<attempt-id>-launch-plan.json
  snapshots/<attempt-id>-admission.json
  snapshots/<attempt-id>-<workspace-id>.json
  snapshots/<result-id>-config.json
  snapshots/<result-id>-state.json
  snapshots/<result-id>-tree.json
  snapshots/<content-id>.json
  boundaries/<attempt-id>.json
  quarantine/
  status.json
  status.final.json
  run.finalized.json
```

`contract.json`, `contract.sha256`, `journal.jsonl`, `results.jsonl`, finalized designs/evidence/logs/snapshots, attempt boundaries, `run.finalized.json`, and Git commits are canonical. Every finalized file under `designs/`, `evidence/`, `logs/`, `snapshots/`, and `boundaries/` must be referenced by canonical state; the final run boundary contains the sorted path/hash manifest and its canonical digest. An orphan finalized artifact is corruption. `contract.md`, `status*.json`, reports, compact memory, candidate rankings, and reviewer memos are derived or audit-only. Rebuild derived files from canonical state; never use them to repair or override it.

Keep the run directory uncommitted and supervisor-owned. If repository-relative discovery is required, isolate it with permissions, a read-only mount, or a separate supervisor checkout. Stop if evaluated code can mutate canonical state.

Write JSON as UTF-8 without a byte-order mark. Write each JSONL object on one LF-terminated line. Reject duplicate keys, blank lines, torn tails, `NaN`, infinity literals, and finite-looking exponent forms that overflow to a non-finite runtime number. Hash exact stored artifact bytes with SHA-256 unless this protocol explicitly specifies canonical-object hashing.

Write in-flight files with `.partial` or `.tmp` suffixes. To finalize, flush and sync the file, atomically replace the destination in the same filesystem, then sync the parent directory when supported. Do not fall back to a direct non-atomic write after replacement fails.

## Machine-readable contract

Make `contract.json` the authoritative contract and `contract.md` its user-readable audit copy. Write `contract.sha256` as exactly the lowercase SHA-256 of `contract.json`, followed by LF.

Use exactly these top-level fields and the exact nested keys shown; reject additional claims rather than leaving them uninterpreted. Digest strings below illustrate their positions; compute them from the actual sealed bytes and objects rather than copying them:

```json
{
  "schema_version": 2,
  "protocol": {
    "version": "1.1.0",
    "validator_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
  },
  "run_tag": "example-run",
  "feedback_mode": "sealed_gate",
  "baseline_commit": "0123456789abcdef0123456789abcdef01234567",
  "deadline_at": "2026-01-02T00:00:00Z",
  "objective": {
    "goal": "increase task success without violating constraints",
    "intended_population": "future inputs matching the product specification",
    "primary_metric": "success_rate",
    "direction": "maximize",
    "target": 0.95,
    "minimum_delta": 0.01,
    "acceptance_split": "validation",
    "hard_constraints": ["integrity", "latency_limit"]
  },
  "measurement": {
    "mode": "deterministic",
    "aggregation": "single",
    "minimum_repetitions": 1,
    "exploration_repetitions": 0,
    "confirmation_repetitions": 0,
    "uncertainty_rule": "none"
  },
  "scope": {
    "writable_paths": ["src"],
    "immutable_paths": ["tools", "tests", "fixtures"],
    "generated_paths": ["var/runtime-state"]
  },
  "generated_state_policy": {
    "mode": "reset_each_launch",
    "initial_state_sha256": "0a13bb4464a37bbf23eab1f2ceaa00b9e3058c4d2463bd823d44fd033dd0bc0f"
  },
  "control_policy": {
    "authorization": "bounded",
    "stop_conditions": [
      "target reached",
      "budget cannot admit another attempt",
      "user interruption",
      "integrity failure"
    ],
    "plateau_trigger": {
      "consecutive_non_improvements": 3,
      "action": "refresh_candidate_pool"
    },
    "counterexamples": {
      "latency_regression": "latency stays within the product limit",
      "unseen_paraphrases": "unseen paraphrases preserve the required behavior"
    },
    "cleanup_grace_seconds": 30,
    "actual_usage_sources": {
      "evaluator_calls": "trusted supervisor launch receipts",
      "validation_queries": "protected-stage access receipts"
    },
    "hard_constraint_rules": {
      "integrity": "all scope, provenance, and leakage checks pass",
      "latency_limit": "the sealed latency telemetry stays within the product limit"
    }
  },
  "evaluation": {
    "argv": ["/usr/bin/python3", "tools/evaluate.py", "--json"],
    "cwd": ".",
    "extractor_path": "tools/evaluate.py",
    "executable_path": "/usr/bin/python3",
    "executable_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "environment": {
      "inherit_parent": false,
      "variables": {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8"
      },
      "secret_variable_names": [],
      "dependency_fingerprints": {}
    },
    "environment_sha256": "0bb1c5e5b0da04eaeb27fe913f082b5f47a645150cd98b57e3ef7bcbd68713b5",
    "timeout_seconds": 60,
    "invocation_sha256": "ef39ca4da02e516168d6c93bcb0fd84a770eb68e8e4aef905cc5bd114283f118",
    "evaluator_sha256": "76d51b7f0ee727316e0c3a5526e4c63f45f8f2166be877b32c7fc403aa6efd1c"
  },
  "immutable_files": [
    {
      "path": "tools/evaluate.py",
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    },
    {
      "path": "tests/cases.json",
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    },
    {
      "path": "fixtures/default.json",
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  ],
  "budgets": {
    "evaluator_calls": {
      "total": 20,
      "operational_reserve": 2,
      "final_test_reserve": 1
    },
    "validation_queries": {
      "total": 4,
      "operational_reserve": 1,
      "final_test_reserve": 1
    }
  },
  "per_attempt_max": {
    "evaluator_calls": 4,
    "validation_queries": 1
  },
  "promotion": {
    "required_stages": ["development", "validation"],
    "required_telemetry": {
      "development": ["success_rate"],
      "validation": ["success_rate"]
    },
    "acceptance_rule": "meet the primary delta and pass every required stage and hard constraint"
  },
  "stage_resources": {
    "development": {
      "resource_manifest_sha256": "1111111111111111111111111111111111111111111111111111111111111111",
      "capability_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
      "visibility": "visible"
    },
    "validation": {
      "resource_manifest_sha256": "3333333333333333333333333333333333333333333333333333333333333333",
      "capability_sha256": "4444444444444444444444444444444444444444444444444444444444444444",
      "visibility": "protected"
    },
    "final_test": {
      "resource_manifest_sha256": "5555555555555555555555555555555555555555555555555555555555555555",
      "capability_sha256": "6666666666666666666666666666666666666666666666666666666666666666",
      "visibility": "protected"
    }
  },
  "stage_access_sha256": {
    "development": "fbbec343059da285e9dbfd385a6a2d816ce8d00f6fc0661267bf79312ad42dba",
    "validation": "5e11c8b15c317dcaff36c238229e9925328b2f2001ce1900b321dbfd7fe13abf",
    "final_test": "fd088f06f318985ec9c011ceab4be5ce272b893ff0c4980749677ee126920031"
  },
  "stage_config_sha256": {
    "development": "17f513d9d20ee51c50414de7c6e3f36fc2f719b96cc0becf5cac49167f0be09f",
    "validation": "0693b8a325445b9100bfa9cec76d1089fcda9adab7bb5b6d83f33620a0ad4994",
    "final_test": "37f9bc03241bf0686207bb0a0ff2ef3a4bc5d8fbb528f5e75e421892d1c61533"
  },
  "final_test": {
    "stage": "final_test",
    "required_telemetry": ["success_rate"],
    "minimum_value": 0.9,
    "maximum_value": null,
    "comparable_to_acceptance_split": false,
    "maximum_regression": null
  }
}
```

This contract schema is version 2. Set `protocol.version` to the installed protocol release and `protocol.validator_sha256` to the exact bytes of the validator that will create and resume the run. The running validator must match both values. Retain those exact validator bytes with the run's trusted operational archive. A later validator mismatch is a hard stop: use the sealed validator to inspect or recover the run, or start a new tag under the new protocol; never rewrite the old contract to migrate it.

Seal every operational authority in `control_policy`. `authorization` is `bounded` or `run_until_interrupted`; a null deadline and unbounded `evaluator_calls` require the latter. `stop_conditions` is a nonempty, user-authorized list. `plateau_trigger` uses a positive consecutive-non-improvement count and either refreshes the candidate pool or stops. `counterexamples` maps one or more stable IDs to the invariant checks every attempt must run. `cleanup_grace_seconds` is positive. `actual_usage_sources` has exactly the budget-unit keys and identifies the trusted receipt source for each. `hard_constraint_rules` has exactly the objective's hard-constraint IDs and seals each definition or threshold so it cannot be reinterpreted after results.

Set `evaluation.environment` to an exact sanitized manifest with only `inherit_parent`, `variables`, `secret_variable_names`, and `dependency_fingerprints`. Set `inherit_parent` to false and launch with a cleared parent environment. Put every resolved nonsecret environment value in `variables`; put only the names of broker-injected secrets in `secret_variable_names`, never their values. Secret inputs must authorize access only and must not select evaluation data, scoring, or behavior. Do not pass execution-control variables such as `PATH`, `PYTHONPATH`, loader-preload/library paths, language startup hooks, module search paths, or runtime option injectors; use direct absolute commands and fingerprinted immutable dependencies instead. Bind relevant lockfiles, container images, service revisions, and other runtime dependencies by stable lowercase ID to lowercase SHA-256 in `dependency_fingerprints`. Compute `environment_sha256` from the manifest's canonical JSON. The validator rejects a conservative set of execution-control names and sensitive-looking values, but the supervisor remains responsible for recognizing additional platform-specific controls and secret names.

Resolve the direct executable before sealing: `executable_path` is its canonical nonsymlinked absolute path, `argv[0]` is exactly that path, and `executable_sha256` hashes its bytes. Do not rely on `PATH`, a shell alias, or an interpreter selected after admission. Compute `evaluation.invocation_sha256` from canonical JSON of `argv`, `cwd`, `extractor_path`, `executable_path`, `executable_sha256`, `environment_sha256`, and the positive integer `timeout_seconds`, using the canonical-object algorithm below. Compute `evaluator_sha256` from canonical JSON of `{"invocation_sha256": <digest>, "immutable_files": <exact contract array>}`. Include the extractor in `immutable_files`; recursively enumerate every regular file under each immutable scope, including trusted harness, fixture, scoring, data, and configuration files. The manifest must exactly cover those scopes. Reject symlinks, gitlinks, missing entries, and unmanifested files.

Resolve `evaluation.cwd` as a real, nonsymlinked directory under the trusted root. Resolve `extractor_path` from that directory, require the exact path in `evaluation.argv`, and include its repository-relative resolved file in `immutable_files`. Recheck the live executable bytes and reconstruct the launch environment from the sealed manifest before every spawn. For each stage, seal a distinct resource-manifest and capability digest plus its visibility. The first promotion stage is `visible`; every later promotion stage and the final stage is `protected`. Compute each `stage_access_sha256` from the canonical stage-resource object. Write one exact effective-configuration artifact per stage with keys `schema_version`, `stage`, `stage_access_sha256`, and `settings`; `stage_config_sha256` is the SHA-256 of those exact stored bytes. Stage access and configuration identities may not be reused across stages.

Use full 40- or 64-hex Git object IDs. Use normalized POSIX-style repository-relative paths: no absolute paths, backslashes, empty components, `..`, or symlinks. The validator resolves immutable paths against `--trusted-root`; without that option it expects the usual `<worktree>/.research-loop/<run-tag>` layout and uses the worktree root.

Budget unit names use lowercase letters, digits, and underscores. Always define `evaluator_calls`; define `validation_queries` whenever a promotion or final stage is protected. Totals are nonnegative integers or `null` for an explicitly unbounded unit. `operational_reserve` and `final_test_reserve` are separate nonnegative integers whose sum cannot exceed a finite total. A final attempt may consume only its final-test reserve and must still preserve the operational reserve; non-final attempts preserve both. `per_attempt_max` values are nonnegative integers. Before sealing, prove each per-attempt cap can cover the largest minimum baseline, candidate, or final plan; each final reserve can cover the complete minimum final plan; and each finite total can cover a baseline, one candidate, the operational reserve, and the full final reserve. Use an RFC 3339 UTC `deadline_at`, or `null` only when the confirmed contract has no deadline. A null deadline plus an unbounded `evaluator_calls` total requires explicit `run_until_interrupted` authorization; a finite unrelated unit does not bound launches. Monetary values use integer minor or micro units, not floats. Scope entries are normalized repository-relative paths. Writable and immutable scopes may not contain or overlap one another, and every hashed immutable file must fall under the immutable scope.

The supported generated-state policy is `reset_each_launch`. Its `initial_state_sha256` is always the digest shown above over the exact UTF-8 bytes `{"entries":[],"mode":"reset_each_launch","schema_version":1}\n`. Every contracted generated path must be absent before spawn and absent again after owned-process cleanup. A cache, database, checkpoint, or metric file that cannot be removed and reinitialized this way requires a different run implementation; never share it across arms or repetitions under this protocol.

Use stable lowercase IDs for required stages and hard constraints. The objective's `acceptance_split` must be one of the required stages, and `promotion.required_telemetry` has exactly those stage keys with one or more finite metric IDs per stage. The first promotion stage is visible and every later promotion stage is protected; every stage has its own resource-manifest, capability, access, and configuration digest, each individually distinct. A `sealed_gate` contract must place at least one visible development stage before its protected acceptance split. Measurement mode is `deterministic` or `noisy`; aggregation is `single`, `mean`, `median`, `min`, or `max`; and `minimum_repetitions` is positive. Deterministic mode uses one repetition, zero exploration and confirmation repetitions, and uncertainty rule `none`. Noisy mode uses mean or median aggregation, uncertainty rule `paired_mean_delta`, and at least one exploration plus one fresh confirmation repetition within the minimum. Every noisy candidate acceptance pair has one incumbent and one candidate repetition with the same nonnull `pair_id`, seed, input, stage, phase, normalized effective-configuration digest, and initialized generated-state digest; cite both arms as stage evidence. Noisy confirmed baseline and final attempts use the same sealed phase counts as single-arm evidence with fresh seed/input identities. Promotion uses the weaker of the direction-adjusted exploration and confirmation mean deltas, and both phases must meet the positive minimum delta. Compute the top-level primary value from the acceptance-stage repetitions for the evaluated arm and copy it to that split's metric; do not enter an independently calculated summary or promote a tie.

Set `final_test` to `null` when unavailable. Otherwise give it a distinct stage not present in `promotion.required_stages`, a nonempty required-telemetry list, and a stage-native absolute threshold. Maximization requires finite `minimum_value` and null `maximum_value`; minimization requires the reverse. Default `comparable_to_acceptance_split` to false in the resolved draft and keep `maximum_regression` null. Use a nonnegative cross-split regression limit only when comparability was independently established and sealed before the run. Only the single terminal `role: final` attempt may access that stage. After a confirmed non-final incumbent exists, ordinary resolved closure requires this attempt even when it fails. Only a `stop_requested` receipt with `source: "user"`, `stop_kind: "user_interruption"`, and `final_test_skip_authorized: true` may omit it; record it as not run and make no final-verification claim. Its local pass verifies the already confirmed incumbent; it does not become an implementation parent or reopen tuning.

The validator checks the primary metric name, direction, minimum delta for candidate promotion, required stages and telemetry, final-test isolation, evidence bindings, admission arithmetic, control-policy shape, and scope shape. It seals but does not reinterpret free-form acceptance or hard-constraint prose, prove that a supervisor attestation was honestly observed, decide whether metrics measure the intended behavior, or establish scientific merit.

## Identifiers and design records

Use three different identities:

- `experiment_id`: stable scientific identity for one locked mechanism, scope, and prediction. Derive it from the design digest or bind an opaque unique ID to that digest.
- `attempt_id`: unique execution identity. Allocate a new one for every candidate commit, operational rerun, retry, or recovery execution.
- `result_id`: immutable evidence identity for one arm, repetition, and launch. Cite prior evidence by its existing ID without copying it into a new attempt. Give each genuinely new launch a new ID even if its output bytes match; identical artifact bytes may share content-addressed storage.

Do not derive identity from a filename, metric, label, iteration alone, or process ID. Reject conflicting reuse of an ID. Treat a new durable launch as new evidence provenance even when its other fields and artifact bytes coincide.

Write `designs/<experiment-id>.json` before implementation with:

- `schema_version`, `experiment_id`, creation time, `implementation_parent` (baseline or a confirmed attempt only), and `evidence_sources` (any cited evidence, without code inheritance).
- Mechanism family, intervention surface, intent, hypothesis, and general failure class.
- Supporting and weakening metric signatures, predicted unseen behavior, invariant or counterexample, falsifier or ablation, and fail-fast condition.
- Allowed files, forbidden changes, cost estimate, risks, and rejected alternatives.
- Any amendment made before evaluation.

Represent `implementation_parent` as `{"kind":"baseline","attempt_id":null,"commit":"<baseline-oid>"}` or `{"kind":"confirmed_attempt","attempt_id":"<prior-attempt-id>","commit":"<confirmed-oid>"}`. Represent `evidence_sources` as a unique array of prior canonical `result_id` values. Represent `cost_estimate` with exactly the contract's budget units. Use normalized repository-relative entries in `allowed_files`; each must fall under the sealed writable scope. Include `risks` and `rejected_alternatives` as string arrays and `amendment` as null or an object with `amended_at`, `reason`, and nonempty `changed_fields`.

Hash the finalized design file. Every candidate result includes its path and digest. The result hypothesis must match the frozen design. Attempts sharing an `experiment_id` must reference the same digest; incorporate any pre-evaluation amendment into that final digested record before the first launch.

For every candidate commit, inspect the complete tree diff from its incumbent-equivalent pre-attempt HEAD. Reject an empty diff, a merge or multi-commit candidate, and any changed path outside both the contract writable scope and design `allowed_files`, or inside immutable, `.git`, or `.research-loop` control state. Reverted history remains evidence and receives the same scope check.

## Prelaunch plan, workspaces, and admission

Before appending `prepared`, atomically publish `snapshots/<attempt-id>-launch-plan.json` and `snapshots/<attempt-id>-admission.json`. `prepared` binds both with exact path/file-SHA references. The launch plan has exactly `schema_version`, `attempt_id`, and nonempty `launches`. Each launch entry has exactly:

- `ordinal`, `stage`, `arm`, `phase`, nullable `pair_id`, nullable `seed`, nullable `input_id`, `workspace_id`, and a `workspace` path/hash reference.
- `allocation` with exactly every contracted budget unit, plus the positive integer `timeout_seconds` copied from `evaluation.timeout_seconds`.
- `stage_access_sha256`, `effective_config_sha256`, and `generated_state_sha256` copied from the sealed stage and initial-state identities.

Entries use contiguous zero-based ordinals, cover every applicable stage in sealed order, and allocate at least one evaluator call each. A deterministic stage has one target-arm launch. A noisy candidate acceptance stage consists of adjacent matched incumbent/candidate pairs with equal phase, pair ID, seed/input, stage configuration, and initial state. Use unique sampling identities, meet both exploration and confirmation minima, and counterbalance which arm runs first overall and within any phase having at least two pairs. A noisy baseline or final attempt uses fresh single-arm exploration and confirmation identities. The plan is immutable after `prepared`; a later intent must equal its planned entry.

Every workspace reference names `snapshots/<attempt-id>-<workspace-id>.json` with exactly:

```json
{
  "schema_version": 1,
  "workspace_id": "ws-001-candidate",
  "arm": "candidate",
  "isolation_mode": "dedicated_git_worktree",
  "root_path": "/absolute/canonical/path/to/worktree",
  "root_identity_sha256": "...",
  "git_dir_identity_sha256": "...",
  "git_common_dir_identity_sha256": "..."
}
```

The validator resolves the live nonsymlinked root and computes each directory identity as canonical SHA-256 of `{"realpath": <resolved path>, "device": <st_dev>, "inode": <st_ino>}`. It also requires the root to be a Git top level disjoint from the supervisor worktree, its Git common directory to equal the supervisor repository's, its per-worktree Git administration directory to be live, its HEAD to equal `evaluated_commit`, every index entry to be normal rather than assume-unchanged or skip-worktree, its index and tracked worktree to be clean, every immutable file to match the contract, no untracked or ignored path to remain, and every generated path to be absent under `reset_each_launch`. Opposing arms must have nonnested physical roots and distinct per-worktree Git administration identities. The launch `cwd` must resolve to a nonsymlinked directory inside that root. Keep every referenced worktree and Git administration directory unchanged and available for validation; do not substitute opaque labels, mutate them after evidence publication, or delete them before the run is archived.

The admission artifact has exactly `allowed`, `deadline_fit`, `checked_at`, `worst_case_end_at`, `cumulative_before`, `requested_worst_case`, and `protected_after_launch`. Its checked time is no later than `prepared`; its time window covers at least the sum of the serial plan's sealed launch timeouts; its requested totals equal the launch-plan allocations; its cumulative totals equal prior finished receipts; and its protection equals operational plus final-test reserve for non-final attempts or operational reserve alone for a final attempt. `allowed` is the exact conjunction of time-window, deadline, per-attempt cap, reserve, and total checks. A denied or non-deadline-fitting admission may be stored as a terminal planning result but may not produce `launch_intent`. At each permitted intent, the remaining admitted time must still cover the sum of timeouts for that ordinal and the unlaunched suffix. Every intent binds the admission artifact's exact file SHA-256.

## Controller and launch receipts

Allow one live supervisor. Prefer an advisory OS lock held for the process lifetime. If portability requires an atomic lock directory, store supervisor PID, process start identity, host, and acquisition time inside it and reclaim only after proving that owner is gone. A PID alone is insufficient because it can be reused.

Before each spawn, append `launch_intent` durably with the complete planned entry, evaluated commit, invocation digest, sealed timeout, workspace reference, effective-configuration/generated-state/evaluated-tree references and digests, requested resources, exact admission artifact SHA, sealed relative working directory, and intended partial log. Immediately after spawn, append `launched` with PID, process group and process-start identity, or a durable external job/allocation identity. At that receipt, require the spawn time plus its sealed timeout to fit both the admitted window and run deadline; an old intent cannot authorize a late spawn. A local receipt requires all three local identity fields and no external identity; an external receipt uses only the external identity. Never reuse either identity tuple anywhere in the run. If the receipt cannot uniquely identify and terminate the work, fence launches and stop.

On resume, classify an intent without a launch receipt only after proving no matching owned process or external job exists. Resume a launch only when its durable identity matches. Put output arriving after cancellation, finalization, or an evidence cutoff in `quarantine/`; never score it.

## Journal schema and lifecycle

Append events with contiguous `seq` values beginning at 1. Every event includes `schema_version: 1`, `seq`, an RFC 3339 UTC `at` value, `event`, and the sealed `contract_sha256`. Attempt events also include `attempt_id`, `experiment_id`, `iteration`, and `role`.

For example:

```json
{"schema_version":1,"seq":1,"at":"2026-01-01T00:00:00Z","event":"prepared","contract_sha256":"...","attempt_id":"a-000","experiment_id":"baseline","iteration":0,"role":"baseline","incumbent_commit":"0123456789abcdef0123456789abcdef01234567","pre_attempt_head_commit":"0123456789abcdef0123456789abcdef01234567","design":null,"launch_plan":{"path":"snapshots/a-000-launch-plan.json","sha256":"..."},"admission":{"path":"snapshots/a-000-admission.json","sha256":"..."},"prior_boundary":null}
```

Use this ordered lifecycle:

```text
prepared
  -> committed?                                     # candidate only; absent on pre-commit closure
  -> [integrity_cleared?                            # sealed gate only; before acceptance intent
      launch_intent
        -> (launch_aborted | launch_unresolved |
            launched -> (launch_unresolved |
                         launch_finished -> (cleanup_confirmed | cleanup_unresolved)?
                                           -> evidence_published?))
      stage_completed?]*                            # one durable checkpoint per entered stage
  -> plan_tail_cancelled?                          # exact unlaunched suffix after stop_requested
  -> evaluated?                                     # after every launch is terminal
  -> decided
  -> rolled_back?                                   # required for rollback_action: revert
  -> finalized
  -> atomic attempt boundary
```

`prepared`, `committed`, `integrity_cleared`, `evaluated`, `decided`, `rolled_back`, and `finalized` occur at most once per attempt. Every `prepared` records both the accepted `incumbent_commit` and actual `pre_attempt_head_commit`, the frozen launch plan, prelaunch admission, and prior atomic boundary. Their trees must match, and the pre-attempt object ID must continue the exact recorded HEAD transition: baseline, kept candidate, or recorded inverse commit. Candidate `prepared` includes the exact design path/hash reference; baseline and final `prepared` use null. The first attempt uses `prior_boundary: null`; each later attempt uses exactly `{"path","sha256","attempt_id","finalized_seq","result_sha256"}` and binds the immediately preceding boundary. This proves preregistration and boundary continuity before implementation. A candidate is one direct commit over the recorded pre-attempt HEAD. Launch events repeat with globally unique `launch_id`, `result_id`, process/external identity, and contiguous `launch_ordinal` values.

`launch_intent` includes `launch_id`, `result_id`, `launch_ordinal`, the exact planned stage/arm/phase/sample/workspace fields, evaluated commit, invocation digest, `timeout_seconds`, stage access, effective-configuration, initial generated-state and evaluated-tree references/digests, `admission_sha256` over the exact stored admission file, requested allocation, working directory, and partial log path. Each allocation has exactly the budget units, and their attempt sum cannot exceed the admitted worst case. If durable observation proves the intent never launched, append `launch_aborted` with the same identities, `absence_verified: true`, and a reason; the whole attempt is then non-promotional. `launched` repeats the identities and includes either a positive PID plus PGID and process-start identity or a nonempty external job ID. `launch_finished` includes outcome (`completed`, `crash`, `timeout`, `cancelled`, or `interrupted`), exact actual use, and whether owned-process cleanup was verified. Elapsed time from `launched.at` through `launch_finished.at` beyond the sealed timeout, or truthful actual use beyond a launch allocation, admission, cap, or total, forces the attempt's non-promotional `timeout` status. When cleanup was initially false, append `cleanup_confirmed` with the same identities and a nonempty verification description only after cleanup is independently established.

When durable observation cannot prove whether an intent launched, append `launch_unresolved`; when cleanup cannot be proved after `launch_finished`, append `cleanup_unresolved`. Both repeat the launch identities, require `fence_applied: true`, and include a reason. After either receipt, reject all later launches, exclude the launch from evidence, decide only `interrupted` with every stage `unknown` and `rollback_action: preserve_and_stop`, mark usage accounting incomplete, and stop the run with both `unresolved_cleanup: true` and `unresolved_recovery: true`.

After a completed launch and verified cleanup, deterministically normalize the evaluator's canonical JSON response. Atomically publish that exact JSON to both `evidence/<result-id>.json` and `logs/<result-id>.log`; keep diagnostic stderr or non-protocol text only as partial or quarantined material. Append `evidence_published` before any stage checkpoint with the launch identities, exact path/hash references, and `repetition_sha256` over the reconstructible normalized repetition plus its config/state/tree references. The normalized identities and finite required telemetry must match the intent and evaluator contract exactly. A completed launch without this receipt is not stage evidence.

Append `integrity_cleared` before the first candidate intent into any protected stage. It binds the candidate commit and latest visible-stage candidate tree digest, requires literal `pass` for both scope and leakage audits, and contains exactly every hard-constraint ID with literal `true`. The sealed contract therefore requires a visible development stage before protected acceptance. It may not be inferred later from result prose. Then append one `stage_completed` per entered stage in sealed order, with `outcome`, exact local published `result_ids`, and the rule evaluation. Missing, aborted, failed, uncleaned, unpublished, or cancelled planned work forces `unknown`; a later protected stage requires the previous checkpoint to be `pass`.

After `stop_requested`, fence new baseline and candidate launches and drain active work. If `final_test_skip_authorized` is false and a configured final verification is required, exactly one terminal `role: final` attempt may still be prepared and launched after the current attempt closes; it is the sole post-stop launch exception and uses only the final reserve. If the stop authorizes skipping final verification, no later launch is legal. If a multi-launch plan still has an unlaunched suffix, append one `plan_tail_cancelled` with `from_ordinal`, the exact remaining `cancelled_ordinals`, and a reason. It is valid only after all existing owned launches are terminal and cleanup-resolved. The interrupted stage checkpoints as `unknown`; do not fabricate launch receipts for the cancelled suffix.

`evaluated` lists exactly the `result_id` values from completed launches that produced valid evidence artifacts. It appears only after every intent is terminal. Aborted or unresolved launches and launches without verified cleanup contribute no evidence; finished receipts still contribute their recorded usage.

`decided` records `status`, `lane`, `evidence_maturity`, `accepted`, reason, intended incumbent, `stage_results`, `stage_evidence`, and `rollback_action` (`none`, `revert`, or `preserve_and_stop`) before branch state changes. `stage_evidence` has exactly the promotion-stage keys, or only the sealed final-test key for `role: final`. A `pass` or `fail` cites one or more completed local `result_id` values whose launch and repetition carry that stage; `unknown` cites none. It may skip launch/evaluation only for a pre-launch `invalid`, `cancelled`, or `interrupted` outcome with `no_evaluation_reason`. A pre-commit candidate can close through that branch with no candidate commit; first quarantine its task-owned diff and restore a clean pre-attempt tree, or leave it incomplete for user resolution.

`rolled_back` includes the recoverable inverse commit and restored incumbent. `finalized` includes the decision sequence, zero-based result index, canonical result digest, and intended boundary path.

Do not append another `prepared` until the prior boundary exists and validates. A run-level `stop_requested` records nonempty `source`, `reason`, and `rule_evaluation`, `stop_kind` (`user_interruption`, `ordinary`, `integrity`, or `resource_exhaustion`), and boolean `final_test_skip_authorized`. The authorization may be true only with `source: "user"` and `stop_kind: "user_interruption"`. After the receipt, reject every new attempt and launch except the single required post-stop final verification described above. End every run with `run_stopped`, including reason, `final_incumbent_commit`, actual `final_head_commit`, `unresolved_cleanup`, and `unresolved_recovery`. Set `unresolved_recovery: true` exactly when the terminal decision uses `preserve_and_stop`; this permits a deliberately preserved divergent HEAD but downgrades every resolved-completion and final-verification claim. `unresolved_cleanup` separately records uncertain process or external-job ownership. The incumbent remains the last accepted implementation identity; when both flags are false, HEAD may instead be a recoverable inverse commit only when its tree equals the incumbent. Reject all later journal events.

## Result records

Append exactly one result object per attempt to `results.jsonl`. Use `null`, never invented values, for unavailable measurements. Keep `repetitions` empty when no completed launch produced valid evidence. Use `role: baseline` for iteration 0, `role: candidate` for ordinary experiments, and `role: final` for the optional one-time completion test. A final attempt evaluates the incumbent without a candidate commit, never changes code, and forbids later attempts.

Require these top-level fields:

- `schema_version`, `attempt_id`, `experiment_id`, `iteration`, and `role` (`baseline`, `candidate`, or `final`).
- `commit`, `incumbent_commit`, `pre_attempt_head_commit`, nullable `rollback_commit`, `started_at`, `ended_at`, and `contract_sha256`.
- `status`, `lane`, `evidence_maturity`, `acceptance_reason`, and `decision`.
- `primary_metric`, nullable finite `primary_value`, `split_metrics`, `aggregation`, and `secondary_metrics`.
- `repetitions`, nonnegative `duration_seconds`, `validation_queries`, `hypothesis`, and `change_summary`.
- `design` (null for baseline and final), `admission`, and `budget`.
- `leakage_audit`, `counterexample_results`, `constraint_results`, one `finding`, and nullable `failure`. Counterexample results have exactly the sealed counterexample IDs and use `true`, `false`, or `null`; `keep` requires every one to be `true`.

Use these status and lane combinations:

| Status | Lane | Meaning |
| --- | --- | --- |
| `keep` | `confirmed` | Complete accepted evidence; promotion is allowed. |
| `deferred` | `incubator` | Promising but required evidence is incomplete or unknown; no promotion or parent use. |
| `discard` | `candidate` or `diagnostic` | Completed non-promotion; retain interpretable evidence. |
| `crash`, `timeout`, `invalid`, `cancelled`, `interrupted` | `diagnostic` | Non-promotional operational or integrity outcome. |

`evidence_maturity` is `complete`, `incomplete`, or `unknown`. Put each contracted promotion stage's `pass`, `fail`, or `unknown` value in `decision.stage_results` and bind it to `decision.stage_evidence`; a final attempt instead uses exactly the final-test stage. Complete evidence cannot contain an unknown required stage. Missing, malformed, or non-finite required telemetry yields `unknown`; no finding or reviewer can convert it to a pass.

`decision` includes `decision_seq`, `journal_event_sha256`, `accepted`, `incumbent_after`, `rollback_required`, `stage_results`, `stage_evidence`, and `rule_evaluation`. Hash the complete parsed `decided` event with the canonical-object algorithm so duplicated decision fields cannot drift. `keep` requires `accepted: true`, complete maturity, every applicable stage passing, and no rollback. Candidate `keep` promotes; final `keep` only verifies the unchanged incumbent. Every other status requires `accepted: false`. A failed final attempt uses a non-keep diagnostic outcome with no rollback and downgrades the final claim; it never becomes tuning feedback.

`finding` includes `type` (`positive`, `negative`, `diagnostic`, `uncertain`, or `procedural`), `summary`, `scope`, `caveats`, `next_action` (`reuse`, `validate`, `avoid`, `diagnose`, `preserve`, or `archive`), nullable `revive_if`, and cited result IDs. Interpretations never replace raw results.

Represent `design` and stored repetition artifacts as safe run-relative path/hash references:

```json
{"path":"snapshots/tree-abc.json","sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}
```

Every repetition includes:

- `result_id`, nullable `pair_id`, `arm` (`incumbent` or `candidate`), phase (`deterministic`, `exploration`, or `confirmation`), and stable lowercase `stage`.
- `evaluated_commit`, nullable `seed`, nullable `input_id`, metrics, nullable finite primary value, and nonnegative duration.
- `result_artifact`, `log`, `effective_config`, `generated_state`, and `evaluated_tree_snapshot` path/hash references.
- `evaluator_sha256`, matching the sealed effective evaluator fingerprint.

The result artifact is a supervisor-produced normalized raw result, not the prose finding. Store this exact minimum JSON shape and make every named value equal the enclosing repetition:

```json
{
  "schema_version": 1,
  "result_id": "r-001",
  "pair_id": "pair-001",
  "arm": "candidate",
  "phase": "confirmation",
  "stage": "validation",
  "evaluated_commit": "0123456789abcdef0123456789abcdef01234567",
  "seed": 42,
  "input_id": "input-42",
  "metrics": {"success_rate": 0.96},
  "primary_value": 0.96,
  "duration_seconds": 12.5,
  "evaluator_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

The effective configuration contains resolved defaults and runtime flags, not only user overrides. Every generated-state reference identifies the same exact canonical empty-state artifact required by `reset_each_launch`; do not substitute an opaque state label or a snapshot of residual runtime files.

Each evaluated-tree snapshot contains `schema_version`, matching `result_id` and commit, that commit's Git `tree_oid`, and explicitly empty `untracked_writable_paths` and `unexpected_paths` arrays. The validator proves the retained live workspace's HEAD, index, tracked changes, untracked and ignored paths, immutable files, and generated-path absence against Git and the contract. Keep that workspace unchanged so final validation still proves the recorded launch boundary.

## Budget admission

The result's parsed `admission` object is structurally equal to the parsed prelaunch admission artifact, and every launch intent binds that file's exact SHA-256 before spawn. `budget` includes `actual_complete`, `actual`, and `remaining_after`. Usage quantities are nonnegative integers and every usage object contains exactly the contracted units, except `remaining_after`, which contains only finite units. Sum `launch_finished.actual_usage` to obtain receipted `budget.actual`; do not accept a disconnected summary. Give a sealed-gate query its own supervisor launch receipt and aggregate evidence artifact so its use is included without exposing protected cases.

Before a baseline or candidate launch, require for every finite unit:

```text
cumulative_actual_before + requested_worst_case + protected_after_launch <= total
```

Require `requested_worst_case` not to exceed `per_attempt_max`, require the admitted time window to cover the sum of serial launch timeouts, require the worst-case end time to fit the sealed deadline, and make `allowed` equal the conjunction of time-window, deadline, cap, reserve, and remaining-budget checks. `remaining_after` equals total minus cumulative actual use through an exact attempt. A baseline or candidate preserves `operational_reserve + final_test_reserve`; a final attempt may request no more than `final_test_reserve` and preserves `operational_reserve`. Other reserve release requires closing without the stronger claim. Screening probes never satisfy promotion evidence. Give every protected-stage launch its own positive `validation_queries` allocation and truthful finished-usage receipt; visible-stage launches use zero. Cite only positively receipted protected results as conclusive evidence for that stage. This applies to every protected promotion stage and the final test, not only the primary acceptance split.

When any launch or cleanup is unresolved, set `budget.actual_complete: false`, report only the sum of finished usage receipts in `actual`, and set every finite `remaining_after` value to `null`. Treat `actual` and the run-level aggregate as lower bounds; do not claim exact cost or remaining capacity. The unresolved fence forbids another launch, so incomplete accounting cannot be used for later admission. Otherwise set `actual_complete: true` and record exact remaining values.

## Atomic attempt boundary

Compute the result digest from the parsed result object encoded as:

```python
json.dumps(
    result,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
).encode("utf-8")
```

After the decision and any rollback:

1. Verify every referenced design, evidence, log, configuration, and snapshot file is already finalized with its bound bytes and digest.
2. Append and sync the one result line.
3. Append and sync `finalized`, binding its zero-based result index and canonical-object digest.
4. Write and sync `boundaries/<attempt-id>.json.tmp`, replace `boundaries/<attempt-id>.json` atomically, and sync `boundaries/`.

The boundary contains:

```json
{
  "schema_version": 1,
  "kind": "attempt-boundary",
  "attempt_id": "a-001",
  "experiment_id": "exp-abc",
  "contract_sha256": "...",
  "decision_seq": 15,
  "finalized_seq": 17,
  "result_index": 1,
  "result_sha256": "...",
  "journal_prefix_sha256": "...",
  "results_prefix_sha256": "...",
  "publication_nonce": "...",
  "finalized_at": "2026-01-01T00:10:00Z"
}
```

`journal_prefix_sha256` covers exact `journal.jsonl` bytes through `finalized_seq`; `results_prefix_sha256` covers exact result bytes through `result_index`. Generate a fresh unpredictable lowercase SHA-256-shaped `publication_nonce` before writing the temporary boundary so an already occupied target cannot masquerade as the current publication. The boundary timestamp cannot predate its `finalized` event. Only this atomically published boundary makes the attempt complete. A result or `finalized` event without a boundary is a recoverable trailing state, never accepted evidence. A boundary with missing or conflicting references is corruption. Never infer completion from process exit, a metric file, Git HEAD, status, or expected-path existence.

## Run finalization

Fence launches, drain or classify the active attempt, publish its boundary, fix final HEAD, and append `run_stopped`. Generate final status and reports as derived output.

Atomically write `run.finalized.json` with:

- `schema_version: 1`, `kind: "run-boundary"`, `run_tag`, `contract_sha256`, `stop_reason`, and `finalized_at`.
- `final_incumbent_commit`, actual `final_head_commit`, `best_confirmed_attempt`, and `best_confirmed_commit`. The best fields name the latest confirmed non-final result for that incumbent, or are both null when none exists.
- `result_count`, `last_journal_seq`, exact complete-file `results_sha256` and `journal_sha256`, plus `last_attempt_boundary_sha256` or null for an empty run.
- `canonical_artifacts`, the sorted exact path/file-SHA manifest of every finalized file under the five canonical namespaces, and `canonical_artifacts_sha256` over that array.
- `actual_budget`, `actual_budget_complete`, `unresolved_cleanup`, and `unresolved_recovery`. When usage completeness is false, totals are receipted lower bounds. The two unresolved flags exactly mirror `run_stopped`; a preserved divergent HEAD is valid only with `unresolved_recovery: true` and supports no resolved-completion claim.

Use exactly those keys. The marker time must not predate the final journal event or terminal attempt boundary. Refuse publication when its target already exists with different bytes.

After this marker exists, do not append canonical state. Send later output to the derived `quarantine/` area without changing the immutable run marker. A continuation or protocol change starts a new tag.

## Validation and recovery

Use the bundled read-only validator:

```bash
python3 <skill-root>/scripts/validate_run_state.py <run-dir>
python3 <skill-root>/scripts/validate_run_state.py <run-dir> --allow-incomplete
python3 <skill-root>/scripts/validate_run_state.py <run-dir> --require-run-finalized
python3 <skill-root>/scripts/validate_run_state.py <run-dir> --trusted-root <evaluator-root> --git-root <worktree>
python3 <skill-root>/scripts/validate_run_state.py --self-test
```

Exit `0` means valid, `3` means one legal recoverable tail, `2` means invalid or corrupt, and `64` means command misuse. The JSON output reports `last_durable_phase` and the next permitted append-only `resume_action`; process liveness and ownership still require external observation. `--allow-incomplete` is required to classify a trailing attempt as recoverable; it never marks that attempt complete. `--require-run-finalized` validates the final marker against the complete canonical files.

Follow the reported recovery action literally:

| Durable tail | Permitted recovery |
| --- | --- |
| `prepared` or `committed` | Reconcile the frozen plan and tree; close invalidly if the exact work cannot continue. |
| `launch_intent` | Prove absence and append `launch_aborted`, or append fenced `launch_unresolved`; never silently respawn. |
| `launched` | Reclaim or drain only the recorded durable identity. |
| cleanup pending | Independently verify and append `cleanup_confirmed`, or append fenced `cleanup_unresolved`. |
| evidence publication pending | Normalize the already captured canonical output and append `evidence_published`; do not rerun. |
| stage launches pending | Append only the next preregistered `launch_intent`. |
| stop with an unlaunched plan suffix | Append `plan_tail_cancelled`. |
| stage checkpoint pending | Append the evidence-derived `stage_completed`. |
| launches terminal | Append `evaluated`. |
| `evaluated` | Apply the sealed decision rule and append `decided`. |
| rollback pending | Execute the recorded inverse commit and append `rolled_back`. |
| decision or result published | Append the next required result/finalization event without rewriting prior bytes. |
| `finalized` | Atomically publish the attempt boundary. |
| `run_stopped` | Atomically publish the exact run boundary. |

If validation fails, do not rewrite canonical history. Preserve the run, quarantine late output, and recover only through the recorded append-only next action or a separately tagged run. If the same bytes cannot produce the same replay result, stop and report the integrity failure.
