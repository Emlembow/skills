#!/usr/bin/env python3
"""Read-only validator for research-loop canonical run state."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


EXIT_VALID = 0
EXIT_INVALID = 2
EXIT_RECOVERABLE = 3
EXIT_USAGE = 64
CONTRACT_SCHEMA_VERSION = 2
PROTOCOL_VERSION = "1.1.0"

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_OID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
UNIT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
ENVIRONMENT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SENSITIVE_ENVIRONMENT_NAME_RE = re.compile(
    r"(?:^|_)(?:AUTH|CREDENTIAL|PASSWORD|PASSWD|PRIVATE_KEY|SECRET|TOKEN|"
    r"API_KEY|ACCESS_KEY)(?:_|$)",
    re.IGNORECASE,
)
EXECUTION_CONTROL_ENVIRONMENT_NAMES = frozenset(
    {
        "BASH_ENV",
        "CDPATH",
        "CLASSPATH",
        "DOTNET_ADDITIONAL_DEPS",
        "DOTNET_SHARED_STORE",
        "DOTNET_STARTUP_HOOKS",
        "ENV",
        "GIT_EXEC_PATH",
        "IFS",
        "JAVA_TOOL_OPTIONS",
        "JDK_JAVA_OPTIONS",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "LUA_CPATH",
        "LUA_PATH",
        "NODE_OPTIONS",
        "NODE_PATH",
        "PATH",
        "PERL5LIB",
        "PERL5OPT",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "RUBYLIB",
        "RUBYOPT",
        "ZDOTDIR",
        "_JAVA_OPTIONS",
    }
)
EXECUTION_CONTROL_ENVIRONMENT_PREFIXES = ("DYLD_", "GIT_CONFIG_")
RFC3339_UTC_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)

ATTEMPT_EVENTS = {
    "prepared",
    "committed",
    "launch_intent",
    "launch_aborted",
    "launch_unresolved",
    "launched",
    "launch_finished",
    "cleanup_confirmed",
    "cleanup_unresolved",
    "evidence_published",
    "plan_tail_cancelled",
    "integrity_cleared",
    "stage_completed",
    "evaluated",
    "decided",
    "rolled_back",
    "finalized",
}
RUN_EVENTS = {"stop_requested", "run_stopped"}
STATUSES = {
    "keep",
    "deferred",
    "discard",
    "crash",
    "timeout",
    "invalid",
    "cancelled",
    "interrupted",
}
LANES = {"confirmed", "incubator", "candidate", "diagnostic"}
MATURITY = {"complete", "incomplete", "unknown"}
EMPTY_GENERATED_STATE_BYTES = (
    b'{"entries":[],"mode":"reset_each_launch","schema_version":1}\n'
)
EMPTY_GENERATED_STATE_SHA256 = hashlib.sha256(
    EMPTY_GENERATED_STATE_BYTES
).hexdigest()


class DuplicateKeyError(ValueError):
    pass


class UsageParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


class Diagnostics:
    def __init__(self) -> None:
        self.errors: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []

    def error(
        self, code: str, path: str, message: str, line: int | None = None
    ) -> None:
        self.errors.append(self._item(code, path, message, line))

    def warning(
        self, code: str, path: str, message: str, line: int | None = None
    ) -> None:
        self.warnings.append(self._item(code, path, message, line))

    @staticmethod
    def _item(
        code: str, path: str, message: str, line: int | None
    ) -> dict[str, Any]:
        item: dict[str, Any] = {"code": code, "path": path, "message": message}
        if line is not None:
            item["line"] = line
        return item

    def sorted_errors(self) -> list[dict[str, Any]]:
        return sorted(self.errors, key=_diagnostic_key)

    def sorted_warnings(self) -> list[dict[str, Any]]:
        return sorted(self.warnings, key=_diagnostic_key)


def _diagnostic_key(item: dict[str, Any]) -> tuple[str, int, str]:
    return (str(item.get("path", "")), int(item.get("line", 0)), str(item["code"]))


def _pairs_to_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite number {value!r}")


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite number {value!r}")
    return parsed


def strict_loads(source: str) -> Any:
    return json.loads(
        source,
        object_pairs_hook=_pairs_to_object,
        parse_constant=_reject_constant,
        parse_float=_parse_finite_float,
    )


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def invocation_fingerprint_payload(evaluation: dict[str, Any]) -> dict[str, Any]:
    """Return the exact resolved fields sealed by invocation_sha256."""
    return {
        "argv": evaluation.get("argv"),
        "cwd": evaluation.get("cwd"),
        "extractor_path": evaluation.get("extractor_path"),
        "executable_path": evaluation.get("executable_path"),
        "executable_sha256": evaluation.get("executable_sha256"),
        "environment_sha256": evaluation.get("environment_sha256"),
        "timeout_seconds": evaluation.get("timeout_seconds"),
    }


def is_execution_control_environment_name(name: str) -> bool:
    return name in EXECUTION_CONTROL_ENVIRONMENT_NAMES or name.startswith(
        EXECUTION_CONTROL_ENVIRONMENT_PREFIXES
    )


def protected_stage_ids(contract: dict[str, Any] | None) -> set[str]:
    if not isinstance(contract, dict):
        return set()
    resources = contract.get("stage_resources")
    if not isinstance(resources, dict):
        return set()
    return {
        stage
        for stage, resource in resources.items()
        if isinstance(stage, str)
        and isinstance(resource, dict)
        and resource.get("visibility") == "protected"
    }


def canonical_jsonl_bytes(records: list[dict[str, Any]]) -> bytes:
    return b"".join(
        (
            json.dumps(
                record,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        for record in records
    )


def jsonl_prefix_sha256(raw: bytes, line_count: int) -> str:
    return hashlib.sha256(b"".join(raw.splitlines(keepends=True)[:line_count])).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json_file(
    path: Path, diagnostics: Diagnostics, *, required: bool = True
) -> dict[str, Any] | None:
    display = str(path)
    if path.is_symlink():
        diagnostics.error("symlink_forbidden", display, "canonical JSON must not be a symlink")
        return None
    if not path.is_file():
        if path.exists():
            diagnostics.error(
                "invalid_publication_target",
                display,
                "canonical JSON path exists but is not a regular file",
            )
            return None
        if required:
            diagnostics.error("missing_file", display, "required JSON file is missing")
        return None
    try:
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError("UTF-8 byte-order mark is not allowed")
        value = strict_loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        diagnostics.error("invalid_json", display, str(error))
        return None
    if not isinstance(value, dict):
        diagnostics.error("invalid_json_type", display, "top-level value must be an object")
        return None
    return value


def load_jsonl(
    path: Path, diagnostics: Diagnostics
) -> tuple[list[dict[str, Any]], bytes]:
    display = str(path)
    if path.is_symlink():
        diagnostics.error("symlink_forbidden", display, "canonical JSONL must not be a symlink")
        return [], b""
    if not path.is_file():
        diagnostics.error("missing_file", display, "required JSONL file is missing")
        return [], b""
    try:
        raw = path.read_bytes()
    except OSError as error:
        diagnostics.error("read_failed", display, str(error))
        return [], b""
    if raw.startswith(b"\xef\xbb\xbf"):
        diagnostics.error("invalid_encoding", display, "UTF-8 byte-order mark is not allowed")
        return [], raw
    if raw and not raw.endswith(b"\n"):
        diagnostics.error("torn_jsonl_tail", display, "file must end with LF")
    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(raw.splitlines(keepends=True), start=1):
        if not raw_line.endswith(b"\n"):
            continue
        line = raw_line[:-1]
        if line.endswith(b"\r"):
            diagnostics.error(
                "invalid_line_ending", display, "use LF, not CRLF", line_number
            )
            line = line[:-1]
        if not line.strip():
            diagnostics.error("blank_jsonl_line", display, "blank lines are forbidden", line_number)
            continue
        try:
            value = strict_loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            diagnostics.error("invalid_jsonl", display, str(error), line_number)
            continue
        if not isinstance(value, dict):
            diagnostics.error(
                "invalid_jsonl_type", display, "line must contain one object", line_number
            )
            continue
        records.append(value)
    return records, raw


def is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def require_string(
    obj: dict[str, Any], key: str, diagnostics: Diagnostics, path: str, line: int | None = None
) -> str | None:
    value = obj.get(key)
    if not isinstance(value, str) or not value:
        diagnostics.error("invalid_field", path, f"{key} must be a nonempty string", line)
        return None
    return value


def require_id(
    obj: dict[str, Any], key: str, diagnostics: Diagnostics, path: str, line: int | None = None
) -> str | None:
    value = obj.get(key)
    if not isinstance(value, str) or SAFE_ID_RE.fullmatch(value) is None:
        diagnostics.error(
            "invalid_id",
            path,
            f"{key} must be 1-128 lowercase ID characters without path separators",
            line,
        )
        return None
    return value


def require_hex64(
    obj: dict[str, Any], key: str, diagnostics: Diagnostics, path: str, line: int | None = None
) -> str | None:
    value = obj.get(key)
    if not isinstance(value, str) or HEX64_RE.fullmatch(value) is None:
        diagnostics.error("invalid_digest", path, f"{key} must be lowercase SHA-256", line)
        return None
    return value


def require_git_oid(
    obj: dict[str, Any], key: str, diagnostics: Diagnostics, path: str, line: int | None = None
) -> str | None:
    value = obj.get(key)
    if not isinstance(value, str) or GIT_OID_RE.fullmatch(value) is None:
        diagnostics.error("invalid_git_oid", path, f"{key} must be a full Git object ID", line)
        return None
    return value


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or RFC3339_UTC_RE.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    return parsed if parsed.tzinfo == timezone.utc else None


def validate_timestamp(
    value: Any, diagnostics: Diagnostics, path: str, field: str, line: int | None = None
) -> datetime | None:
    parsed = parse_timestamp(value)
    if parsed is None:
        diagnostics.error(
            "invalid_timestamp", path, f"{field} must be an RFC 3339 UTC timestamp", line
        )
        return None
    return parsed


def validate_relative_syntax(
    value: Any,
    diagnostics: Diagnostics,
    path: str,
    field: str,
    line: int | None = None,
    *,
    allow_dot: bool = False,
) -> PurePosixPath | None:
    if not isinstance(value, str) or not value:
        diagnostics.error("invalid_path", path, f"{field} must be a relative path", line)
        return None
    if value == "." and allow_dot:
        return PurePosixPath(".")
    if value == ".":
        diagnostics.error("unsafe_path", path, f"{field} may not be '.'", line)
        return None
    if "\\" in value:
        diagnostics.error("invalid_path", path, f"{field} must use POSIX separators", line)
        return None
    candidate = PurePosixPath(value)
    if (
        candidate.is_absolute()
        or candidate.as_posix() != value
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        diagnostics.error(
            "unsafe_path", path, f"{field} must be normalized and stay within its root", line
        )
        return None
    return candidate


def resolve_regular_file(
    root: Path,
    relative: PurePosixPath,
    diagnostics: Diagnostics,
    path: str,
    field: str,
    line: int | None = None,
) -> Path | None:
    root_resolved = root.resolve()
    target = root.joinpath(*relative.parts)
    try:
        target_resolved = target.resolve(strict=True)
    except OSError:
        diagnostics.error("missing_artifact", path, f"{field} does not exist: {relative}", line)
        return None
    try:
        target_resolved.relative_to(root_resolved)
    except ValueError:
        diagnostics.error("unsafe_path", path, f"{field} escapes its root: {relative}", line)
        return None
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            diagnostics.error("symlink_forbidden", path, f"{field} traverses a symlink: {relative}", line)
            return None
    if not target_resolved.is_file():
        diagnostics.error("invalid_artifact", path, f"{field} must name a regular file", line)
        return None
    return target_resolved


def validate_artifact_ref(
    value: Any,
    root: Path,
    diagnostics: Diagnostics,
    path: str,
    field: str,
    line: int | None = None,
    *,
    expected_prefix: str | None = None,
    expected_path: str | None = None,
    expected_keys: set[str] | None = None,
) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        diagnostics.error("invalid_artifact_ref", path, f"{field} must be a path/hash object", line)
        return None
    allowed_keys = expected_keys or {"path", "sha256"}
    if set(value) != allowed_keys:
        diagnostics.error(
            "artifact_ref_schema_mismatch",
            path,
            f"{field} keys must be exactly {sorted(allowed_keys)}",
            line,
        )
    relative = validate_relative_syntax(value.get("path"), diagnostics, path, f"{field}.path", line)
    digest = value.get("sha256")
    if not isinstance(digest, str) or HEX64_RE.fullmatch(digest) is None:
        diagnostics.error(
            "invalid_digest", path, f"{field}.sha256 must be lowercase SHA-256", line
        )
        return None
    if relative is None:
        return None
    normalized = relative.as_posix()
    if expected_prefix is not None and relative.parts[0] != expected_prefix:
        diagnostics.error(
            "artifact_role_mismatch",
            path,
            f"{field} must be stored under {expected_prefix}/",
            line,
        )
        return None
    if expected_path is not None and normalized != expected_path:
        diagnostics.error(
            "artifact_role_mismatch",
            path,
            f"{field} must be {expected_path}",
            line,
        )
        return None
    target = resolve_regular_file(root, relative, diagnostics, path, field, line)
    if target is None:
        return None
    actual = file_sha256(target)
    if actual != digest:
        diagnostics.error(
            "artifact_hash_mismatch",
            path,
            f"{field} digest mismatch for {relative}: expected {digest}, got {actual}",
            line,
        )
        return None
    return (normalized, digest)


def load_referenced_json(
    reference: tuple[str, str] | None,
    root: Path,
    diagnostics: Diagnostics,
) -> dict[str, Any] | None:
    if reference is None:
        return None
    return load_json_file(root / reference[0], diagnostics)


def validate_design_record(
    reference: tuple[str, str] | None,
    result: dict[str, Any],
    run_dir: Path,
    contract: dict[str, Any] | None,
    budgets: dict[str, tuple[int | None, int, int]],
    confirmed_parents: dict[str, str],
    prior_result_ids: set[str],
    diagnostics: Diagnostics,
    result_path: str,
    line: int,
) -> None:
    design = load_referenced_json(reference, run_dir, diagnostics)
    if design is None or reference is None:
        return
    design_path = str(run_dir / reference[0])
    if design.get("schema_version") != 1:
        diagnostics.error("schema_version", design_path, "schema_version must equal 1")
    experiment_id = require_id(design, "experiment_id", diagnostics, design_path)
    if experiment_id != result.get("experiment_id"):
        diagnostics.error(
            "design_identity_mismatch",
            result_path,
            "design experiment_id differs from the result",
            line,
        )
    validate_timestamp(design.get("created_at"), diagnostics, design_path, "created_at")
    parent = design.get("implementation_parent")
    if not isinstance(parent, dict):
        diagnostics.error(
            "invalid_implementation_parent",
            design_path,
            "implementation_parent must be an object",
        )
    else:
        kind = parent.get("kind")
        parent_commit = require_git_oid(parent, "commit", diagnostics, design_path)
        if parent_commit != result.get("incumbent_commit"):
            diagnostics.error(
                "implementation_parent_mismatch",
                result_path,
                "design parent commit differs from the pre-attempt incumbent",
                line,
            )
        if kind == "baseline":
            if parent.get("attempt_id") is not None:
                diagnostics.error(
                    "invalid_implementation_parent",
                    design_path,
                    "baseline parent attempt_id must be null",
                )
            baseline = contract.get("baseline_commit") if contract else None
            if parent_commit != baseline:
                diagnostics.error(
                    "invalid_implementation_parent",
                    design_path,
                    "baseline parent commit differs from the sealed baseline",
                )
        elif kind == "confirmed_attempt":
            parent_attempt = require_id(parent, "attempt_id", diagnostics, design_path)
            if parent_attempt not in confirmed_parents:
                diagnostics.error(
                    "unconfirmed_implementation_parent",
                    design_path,
                    "implementation parent must name a prior confirmed attempt",
                )
            elif confirmed_parents[parent_attempt] != parent_commit:
                diagnostics.error(
                    "implementation_parent_mismatch",
                    design_path,
                    "implementation parent commit differs from the confirmed attempt",
                )
        else:
            diagnostics.error(
                "invalid_implementation_parent",
                design_path,
                "implementation_parent.kind must be baseline or confirmed_attempt",
            )
    sources = design.get("evidence_sources")
    if not isinstance(sources, list):
        diagnostics.error(
            "invalid_evidence_sources", design_path, "evidence_sources must be an array"
        )
    else:
        seen_sources: set[str] = set()
        for source in sources:
            if not isinstance(source, str) or SAFE_ID_RE.fullmatch(source) is None:
                diagnostics.error(
                    "invalid_evidence_source",
                    design_path,
                    "evidence source IDs must use the safe identifier syntax",
                )
                continue
            if source in seen_sources:
                diagnostics.error(
                    "duplicate_evidence_source",
                    design_path,
                    f"duplicate evidence source {source!r}",
                )
            elif source not in prior_result_ids:
                diagnostics.error(
                    "unknown_evidence_source",
                    design_path,
                    f"evidence source {source!r} is not prior canonical evidence",
                )
            seen_sources.add(source)
    for field in (
        "mechanism_family",
        "intervention_surface",
        "intent",
        "hypothesis",
        "general_failure_class",
        "supporting_metric_signature",
        "weakening_metric_signature",
        "predicted_unseen_behavior",
        "invariant_or_counterexample",
        "falsifier_or_ablation",
        "fail_fast_condition",
    ):
        require_string(design, field, diagnostics, design_path)
    if "hypothesis" in result and design.get("hypothesis") != result.get("hypothesis"):
        diagnostics.error(
            "design_hypothesis_mismatch",
            result_path,
            "result hypothesis differs from the frozen design",
            line,
        )
    allowed_files = validate_path_array(
        design.get("allowed_files"),
        diagnostics,
        design_path,
        "allowed_files",
        allow_empty=False,
    )
    writable_scope = (
        contract.get("scope", {}).get("writable_paths", [])
        if isinstance(contract, dict) and isinstance(contract.get("scope"), dict)
        else []
    )
    if not isinstance(writable_scope, list):
        writable_scope = []
    if isinstance(writable_scope, list):
        for allowed in allowed_files:
            if not any(
                isinstance(scope_path, str) and path_within_scope(allowed, scope_path)
                for scope_path in writable_scope
            ):
                diagnostics.error(
                    "design_scope_mismatch",
                    design_path,
                    f"allowed file {allowed!r} is outside the sealed writable scope",
                )
    validate_string_array(
        design.get("forbidden_changes"),
        diagnostics,
        design_path,
        "forbidden_changes",
        allow_empty=False,
    )
    cost = validate_usage_map(
        design.get("cost_estimate"), diagnostics, design_path, "cost_estimate"
    ) or {}
    require_exact_units(cost, set(budgets), diagnostics, design_path, "cost_estimate")
    validate_string_array(design.get("risks"), diagnostics, design_path, "risks")
    validate_string_array(
        design.get("rejected_alternatives"),
        diagnostics,
        design_path,
        "rejected_alternatives",
    )
    amendment = design.get("amendment")
    if amendment is not None:
        if not isinstance(amendment, dict):
            diagnostics.error(
                "invalid_amendment", design_path, "amendment must be an object or null"
            )
        else:
            validate_timestamp(
                amendment.get("amended_at"), diagnostics, design_path, "amendment.amended_at"
            )
            require_string(amendment, "reason", diagnostics, design_path)
            validate_string_array(
                amendment.get("changed_fields"),
                diagnostics,
                design_path,
                "amendment.changed_fields",
                allow_empty=False,
            )


def validate_raw_evidence(
    reference: tuple[str, str] | None,
    repetition: dict[str, Any],
    run_dir: Path,
    diagnostics: Diagnostics,
    result_path: str,
    field: str,
    line: int,
) -> None:
    raw = load_referenced_json(reference, run_dir, diagnostics)
    if raw is None or reference is None:
        return
    raw_path = str(run_dir / reference[0])
    expected_keys = {
        "schema_version",
        "result_id",
        "pair_id",
        "arm",
        "phase",
        "stage",
        "evaluated_commit",
        "seed",
        "input_id",
        "metrics",
        "primary_value",
        "duration_seconds",
        "evaluator_sha256",
    }
    if set(raw) != expected_keys:
        diagnostics.error(
            "invalid_raw_evidence",
            raw_path,
            f"raw evidence keys must be exactly {sorted(expected_keys)}",
        )
    if raw.get("schema_version") != 1:
        diagnostics.error("schema_version", raw_path, "schema_version must equal 1")
    require_id(raw, "result_id", diagnostics, raw_path)
    if raw.get("pair_id") is not None and (
        not isinstance(raw.get("pair_id"), str)
        or SAFE_ID_RE.fullmatch(raw["pair_id"]) is None
    ):
        diagnostics.error("invalid_pair_id", raw_path, "pair_id must be a safe ID or null")
    require_git_oid(raw, "evaluated_commit", diagnostics, raw_path)
    require_hex64(raw, "evaluator_sha256", diagnostics, raw_path)
    if not isinstance(raw.get("arm"), str) or raw.get("arm") not in {"incumbent", "candidate"}:
        diagnostics.error("invalid_arm", raw_path, "arm is invalid")
    if not isinstance(raw.get("phase"), str) or raw.get("phase") not in {"deterministic", "exploration", "confirmation"}:
        diagnostics.error("invalid_phase", raw_path, "phase is invalid")
    if not isinstance(raw.get("stage"), str) or UNIT_RE.fullmatch(raw["stage"]) is None:
        diagnostics.error("invalid_stage", raw_path, "stage must be a stable lowercase ID")
    if not isinstance(raw.get("metrics"), dict):
        diagnostics.error("invalid_metric", raw_path, "metrics must be an object")
    else:
        validate_finite_tree(raw["metrics"], diagnostics, raw_path, "metrics")
    raw_primary = raw.get("primary_value")
    if raw_primary is not None and not is_finite_number(raw_primary):
        diagnostics.error(
            "invalid_metric", raw_path, "primary_value must be finite or null"
        )
    raw_duration = raw.get("duration_seconds")
    if not is_finite_number(raw_duration) or raw_duration < 0:
        diagnostics.error(
            "invalid_duration", raw_path, "duration_seconds must be nonnegative and finite"
        )
    if raw.get("input_id") is not None and (
        not isinstance(raw.get("input_id"), str) or not raw.get("input_id")
    ):
        diagnostics.error("invalid_input_id", raw_path, "input_id must be nonempty or null")
    seed = raw.get("seed")
    if seed is not None and (
        isinstance(seed, bool) or not isinstance(seed, (int, str)) or seed == ""
    ):
        diagnostics.error("invalid_seed", raw_path, "seed must be an integer, string, or null")
    for key in (
        "result_id",
        "pair_id",
        "arm",
        "phase",
        "stage",
        "evaluated_commit",
        "seed",
        "input_id",
        "metrics",
        "primary_value",
        "duration_seconds",
        "evaluator_sha256",
    ):
        if raw.get(key) != repetition.get(key):
            diagnostics.error(
                "evidence_content_mismatch",
                result_path,
                f"{field}.{key} differs from normalized raw evidence",
                line,
            )


def validate_tree_snapshot(
    reference: tuple[str, str] | None,
    repetition: dict[str, Any],
    run_dir: Path,
    diagnostics: Diagnostics,
    result_path: str,
    field: str,
    line: int,
) -> None:
    snapshot = load_referenced_json(reference, run_dir, diagnostics)
    if snapshot is None or reference is None:
        return
    snapshot_path = str(run_dir / reference[0])
    if snapshot.get("schema_version") != 1:
        diagnostics.error("schema_version", snapshot_path, "schema_version must equal 1")
    require_id(snapshot, "result_id", diagnostics, snapshot_path)
    require_git_oid(snapshot, "commit", diagnostics, snapshot_path)
    require_git_oid(snapshot, "tree_oid", diagnostics, snapshot_path)
    for key in ("untracked_writable_paths", "unexpected_paths"):
        if snapshot.get(key) != []:
            diagnostics.error(
                "unclean_tree_snapshot",
                snapshot_path,
                f"{key} must be an explicitly empty array",
            )
    if snapshot.get("result_id") != repetition.get("result_id"):
        diagnostics.error(
            "tree_snapshot_mismatch",
            result_path,
            f"{field} snapshot result_id differs",
            line,
        )
    if snapshot.get("commit") != repetition.get("evaluated_commit"):
        diagnostics.error(
            "tree_snapshot_mismatch",
            result_path,
            f"{field} snapshot commit differs",
            line,
        )


def validate_effective_config(
    reference: tuple[str, str] | None,
    repetition: dict[str, Any],
    contract: dict[str, Any] | None,
    run_dir: Path,
    diagnostics: Diagnostics,
    result_path: str,
    field: str,
    line: int,
) -> None:
    config = load_referenced_json(reference, run_dir, diagnostics)
    if config is None or reference is None:
        return
    config_path = str(run_dir / reference[0])
    expected_keys = {"schema_version", "stage", "stage_access_sha256", "settings"}
    if set(config) != expected_keys:
        diagnostics.error(
            "invalid_effective_config",
            config_path,
            f"effective configuration keys must be exactly {sorted(expected_keys)}",
        )
    if config.get("schema_version") != 1:
        diagnostics.error("schema_version", config_path, "schema_version must equal 1")
    stage = repetition.get("stage")
    if config.get("stage") != stage:
        diagnostics.error(
            "effective_config_stage_mismatch",
            result_path,
            f"{field} effective configuration is bound to a different stage",
            line,
        )
    expected_access = (
        contract.get("stage_access_sha256", {}).get(stage)
        if isinstance(contract, dict)
        and isinstance(contract.get("stage_access_sha256"), dict)
        else None
    )
    if config.get("stage_access_sha256") != expected_access:
        diagnostics.error(
            "effective_config_access_mismatch",
            result_path,
            f"{field} effective configuration uses the wrong sealed access identity",
            line,
        )
    expected_config_digest = (
        contract.get("stage_config_sha256", {}).get(stage)
        if isinstance(contract, dict)
        and isinstance(contract.get("stage_config_sha256"), dict)
        else None
    )
    if reference[1] != expected_config_digest:
        diagnostics.error(
            "effective_config_digest_mismatch",
            result_path,
            f"{field} effective configuration differs from the sealed stage configuration",
            line,
        )
    if not isinstance(config.get("settings"), dict):
        diagnostics.error(
            "invalid_effective_config",
            config_path,
            "effective configuration settings must be an object",
        )


def validate_workspace_snapshot(
    reference: tuple[str, str] | None,
    workspace_id: Any,
    arm: Any,
    run_dir: Path,
    diagnostics: Diagnostics,
    path: str,
    line: int,
    *,
    contract: dict[str, Any] | None,
    supervisor_git_root: Path | None,
    verify_git: bool,
    expected_commit: Any = None,
    expected_cwd: Any = None,
) -> dict[str, Any] | None:
    workspace = load_referenced_json(reference, run_dir, diagnostics)
    if workspace is None or reference is None:
        return None
    workspace_path = str(run_dir / reference[0])
    expected_keys = {
        "schema_version",
        "workspace_id",
        "arm",
        "isolation_mode",
        "root_path",
        "root_identity_sha256",
        "git_dir_identity_sha256",
        "git_common_dir_identity_sha256",
    }
    if set(workspace) != expected_keys:
        diagnostics.error(
            "invalid_workspace_snapshot",
            workspace_path,
            f"workspace snapshot keys must be exactly {sorted(expected_keys)}",
        )
    if workspace.get("schema_version") != 1:
        diagnostics.error("schema_version", workspace_path, "schema_version must equal 1")
    if workspace.get("workspace_id") != workspace_id or workspace.get("arm") != arm:
        diagnostics.error(
            "workspace_identity_mismatch",
            path,
            "workspace snapshot identity differs from the planned launch arm",
            line,
        )
    if workspace.get("isolation_mode") != "dedicated_git_worktree":
        diagnostics.error(
            "invalid_workspace_snapshot",
            workspace_path,
            "workspace isolation_mode must be dedicated_git_worktree",
        )
    root_path = workspace.get("root_path")
    if not isinstance(root_path, str) or not Path(root_path).is_absolute():
        diagnostics.error(
            "invalid_workspace_root",
            workspace_path,
            "workspace root_path must be an absolute canonical path",
        )
        return None
    root_candidate = Path(root_path)
    if root_candidate.is_symlink():
        diagnostics.error(
            "symlink_forbidden",
            workspace_path,
            "workspace root_path must not be a symlink",
        )
        return None
    try:
        root = root_candidate.resolve(strict=True)
    except OSError:
        diagnostics.error(
            "missing_workspace_root",
            workspace_path,
            "workspace root_path does not exist",
        )
        return None
    if str(root) != root_path:
        diagnostics.error(
            "noncanonical_workspace_root",
            workspace_path,
            "workspace root_path must equal its resolved path without symlink traversal",
        )
    if not root.is_dir():
        diagnostics.error(
            "invalid_workspace_root",
            workspace_path,
            "workspace root_path must be a directory",
        )
        return None
    try:
        root.relative_to(run_dir.resolve())
    except ValueError:
        pass
    else:
        diagnostics.error(
            "workspace_inside_control_state",
            workspace_path,
            "an evaluation workspace must not be inside the canonical run directory",
        )
    root_stat = root.stat()
    root_identity = canonical_sha256(
        {
            "realpath": str(root),
            "device": root_stat.st_dev,
            "inode": root_stat.st_ino,
        }
    )
    recorded_root_identity = require_hex64(
        workspace, "root_identity_sha256", diagnostics, workspace_path
    )
    if recorded_root_identity != root_identity:
        diagnostics.error(
            "workspace_root_identity_mismatch",
            workspace_path,
            "root_identity_sha256 does not derive from the resolved physical directory",
        )

    if expected_cwd is not None:
        relative_cwd = validate_relative_syntax(
            expected_cwd,
            diagnostics,
            path,
            "cwd",
            line,
            allow_dot=True,
        )
        if relative_cwd is not None:
            cursor = root
            for part in relative_cwd.parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    diagnostics.error(
                        "symlink_forbidden",
                        path,
                        "launch cwd traverses a workspace symlink",
                        line,
                    )
                    break
            try:
                resolved_cwd = root.joinpath(*relative_cwd.parts).resolve(strict=True)
                resolved_cwd.relative_to(root)
                if not resolved_cwd.is_dir():
                    raise NotADirectoryError(str(resolved_cwd))
            except (OSError, ValueError):
                diagnostics.error(
                    "invalid_workspace_cwd",
                    path,
                    "launch cwd must resolve to a real directory inside its bound workspace root",
                    line,
                )

    git_dir_identity: str | None = None
    git_common_identity: str | None = None
    if verify_git:
        def git_value(
            repository: Path, *arguments: str, require_output: bool = True
        ) -> str | None:
            try:
                completed = subprocess.run(
                    ["git", "-C", str(repository), *arguments],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except OSError as error:
                diagnostics.error("git_unavailable", workspace_path, str(error))
                return None
            if completed.returncode != 0 or (
                require_output and not completed.stdout.strip()
            ):
                diagnostics.error(
                    "workspace_git_unreadable",
                    workspace_path,
                    f"unable to resolve workspace Git identity with {' '.join(arguments)}",
                )
                return None
            return completed.stdout.strip() if require_output else completed.stdout

        top_level = git_value(root, "rev-parse", "--show-toplevel")
        if top_level is not None:
            try:
                resolved_top = Path(top_level).resolve(strict=True)
            except OSError:
                resolved_top = None
            if resolved_top != root:
                diagnostics.error(
                    "workspace_not_git_toplevel",
                    workspace_path,
                    "workspace root must be the top level of its dedicated Git worktree",
                )
        git_dir_value = git_value(root, "rev-parse", "--absolute-git-dir")
        common_dir_value = git_value(root, "rev-parse", "--git-common-dir")

        def resolved_git_directory(value: str | None) -> Path | None:
            if value is None:
                return None
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = root / candidate
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                diagnostics.error(
                    "workspace_git_unreadable",
                    workspace_path,
                    "workspace Git administration directory does not exist",
                )
                return None
            if not resolved.is_dir():
                diagnostics.error(
                    "workspace_git_unreadable",
                    workspace_path,
                    "workspace Git administration path must be a directory",
                )
                return None
            return resolved

        git_dir = resolved_git_directory(git_dir_value)
        common_dir = resolved_git_directory(common_dir_value)
        if git_dir is not None:
            git_dir_stat = git_dir.stat()
            git_dir_identity = canonical_sha256(
                {
                    "realpath": str(git_dir),
                    "device": git_dir_stat.st_dev,
                    "inode": git_dir_stat.st_ino,
                }
            )
        if common_dir is not None:
            common_dir_stat = common_dir.stat()
            git_common_identity = canonical_sha256(
                {
                    "realpath": str(common_dir),
                    "device": common_dir_stat.st_dev,
                    "inode": common_dir_stat.st_ino,
                }
            )
        if supervisor_git_root is not None and common_dir is not None:
            supervisor_common_value = git_value(
                supervisor_git_root.resolve(), "rev-parse", "--git-common-dir"
            )
            if supervisor_common_value is not None:
                supervisor_common = Path(supervisor_common_value)
                if not supervisor_common.is_absolute():
                    supervisor_common = supervisor_git_root.resolve() / supervisor_common
                try:
                    supervisor_common = supervisor_common.resolve(strict=True)
                except OSError:
                    supervisor_common = None
                if supervisor_common != common_dir:
                    diagnostics.error(
                        "foreign_workspace_repository",
                        workspace_path,
                        "workspace must share the supervisor worktree's Git common directory",
                    )
            supervisor_root = supervisor_git_root.resolve()
            roots_overlap = root == supervisor_root
            if not roots_overlap:
                try:
                    root.relative_to(supervisor_root)
                    roots_overlap = True
                except ValueError:
                    try:
                        supervisor_root.relative_to(root)
                        roots_overlap = True
                    except ValueError:
                        pass
            if roots_overlap:
                diagnostics.error(
                    "workspace_not_dedicated",
                    workspace_path,
                    "evaluation workspace must be disjoint from the supervisor worktree",
                )
        recorded_git_dir = workspace.get("git_dir_identity_sha256")
        recorded_common_dir = workspace.get("git_common_dir_identity_sha256")
        if recorded_git_dir != git_dir_identity:
            diagnostics.error(
                "workspace_git_identity_mismatch",
                workspace_path,
                "git_dir_identity_sha256 does not derive from the live Git worktree",
            )
        if recorded_common_dir != git_common_identity:
            diagnostics.error(
                "workspace_git_identity_mismatch",
                workspace_path,
                "git_common_dir_identity_sha256 does not derive from the live repository",
            )
        head = git_value(root, "rev-parse", "HEAD")
        if expected_commit is not None and head != expected_commit:
            diagnostics.error(
                "workspace_commit_mismatch",
                path,
                "launch workspace HEAD differs from its evaluated commit",
                line,
            )
        tracked_dirty: set[str] = set()
        untracked_or_ignored: set[str] = set()
        index_flag_records = git_value(
            root, "ls-files", "-v", "-z", require_output=False
        )
        unsafe_index_entries: list[str] = []
        if index_flag_records is not None:
            for record in index_flag_records.split("\0"):
                if not record:
                    continue
                if len(record) < 3 or record[1] != " " or record[0] != "H":
                    unsafe_index_entries.append(record[2:] if len(record) >= 3 else record)
        if unsafe_index_entries:
            diagnostics.error(
                "unsafe_workspace_index_flags",
                workspace_path,
                "evaluation workspace contains assume-unchanged, skip-worktree, or non-normal index entries: "
                f"{sorted(unsafe_index_entries)[:8]}",
            )
        for arguments, destination in (
            (("diff", "--no-ext-diff", "--name-only", "-z"), tracked_dirty),
            (("diff", "--cached", "--no-ext-diff", "--name-only", "-z"), tracked_dirty),
            (("ls-files", "--others", "--exclude-standard", "-z"), untracked_or_ignored),
            (
                ("ls-files", "--others", "--ignored", "--exclude-standard", "-z"),
                untracked_or_ignored,
            ),
        ):
            value = git_value(root, *arguments, require_output=False)
            if value is not None:
                destination.update(item for item in value.split("\0") if item)
        if tracked_dirty:
            diagnostics.error(
                "dirty_evaluation_workspace",
                workspace_path,
                f"evaluation workspace has tracked or staged changes: {sorted(tracked_dirty)[:8]}",
            )
        generated_scope = (
            contract.get("scope", {}).get("generated_paths", [])
            if isinstance(contract, dict)
            and isinstance(contract.get("scope"), dict)
            else []
        )
        unexpected_untracked = sorted(untracked_or_ignored)
        if unexpected_untracked:
            diagnostics.error(
                "dirty_evaluation_workspace",
                workspace_path,
                f"evaluation workspace has unexpected untracked or ignored paths: {unexpected_untracked[:8]}",
            )
        for scope_path in generated_scope:
            if not isinstance(scope_path, str):
                continue
            target = root.joinpath(*PurePosixPath(scope_path).parts)
            if target.is_symlink() or target.exists():
                diagnostics.error(
                    "generated_state_not_reset",
                    workspace_path,
                    f"reset_each_launch requires an absent generated path: {scope_path}",
                )
        immutable_files = (
            contract.get("immutable_files", [])
            if isinstance(contract, dict)
            else []
        )
        if isinstance(immutable_files, list):
            for entry in immutable_files:
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                    continue
                relative = PurePosixPath(entry["path"])
                target = resolve_regular_file(
                    root,
                    relative,
                    diagnostics,
                    workspace_path,
                    f"immutable_files[{entry['path']!r}]",
                )
                if target is not None and file_sha256(target) != entry.get("sha256"):
                    diagnostics.error(
                        "workspace_immutable_mismatch",
                        workspace_path,
                        f"workspace immutable file differs from the sealed manifest: {entry['path']}",
                    )
    else:
        if workspace.get("git_dir_identity_sha256") is not None:
            require_hex64(
                workspace, "git_dir_identity_sha256", diagnostics, workspace_path
            )
        if workspace.get("git_common_dir_identity_sha256") is not None:
            require_hex64(
                workspace,
                "git_common_dir_identity_sha256",
                diagnostics,
                workspace_path,
            )
        git_dir_identity = workspace.get("git_dir_identity_sha256")
        git_common_identity = workspace.get("git_common_dir_identity_sha256")

    return {
        "root": str(root),
        "root_identity": root_identity,
        "git_dir_identity": git_dir_identity,
        "git_common_identity": git_common_identity,
    }


def validate_launch_plan(
    reference: tuple[str, str] | None,
    run_dir: Path,
    attempt_id: Any,
    role: Any,
    applicable_stages: list[str],
    contract: dict[str, Any] | None,
    budget_units: set[str],
    diagnostics: Diagnostics,
    path: str,
    line: int,
    *,
    supervisor_git_root: Path | None,
    verify_git: bool,
) -> list[dict[str, Any]]:
    plan = load_referenced_json(reference, run_dir, diagnostics)
    if plan is None or reference is None:
        return []
    plan_path = str(run_dir / reference[0])
    if set(plan) != {"schema_version", "attempt_id", "launches"}:
        diagnostics.error(
            "invalid_launch_plan",
            plan_path,
            "launch plan keys must be exactly schema_version, attempt_id, and launches",
        )
    if plan.get("schema_version") != 1:
        diagnostics.error("schema_version", plan_path, "schema_version must equal 1")
    if plan.get("attempt_id") != attempt_id:
        diagnostics.error(
            "launch_plan_attempt_mismatch",
            path,
            "launch plan attempt_id differs from prepared",
            line,
        )
    launches = plan.get("launches")
    if not isinstance(launches, list) or not launches:
        diagnostics.error(
            "invalid_launch_plan",
            plan_path,
            "launches must be a nonempty array",
        )
        return []
    expected_keys = {
        "ordinal",
        "stage",
        "arm",
        "phase",
        "pair_id",
        "seed",
        "input_id",
        "workspace_id",
        "workspace",
        "allocation",
        "timeout_seconds",
        "stage_access_sha256",
        "effective_config_sha256",
        "generated_state_sha256",
    }
    validated: list[dict[str, Any]] = []
    workspace_roots: dict[int, dict[str, Any] | None] = {}
    for index, launch in enumerate(launches):
        if not isinstance(launch, dict):
            diagnostics.error(
                "invalid_launch_plan", plan_path, f"launches[{index}] must be an object"
            )
            continue
        if set(launch) != expected_keys:
            diagnostics.error(
                "invalid_launch_plan",
                plan_path,
                f"launches[{index}] keys must be exactly {sorted(expected_keys)}",
            )
        if launch.get("ordinal") != index:
            diagnostics.error(
                "invalid_launch_plan",
                plan_path,
                f"launches[{index}].ordinal must equal {index}",
            )
        stage = launch.get("stage")
        if not isinstance(stage, str) or UNIT_RE.fullmatch(stage) is None:
            diagnostics.error(
                "invalid_launch_plan", plan_path, f"launches[{index}].stage is invalid"
            )
        if launch.get("arm") not in ("incumbent", "candidate"):
            diagnostics.error(
                "invalid_launch_plan", plan_path, f"launches[{index}].arm is invalid"
            )
        if launch.get("phase") not in (
            "deterministic",
            "exploration",
            "confirmation",
        ):
            diagnostics.error(
                "invalid_launch_plan", plan_path, f"launches[{index}].phase is invalid"
            )
        pair_id = launch.get("pair_id")
        if pair_id is not None and (
            not isinstance(pair_id, str) or SAFE_ID_RE.fullmatch(pair_id) is None
        ):
            diagnostics.error(
                "invalid_launch_plan", plan_path, f"launches[{index}].pair_id is invalid"
            )
        seed = launch.get("seed")
        if seed is not None and (
            isinstance(seed, bool) or not isinstance(seed, (int, str)) or seed == ""
        ):
            diagnostics.error(
                "invalid_launch_plan", plan_path, f"launches[{index}].seed is invalid"
            )
        input_id = launch.get("input_id")
        if input_id is not None and (
            not isinstance(input_id, str) or not input_id
        ):
            diagnostics.error(
                "invalid_launch_plan", plan_path, f"launches[{index}].input_id is invalid"
            )
        workspace_id = launch.get("workspace_id")
        if not isinstance(workspace_id, str) or SAFE_ID_RE.fullmatch(workspace_id) is None:
            diagnostics.error(
                "invalid_launch_plan",
                plan_path,
                f"launches[{index}].workspace_id is invalid",
            )
        workspace_ref = validate_artifact_ref(
            launch.get("workspace"),
            run_dir,
            diagnostics,
            plan_path,
            f"launches[{index}].workspace",
            expected_path=(
                f"snapshots/{attempt_id}-{workspace_id}.json"
                if isinstance(workspace_id, str)
                else None
            ),
        )
        workspace_roots[index] = validate_workspace_snapshot(
            workspace_ref,
            workspace_id,
            launch.get("arm"),
            run_dir,
            diagnostics,
            plan_path,
            1,
            supervisor_git_root=supervisor_git_root,
            verify_git=verify_git,
            contract=contract,
        )
        allocation = validate_usage_map(
            launch.get("allocation"),
            diagnostics,
            plan_path,
            f"launches[{index}].allocation",
        ) or {}
        require_exact_units(
            allocation,
            budget_units,
            diagnostics,
            plan_path,
            f"launches[{index}].allocation",
        )
        for digest_field in (
            "stage_access_sha256",
            "effective_config_sha256",
            "generated_state_sha256",
        ):
            require_hex64(launch, digest_field, diagnostics, plan_path)
        if not is_positive_int(launch.get("timeout_seconds")):
            diagnostics.error(
                "invalid_launch_timeout",
                plan_path,
                f"launches[{index}].timeout_seconds must be a positive integer",
            )
        validated.append(launch)

    stage_access = (
        contract.get("stage_access_sha256", {})
        if isinstance(contract, dict)
        and isinstance(contract.get("stage_access_sha256"), dict)
        else {}
    )
    stage_config = (
        contract.get("stage_config_sha256", {})
        if isinstance(contract, dict)
        and isinstance(contract.get("stage_config_sha256"), dict)
        else {}
    )
    generated_digest = (
        contract.get("generated_state_policy", {}).get("initial_state_sha256")
        if isinstance(contract, dict)
        and isinstance(contract.get("generated_state_policy"), dict)
        else None
    )
    sealed_timeout = (
        contract.get("evaluation", {}).get("timeout_seconds")
        if isinstance(contract, dict)
        and isinstance(contract.get("evaluation"), dict)
        else None
    )
    allocation_totals = {unit: 0 for unit in budget_units}
    for index, launch in enumerate(validated):
        stage = launch.get("stage")
        for field, expected in (
            ("stage_access_sha256", stage_access.get(stage)),
            ("effective_config_sha256", stage_config.get(stage)),
            ("generated_state_sha256", generated_digest),
            ("timeout_seconds", sealed_timeout),
        ):
            if launch.get(field) != expected:
                diagnostics.error(
                    "launch_plan_contract_mismatch",
                    plan_path,
                    f"launches[{index}].{field} differs from the sealed contract",
                )
        allocation = launch.get("allocation")
        if isinstance(allocation, dict):
            for unit in budget_units:
                amount = allocation.get(unit)
                if is_nonnegative_int(amount):
                    allocation_totals[unit] += amount
        if not is_positive_int(
            allocation.get("evaluator_calls") if isinstance(allocation, dict) else None
        ):
            diagnostics.error(
                "invalid_launch_plan_allocation",
                plan_path,
                f"launches[{index}] must allocate at least one evaluator call",
            )

    per_attempt_max = (
        contract.get("per_attempt_max", {}) if isinstance(contract, dict) else {}
    )
    if isinstance(per_attempt_max, dict):
        for unit, total in allocation_totals.items():
            maximum = per_attempt_max.get(unit)
            if is_nonnegative_int(maximum) and total > maximum:
                diagnostics.error(
                    "launch_plan_exceeds_cap",
                    plan_path,
                    f"planned {unit} exceeds the sealed per-attempt maximum",
                )

    plan_stages = [launch.get("stage") for launch in validated]
    if set(plan_stages) != set(applicable_stages):
        diagnostics.error(
            "launch_plan_stage_coverage",
            plan_path,
            "launch plan stages must exactly cover the applicable sealed stage sequence",
        )
    stage_positions = {
        stage: index for index, stage in enumerate(applicable_stages)
    }
    ordered_positions = [
        stage_positions[stage] for stage in plan_stages if stage in stage_positions
    ]
    if ordered_positions != sorted(ordered_positions):
        diagnostics.error(
            "launch_plan_stage_order",
            plan_path,
            "launch plan entries must be grouped in sealed stage order",
        )

    objective = (
        contract.get("objective", {}) if isinstance(contract, dict) else {}
    )
    final_test = contract.get("final_test") if isinstance(contract, dict) else None
    acceptance_stage = (
        final_test.get("stage")
        if role == "final" and isinstance(final_test, dict)
        else objective.get("acceptance_split")
        if isinstance(objective, dict)
        else None
    )
    protected_stages = protected_stage_ids(contract)
    if protected_stages:
        for index, launch in enumerate(validated):
            allocation = (
                launch.get("allocation")
                if isinstance(launch.get("allocation"), dict)
                else {}
            )
            query_use = allocation.get("validation_queries")
            if launch.get("stage") in protected_stages and not is_positive_int(
                query_use
            ):
                diagnostics.error(
                    "launch_plan_missing_gate_use",
                    plan_path,
                    f"launches[{index}] protected-stage allocation must reserve a validation query",
                )
            elif launch.get("stage") not in protected_stages and query_use not in (
                None,
                0,
            ):
                diagnostics.error(
                    "launch_plan_unexpected_gate_use",
                    plan_path,
                    f"launches[{index}] visible-stage allocation must use zero validation queries",
                )

    measurement = (
        contract.get("measurement", {}) if isinstance(contract, dict) else {}
    )
    mode = measurement.get("mode") if isinstance(measurement, dict) else None
    target_arm = "candidate" if role == "candidate" else "incumbent"
    workspace_arms: dict[Any, Any] = {}
    workspace_root_arms: dict[Any, Any] = {}
    workspace_git_dir_arms: dict[Any, Any] = {}
    arm_workspaces: dict[Any, set[Any]] = {}
    for launch_index, launch in enumerate(validated):
        workspace_id = launch.get("workspace_id")
        arm = launch.get("arm")
        prior_arm = workspace_arms.get(workspace_id)
        if prior_arm is not None and prior_arm != arm:
            diagnostics.error(
                "shared_arm_workspace",
                plan_path,
                "incumbent and candidate arms must use distinct isolated workspaces",
            )
        workspace_arms[workspace_id] = arm
        arm_workspaces.setdefault(arm, set()).add(workspace_id)
        workspace_identity = workspace_roots.get(launch_index) or {}
        root_identity = workspace_identity.get("root_identity")
        prior_root_arm = workspace_root_arms.get(root_identity)
        if root_identity is not None and prior_root_arm is not None and prior_root_arm != arm:
            diagnostics.error(
                "shared_arm_workspace_root",
                plan_path,
                "opposing arms must bind distinct resolved workspace-root identities",
            )
        if root_identity is not None:
            workspace_root_arms[root_identity] = arm
        git_dir_identity = workspace_identity.get("git_dir_identity")
        prior_git_dir_arm = workspace_git_dir_arms.get(git_dir_identity)
        if (
            git_dir_identity is not None
            and prior_git_dir_arm is not None
            and prior_git_dir_arm != arm
        ):
            diagnostics.error(
                "shared_arm_workspace_git_dir",
                plan_path,
                "opposing arms must bind distinct live Git worktree administration directories",
            )
        if git_dir_identity is not None:
            workspace_git_dir_arms[git_dir_identity] = arm
    opposing_roots = {
        arm: {
            identity.get("root")
            for index, identity in workspace_roots.items()
            if identity is not None
            and index < len(launches)
            and isinstance(launches[index], dict)
            and launches[index].get("arm") == arm
        }
        for arm in ("incumbent", "candidate")
    }
    for incumbent_root in opposing_roots["incumbent"]:
        for candidate_root in opposing_roots["candidate"]:
            if not isinstance(incumbent_root, str) or not isinstance(candidate_root, str):
                continue
            incumbent_path = Path(incumbent_root)
            candidate_path = Path(candidate_root)
            try:
                candidate_path.relative_to(incumbent_path)
                nested = True
            except ValueError:
                try:
                    incumbent_path.relative_to(candidate_path)
                    nested = True
                except ValueError:
                    nested = False
            if nested:
                diagnostics.error(
                    "nested_arm_workspace_roots",
                    plan_path,
                    "opposing arm workspace roots must not contain one another",
                )
    if role == "candidate" and mode == "noisy" and not (
        arm_workspaces.get("incumbent") and arm_workspaces.get("candidate")
    ):
        diagnostics.error(
            "missing_isolated_arm_workspace",
            plan_path,
            "candidate evaluation requires isolated incumbent and candidate workspaces",
        )
    for stage in applicable_stages:
        stage_launches = [
            launch for launch in validated if launch.get("stage") == stage
        ]
        if not stage_launches:
            continue
        if mode == "deterministic":
            if len(stage_launches) != 1:
                diagnostics.error(
                    "invalid_deterministic_plan",
                    plan_path,
                    f"deterministic stage {stage!r} requires exactly one launch",
                )
            for launch in stage_launches:
                if (
                    launch.get("arm") != target_arm
                    or launch.get("phase") != "deterministic"
                    or launch.get("pair_id") is not None
                    or launch.get("seed") is not None
                    or launch.get("input_id") is not None
                ):
                    diagnostics.error(
                        "invalid_deterministic_plan",
                        plan_path,
                        f"deterministic stage {stage!r} has an invalid arm or sampling identity",
                    )
            continue
        if mode != "noisy":
            continue
        if stage != acceptance_stage:
            if len(stage_launches) != 1:
                diagnostics.error(
                    "invalid_noisy_plan",
                    plan_path,
                    f"non-acceptance noisy stage {stage!r} requires exactly one launch",
                )
            for launch in stage_launches:
                if (
                    launch.get("arm") != target_arm
                    or launch.get("phase") != "exploration"
                    or launch.get("pair_id") is not None
                    or (launch.get("seed"), launch.get("input_id")) == (None, None)
                ):
                    diagnostics.error(
                        "invalid_noisy_plan",
                        plan_path,
                        f"non-acceptance noisy stage {stage!r} has an invalid arm or sampling identity",
                    )
            continue

        required_exploration = measurement.get("exploration_repetitions")
        required_confirmation = measurement.get("confirmation_repetitions")
        required_total = measurement.get("minimum_repetitions")
        target_launches = [
            launch for launch in stage_launches if launch.get("arm") == target_arm
        ]
        phase_counts = {
            phase: sum(
                1 for launch in target_launches if launch.get("phase") == phase
            )
            for phase in ("exploration", "confirmation")
        }
        if (
            not is_nonnegative_int(required_exploration)
            or not is_nonnegative_int(required_confirmation)
            or not is_positive_int(required_total)
            or phase_counts["exploration"] < required_exploration
            or phase_counts["confirmation"] < required_confirmation
            or len(target_launches) < required_total
        ):
            diagnostics.error(
                "insufficient_noisy_launch_plan",
                plan_path,
                "noisy acceptance plan cannot satisfy the sealed repetition and phase minima",
            )
        if role == "candidate":
            if len(stage_launches) % 2 != 0:
                diagnostics.error(
                    "invalid_noisy_pair_plan",
                    plan_path,
                    "noisy candidate acceptance launches must form adjacent two-arm pairs",
                )
            seen_pairs: set[Any] = set()
            seen_identities: set[tuple[Any, Any]] = set()
            first_arm_counts = {"incumbent": 0, "candidate": 0}
            phase_first_arm_counts = {
                "exploration": {"incumbent": 0, "candidate": 0},
                "confirmation": {"incumbent": 0, "candidate": 0},
            }
            for pair_index in range(0, len(stage_launches), 2):
                pair = stage_launches[pair_index : pair_index + 2]
                if len(pair) != 2:
                    continue
                left, right = pair
                pair_id = left.get("pair_id")
                identity = (left.get("seed"), left.get("input_id"))
                if (
                    {left.get("arm"), right.get("arm")}
                    != {"incumbent", "candidate"}
                    or not isinstance(pair_id, str)
                    or right.get("pair_id") != pair_id
                    or left.get("phase") != right.get("phase")
                    or left.get("seed") != right.get("seed")
                    or left.get("input_id") != right.get("input_id")
                    or identity == (None, None)
                    or left.get("effective_config_sha256")
                    != right.get("effective_config_sha256")
                    or left.get("generated_state_sha256")
                    != right.get("generated_state_sha256")
                ):
                    diagnostics.error(
                        "invalid_noisy_pair_plan",
                        plan_path,
                        f"acceptance pair at launch index {pair_index} is not a matched incumbent/candidate pair",
                    )
                first_arm = left.get("arm")
                phase = left.get("phase")
                if first_arm in first_arm_counts:
                    first_arm_counts[first_arm] += 1
                    if phase in phase_first_arm_counts:
                        phase_first_arm_counts[phase][first_arm] += 1
                if pair_id in seen_pairs:
                    diagnostics.error(
                        "duplicate_noisy_pair_plan",
                        plan_path,
                        f"pair_id {pair_id!r} is reused",
                    )
                if identity in seen_identities:
                    diagnostics.error(
                        "reused_noisy_plan_identity",
                        plan_path,
                        "noisy pairs must use unique seed/input identities",
                    )
                seen_pairs.add(pair_id)
                seen_identities.add(identity)
            if abs(
                first_arm_counts["incumbent"] - first_arm_counts["candidate"]
            ) > 1:
                diagnostics.error(
                    "uncounterbalanced_arm_order",
                    plan_path,
                    "noisy pair arm order must be counterbalanced across the acceptance plan",
                )
            for phase, counts in phase_first_arm_counts.items():
                if sum(counts.values()) >= 2 and abs(
                    counts["incumbent"] - counts["candidate"]
                ) > 1:
                    diagnostics.error(
                        "uncounterbalanced_arm_order",
                        plan_path,
                        f"noisy pair arm order must be counterbalanced within {phase}",
                    )
        else:
            seen_identities: set[tuple[Any, Any]] = set()
            for launch in stage_launches:
                identity = (launch.get("seed"), launch.get("input_id"))
                if (
                    launch.get("arm") != "incumbent"
                    or launch.get("pair_id") is not None
                    or launch.get("phase") not in ("exploration", "confirmation")
                    or identity == (None, None)
                ):
                    diagnostics.error(
                        "invalid_noisy_plan",
                        plan_path,
                        "noisy baseline/final acceptance launches require unique incumbent identities",
                    )
                if identity in seen_identities:
                    diagnostics.error(
                        "reused_noisy_plan_identity",
                        plan_path,
                        "noisy baseline/final launches must use unique seed/input identities",
                    )
                seen_identities.add(identity)
    return validated


def validate_admission_snapshot(
    reference: tuple[str, str] | None,
    launch_plan: list[dict[str, Any]],
    role: Any,
    prior_usage: dict[str, int],
    prepared_at: datetime | None,
    contract: dict[str, Any] | None,
    budget_units: set[str],
    run_dir: Path,
    diagnostics: Diagnostics,
    path: str,
    line: int,
) -> dict[str, Any] | None:
    admission = load_referenced_json(reference, run_dir, diagnostics)
    if admission is None or reference is None:
        return None
    admission_path = str(run_dir / reference[0])
    expected_keys = {
        "allowed",
        "deadline_fit",
        "checked_at",
        "worst_case_end_at",
        "cumulative_before",
        "requested_worst_case",
        "protected_after_launch",
    }
    if set(admission) != expected_keys:
        diagnostics.error(
            "invalid_admission",
            admission_path,
            f"admission keys must be exactly {sorted(expected_keys)}",
        )
    if not isinstance(admission.get("allowed"), bool) or not isinstance(
        admission.get("deadline_fit"), bool
    ):
        diagnostics.error(
            "invalid_admission",
            admission_path,
            "admission flags must be boolean",
        )
    checked_at = validate_timestamp(
        admission.get("checked_at"), diagnostics, admission_path, "checked_at"
    )
    worst_case_end_at = validate_timestamp(
        admission.get("worst_case_end_at"),
        diagnostics,
        admission_path,
        "worst_case_end_at",
    )
    if checked_at is not None and prepared_at is not None and checked_at > prepared_at:
        diagnostics.error(
            "admission_after_prepared",
            path,
            "sealed admission must be checked no later than the prepared event",
            line,
        )
    if (
        worst_case_end_at is not None
        and prepared_at is not None
        and prepared_at > worst_case_end_at
    ):
        diagnostics.error(
            "prepared_after_admission_window",
            path,
            "prepared event occurs after the admitted worst-case window",
            line,
        )
    deadline_value = contract.get("deadline_at") if isinstance(contract, dict) else None
    deadline = (
        parse_timestamp(deadline_value) if isinstance(deadline_value, str) else None
    )
    deadline_fit = bool(
        checked_at is not None
        and worst_case_end_at is not None
        and checked_at <= worst_case_end_at
        and (deadline is None or worst_case_end_at <= deadline)
    )
    if admission.get("deadline_fit") is not deadline_fit:
        diagnostics.error(
            "deadline_fit_mismatch",
            admission_path,
            "admission.deadline_fit does not match its sealed time window",
        )
    cumulative_before = validate_usage_map(
        admission.get("cumulative_before"),
        diagnostics,
        admission_path,
        "cumulative_before",
    ) or {}
    requested = validate_usage_map(
        admission.get("requested_worst_case"),
        diagnostics,
        admission_path,
        "requested_worst_case",
    ) or {}
    protected = validate_usage_map(
        admission.get("protected_after_launch"),
        diagnostics,
        admission_path,
        "protected_after_launch",
    ) or {}
    for field, value in (
        ("cumulative_before", cumulative_before),
        ("requested_worst_case", requested),
        ("protected_after_launch", protected),
    ):
        require_exact_units(
            value, budget_units, diagnostics, admission_path, field
        )
    planned = {unit: 0 for unit in budget_units}
    for launch in launch_plan:
        allocation = launch.get("allocation")
        if not isinstance(allocation, dict):
            continue
        for unit in budget_units:
            amount = allocation.get(unit)
            if is_nonnegative_int(amount):
                planned[unit] += amount
    if requested != planned:
        diagnostics.error(
            "admission_plan_mismatch",
            admission_path,
            "requested_worst_case must equal the sealed launch-plan allocation",
        )
    constraints_fit = deadline_fit
    planned_timeout_seconds = sum(
        launch.get("timeout_seconds", 0)
        for launch in launch_plan
        if is_positive_int(launch.get("timeout_seconds"))
    )
    if checked_at is not None and worst_case_end_at is not None:
        admitted_seconds = (worst_case_end_at - checked_at).total_seconds()
        if admitted_seconds < planned_timeout_seconds:
            constraints_fit = False
            if admission.get("allowed") is True:
                diagnostics.error(
                    "admission_timeout_window_too_short",
                    admission_path,
                    "allowed admission window cannot cover the serial launch-plan timeouts",
                )
    budgets = contract.get("budgets", {}) if isinstance(contract, dict) else {}
    maxima = (
        contract.get("per_attempt_max", {}) if isinstance(contract, dict) else {}
    )
    for unit in budget_units:
        if cumulative_before.get(unit) != prior_usage.get(unit, 0):
            diagnostics.error(
                "cumulative_before_mismatch",
                admission_path,
                f"cumulative_before.{unit} differs from prior launch receipts",
            )
            constraints_fit = False
        maximum = maxima.get(unit) if isinstance(maxima, dict) else None
        if not is_nonnegative_int(maximum) or requested.get(unit, 0) > maximum:
            constraints_fit = False
        budget = budgets.get(unit) if isinstance(budgets, dict) else None
        if not isinstance(budget, dict):
            constraints_fit = False
            continue
        operational_reserve = budget.get("operational_reserve")
        final_test_reserve = budget.get("final_test_reserve")
        total = budget.get("total")
        required_protection = (
            operational_reserve
            + (final_test_reserve if role != "final" else 0)
            if is_nonnegative_int(operational_reserve)
            and is_nonnegative_int(final_test_reserve)
            else None
        )
        if required_protection is None or protected.get(unit, 0) < required_protection:
            constraints_fit = False
        if role == "final" and (
            not is_nonnegative_int(final_test_reserve)
            or requested.get(unit, 0) > final_test_reserve
        ):
            constraints_fit = False
        if total is not None and (
            not is_nonnegative_int(total)
            or requested.get(unit, 0) + protected.get(unit, 0)
            > total - prior_usage.get(unit, 0)
        ):
            constraints_fit = False
    if admission.get("allowed") is not constraints_fit:
        diagnostics.error(
            "admission_decision_mismatch",
            admission_path,
            "admission.allowed does not match sealed budget, reserve, cap, and deadline checks",
        )
    return admission


def validate_usage_map(
    value: Any, diagnostics: Diagnostics, path: str, field: str, line: int | None = None
) -> dict[str, int] | None:
    if not isinstance(value, dict):
        diagnostics.error("invalid_budget", path, f"{field} must be an object", line)
        return None
    output: dict[str, int] = {}
    for unit, amount in value.items():
        if not isinstance(unit, str) or UNIT_RE.fullmatch(unit) is None:
            diagnostics.error("invalid_budget_unit", path, f"invalid budget unit {unit!r}", line)
            continue
        if not is_nonnegative_int(amount):
            diagnostics.error(
                "invalid_budget_value", path, f"{field}.{unit} must be a nonnegative integer", line
            )
            continue
        output[unit] = amount
    return output


def validate_nullable_usage_map(
    value: Any,
    diagnostics: Diagnostics,
    path: str,
    field: str,
    line: int | None = None,
) -> dict[str, int | None] | None:
    if not isinstance(value, dict):
        diagnostics.error("invalid_budget", path, f"{field} must be an object", line)
        return None
    output: dict[str, int | None] = {}
    for unit, amount in value.items():
        if not isinstance(unit, str) or UNIT_RE.fullmatch(unit) is None:
            diagnostics.error("invalid_budget_unit", path, f"invalid budget unit {unit!r}", line)
            continue
        if amount is not None and not is_nonnegative_int(amount):
            diagnostics.error(
                "invalid_budget_value",
                path,
                f"{field}.{unit} must be a nonnegative integer or null",
                line,
            )
            continue
        output[unit] = amount
    return output


def require_exact_units(
    values: dict[str, Any],
    expected: set[str],
    diagnostics: Diagnostics,
    path: str,
    field: str,
    line: int | None = None,
) -> None:
    actual = set(values)
    if actual != expected:
        diagnostics.error(
            "budget_units_mismatch",
            path,
            f"{field} units must be exactly {sorted(expected)}; got {sorted(actual)}",
            line,
        )


def validate_finite_tree(
    value: Any, diagnostics: Diagnostics, path: str, field: str, line: int | None = None
) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        diagnostics.error("nonfinite_metric", path, f"{field} contains a non-finite number", line)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            validate_finite_tree(item, diagnostics, path, f"{field}[{index}]", line)
    elif isinstance(value, dict):
        for key, item in value.items():
            validate_finite_tree(item, diagnostics, path, f"{field}.{key}", line)


def all_constraint_leaves_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        return all(all_constraint_leaves_true(item) for item in value.values())
    if isinstance(value, list):
        return all(all_constraint_leaves_true(item) for item in value)
    return False


def validate_string_array(
    value: Any,
    diagnostics: Diagnostics,
    path: str,
    field: str,
    line: int | None = None,
    *,
    allow_empty: bool = True,
) -> list[str]:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or not all(isinstance(item, str) and bool(item.strip()) for item in value)
    ):
        qualifier = "a string array" if allow_empty else "a nonempty string array"
        diagnostics.error("invalid_field", path, f"{field} must be {qualifier}", line)
        return []
    if len(set(value)) != len(value):
        diagnostics.error("duplicate_value", path, f"{field} must not contain duplicates", line)
    return value


def validate_path_array(
    value: Any,
    diagnostics: Diagnostics,
    path: str,
    field: str,
    line: int | None = None,
    *,
    allow_empty: bool = True,
) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "an array" if allow_empty else "a nonempty array"
        diagnostics.error("invalid_path_scope", path, f"{field} must be {qualifier}", line)
        return []
    output: list[str] = []
    for index, item in enumerate(value):
        relative = validate_relative_syntax(
            item, diagnostics, path, f"{field}[{index}]", line
        )
        if relative is not None:
            output.append(relative.as_posix())
    if len(set(output)) != len(output):
        diagnostics.error("duplicate_path_scope", path, f"{field} must not contain duplicates", line)
    return output


def path_within_scope(path: str, scope: str) -> bool:
    path_parts = PurePosixPath(path).parts
    scope_parts = PurePosixPath(scope).parts
    return path_parts[: len(scope_parts)] == scope_parts


def validate_contract(
    run_dir: Path, trusted_root: Path | None, diagnostics: Diagnostics
) -> tuple[
    dict[str, Any] | None,
    str | None,
    dict[str, tuple[int | None, int, int]],
    list[str],
]:
    contract_path = run_dir / "contract.json"
    seal_path = run_dir / "contract.sha256"
    contract = load_json_file(contract_path, diagnostics)
    actual_digest: str | None = None
    if contract_path.is_file():
        actual_digest = file_sha256(contract_path)
    if not seal_path.is_file():
        diagnostics.error("missing_file", str(seal_path), "contract seal is missing")
    elif seal_path.is_symlink():
        diagnostics.error(
            "symlink_forbidden", str(seal_path), "contract seal must not be a symlink"
        )
    else:
        try:
            raw_seal = seal_path.read_bytes()
        except OSError as error:
            diagnostics.error("read_failed", str(seal_path), str(error))
        else:
            if re.fullmatch(rb"[0-9a-f]{64}\n", raw_seal) is None:
                diagnostics.error(
                    "invalid_contract_seal",
                    str(seal_path),
                    "seal must be exactly lowercase SHA-256 followed by LF",
                )
            elif actual_digest is not None and raw_seal[:-1].decode("ascii") != actual_digest:
                diagnostics.error(
                    "contract_hash_mismatch",
                    str(seal_path),
                    "contract.json does not match contract.sha256",
                )
    budgets: dict[str, tuple[int | None, int, int]] = {}
    required_stages: list[str] = []
    invocation_digest: str | None = None
    evaluator_digest: str | None = None
    if contract is None:
        return None, actual_digest, budgets, required_stages
    path = str(contract_path)
    root = trusted_root
    if root is None:
        root = (
            run_dir.parent.parent
            if run_dir.parent.name == ".research-loop"
            else run_dir.parent
        )
    root = root.resolve()
    resolved_extractor_manifest_path: str | None = None
    expected_contract_keys = {
        "schema_version",
        "protocol",
        "run_tag",
        "feedback_mode",
        "baseline_commit",
        "deadline_at",
        "objective",
        "measurement",
        "scope",
        "generated_state_policy",
        "control_policy",
        "evaluation",
        "immutable_files",
        "budgets",
        "per_attempt_max",
        "promotion",
        "stage_resources",
        "stage_access_sha256",
        "stage_config_sha256",
        "final_test",
    }
    if set(contract) != expected_contract_keys:
        diagnostics.error(
            "contract_schema_mismatch",
            path,
            f"contract keys must be exactly {sorted(expected_contract_keys)}",
        )
    if contract.get("schema_version") != CONTRACT_SCHEMA_VERSION:
        diagnostics.error(
            "schema_version",
            path,
            f"schema_version must equal {CONTRACT_SCHEMA_VERSION}",
        )
    protocol = contract.get("protocol")
    if not isinstance(protocol, dict) or set(protocol) != {
        "version",
        "validator_sha256",
    }:
        diagnostics.error(
            "invalid_protocol_identity",
            path,
            "protocol keys must be exactly version and validator_sha256",
        )
    else:
        if protocol.get("version") != PROTOCOL_VERSION:
            diagnostics.error(
                "protocol_version_mismatch",
                path,
                f"contract requires protocol {protocol.get('version')!r}; this validator implements {PROTOCOL_VERSION!r}",
            )
        validator_digest = require_hex64(
            protocol, "validator_sha256", diagnostics, path
        )
        current_validator_digest = file_sha256(Path(__file__).resolve(strict=True))
        if (
            validator_digest is not None
            and validator_digest != current_validator_digest
        ):
            diagnostics.error(
                "validator_version_mismatch",
                path,
                "contract requires different validator bytes; resume only with the exact sealed validator",
            )
    run_tag = contract.get("run_tag")
    if not isinstance(run_tag, str) or SLUG_RE.fullmatch(run_tag) is None:
        diagnostics.error("invalid_run_tag", path, "run_tag must be a lowercase slug")
    elif run_tag != run_dir.name:
        diagnostics.error("run_tag_mismatch", path, "run_tag must match the run directory name")
    require_git_oid(contract, "baseline_commit", diagnostics, path)
    if not isinstance(contract.get("feedback_mode"), str) or contract.get("feedback_mode") not in {"sealed_gate", "visible_only"}:
        diagnostics.error(
            "invalid_feedback_mode",
            path,
            "feedback_mode must be sealed_gate or visible_only",
        )
    deadline_at = contract.get("deadline_at")
    if deadline_at is not None:
        validate_timestamp(deadline_at, diagnostics, path, "deadline_at")
    objective = contract.get("objective")
    if not isinstance(objective, dict):
        diagnostics.error("invalid_objective", path, "objective must be an object")
    else:
        expected_objective_keys = {
            "goal",
            "intended_population",
            "primary_metric",
            "direction",
            "target",
            "minimum_delta",
            "acceptance_split",
            "hard_constraints",
        }
        if set(objective) != expected_objective_keys:
            diagnostics.error(
                "invalid_objective",
                path,
                f"objective keys must be exactly {sorted(expected_objective_keys)}",
            )
        for field in ("goal", "intended_population", "primary_metric", "acceptance_split"):
            require_string(objective, field, diagnostics, path)
        if not isinstance(objective.get("direction"), str) or objective.get("direction") not in {"maximize", "minimize"}:
            diagnostics.error(
                "invalid_direction",
                path,
                "objective.direction must be maximize or minimize",
            )
        target = objective.get("target")
        if target is not None and not is_finite_number(target):
            diagnostics.error("invalid_target", path, "objective.target must be finite or null")
        minimum_delta = objective.get("minimum_delta")
        if not is_finite_number(minimum_delta) or minimum_delta <= 0:
            diagnostics.error(
                "invalid_minimum_delta",
                path,
                "objective.minimum_delta must be positive and finite",
            )
        hard_constraints = validate_string_array(
            objective.get("hard_constraints"),
            diagnostics,
            path,
            "objective.hard_constraints",
        )
        for constraint in hard_constraints:
            if not isinstance(constraint, str) or UNIT_RE.fullmatch(constraint) is None:
                diagnostics.error(
                    "invalid_constraint_id",
                    path,
                    "hard constraint IDs must use lowercase letters, digits, and underscores",
                )
    measurement = contract.get("measurement")
    if not isinstance(measurement, dict):
        diagnostics.error("invalid_measurement", path, "measurement must be an object")
    else:
        expected_measurement_keys = {
            "mode",
            "aggregation",
            "minimum_repetitions",
            "exploration_repetitions",
            "confirmation_repetitions",
            "uncertainty_rule",
        }
        if set(measurement) != expected_measurement_keys:
            diagnostics.error(
                "invalid_measurement",
                path,
                f"measurement keys must be exactly {sorted(expected_measurement_keys)}",
            )
        if not isinstance(measurement.get("mode"), str) or measurement.get("mode") not in {"deterministic", "noisy"}:
            diagnostics.error(
                "invalid_measurement", path, "measurement.mode must be deterministic or noisy"
            )
        if not isinstance(measurement.get("aggregation"), str) or measurement.get("aggregation") not in {"single", "mean", "median", "min", "max"}:
            diagnostics.error(
                "invalid_aggregation",
                path,
                "measurement.aggregation must be single, mean, median, min, or max",
            )
        minimum_repetitions = measurement.get("minimum_repetitions")
        if not is_positive_int(minimum_repetitions):
            diagnostics.error(
                "invalid_measurement",
                path,
                "measurement.minimum_repetitions must be a positive integer",
            )
        if (
            measurement.get("mode") == "deterministic"
            and minimum_repetitions != 1
        ):
            diagnostics.error(
                "invalid_measurement",
                path,
                "deterministic measurement requires exactly one repetition",
            )
        confirmation_repetitions = measurement.get("confirmation_repetitions")
        if not is_nonnegative_int(confirmation_repetitions):
            diagnostics.error(
                "invalid_measurement",
                path,
                "measurement.confirmation_repetitions must be a nonnegative integer",
            )
        exploration_repetitions = measurement.get("exploration_repetitions")
        if not is_nonnegative_int(exploration_repetitions):
            diagnostics.error(
                "invalid_measurement",
                path,
                "measurement.exploration_repetitions must be a nonnegative integer",
            )
        uncertainty_rule = require_string(
            measurement, "uncertainty_rule", diagnostics, path
        )
        if measurement.get("mode") == "deterministic":
            if (
                confirmation_repetitions != 0
                or exploration_repetitions != 0
                or uncertainty_rule != "none"
            ):
                diagnostics.error(
                    "invalid_measurement",
                    path,
                    "deterministic measurement requires zero exploration/confirmation repetitions and uncertainty_rule none",
                )
        elif measurement.get("mode") == "noisy":
            if measurement.get("aggregation") not in ("mean", "median"):
                diagnostics.error(
                    "invalid_measurement",
                    path,
                    "noisy measurement requires mean or median aggregation",
                )
            if (
                not is_positive_int(confirmation_repetitions)
                or not is_positive_int(exploration_repetitions)
                or not is_positive_int(minimum_repetitions)
                or confirmation_repetitions + exploration_repetitions
                > minimum_repetitions
                or uncertainty_rule != "paired_mean_delta"
            ):
                diagnostics.error(
                    "invalid_measurement",
                    path,
                    "noisy measurement requires paired_mean_delta and one or more confirmation pairs within the minimum repetitions",
                )
    writable_scope: list[str] = []
    immutable_scope: list[str] = []
    generated_scope: list[str] = []
    scope = contract.get("scope")
    if not isinstance(scope, dict):
        diagnostics.error("invalid_scope", path, "scope must be an object")
    else:
        expected_scope_keys = {
            "writable_paths",
            "immutable_paths",
            "generated_paths",
        }
        if set(scope) != expected_scope_keys:
            diagnostics.error(
                "invalid_scope",
                path,
                f"scope keys must be exactly {sorted(expected_scope_keys)}",
            )
        writable_scope = validate_path_array(
            scope.get("writable_paths"),
            diagnostics,
            path,
            "scope.writable_paths",
            allow_empty=False,
        )
        immutable_scope = validate_path_array(
            scope.get("immutable_paths"),
            diagnostics,
            path,
            "scope.immutable_paths",
            allow_empty=False,
        )
        generated_scope = validate_path_array(
            scope.get("generated_paths"),
            diagnostics,
            path,
            "scope.generated_paths",
        )
        for scoped_path in writable_scope + immutable_scope + generated_scope:
            if any(part in {".git", ".research-loop"} for part in PurePosixPath(scoped_path).parts):
                diagnostics.error(
                    "protected_control_scope",
                    path,
                    f"contract scope may not include control path {scoped_path!r}",
                )
        for writable in writable_scope:
            for immutable in immutable_scope:
                if path_within_scope(writable, immutable) or path_within_scope(
                    immutable, writable
                ):
                    diagnostics.error(
                        "overlapping_scope",
                        path,
                        f"writable path {writable!r} overlaps immutable path {immutable!r}",
                    )
        for generated in generated_scope:
            for writable in writable_scope:
                if path_within_scope(generated, writable) or path_within_scope(
                    writable, generated
                ):
                    diagnostics.error(
                        "overlapping_scope",
                        path,
                        f"generated path {generated!r} overlaps writable path {writable!r}",
                    )
            for immutable in immutable_scope:
                if path_within_scope(generated, immutable) or path_within_scope(
                    immutable, generated
                ):
                    diagnostics.error(
                        "overlapping_scope",
                        path,
                        f"generated path {generated!r} overlaps immutable path {immutable!r}",
                    )
    generated_state_policy = contract.get("generated_state_policy")
    if not isinstance(generated_state_policy, dict):
        diagnostics.error(
            "invalid_generated_state_policy",
            path,
            "generated_state_policy must be an object",
        )
    else:
        expected_generated_policy_keys = {"mode", "initial_state_sha256"}
        if set(generated_state_policy) != expected_generated_policy_keys:
            diagnostics.error(
                "invalid_generated_state_policy",
                path,
                "generated_state_policy keys must be exactly mode and initial_state_sha256",
            )
        if generated_state_policy.get("mode") != "reset_each_launch":
            diagnostics.error(
                "invalid_generated_state_policy",
                path,
                "generated-state mode must be reset_each_launch",
            )
        initial_state_digest = require_hex64(
            generated_state_policy,
            "initial_state_sha256",
            diagnostics,
            path,
        )
        if initial_state_digest != EMPTY_GENERATED_STATE_SHA256:
            diagnostics.error(
                "invalid_generated_state_policy",
                path,
                "reset_each_launch must bind the canonical empty generated-state artifact",
            )
    evaluation = contract.get("evaluation")
    if not isinstance(evaluation, dict):
        diagnostics.error("invalid_evaluation", path, "evaluation must be an object")
    else:
        expected_evaluation_keys = {
            "argv",
            "cwd",
            "extractor_path",
            "executable_path",
            "executable_sha256",
            "environment",
            "environment_sha256",
            "timeout_seconds",
            "invocation_sha256",
            "evaluator_sha256",
        }
        if set(evaluation) != expected_evaluation_keys:
            diagnostics.error(
                "invalid_evaluation",
                path,
                f"evaluation keys must be exactly {sorted(expected_evaluation_keys)}",
            )
        argv = evaluation.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(item, str) and item for item in argv)
        ):
            diagnostics.error(
                "invalid_invocation",
                path,
                "evaluation.argv must be a nonempty string array",
            )
        cwd_relative = validate_relative_syntax(
            evaluation.get("cwd"),
            diagnostics,
            path,
            "evaluation.cwd",
            allow_dot=True,
        )
        extractor_relative = validate_relative_syntax(
            evaluation.get("extractor_path"), diagnostics, path, "evaluation.extractor_path"
        )
        cwd_target: Path | None = None
        if cwd_relative is not None:
            cursor = root
            cwd_symlink = False
            for part in cwd_relative.parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    diagnostics.error(
                        "symlink_forbidden",
                        path,
                        f"evaluation.cwd traverses a symlink: {cwd_relative.as_posix()}",
                    )
                    cwd_symlink = True
                    break
            if not cwd_symlink:
                try:
                    resolved_cwd = cursor.resolve(strict=True)
                    resolved_cwd.relative_to(root)
                except (OSError, ValueError):
                    diagnostics.error(
                        "invalid_evaluation_cwd",
                        path,
                        "evaluation.cwd must resolve inside the trusted root",
                    )
                else:
                    if not resolved_cwd.is_dir():
                        diagnostics.error(
                            "invalid_evaluation_cwd",
                            path,
                            "evaluation.cwd must resolve to a directory",
                        )
                    else:
                        cwd_target = resolved_cwd
        if cwd_target is not None and extractor_relative is not None:
            extractor_target = resolve_regular_file(
                cwd_target,
                extractor_relative,
                diagnostics,
                path,
                "evaluation.extractor_path",
            )
            if extractor_target is not None:
                try:
                    resolved_extractor_manifest_path = extractor_target.relative_to(
                        root
                    ).as_posix()
                except ValueError:
                    diagnostics.error(
                        "extractor_outside_trusted_root",
                        path,
                        "evaluation extractor escapes the trusted root",
                    )
        if (
            isinstance(argv, list)
            and isinstance(evaluation.get("extractor_path"), str)
            and evaluation["extractor_path"] not in argv
        ):
            diagnostics.error(
                "extractor_not_invoked",
                path,
                "evaluation.argv must explicitly invoke evaluation.extractor_path",
            )
        executable_path = require_string(
            evaluation, "executable_path", diagnostics, path
        )
        executable_digest = require_hex64(
            evaluation, "executable_sha256", diagnostics, path
        )
        if executable_path is not None:
            executable_target = Path(executable_path)
            if not executable_target.is_absolute():
                diagnostics.error(
                    "invalid_executable_path",
                    path,
                    "evaluation.executable_path must be absolute",
                )
            else:
                try:
                    resolved_executable = executable_target.resolve(strict=True)
                except OSError:
                    diagnostics.error(
                        "invalid_executable_path",
                        path,
                        "evaluation.executable_path must name an existing executable",
                    )
                else:
                    if str(resolved_executable) != executable_path:
                        diagnostics.error(
                            "noncanonical_executable_path",
                            path,
                            "evaluation.executable_path must be its canonical nonsymlinked absolute path",
                        )
                    if not resolved_executable.is_file() or not os.access(
                        resolved_executable, os.X_OK
                    ):
                        diagnostics.error(
                            "invalid_executable_path",
                            path,
                            "evaluation.executable_path must name an executable regular file",
                        )
                    elif (
                        executable_digest is not None
                        and file_sha256(resolved_executable) != executable_digest
                    ):
                        diagnostics.error(
                            "executable_hash_mismatch",
                            path,
                            "evaluation executable bytes do not match executable_sha256",
                        )
        if (
            isinstance(argv, list)
            and argv
            and isinstance(argv[0], str)
            and executable_path is not None
            and argv[0] != executable_path
        ):
            diagnostics.error(
                "executable_argv_mismatch",
                path,
                "evaluation.argv[0] must equal the resolved executable_path",
            )
        environment = evaluation.get("environment")
        if not isinstance(environment, dict):
            diagnostics.error(
                "invalid_environment_manifest",
                path,
                "evaluation.environment must be an object",
            )
        else:
            expected_environment_keys = {
                "inherit_parent",
                "variables",
                "secret_variable_names",
                "dependency_fingerprints",
            }
            if set(environment) != expected_environment_keys:
                diagnostics.error(
                    "invalid_environment_manifest",
                    path,
                    "evaluation.environment keys must be exactly "
                    f"{sorted(expected_environment_keys)}",
                )
            if environment.get("inherit_parent") is not False:
                diagnostics.error(
                    "inherited_environment_forbidden",
                    path,
                    "evaluation.environment.inherit_parent must be false",
                )
            variables = environment.get("variables")
            variable_names: set[str] = set()
            if not isinstance(variables, dict):
                diagnostics.error(
                    "invalid_environment_manifest",
                    path,
                    "evaluation.environment.variables must be an object",
                )
            else:
                for name, value in variables.items():
                    if (
                        not isinstance(name, str)
                        or ENVIRONMENT_NAME_RE.fullmatch(name) is None
                    ):
                        diagnostics.error(
                            "invalid_environment_variable",
                            path,
                            f"invalid environment variable name {name!r}",
                        )
                        continue
                    variable_names.add(name)
                    if is_execution_control_environment_name(name):
                        diagnostics.error(
                            "execution_control_environment_variable",
                            path,
                            f"environment variable {name!r} may alter executable or loaded code",
                        )
                    if SENSITIVE_ENVIRONMENT_NAME_RE.search(name) is not None:
                        diagnostics.error(
                            "secret_environment_value",
                            path,
                            f"sensitive environment variable {name!r} must be listed by name only",
                        )
                    if not isinstance(value, str) or "\x00" in value:
                        diagnostics.error(
                            "invalid_environment_variable",
                            path,
                            f"environment variable {name!r} must have a NUL-free string value",
                        )
            secret_variable_names = environment.get("secret_variable_names")
            secret_names: set[str] = set()
            if not isinstance(secret_variable_names, list) or not all(
                isinstance(name, str)
                and ENVIRONMENT_NAME_RE.fullmatch(name) is not None
                for name in secret_variable_names
            ):
                diagnostics.error(
                    "invalid_environment_manifest",
                    path,
                    "evaluation.environment.secret_variable_names must be an environment-name array",
                )
            else:
                secret_names = set(secret_variable_names)
                if len(secret_names) != len(secret_variable_names):
                    diagnostics.error(
                        "duplicate_environment_variable",
                        path,
                        "evaluation.environment.secret_variable_names must not contain duplicates",
                    )
                for name in secret_variable_names:
                    if is_execution_control_environment_name(name):
                        diagnostics.error(
                            "execution_control_environment_variable",
                            path,
                            f"secret environment variable {name!r} may alter executable or loaded code",
                        )
                overlap = sorted(variable_names & secret_names)
                if overlap:
                    diagnostics.error(
                        "secret_environment_value",
                        path,
                        f"secret environment variables must not appear in variables: {overlap}",
                    )
            dependency_fingerprints = environment.get("dependency_fingerprints")
            if not isinstance(dependency_fingerprints, dict):
                diagnostics.error(
                    "invalid_environment_manifest",
                    path,
                    "evaluation.environment.dependency_fingerprints must be an object",
                )
            else:
                for name, digest in dependency_fingerprints.items():
                    if (
                        not isinstance(name, str)
                        or SAFE_ID_RE.fullmatch(name) is None
                        or not isinstance(digest, str)
                        or HEX64_RE.fullmatch(digest) is None
                    ):
                        diagnostics.error(
                            "invalid_dependency_fingerprint",
                            path,
                            "dependency fingerprints require lowercase stable IDs and SHA-256 values",
                        )
        environment_digest = require_hex64(
            evaluation, "environment_sha256", diagnostics, path
        )
        if environment_digest is not None and isinstance(environment, dict):
            if canonical_sha256(environment) != environment_digest:
                diagnostics.error(
                    "environment_hash_mismatch",
                    path,
                    "evaluation.environment does not match environment_sha256",
                )
        timeout_seconds = evaluation.get("timeout_seconds")
        if not is_positive_int(timeout_seconds):
            diagnostics.error(
                "invalid_timeout",
                path,
                "evaluation.timeout_seconds must be a positive integer",
            )
        invocation_digest = require_hex64(
            evaluation, "invocation_sha256", diagnostics, path
        )
        evaluator_digest = require_hex64(
            evaluation, "evaluator_sha256", diagnostics, path
        )
        if (
            isinstance(argv, list)
            and all(isinstance(item, str) for item in argv)
            and isinstance(evaluation.get("cwd"), str)
            and isinstance(evaluation.get("extractor_path"), str)
            and executable_path is not None
            and executable_digest is not None
            and is_positive_int(timeout_seconds)
            and environment_digest is not None
            and invocation_digest is not None
        ):
            calculated = canonical_sha256(invocation_fingerprint_payload(evaluation))
            if calculated != invocation_digest:
                diagnostics.error(
                    "invocation_hash_mismatch",
                    path,
                    "evaluation.invocation_sha256 does not match resolved invocation fields",
                )
    immutable_files = contract.get("immutable_files")
    if not isinstance(immutable_files, list):
        diagnostics.error("invalid_immutable_files", path, "immutable_files must be an array")
    else:
        seen_paths: set[str] = set()
        for index, item in enumerate(immutable_files):
            field = f"immutable_files[{index}]"
            if not isinstance(item, dict):
                diagnostics.error("invalid_immutable_file", path, f"{field} must be an object")
                continue
            if set(item) != {"path", "sha256"}:
                diagnostics.error(
                    "invalid_immutable_file",
                    path,
                    f"{field} keys must be exactly path and sha256",
                )
            relative = validate_relative_syntax(item.get("path"), diagnostics, path, f"{field}.path")
            digest = item.get("sha256")
            if not isinstance(digest, str) or HEX64_RE.fullmatch(digest) is None:
                diagnostics.error("invalid_digest", path, f"{field}.sha256 must be lowercase SHA-256")
                continue
            if relative is None:
                continue
            name = relative.as_posix()
            if name in seen_paths:
                diagnostics.error("duplicate_immutable_path", path, f"duplicate immutable path {name}")
                continue
            seen_paths.add(name)
            if immutable_scope and not any(
                path_within_scope(name, scope_path) for scope_path in immutable_scope
            ):
                diagnostics.error(
                    "immutable_scope_mismatch",
                    path,
                    f"immutable file {name!r} is outside scope.immutable_paths",
                )
            target = resolve_regular_file(root, relative, diagnostics, path, field)
            if target is not None and file_sha256(target) != digest:
                diagnostics.error(
                    "immutable_hash_mismatch", path, f"immutable file changed: {name}"
                )
        discovered_paths: set[str] = set()
        root_resolved = root.resolve()
        for scope_name in immutable_scope:
            relative_scope = PurePosixPath(scope_name)
            scope_target = root.joinpath(*relative_scope.parts)
            cursor = root
            traverses_symlink = False
            for part in relative_scope.parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    diagnostics.error(
                        "symlink_forbidden",
                        path,
                        f"immutable scope traverses a symlink: {scope_name}",
                    )
                    traverses_symlink = True
                    break
            if traverses_symlink:
                continue
            try:
                resolved_scope = scope_target.resolve(strict=True)
                resolved_scope.relative_to(root_resolved)
            except (OSError, ValueError):
                diagnostics.error(
                    "invalid_immutable_scope",
                    path,
                    f"immutable scope is missing or escapes the trusted root: {scope_name}",
                )
                continue
            if resolved_scope.is_file():
                discovered_paths.add(relative_scope.as_posix())
                continue
            if not resolved_scope.is_dir():
                diagnostics.error(
                    "invalid_immutable_scope",
                    path,
                    f"immutable scope must name a regular file or directory: {scope_name}",
                )
                continue
            for discovered in sorted(resolved_scope.rglob("*")):
                relative_discovered = discovered.relative_to(root_resolved).as_posix()
                if discovered.is_symlink():
                    diagnostics.error(
                        "symlink_forbidden",
                        path,
                        f"immutable scope contains a symlink: {relative_discovered}",
                    )
                elif discovered.is_file():
                    discovered_paths.add(relative_discovered)
                elif not discovered.is_dir():
                    diagnostics.error(
                        "invalid_immutable_scope",
                        path,
                        f"immutable scope contains a non-regular entry: {relative_discovered}",
                    )
        if immutable_scope and discovered_paths != seen_paths:
            missing = sorted(discovered_paths - seen_paths)
            extra = sorted(seen_paths - discovered_paths)
            details: list[str] = []
            if missing:
                details.append(f"unmanifested={missing[:8]}")
            if extra:
                details.append(f"outside_scope_contents={extra[:8]}")
            diagnostics.error(
                "immutable_coverage_mismatch",
                path,
                "immutable_files must exactly cover every regular file under immutable_paths"
                + (f" ({'; '.join(details)})" if details else ""),
            )
        if (
            isinstance(resolved_extractor_manifest_path, str)
            and resolved_extractor_manifest_path not in seen_paths
        ):
            diagnostics.error(
                "extractor_not_immutable",
                path,
                "evaluation.extractor_path must appear in immutable_files",
            )
        if invocation_digest is not None and evaluator_digest is not None:
            calculated_evaluator = canonical_sha256(
                {
                    "invocation_sha256": invocation_digest,
                    "immutable_files": immutable_files,
                }
            )
            if calculated_evaluator != evaluator_digest:
                diagnostics.error(
                    "evaluator_hash_mismatch",
                    path,
                    "evaluation.evaluator_sha256 does not match invocation and immutable_files",
                )
    raw_budgets = contract.get("budgets")
    if not isinstance(raw_budgets, dict):
        diagnostics.error("invalid_budgets", path, "budgets must be an object")
    else:
        for unit, spec in raw_budgets.items():
            if not isinstance(unit, str) or UNIT_RE.fullmatch(unit) is None:
                diagnostics.error("invalid_budget_unit", path, f"invalid budget unit {unit!r}")
                continue
            if not isinstance(spec, dict):
                diagnostics.error("invalid_budget", path, f"budgets.{unit} must be an object")
                continue
            total = spec.get("total")
            expected_budget_keys = {
                "total",
                "operational_reserve",
                "final_test_reserve",
            }
            if set(spec) != expected_budget_keys:
                diagnostics.error(
                    "invalid_budget",
                    path,
                    f"budgets.{unit} keys must be exactly {sorted(expected_budget_keys)}",
                )
            operational_reserve = spec.get("operational_reserve")
            final_test_reserve = spec.get("final_test_reserve")
            if total is not None and not is_nonnegative_int(total):
                diagnostics.error("invalid_budget", path, f"budgets.{unit}.total is invalid")
                continue
            if not is_nonnegative_int(operational_reserve):
                diagnostics.error(
                    "invalid_budget",
                    path,
                    f"budgets.{unit}.operational_reserve is invalid",
                )
                continue
            if not is_nonnegative_int(final_test_reserve):
                diagnostics.error(
                    "invalid_budget",
                    path,
                    f"budgets.{unit}.final_test_reserve is invalid",
                )
                continue
            if total is not None and operational_reserve + final_test_reserve > total:
                diagnostics.error(
                    "invalid_budget",
                    path,
                    f"budgets.{unit} reserves exceed total",
                )
            budgets[unit] = (total, operational_reserve, final_test_reserve)
    per_attempt_max = validate_usage_map(
        contract.get("per_attempt_max"),
        diagnostics,
        path,
        "per_attempt_max",
    ) or {}
    require_exact_units(
        per_attempt_max,
        set(budgets),
        diagnostics,
        path,
        "per_attempt_max",
    )
    for unit, maximum in per_attempt_max.items():
        total = budgets.get(unit, (None, 0))[0]
        if total is not None and maximum > total:
            diagnostics.error(
                "invalid_budget",
                path,
                f"per_attempt_max.{unit} exceeds its total budget",
            )
    promotion = contract.get("promotion")
    if not isinstance(promotion, dict):
        diagnostics.error("invalid_promotion", path, "promotion must be an object")
    else:
        expected_promotion_keys = {
            "required_stages",
            "required_telemetry",
            "acceptance_rule",
        }
        if set(promotion) != expected_promotion_keys:
            diagnostics.error(
                "invalid_promotion",
                path,
                f"promotion keys must be exactly {sorted(expected_promotion_keys)}",
            )
        require_string(promotion, "acceptance_rule", diagnostics, path)
        stages = promotion.get("required_stages")
        if (
            not isinstance(stages, list)
            or not stages
            or not all(isinstance(item, str) and item for item in stages)
        ):
            diagnostics.error(
                "invalid_required_stages",
                path,
                "promotion.required_stages must be a nonempty string array",
            )
        elif len(set(stages)) != len(stages):
            diagnostics.error("duplicate_required_stage", path, "required stages must be unique")
        else:
            required_stages = stages
            for stage in stages:
                if UNIT_RE.fullmatch(stage) is None:
                    diagnostics.error(
                        "invalid_required_stage",
                        path,
                        "required stage IDs must use lowercase letters, digits, and underscores",
                    )
        required_telemetry = promotion.get("required_telemetry")
        if not isinstance(required_telemetry, dict) or set(required_telemetry) != set(
            required_stages
        ):
            diagnostics.error(
                "invalid_required_telemetry",
                path,
                "promotion.required_telemetry keys must exactly match required stages",
            )
        else:
            for stage, metric_ids in required_telemetry.items():
                validated = validate_string_array(
                    metric_ids,
                    diagnostics,
                    path,
                    f"promotion.required_telemetry.{stage}",
                    allow_empty=False,
                )
                for metric_id in validated:
                    if UNIT_RE.fullmatch(metric_id) is None:
                        diagnostics.error(
                            "invalid_telemetry_id",
                            path,
                            "required telemetry IDs must use lowercase letters, digits, and underscores",
                        )
        acceptance_split = (
            contract.get("objective", {}).get("acceptance_split")
            if isinstance(contract.get("objective"), dict)
            else None
        )
        if acceptance_split not in required_stages:
            diagnostics.error(
                "acceptance_stage_missing",
                path,
                "objective.acceptance_split must be a required promotion stage",
            )
        elif (
            contract.get("feedback_mode") == "sealed_gate"
            and required_stages
            and acceptance_split == required_stages[0]
        ):
            diagnostics.error(
                "sealed_gate_without_development_stage",
                path,
                "sealed_gate requires a visible development stage before its protected acceptance split",
            )
    final_test = contract.get("final_test")
    final_stage: str | None = None
    if final_test is not None:
        if not isinstance(final_test, dict):
            diagnostics.error(
                "invalid_final_test", path, "final_test must be an object or null"
            )
        else:
            expected_final_keys = {
                "stage",
                "required_telemetry",
                "minimum_value",
                "maximum_value",
                "comparable_to_acceptance_split",
                "maximum_regression",
            }
            if set(final_test) != expected_final_keys:
                diagnostics.error(
                    "invalid_final_test",
                    path,
                    f"final_test keys must be exactly {sorted(expected_final_keys)}",
                )
            final_stage = require_string(final_test, "stage", diagnostics, path)
            if isinstance(final_stage, str):
                if UNIT_RE.fullmatch(final_stage) is None:
                    diagnostics.error(
                        "invalid_final_test",
                        path,
                        "final_test.stage must be a stable lowercase ID",
                    )
                if final_stage in required_stages:
                    diagnostics.error(
                        "final_test_not_disjoint",
                        path,
                        "final_test.stage must be distinct from promotion stages",
                    )
            telemetry = validate_string_array(
                final_test.get("required_telemetry"),
                diagnostics,
                path,
                "final_test.required_telemetry",
                allow_empty=False,
            )
            for metric_id in telemetry:
                if UNIT_RE.fullmatch(metric_id) is None:
                    diagnostics.error(
                        "invalid_telemetry_id",
                        path,
                        "final-test telemetry IDs must use lowercase letters, digits, and underscores",
                    )
            direction = (
                contract.get("objective", {}).get("direction")
                if isinstance(contract.get("objective"), dict)
                else None
            )
            minimum_value = final_test.get("minimum_value")
            maximum_value = final_test.get("maximum_value")
            if direction == "maximize":
                if not is_finite_number(minimum_value) or maximum_value is not None:
                    diagnostics.error(
                        "invalid_final_test",
                        path,
                        "a maximize final test requires finite minimum_value and null maximum_value",
                    )
            elif direction == "minimize":
                if not is_finite_number(maximum_value) or minimum_value is not None:
                    diagnostics.error(
                        "invalid_final_test",
                        path,
                        "a minimize final test requires finite maximum_value and null minimum_value",
                    )
            comparable = final_test.get("comparable_to_acceptance_split")
            if not isinstance(comparable, bool):
                diagnostics.error(
                    "invalid_final_test",
                    path,
                    "final_test.comparable_to_acceptance_split must be boolean",
                )
            maximum_regression = final_test.get("maximum_regression")
            if comparable is True:
                if maximum_regression is not None and (
                    not is_finite_number(maximum_regression)
                    or maximum_regression < 0
                ):
                    diagnostics.error(
                        "invalid_final_test",
                        path,
                        "final_test.maximum_regression must be null or finite and nonnegative",
                    )
            elif maximum_regression is not None:
                diagnostics.error(
                    "invalid_final_test",
                    path,
                    "maximum_regression requires comparable_to_acceptance_split true",
                )
    stage_access = contract.get("stage_access_sha256")
    expected_access_stages = set(required_stages)
    if isinstance(final_stage, str):
        expected_access_stages.add(final_stage)
    stage_resources = contract.get("stage_resources")
    if not isinstance(stage_resources, dict) or set(stage_resources) != expected_access_stages:
        diagnostics.error(
            "invalid_stage_resources",
            path,
            "stage_resources keys must exactly match promotion and configured final stages",
        )
        stage_resources = {}
    else:
        resource_manifest_digests: list[str] = []
        capability_digests: list[str] = []
        for stage, resource in stage_resources.items():
            if not isinstance(resource, dict) or set(resource) != {
                "resource_manifest_sha256",
                "capability_sha256",
                "visibility",
            }:
                diagnostics.error(
                    "invalid_stage_resources",
                    path,
                    f"stage_resources.{stage} has an invalid schema",
                )
                continue
            manifest_digest = require_hex64(
                resource, "resource_manifest_sha256", diagnostics, path
            )
            capability_digest = require_hex64(
                resource, "capability_sha256", diagnostics, path
            )
            if manifest_digest is not None:
                resource_manifest_digests.append(manifest_digest)
            if capability_digest is not None:
                capability_digests.append(capability_digest)
            expected_visibility = (
                "visible" if stage == required_stages[0] else "protected"
            ) if required_stages else "protected"
            if stage == final_stage:
                expected_visibility = "protected"
            if resource.get("visibility") != expected_visibility:
                diagnostics.error(
                    "invalid_stage_visibility",
                    path,
                    f"stage_resources.{stage}.visibility must be {expected_visibility}",
                )
        if len(resource_manifest_digests) != len(set(resource_manifest_digests)):
            diagnostics.error(
                "stage_resource_manifests_not_disjoint",
                path,
                "every evaluation stage requires a distinct resource-manifest digest",
            )
        if len(capability_digests) != len(set(capability_digests)):
            diagnostics.error(
                "stage_capabilities_not_disjoint",
                path,
                "every evaluation stage requires a distinct capability digest",
            )
    if not isinstance(stage_access, dict) or set(stage_access) != expected_access_stages:
        diagnostics.error(
            "invalid_stage_access",
            path,
            "stage_access_sha256 keys must exactly match promotion and configured final stages",
        )
    else:
        valid_access_digests: list[str] = []
        for stage, digest in stage_access.items():
            if not isinstance(digest, str) or HEX64_RE.fullmatch(digest) is None:
                diagnostics.error(
                    "invalid_stage_access",
                    path,
                    f"stage_access_sha256.{stage} must be lowercase SHA-256",
                )
            else:
                valid_access_digests.append(digest)
                if isinstance(stage_resources.get(stage), dict) and digest != canonical_sha256(
                    stage_resources[stage]
                ):
                    diagnostics.error(
                        "stage_access_derivation_mismatch",
                        path,
                        f"stage_access_sha256.{stage} must derive from its sealed resource/capability manifest",
                    )
        if len(valid_access_digests) != len(set(valid_access_digests)):
            diagnostics.error(
                "stage_access_not_disjoint",
                path,
                "every evaluation stage requires a distinct sealed access identity",
            )
        if isinstance(final_stage, str) and stage_access.get(final_stage) in [
            stage_access.get(stage) for stage in required_stages
        ]:
            diagnostics.error(
                "final_access_not_disjoint",
                path,
                "final-test access identity must differ from every promotion-stage identity",
            )
    stage_config = contract.get("stage_config_sha256")
    if not isinstance(stage_config, dict) or set(stage_config) != expected_access_stages:
        diagnostics.error(
            "invalid_stage_config",
            path,
            "stage_config_sha256 keys must exactly match promotion and configured final stages",
        )
    else:
        valid_config_digests: list[str] = []
        for stage, digest in stage_config.items():
            if not isinstance(digest, str) or HEX64_RE.fullmatch(digest) is None:
                diagnostics.error(
                    "invalid_stage_config",
                    path,
                    f"stage_config_sha256.{stage} must be lowercase SHA-256",
                )
            else:
                valid_config_digests.append(digest)
        if len(valid_config_digests) != len(set(valid_config_digests)):
            diagnostics.error(
                "stage_config_not_disjoint",
                path,
                "every evaluation stage requires a distinct sealed configuration digest",
            )
    control_policy = contract.get("control_policy")
    if not isinstance(control_policy, dict):
        diagnostics.error(
            "invalid_control_policy",
            path,
            "control_policy must be an object",
        )
    else:
        expected_control_keys = {
            "authorization",
            "stop_conditions",
            "plateau_trigger",
            "counterexamples",
            "cleanup_grace_seconds",
            "actual_usage_sources",
            "hard_constraint_rules",
        }
        if set(control_policy) != expected_control_keys:
            diagnostics.error(
                "invalid_control_policy",
                path,
                f"control_policy keys must be exactly {sorted(expected_control_keys)}",
            )
        authorization = control_policy.get("authorization")
        if authorization not in {"bounded", "run_until_interrupted"}:
            diagnostics.error(
                "invalid_control_authorization",
                path,
                "control_policy.authorization must be bounded or run_until_interrupted",
            )
        validate_string_array(
            control_policy.get("stop_conditions"),
            diagnostics,
            path,
            "control_policy.stop_conditions",
            allow_empty=False,
        )
        plateau_trigger = control_policy.get("plateau_trigger")
        if not isinstance(plateau_trigger, dict) or set(plateau_trigger) != {
            "consecutive_non_improvements",
            "action",
        }:
            diagnostics.error(
                "invalid_plateau_trigger",
                path,
                "plateau_trigger keys must be exactly consecutive_non_improvements and action",
            )
        else:
            if not is_positive_int(
                plateau_trigger.get("consecutive_non_improvements")
            ):
                diagnostics.error(
                    "invalid_plateau_trigger",
                    path,
                    "plateau_trigger.consecutive_non_improvements must be positive",
                )
            if plateau_trigger.get("action") not in {
                "refresh_candidate_pool",
                "stop",
            }:
                diagnostics.error(
                    "invalid_plateau_trigger",
                    path,
                    "plateau_trigger.action must be refresh_candidate_pool or stop",
                )
        counterexamples = control_policy.get("counterexamples")
        if (
            not isinstance(counterexamples, dict)
            or not counterexamples
            or any(
                not isinstance(counterexample_id, str)
                or SAFE_ID_RE.fullmatch(counterexample_id) is None
                or not isinstance(rule, str)
                or not rule.strip()
                for counterexample_id, rule in counterexamples.items()
            )
        ):
            diagnostics.error(
                "invalid_counterexamples",
                path,
                "control_policy.counterexamples must map one or more stable IDs to nonempty sealed rules",
            )
        if not is_positive_int(control_policy.get("cleanup_grace_seconds")):
            diagnostics.error(
                "invalid_cleanup_grace",
                path,
                "control_policy.cleanup_grace_seconds must be positive",
            )
        actual_usage_sources = control_policy.get("actual_usage_sources")
        if not isinstance(actual_usage_sources, dict) or set(
            actual_usage_sources
        ) != set(budgets):
            diagnostics.error(
                "invalid_actual_usage_sources",
                path,
                "control_policy.actual_usage_sources keys must exactly match budget units",
            )
        elif not all(
            isinstance(source, str) and bool(source.strip())
            for source in actual_usage_sources.values()
        ):
            diagnostics.error(
                "invalid_actual_usage_sources",
                path,
                "every actual-usage source must be a nonempty sealed description",
            )
        hard_constraints = (
            contract.get("objective", {}).get("hard_constraints", [])
            if isinstance(contract.get("objective"), dict)
            else []
        )
        hard_constraint_rules = control_policy.get("hard_constraint_rules")
        expected_constraints = (
            set(hard_constraints) if isinstance(hard_constraints, list) else set()
        )
        if not isinstance(hard_constraint_rules, dict) or set(
            hard_constraint_rules
        ) != expected_constraints:
            diagnostics.error(
                "invalid_hard_constraint_rules",
                path,
                "control_policy.hard_constraint_rules keys must exactly match objective.hard_constraints",
            )
        elif not all(
            isinstance(rule, str) and bool(rule.strip())
            for rule in hard_constraint_rules.values()
        ):
            diagnostics.error(
                "invalid_hard_constraint_rules",
                path,
                "every hard constraint requires a nonempty sealed rule",
            )
        evaluator_total = budgets.get("evaluator_calls", (None, 0, 0))[0]
        finite_control_bound = (
            contract.get("deadline_at") is not None
            or evaluator_total is not None
        )
        if not finite_control_bound and authorization != "run_until_interrupted":
            diagnostics.error(
                "missing_open_ended_authorization",
                path,
                "a contract without a finite deadline or budget requires run_until_interrupted authorization",
            )
    protected_stages = protected_stage_ids(contract)
    if "evaluator_calls" not in budgets:
        diagnostics.error(
            "missing_required_budget_unit",
            path,
            "budgets must define evaluator_calls",
        )
    if protected_stages and "validation_queries" not in budgets:
        diagnostics.error(
            "missing_required_budget_unit",
            path,
            "budgets must define validation_queries when any stage is protected",
        )
    measurement_mode = (
        measurement.get("mode") if isinstance(measurement, dict) else None
    )
    minimum_repetitions = (
        measurement.get("minimum_repetitions")
        if isinstance(measurement, dict)
        else None
    )
    repetitions: int | None = None
    if measurement_mode == "deterministic" and minimum_repetitions == 1:
        repetitions = 1
    elif measurement_mode == "noisy" and is_positive_int(minimum_repetitions):
        repetitions = minimum_repetitions
    acceptance_split = (
        contract.get("objective", {}).get("acceptance_split")
        if isinstance(contract.get("objective"), dict)
        else None
    )
    if (
        repetitions is not None
        and required_stages
        and acceptance_split in required_stages
    ):
        nonacceptance_launches = len(required_stages) - 1
        baseline_evaluator_calls = nonacceptance_launches + repetitions
        candidate_acceptance_launches = (
            repetitions * 2 if measurement_mode == "noisy" else 1
        )
        candidate_evaluator_calls = (
            nonacceptance_launches + candidate_acceptance_launches
        )
        final_evaluator_calls = repetitions if isinstance(final_stage, str) else 0

        def protected_query_count(*, candidate: bool = False) -> int:
            count = 0
            for stage in required_stages:
                if stage not in protected_stages:
                    continue
                if stage == acceptance_split:
                    count += candidate_acceptance_launches if candidate else repetitions
                else:
                    count += 1
            return count

        baseline_validation_queries = protected_query_count()
        candidate_validation_queries = protected_query_count(candidate=True)
        final_validation_queries = (
            final_evaluator_calls
            if isinstance(final_stage, str) and final_stage in protected_stages
            else 0
        )

        def require_viable_budget(
            unit: str,
            baseline_need: int,
            candidate_need: int,
            final_need: int,
        ) -> None:
            if unit not in budgets:
                return
            total, operational_reserve, final_test_reserve = budgets[unit]
            maximum = per_attempt_max.get(unit)
            minimum_attempt_need = max(
                baseline_need,
                candidate_need,
                final_need,
            )
            if not is_nonnegative_int(maximum) or maximum < minimum_attempt_need:
                diagnostics.error(
                    "insufficient_per_attempt_budget",
                    path,
                    f"per_attempt_max.{unit} cannot cover the largest minimum sealed attempt ({minimum_attempt_need})",
                )
            if final_need > final_test_reserve:
                diagnostics.error(
                    "insufficient_final_test_reserve",
                    path,
                    f"budgets.{unit}.final_test_reserve cannot cover the minimum final plan ({final_need})",
                )
            minimum_nonfinal_total = (
                baseline_need
                + candidate_need
                + operational_reserve
                + final_test_reserve
            )
            if total is not None and total < minimum_nonfinal_total:
                diagnostics.error(
                    "insufficient_run_budget",
                    path,
                    f"budgets.{unit}.total cannot cover a baseline, one candidate, and sealed reserves ({minimum_nonfinal_total})",
                )

        require_viable_budget(
            "evaluator_calls",
            baseline_evaluator_calls,
            candidate_evaluator_calls,
            final_evaluator_calls,
        )
        if protected_stages:
            require_viable_budget(
                "validation_queries",
                baseline_validation_queries,
                candidate_validation_queries,
                final_validation_queries,
            )
    return contract, actual_digest, budgets, required_stages


def validate_outcome(
    status: Any,
    lane: Any,
    maturity: Any,
    accepted: Any,
    stage_results: Any,
    required_stages: list[str],
    diagnostics: Diagnostics,
    path: str,
    line: int | None = None,
) -> None:
    if not isinstance(status, str) or status not in STATUSES:
        diagnostics.error("invalid_status", path, f"unknown status {status!r}", line)
    if not isinstance(lane, str) or lane not in LANES:
        diagnostics.error("invalid_lane", path, f"unknown lane {lane!r}", line)
    if not isinstance(maturity, str) or maturity not in MATURITY:
        diagnostics.error("invalid_maturity", path, f"unknown maturity {maturity!r}", line)
    if not isinstance(accepted, bool):
        diagnostics.error("invalid_accepted", path, "accepted must be boolean", line)
    if not isinstance(stage_results, dict):
        diagnostics.error("invalid_stage_results", path, "stage_results must be an object", line)
        stage_results = {}
    elif set(stage_results) != set(required_stages):
        diagnostics.error(
            "stage_results_mismatch",
            path,
            "stage_results keys must exactly match applicable stages",
            line,
        )
    for stage in required_stages:
        if stage_results.get(stage) not in ("pass", "fail", "unknown"):
            diagnostics.error(
                "missing_stage_result", path, f"required stage {stage!r} is absent or invalid", line
            )
    if maturity == "complete" and any(
        stage_results.get(stage) == "unknown" for stage in required_stages
    ):
        diagnostics.error(
            "illegal_maturity",
            path,
            "complete evidence cannot contain an unknown required stage",
            line,
        )
    if status == "keep":
        if lane != "confirmed" or maturity != "complete" or accepted is not True:
            diagnostics.error(
                "illegal_promotion", path, "keep requires confirmed, complete, accepted evidence", line
            )
        for stage in required_stages:
            if stage_results.get(stage) != "pass":
                diagnostics.error(
                    "illegal_promotion", path, f"keep requires stage {stage!r} to pass", line
                )
    elif accepted is True:
        diagnostics.error("illegal_acceptance", path, "only keep may set accepted true", line)
    if status == "deferred" and lane != "incubator":
        diagnostics.error("illegal_lane", path, "deferred requires incubator lane", line)
    if status == "deferred" and maturity not in ("incomplete", "unknown"):
        diagnostics.error(
            "illegal_maturity", path, "deferred evidence must be incomplete or unknown", line
        )
    if status == "discard" and lane not in ("candidate", "diagnostic"):
        diagnostics.error("illegal_lane", path, "discard requires candidate or diagnostic lane", line)
    if status in ("crash", "timeout", "invalid", "cancelled", "interrupted") and lane != "diagnostic":
        diagnostics.error("illegal_lane", path, f"{status} requires diagnostic lane", line)


def validate_stage_evidence(
    stage_results: Any,
    stage_evidence: Any,
    required_stages: list[str],
    available_results: dict[str, str],
    diagnostics: Diagnostics,
    path: str,
    line: int | None = None,
) -> None:
    if not isinstance(stage_evidence, dict):
        diagnostics.error(
            "invalid_stage_evidence", path, "stage_evidence must be an object", line
        )
        return
    if set(stage_evidence) != set(required_stages):
        diagnostics.error(
            "stage_evidence_mismatch",
            path,
            "stage_evidence keys must exactly match required stages",
            line,
        )
    results = stage_results if isinstance(stage_results, dict) else {}
    for stage in required_stages:
        citations = stage_evidence.get(stage)
        if not isinstance(citations, list) or not all(
            isinstance(result_id, str) and SAFE_ID_RE.fullmatch(result_id)
            for result_id in citations or []
        ):
            diagnostics.error(
                "invalid_stage_evidence",
                path,
                f"stage_evidence.{stage} must be an array of result IDs",
                line,
            )
            continue
        if len(set(citations)) != len(citations):
            diagnostics.error(
                "duplicate_stage_evidence",
                path,
                f"stage_evidence.{stage} contains duplicates",
                line,
            )
        stage_result = results.get(stage)
        if stage_result in ("pass", "fail") and not citations:
            diagnostics.error(
                "missing_stage_evidence",
                path,
                f"stage {stage!r} is {stage_result} without cited evidence",
                line,
            )
        if stage_result == "unknown" and citations:
            diagnostics.error(
                "unexpected_stage_evidence",
                path,
                f"unknown stage {stage!r} must not cite conclusive evidence",
                line,
            )
        for result_id in citations:
            if available_results.get(result_id) != stage:
                diagnostics.error(
                    "stage_evidence_mismatch",
                    path,
                    f"result {result_id!r} is not completed evidence for stage {stage!r}",
                    line,
                )


def validate_journal(
    records: list[dict[str, Any]],
    contract: dict[str, Any] | None,
    contract_digest: str | None,
    required_stages: list[str],
    diagnostics: Diagnostics,
    path: Path,
    *,
    supervisor_git_root: Path | None,
    verify_git: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, dict[str, Any] | None]:
    attempts: list[dict[str, Any]] = []
    attempt_ids: set[str] = set()
    launch_ids: set[str] = set()
    result_ids: set[str] = set()
    process_identities: set[tuple[Any, ...]] = set()
    active: dict[str, Any] | None = None
    stop_requested: dict[str, Any] | None = None
    stop_requested_at: datetime | None = None
    run_stopped: dict[str, Any] | None = None
    final_role_seen = False
    display = str(path)
    baseline_commit = contract.get("baseline_commit") if contract else None
    expected_incumbent = baseline_commit
    invocation_digest = (
        contract.get("evaluation", {}).get("invocation_sha256") if contract else None
    )
    contract_units = set(contract.get("budgets", {})) if contract else set()
    final_test = contract.get("final_test") if contract else None
    final_test_stage = (
        final_test.get("stage") if isinstance(final_test, dict) else None
    )
    protected_stages = protected_stage_ids(contract)
    cleanup_grace_seconds = (
        contract.get("control_policy", {}).get("cleanup_grace_seconds")
        if contract and isinstance(contract.get("control_policy"), dict)
        else None
    )
    deadline: datetime | None = None
    deadline_value = contract.get("deadline_at") if contract else None
    if isinstance(deadline_value, str) and RFC3339_UTC_RE.fullmatch(deadline_value):
        try:
            deadline = datetime.fromisoformat(deadline_value[:-1] + "+00:00")
        except ValueError:
            deadline = None
    previous_event_at: datetime | None = None
    for index, event in enumerate(records):
        line = index + 1
        if event.get("schema_version") != 1:
            diagnostics.error("schema_version", display, "schema_version must equal 1", line)
        if event.get("seq") != line:
            diagnostics.error("journal_sequence", display, f"seq must equal {line}", line)
        event_at = validate_timestamp(event.get("at"), diagnostics, display, "at", line)
        if event_at is not None:
            if previous_event_at is not None and event_at < previous_event_at:
                diagnostics.error(
                    "journal_time_regression",
                    display,
                    "journal timestamps must be nondecreasing",
                    line,
                )
            previous_event_at = event_at
        if event.get("contract_sha256") != contract_digest:
            diagnostics.error("contract_digest_mismatch", display, "event contract digest differs", line)
        event_name = event.get("event")
        if not isinstance(event_name, str) or event_name not in ATTEMPT_EVENTS | RUN_EVENTS:
            diagnostics.error("unknown_event", display, f"unknown event {event_name!r}", line)
            continue
        if (
            deadline is not None
            and event_at is not None
            and event_name in {"launch_intent", "launched", "launch_finished"}
            and event_at > deadline
        ):
            if event_name == "launch_finished":
                diagnostics.warning(
                    "operational_overrun",
                    display,
                    "launch finished after the sealed deadline and must close as timeout",
                    line,
                )
            else:
                diagnostics.error(
                    "launch_after_deadline",
                    display,
                    "new evaluation work began after the sealed deadline",
                    line,
                )
        if run_stopped is not None:
            diagnostics.error("event_after_run_stopped", display, "no event may follow run_stopped", line)
        if event_name == "stop_requested":
            if stop_requested is not None:
                diagnostics.error("duplicate_stop", display, "stop_requested may occur once", line)
            require_string(event, "source", diagnostics, display, line)
            require_string(event, "reason", diagnostics, display, line)
            require_string(event, "rule_evaluation", diagnostics, display, line)
            stop_kind = event.get("stop_kind")
            if stop_kind not in {
                "user_interruption",
                "ordinary",
                "integrity",
                "resource_exhaustion",
            }:
                diagnostics.error(
                    "invalid_stop_kind",
                    display,
                    "stop_kind must classify the stop source",
                    line,
                )
            skip_authorized = event.get("final_test_skip_authorized")
            if not isinstance(skip_authorized, bool):
                diagnostics.error(
                    "invalid_final_skip_authorization",
                    display,
                    "final_test_skip_authorized must be boolean",
                    line,
                )
            elif skip_authorized and (
                stop_kind != "user_interruption" or event.get("source") != "user"
            ):
                diagnostics.error(
                    "invalid_final_skip_authorization",
                    display,
                    "only an explicit user interruption may authorize skipping final verification",
                    line,
                )
            stop_requested = event
            stop_requested_at = event_at
            continue
        if event_name == "run_stopped":
            if active is not None:
                diagnostics.error("stop_with_active_attempt", display, "run_stopped requires no active attempt", line)
            if not attempts and stop_requested is None:
                diagnostics.error(
                    "empty_run_without_stop_request",
                    display,
                    "a run with no attempts requires an explicit stop_requested receipt",
                    line,
                )
            require_string(event, "reason", diagnostics, display, line)
            final_incumbent = require_git_oid(
                event, "final_incumbent_commit", diagnostics, display, line
            )
            require_git_oid(event, "final_head_commit", diagnostics, display, line)
            if final_incumbent != expected_incumbent:
                diagnostics.error(
                    "final_incumbent_mismatch",
                    display,
                    "run_stopped final incumbent differs from journal decisions",
                    line,
                )
            if not isinstance(event.get("unresolved_cleanup"), bool):
                diagnostics.error(
                    "invalid_cleanup_state", display, "unresolved_cleanup must be boolean", line
                )
            unresolved_recovery = event.get("unresolved_recovery")
            if not isinstance(unresolved_recovery, bool):
                diagnostics.error(
                    "invalid_recovery_state",
                    display,
                    "unresolved_recovery must be boolean",
                    line,
                )
            expected_unresolved_recovery = bool(
                attempts
                and (attempts[-1].get("decision") or {}).get("rollback_action")
                == "preserve_and_stop"
            )
            if unresolved_recovery is not expected_unresolved_recovery:
                diagnostics.error(
                    "recovery_state_mismatch",
                    display,
                    "run_stopped unresolved_recovery must match the terminal preserve_and_stop decision",
                    line,
                )
            run_stopped = event
            continue
        for key in ("attempt_id", "experiment_id"):
            require_id(event, key, diagnostics, display, line)
        require_string(event, "role", diagnostics, display, line)
        iteration = event.get("iteration")
        if not is_nonnegative_int(iteration):
            diagnostics.error("invalid_iteration", display, "iteration must be nonnegative integer", line)
        if event_name == "prepared":
            post_stop_final = bool(
                stop_requested is not None
                and event.get("role") == "final"
                and stop_requested.get("final_test_skip_authorized") is False
            )
            if stop_requested is not None and not post_stop_final:
                diagnostics.error(
                    "launch_after_stop",
                    display,
                    "only required final verification may be prepared after a stop request",
                    line,
                )
            if active is not None:
                diagnostics.error("overlapping_attempt", display, "previous attempt is not finalized", line)
            attempt_id = event.get("attempt_id")
            if attempt_id in attempt_ids:
                diagnostics.error("duplicate_attempt_id", display, f"duplicate attempt_id {attempt_id!r}", line)
            if iteration != len(attempts):
                diagnostics.error(
                    "iteration_sequence", display, f"iteration must equal {len(attempts)}", line
                )
            role = event.get("role")
            if iteration == 0 and role != "baseline":
                diagnostics.error("invalid_role", display, "iteration 0 must be baseline", line)
            if iteration != 0 and role not in ("candidate", "final"):
                diagnostics.error(
                    "invalid_role", display, "later iterations must be candidate or final", line
                )
            if final_role_seen:
                diagnostics.error("attempt_after_final", display, "no attempt may follow role final", line)
            if role == "final":
                final_role_seen = True
                if not isinstance(final_test_stage, str):
                    diagnostics.error(
                        "final_test_not_contracted",
                        display,
                        "role final requires a sealed final_test contract",
                        line,
                    )
            if role == "candidate":
                validate_artifact_ref(
                    event.get("design"),
                    path.parent,
                    diagnostics,
                    display,
                    "design",
                    line,
                    expected_path=f"designs/{event.get('experiment_id')}.json",
                )
            elif event.get("design") is not None:
                diagnostics.error(
                    "noncandidate_design",
                    display,
                    "baseline and final prepared events require design null",
                    line,
                )
            launch_plan_ref = validate_artifact_ref(
                event.get("launch_plan"),
                path.parent,
                diagnostics,
                display,
                "launch_plan",
                line,
                expected_path=f"snapshots/{attempt_id}-launch-plan.json",
            )
            launch_plan = validate_launch_plan(
                launch_plan_ref,
                path.parent,
                attempt_id,
                role,
                (
                    [final_test_stage]
                    if role == "final" and isinstance(final_test_stage, str)
                    else required_stages
                ),
                contract,
                contract_units,
                diagnostics,
                display,
                line,
                supervisor_git_root=supervisor_git_root,
                verify_git=verify_git,
            )
            admission_ref = validate_artifact_ref(
                event.get("admission"),
                path.parent,
                diagnostics,
                display,
                "admission",
                line,
                expected_path=f"snapshots/{attempt_id}-admission.json",
            )
            prior_usage = {unit: 0 for unit in contract_units}
            for prior_attempt in attempts:
                for launch in prior_attempt.get("launches", []):
                    actual_usage = (launch.get("finished") or {}).get(
                        "actual_usage", {}
                    )
                    if not isinstance(actual_usage, dict):
                        continue
                    for unit in contract_units:
                        amount = actual_usage.get(unit)
                        if is_nonnegative_int(amount):
                            prior_usage[unit] += amount
            admission_record = validate_admission_snapshot(
                admission_ref,
                launch_plan,
                role,
                prior_usage,
                event_at,
                contract,
                contract_units,
                path.parent,
                diagnostics,
                display,
                line,
            )
            prior_boundary = event.get("prior_boundary")
            if iteration == 0:
                if prior_boundary is not None:
                    diagnostics.error(
                        "unexpected_prior_boundary",
                        display,
                        "the first attempt must use prior_boundary null",
                        line,
                    )
            else:
                previous = attempts[-1] if attempts else None
                if not isinstance(prior_boundary, dict) or previous is None:
                    diagnostics.error(
                        "missing_prior_boundary",
                        display,
                        "every later prepared event must bind the prior atomic boundary",
                        line,
                    )
                else:
                    previous_id = previous.get("attempt_id")
                    reference = validate_artifact_ref(
                        prior_boundary,
                        path.parent,
                        diagnostics,
                        display,
                        "prior_boundary",
                        line,
                        expected_path=f"boundaries/{previous_id}.json",
                        expected_keys={
                            "path",
                            "sha256",
                            "attempt_id",
                            "finalized_seq",
                            "result_sha256",
                        },
                    )
                    expected_metadata = {
                        "attempt_id": previous_id,
                        "finalized_seq": previous.get("finalized_seq"),
                        "result_sha256": (previous.get("finalized") or {}).get(
                            "result_sha256"
                        ),
                    }
                    for metadata_field, expected_value in expected_metadata.items():
                        if prior_boundary.get(metadata_field) != expected_value:
                            diagnostics.error(
                                "prior_boundary_mismatch",
                                display,
                                f"prior_boundary.{metadata_field} differs from the prior finalized attempt",
                                line,
                            )
                    if reference is None:
                        diagnostics.error(
                            "prior_boundary_unavailable",
                            display,
                            "the prior atomic boundary must exist before another attempt is prepared",
                            line,
                        )
                    else:
                        prior_boundary_record = load_json_file(
                            path.parent / reference[0], diagnostics
                        )
                        prior_boundary_at = (
                            parse_timestamp(prior_boundary_record.get("finalized_at"))
                            if prior_boundary_record is not None
                            else None
                        )
                        if (
                            prior_boundary_at is not None
                            and event_at is not None
                            and prior_boundary_at > event_at
                        ):
                            diagnostics.error(
                                "prior_boundary_time_regression",
                                display,
                                "prepared event predates publication of its bound prior boundary",
                                line,
                            )
            incumbent = require_git_oid(event, "incumbent_commit", diagnostics, display, line)
            pre_attempt_head = require_git_oid(
                event, "pre_attempt_head_commit", diagnostics, display, line
            )
            if iteration == 0 and baseline_commit is not None and incumbent != baseline_commit:
                diagnostics.error("baseline_mismatch", display, "baseline incumbent differs from contract", line)
            if expected_incumbent is not None and incumbent != expected_incumbent:
                diagnostics.error(
                    "incumbent_chain",
                    display,
                    "prepared incumbent differs from the prior recorded decision",
                    line,
                )
            if attempts and attempts[-1].get("decision", {}).get("status") != "keep" and attempts[0].get("decision", {}).get("status") != "keep":
                diagnostics.error("failed_baseline_continued", display, "candidate follows failed baseline", line)
            if attempts and attempts[-1].get("decision", {}).get("rollback_action") == "preserve_and_stop":
                diagnostics.error("continued_after_preserve", display, "cannot continue after preserve_and_stop", line)
            active = {
                "attempt_id": attempt_id,
                "experiment_id": event.get("experiment_id"),
                "iteration": iteration,
                "role": event.get("role"),
                "incumbent_commit": incumbent,
                "pre_attempt_head_commit": pre_attempt_head,
                "candidate_commit": None,
                "design": event.get("design"),
                "launch_plan_ref": event.get("launch_plan"),
                "launch_plan": launch_plan,
                "admission_ref": event.get("admission"),
                "admission_record": admission_record,
                "prepared": event,
                "prepared_seq": line,
                "launches": [],
                "launch_by_id": {},
                "stage_checkpoints": {},
                "tail_cancelled": None,
                "integrity_cleared": None,
                "evaluated": None,
                "decision": None,
                "rolled_back": None,
                "finalized": None,
                "phase": "prepared",
                "required_stages": (
                    [final_test_stage]
                    if role == "final" and isinstance(final_test_stage, str)
                    else required_stages
                ),
            }
            attempts.append(active)
            if isinstance(attempt_id, str):
                attempt_ids.add(attempt_id)
            continue
        if active is None:
            diagnostics.error("event_without_attempt", display, f"{event_name} has no active attempt", line)
            continue
        for key in ("attempt_id", "experiment_id", "iteration", "role"):
            if event.get(key) != active.get(key):
                diagnostics.error("attempt_identity_drift", display, f"{key} changed within attempt", line)
        if (
            stop_requested_at is not None
            and event_at is not None
            and active.get("role") != "final"
        ):
            if event_name == "launched":
                diagnostics.error(
                    "launch_after_stop",
                    display,
                    "a non-final launch may not spawn after stop_requested",
                    line,
                )
            if (
                event_name in {"launch_finished", "cleanup_confirmed"}
                and is_positive_int(cleanup_grace_seconds)
                and event_at
                > stop_requested_at
                + timedelta(seconds=cleanup_grace_seconds)
            ):
                diagnostics.error(
                    "stop_cleanup_grace_exceeded",
                    display,
                    "resolved non-final launch or cleanup receipt exceeds the sealed stop grace; append the appropriate fenced unresolved receipt at grace expiry instead",
                    line,
                )
        if event_name == "committed":
            if active["role"] != "candidate" or active["phase"] != "prepared":
                diagnostics.error("journal_order", display, "committed is out of order", line)
            active["candidate_commit"] = require_git_oid(
                event, "candidate_commit", diagnostics, display, line
            )
            active["phase"] = "committed"
        elif event_name == "launch_intent":
            post_stop_final = bool(
                stop_requested is not None
                and active.get("role") == "final"
                and stop_requested.get("final_test_skip_authorized") is False
            )
            if stop_requested is not None and not post_stop_final:
                diagnostics.error(
                    "launch_after_stop",
                    display,
                    "only required final verification may launch after a stop request",
                    line,
                )
            if active.get("tail_cancelled") is not None:
                diagnostics.error(
                    "launch_after_tail_cancellation",
                    display,
                    "no launch may follow plan-tail cancellation",
                    line,
                )
            admitted_end = parse_timestamp(
                (active.get("admission_record") or {}).get("worst_case_end_at")
            )
            admission_record = active.get("admission_record") or {}
            if (
                admission_record.get("allowed") is not True
                or admission_record.get("deadline_fit") is not True
            ):
                diagnostics.error(
                    "launch_without_admission",
                    display,
                    "launch intent requires an affirmatively allowed, deadline-fitting sealed admission",
                    line,
                )
            if (
                admitted_end is not None
                and event_at is not None
                and event_at > admitted_end
            ):
                diagnostics.error(
                    "launch_after_admission_window",
                    display,
                    "launch intent occurs after the admitted worst-case window",
                    line,
                )
            if any(
                launch.get("state") == "unresolved"
                or launch.get("cleanup_unresolved") is not None
                for launch in active["launches"]
            ):
                diagnostics.error(
                    "launch_after_fence",
                    display,
                    "no launch may follow an unresolved-work fence",
                    line,
                )
            if any(
                launch.get("state") in {"intent", "launched"}
                or (
                    launch.get("state") == "finished"
                    and (launch.get("finished") or {}).get("cleanup_verified") is not True
                    and launch.get("cleanup_confirmed") is None
                    and launch.get("cleanup_unresolved") is None
                )
                for launch in active["launches"]
            ):
                diagnostics.error(
                    "overlapping_launch",
                    display,
                    "a new launch requires every prior launch to be terminal with resolved cleanup",
                    line,
                )
            expected_phase = (
                "committed" if active["role"] == "candidate" else "prepared"
            )
            if active["phase"] != expected_phase:
                diagnostics.error("journal_order", display, "launch_intent is out of order", line)
            launch_id = require_id(event, "launch_id", diagnostics, display, line)
            result_id = require_id(event, "result_id", diagnostics, display, line)
            ordinal = event.get("launch_ordinal")
            if ordinal != len(active["launches"]):
                diagnostics.error(
                    "launch_ordinal", display, f"launch_ordinal must equal {len(active['launches'])}", line
                )
            planned_launch = (
                active["launch_plan"][ordinal]
                if is_nonnegative_int(ordinal)
                and ordinal < len(active["launch_plan"])
                else None
            )
            if planned_launch is None:
                diagnostics.error(
                    "launch_not_preregistered",
                    display,
                    "launch ordinal is absent from the sealed launch plan",
                    line,
                )
            remaining_timeout_seconds = (
                sum(
                    planned.get("timeout_seconds", 0)
                    for planned in active.get("launch_plan", [])[ordinal:]
                    if is_positive_int(planned.get("timeout_seconds"))
                )
                if is_nonnegative_int(ordinal)
                else 0
            )
            if (
                admitted_end is not None
                and event_at is not None
                and event_at + timedelta(seconds=remaining_timeout_seconds)
                > admitted_end
            ):
                diagnostics.error(
                    "insufficient_remaining_timeout_window",
                    display,
                    "launch intent lacks enough admitted wall time for the remaining serial plan",
                    line,
                )
            if launch_id in launch_ids:
                diagnostics.error("duplicate_launch_id", display, f"duplicate launch_id {launch_id!r}", line)
            if result_id in result_ids:
                diagnostics.error("duplicate_result_id", display, f"duplicate result_id {result_id!r}", line)
            evaluated_commit = require_git_oid(
                event, "evaluated_commit", diagnostics, display, line
            )
            allowed_commits = {active["incumbent_commit"], active["candidate_commit"]}
            if evaluated_commit not in allowed_commits:
                diagnostics.error("unexpected_evaluated_commit", display, "launch evaluates unknown commit", line)
            expected_arm_commit = (
                active["candidate_commit"]
                if event.get("arm") == "candidate"
                else active["incumbent_commit"]
                if event.get("arm") == "incumbent"
                else None
            )
            if expected_arm_commit is None or evaluated_commit != expected_arm_commit:
                diagnostics.error(
                    "launch_arm_commit_mismatch",
                    display,
                    "launch arm does not match the evaluated commit",
                    line,
                )
            if event.get("invocation_sha256") != invocation_digest:
                diagnostics.error("invocation_digest_mismatch", display, "launch invocation differs", line)
            timeout_seconds = event.get("timeout_seconds")
            sealed_timeout = (
                contract.get("evaluation", {}).get("timeout_seconds")
                if contract and isinstance(contract.get("evaluation"), dict)
                else None
            )
            if not is_positive_int(timeout_seconds) or timeout_seconds != sealed_timeout:
                diagnostics.error(
                    "launch_timeout_mismatch",
                    display,
                    "launch timeout differs from the sealed evaluation timeout",
                    line,
                )
            workspace_id = event.get("workspace_id")
            if (
                not isinstance(workspace_id, str)
                or SAFE_ID_RE.fullmatch(workspace_id) is None
            ):
                diagnostics.error(
                    "invalid_workspace_id",
                    display,
                    "launch intent requires a safe isolated workspace identity",
                    line,
                )
            stage = event.get("stage")
            if not isinstance(stage, str) or UNIT_RE.fullmatch(stage) is None:
                diagnostics.error(
                    "invalid_stage", display, "launch stage must be a stable lowercase ID", line
                )
            elif active["role"] == "final" and stage != final_test_stage:
                diagnostics.error(
                    "invalid_final_stage",
                    display,
                    "final attempts may launch only the sealed final-test stage",
                    line,
                )
            elif active["role"] != "final" and stage == final_test_stage:
                diagnostics.error(
                    "premature_final_test",
                    display,
                    "baseline and candidate attempts may not access the final-test stage",
                    line,
                )
            elif stage not in active["required_stages"]:
                diagnostics.error(
                    "unexpected_stage",
                    display,
                    "launch stage is not part of the applicable sealed stage sequence",
                    line,
                )
            if isinstance(stage, str) and stage in active["stage_checkpoints"]:
                diagnostics.error(
                    "launch_after_stage_completion",
                    display,
                    f"stage {stage!r} is already durably completed",
                    line,
                )
            if isinstance(stage, str) and stage in active["required_stages"]:
                stage_index = active["required_stages"].index(stage)
                for prior_stage in active["required_stages"][:stage_index]:
                    checkpoint = active["stage_checkpoints"].get(prior_stage)
                    if checkpoint is None or checkpoint.get("outcome") != "pass":
                        diagnostics.error(
                            "protected_stage_without_pass",
                            display,
                            f"stage {stage!r} requires a durable pass for prior stage {prior_stage!r}",
                            line,
                        )
            if (
                contract
                and active.get("role") == "candidate"
                and stage in protected_stages
                and active.get("integrity_cleared") is None
            ):
                diagnostics.error(
                    "protected_stage_without_integrity_clearance",
                    display,
                    "candidate protected-stage intent requires a durable integrity clearance",
                    line,
                )
            stage_access_digest = require_hex64(
                event, "stage_access_sha256", diagnostics, display, line
            )
            expected_stage_access = (
                contract.get("stage_access_sha256", {}).get(stage)
                if contract and isinstance(contract.get("stage_access_sha256"), dict)
                else None
            )
            if stage_access_digest != expected_stage_access:
                diagnostics.error(
                    "stage_access_mismatch",
                    display,
                    "launch intent stage access differs from the sealed stage identity",
                    line,
                )
            effective_config_digest = require_hex64(
                event, "effective_config_sha256", diagnostics, display, line
            )
            expected_stage_config = (
                contract.get("stage_config_sha256", {}).get(stage)
                if contract and isinstance(contract.get("stage_config_sha256"), dict)
                else None
            )
            if effective_config_digest != expected_stage_config:
                diagnostics.error(
                    "effective_config_digest_mismatch",
                    display,
                    "launch intent configuration differs from the sealed stage configuration",
                    line,
                )
            generated_state_digest = require_hex64(
                event, "generated_state_sha256", diagnostics, display, line
            )
            expected_initial_state = (
                contract.get("generated_state_policy", {}).get("initial_state_sha256")
                if contract
                and isinstance(contract.get("generated_state_policy"), dict)
                else None
            )
            if generated_state_digest != expected_initial_state:
                diagnostics.error(
                    "generated_state_policy_mismatch",
                    display,
                    "launch initial generated-state digest differs from the sealed policy",
                    line,
                )
            require_hex64(event, "evaluated_tree_sha256", diagnostics, display, line)
            admission_digest = require_hex64(
                event, "admission_sha256", diagnostics, display, line
            )
            expected_admission_digest = (
                active.get("admission_ref", {}).get("sha256")
                if isinstance(active.get("admission_ref"), dict)
                else None
            )
            if admission_digest != expected_admission_digest:
                diagnostics.error(
                    "admission_digest_mismatch",
                    display,
                    "launch intent does not bind the sealed admission artifact",
                    line,
                )
            allocation = validate_usage_map(
                event.get("allocation"), diagnostics, display, "allocation", line
            ) or {}
            require_exact_units(
                allocation,
                contract_units,
                diagnostics,
                display,
                "allocation",
                line,
            )
            if planned_launch is not None:
                for planned_field in (
                    "stage",
                    "arm",
                    "phase",
                    "pair_id",
                    "seed",
                    "input_id",
                    "workspace_id",
                    "workspace",
                    "allocation",
                    "timeout_seconds",
                    "stage_access_sha256",
                    "effective_config_sha256",
                    "generated_state_sha256",
                ):
                    if event.get(planned_field) != planned_launch.get(planned_field):
                        diagnostics.error(
                            "launch_plan_mismatch",
                            display,
                            f"launch {planned_field} differs from the preregistered plan",
                            line,
                        )
            launch_artifacts: dict[str, Any] = {}
            launch_artifact_refs: dict[str, tuple[str, str] | None] = {}
            workspace_ref = validate_artifact_ref(
                event.get("workspace"),
                path.parent,
                diagnostics,
                display,
                "workspace",
                line,
                expected_path=(
                    f"snapshots/{active['attempt_id']}-{workspace_id}.json"
                    if isinstance(workspace_id, str)
                    else None
                ),
            )
            validate_workspace_snapshot(
                workspace_ref,
                workspace_id,
                event.get("arm"),
                path.parent,
                diagnostics,
                display,
                line,
                supervisor_git_root=supervisor_git_root,
                verify_git=verify_git,
                contract=contract,
                expected_commit=evaluated_commit,
                expected_cwd=event.get("cwd"),
            )
            launch_artifacts["workspace"] = event.get("workspace")
            for artifact_field, digest_field, expected_artifact_path in (
                (
                    "effective_config",
                    "effective_config_sha256",
                    f"snapshots/{result_id}-config.json",
                ),
                (
                    "generated_state",
                    "generated_state_sha256",
                    f"snapshots/{result_id}-state.json",
                ),
                (
                    "evaluated_tree_snapshot",
                    "evaluated_tree_sha256",
                    f"snapshots/{result_id}-tree.json",
                ),
            ):
                artifact_ref = validate_artifact_ref(
                    event.get(artifact_field),
                    path.parent,
                    diagnostics,
                    display,
                    artifact_field,
                    line,
                    expected_path=expected_artifact_path,
                )
                launch_artifacts[artifact_field] = event.get(artifact_field)
                launch_artifact_refs[artifact_field] = artifact_ref
                if (
                    artifact_ref is not None
                    and artifact_ref[1] != event.get(digest_field)
                ):
                    diagnostics.error(
                        "launch_artifact_digest_mismatch",
                        display,
                        f"{artifact_field} reference differs from {digest_field}",
                        line,
                    )
            launch_binding = {
                "result_id": result_id,
                "stage": stage,
                "evaluated_commit": evaluated_commit,
            }
            validate_effective_config(
                launch_artifact_refs.get("effective_config"),
                launch_binding,
                contract,
                path.parent,
                diagnostics,
                display,
                "launch_intent.effective_config",
                line,
            )
            validate_tree_snapshot(
                launch_artifact_refs.get("evaluated_tree_snapshot"),
                launch_binding,
                path.parent,
                diagnostics,
                display,
                "launch_intent.evaluated_tree_snapshot",
                line,
            )
            artifact_paths = [
                reference[0]
                for reference in launch_artifact_refs.values()
                if reference is not None
            ]
            if len(artifact_paths) != len(set(artifact_paths)):
                diagnostics.error(
                    "artifact_role_collision",
                    display,
                    "launch-intent artifact roles must use distinct paths",
                    line,
                )
            validate_relative_syntax(event.get("cwd"), diagnostics, display, "cwd", line, allow_dot=True)
            sealed_cwd = (
                contract.get("evaluation", {}).get("cwd")
                if contract and isinstance(contract.get("evaluation"), dict)
                else None
            )
            if event.get("cwd") != sealed_cwd:
                diagnostics.error(
                    "launch_cwd_mismatch",
                    display,
                    "launch cwd differs from the sealed evaluation cwd",
                    line,
                )
            partial = validate_relative_syntax(
                event.get("log_partial_path"), diagnostics, display, "log_partial_path", line
            )
            if partial is not None and not partial.name.endswith((".partial", ".tmp")):
                diagnostics.error(
                    "invalid_partial_path", display, "log_partial_path needs .partial or .tmp suffix", line
                )
            launch = {
                "launch_id": launch_id,
                "result_id": result_id,
                "ordinal": ordinal,
                "intent": event,
                "artifacts": launch_artifacts,
                "state": "intent",
                "aborted": None,
                "unresolved": None,
                "launched": None,
                "finished": None,
                "cleanup_confirmed": None,
                "cleanup_unresolved": None,
                "evidence_published": None,
                "published_refs": {},
            }
            active["launches"].append(launch)
            if isinstance(launch_id, str):
                active["launch_by_id"][launch_id] = launch
                launch_ids.add(launch_id)
            if isinstance(result_id, str):
                result_ids.add(result_id)
        elif event_name == "launch_aborted":
            launch_id = event.get("launch_id")
            launch = active["launch_by_id"].get(launch_id)
            if launch is None:
                diagnostics.error("unknown_launch", display, f"unknown launch_id {launch_id!r}", line)
                continue
            if event.get("result_id") != launch["result_id"] or event.get("launch_ordinal") != launch["ordinal"]:
                diagnostics.error("launch_identity_drift", display, "launch identity changed", line)
            if launch["state"] != "intent":
                diagnostics.error("journal_order", display, "launch_aborted is out of order", line)
            if event.get("absence_verified") is not True:
                diagnostics.error(
                    "launch_absence_unverified",
                    display,
                    "launch_aborted requires absence_verified true",
                    line,
                )
            require_string(event, "reason", diagnostics, display, line)
            launch["aborted"] = event
            launch["state"] = "aborted"
        elif event_name == "launch_unresolved":
            launch_id = event.get("launch_id")
            launch = active["launch_by_id"].get(launch_id)
            if launch is None:
                diagnostics.error("unknown_launch", display, f"unknown launch_id {launch_id!r}", line)
                continue
            if event.get("result_id") != launch["result_id"] or event.get("launch_ordinal") != launch["ordinal"]:
                diagnostics.error("launch_identity_drift", display, "launch identity changed", line)
            if launch["state"] not in {"intent", "launched"}:
                diagnostics.error("journal_order", display, "launch_unresolved is out of order", line)
            if event.get("fence_applied") is not True:
                diagnostics.error(
                    "unfenced_unresolved_work",
                    display,
                    "launch_unresolved requires fence_applied true",
                    line,
                )
            require_string(event, "reason", diagnostics, display, line)
            launch["unresolved"] = event
            launch["state"] = "unresolved"
        elif event_name in {"launched", "launch_finished"}:
            launch_id = event.get("launch_id")
            launch = active["launch_by_id"].get(launch_id)
            if launch is None:
                diagnostics.error("unknown_launch", display, f"unknown launch_id {launch_id!r}", line)
                continue
            if event.get("result_id") != launch["result_id"] or event.get("launch_ordinal") != launch["ordinal"]:
                diagnostics.error("launch_identity_drift", display, "launch identity changed", line)
            if event_name == "launched":
                admitted_end = parse_timestamp(
                    (active.get("admission_record") or {}).get("worst_case_end_at")
                )
                if (
                    admitted_end is not None
                    and event_at is not None
                    and event_at > admitted_end
                ):
                    diagnostics.error(
                        "launch_after_admission_window",
                        display,
                        "spawn receipt occurs after the admitted worst-case window",
                        line,
                    )
                intent_timeout = (launch.get("intent") or {}).get(
                    "timeout_seconds"
                )
                if (
                    admitted_end is not None
                    and event_at is not None
                    and is_positive_int(intent_timeout)
                    and event_at + timedelta(seconds=intent_timeout) > admitted_end
                ):
                    diagnostics.error(
                        "insufficient_spawn_timeout_window",
                        display,
                        "spawn lacks enough admitted wall time for its sealed timeout",
                        line,
                    )
                if (
                    deadline is not None
                    and event_at is not None
                    and is_positive_int(intent_timeout)
                    and event_at + timedelta(seconds=intent_timeout) > deadline
                ):
                    diagnostics.error(
                        "insufficient_spawn_deadline_window",
                        display,
                        "spawn timeout horizon exceeds the sealed run deadline",
                        line,
                    )
                if launch["state"] != "intent":
                    diagnostics.error("journal_order", display, "launched is out of order", line)
                local_ok = is_positive_int(event.get("pid")) and is_positive_int(
                    event.get("pgid")
                ) and isinstance(
                    event.get("process_start_id"), str
                ) and bool(event.get("process_start_id")) and event.get(
                    "external_job_id"
                ) is None
                external_ok = (
                    isinstance(event.get("external_job_id"), str)
                    and bool(event.get("external_job_id"))
                    and event.get("pid") is None
                    and event.get("pgid") is None
                    and event.get("process_start_id") is None
                )
                if not (local_ok or external_ok):
                    diagnostics.error(
                        "missing_process_identity",
                        display,
                        "launched needs exactly one identity mode: PID/PGID/start or external_job_id",
                        line,
                    )
                if local_ok:
                    process_identity = (
                        "local",
                        event.get("pid"),
                        event.get("pgid"),
                        event.get("process_start_id"),
                    )
                elif external_ok:
                    process_identity = ("external", event.get("external_job_id"))
                else:
                    process_identity = None
                if process_identity is not None:
                    if process_identity in process_identities:
                        diagnostics.error(
                            "reused_process_identity",
                            display,
                            "durable process or external-job identity was reused across launches",
                            line,
                        )
                    process_identities.add(process_identity)
                launch["launched"] = event
                launch["state"] = "launched"
            else:
                if launch["state"] != "launched":
                    diagnostics.error("journal_order", display, "launch_finished is out of order", line)
                if event.get("outcome") not in (
                    "completed",
                    "crash",
                    "timeout",
                    "cancelled",
                    "interrupted",
                ):
                    diagnostics.error("invalid_launch_outcome", display, "unknown launch outcome", line)
                launch_usage = validate_usage_map(
                    event.get("actual_usage"), diagnostics, display, "actual_usage", line
                ) or {}
                require_exact_units(
                    launch_usage,
                    contract_units,
                    diagnostics,
                    display,
                    "actual_usage",
                    line,
                )
                if not isinstance(event.get("cleanup_verified"), bool):
                    diagnostics.error(
                        "invalid_cleanup_state", display, "cleanup_verified must be boolean", line
                    )
                launch["finished"] = event
                launch["state"] = "finished"
        elif event_name == "cleanup_confirmed":
            launch_id = event.get("launch_id")
            launch = active["launch_by_id"].get(launch_id)
            if launch is None:
                diagnostics.error("unknown_launch", display, f"unknown launch_id {launch_id!r}", line)
                continue
            if event.get("result_id") != launch["result_id"] or event.get("launch_ordinal") != launch["ordinal"]:
                diagnostics.error("launch_identity_drift", display, "launch identity changed", line)
            if (
                launch["state"] != "finished"
                or (launch.get("finished") or {}).get("cleanup_verified") is not False
                or launch.get("cleanup_confirmed") is not None
            ):
                diagnostics.error("journal_order", display, "cleanup_confirmed is out of order", line)
            require_string(event, "verification", diagnostics, display, line)
            launch["cleanup_confirmed"] = event
        elif event_name == "cleanup_unresolved":
            launch_id = event.get("launch_id")
            launch = active["launch_by_id"].get(launch_id)
            if launch is None:
                diagnostics.error("unknown_launch", display, f"unknown launch_id {launch_id!r}", line)
                continue
            if event.get("result_id") != launch["result_id"] or event.get("launch_ordinal") != launch["ordinal"]:
                diagnostics.error("launch_identity_drift", display, "launch identity changed", line)
            if (
                launch["state"] != "finished"
                or (launch.get("finished") or {}).get("cleanup_verified") is not False
                or launch.get("cleanup_confirmed") is not None
                or launch.get("cleanup_unresolved") is not None
            ):
                diagnostics.error("journal_order", display, "cleanup_unresolved is out of order", line)
            if event.get("fence_applied") is not True:
                diagnostics.error(
                    "unfenced_unresolved_work",
                    display,
                    "cleanup_unresolved requires fence_applied true",
                    line,
                )
            require_string(event, "reason", diagnostics, display, line)
            launch["cleanup_unresolved"] = event
        elif event_name == "evidence_published":
            launch_id = event.get("launch_id")
            launch = active["launch_by_id"].get(launch_id)
            if launch is None:
                diagnostics.error(
                    "unknown_launch", display, f"unknown launch_id {launch_id!r}", line
                )
                continue
            if (
                event.get("result_id") != launch["result_id"]
                or event.get("launch_ordinal") != launch["ordinal"]
            ):
                diagnostics.error(
                    "launch_identity_drift", display, "launch identity changed", line
                )
            cleanup_resolved = (
                (launch.get("finished") or {}).get("cleanup_verified") is True
                or launch.get("cleanup_confirmed") is not None
            )
            if (
                launch.get("state") != "finished"
                or (launch.get("finished") or {}).get("outcome") != "completed"
                or not cleanup_resolved
                or launch.get("evidence_published") is not None
            ):
                diagnostics.error(
                    "journal_order",
                    display,
                    "evidence_published requires one completed, cleaned launch",
                    line,
                )
            publication_stage = (launch.get("intent") or {}).get("stage")
            expected_phase = (
                "committed" if active.get("role") == "candidate" else "prepared"
            )
            if (
                active.get("phase") != expected_phase
                or publication_stage in active.get("stage_checkpoints", {})
            ):
                diagnostics.error(
                    "late_evidence_publication",
                    display,
                    "evidence must be published before its stage checkpoint and evaluation cutoff",
                    line,
                )
            raw_ref = validate_artifact_ref(
                event.get("result_artifact"),
                path.parent,
                diagnostics,
                display,
                "result_artifact",
                line,
                expected_path=f"evidence/{launch['result_id']}.json",
            )
            log_ref = validate_artifact_ref(
                event.get("log"),
                path.parent,
                diagnostics,
                display,
                "log",
                line,
                expected_path=f"logs/{launch['result_id']}.log",
            )
            repetition_digest = require_hex64(
                event, "repetition_sha256", diagnostics, display, line
            )
            raw_record = load_referenced_json(raw_ref, path.parent, diagnostics)
            log_record = load_referenced_json(log_ref, path.parent, diagnostics)
            if (
                raw_record is not None
                and log_record is not None
                and log_record != raw_record
            ):
                diagnostics.error(
                    "extractor_output_mismatch",
                    display,
                    "normalized evidence must exactly match the captured canonical evaluator output",
                    line,
                )
            if raw_record is not None:
                validate_raw_evidence(
                    raw_ref,
                    raw_record,
                    path.parent,
                    diagnostics,
                    display,
                    "evidence_published",
                    line,
                )
                intent = launch.get("intent") or {}
                for identity_field in (
                    "result_id",
                    "pair_id",
                    "arm",
                    "phase",
                    "stage",
                    "evaluated_commit",
                    "seed",
                    "input_id",
                ):
                    if raw_record.get(identity_field) != intent.get(identity_field):
                        diagnostics.error(
                            "published_evidence_identity_mismatch",
                            display,
                            f"published raw evidence {identity_field} differs from launch intent",
                            line,
                        )
                expected_evaluator = (
                    contract.get("evaluation", {}).get("evaluator_sha256")
                    if contract
                    and isinstance(contract.get("evaluation"), dict)
                    else None
                )
                if raw_record.get("evaluator_sha256") != expected_evaluator:
                    diagnostics.error(
                        "evaluator_digest_mismatch",
                        display,
                        "published evidence uses the wrong sealed evaluator",
                        line,
                    )
                metrics = raw_record.get("metrics")
                primary_metric = (
                    contract.get("objective", {}).get("primary_metric")
                    if contract and isinstance(contract.get("objective"), dict)
                    else None
                )
                if (
                    not isinstance(metrics, dict)
                    or not isinstance(primary_metric, str)
                    or not is_finite_number(metrics.get(primary_metric))
                    or raw_record.get("primary_value") != metrics.get(primary_metric)
                ):
                    diagnostics.error(
                        "published_primary_metric_mismatch",
                        display,
                        "published primary value must equal the finite sealed primary metric",
                        line,
                    )
                stage = intent.get("stage")
                if active.get("role") == "final":
                    required_telemetry = (
                        contract.get("final_test", {}).get("required_telemetry", [])
                        if contract and isinstance(contract.get("final_test"), dict)
                        else []
                    )
                else:
                    required_telemetry = (
                        contract.get("promotion", {})
                        .get("required_telemetry", {})
                        .get(stage, [])
                        if contract
                        and isinstance(contract.get("promotion"), dict)
                        and isinstance(
                            contract.get("promotion", {}).get("required_telemetry"),
                            dict,
                        )
                        else []
                    )
                for metric_id in required_telemetry:
                    if not isinstance(metrics, dict) or not is_finite_number(
                        metrics.get(metric_id)
                    ):
                        diagnostics.error(
                            "missing_published_telemetry",
                            display,
                            f"published evidence lacks finite telemetry {metric_id!r}",
                            line,
                        )
                normalized_repetition = {
                    key: value
                    for key, value in raw_record.items()
                    if key != "schema_version"
                }
                normalized_repetition.update(
                    {
                        "result_artifact": event.get("result_artifact"),
                        "log": event.get("log"),
                        "effective_config": (launch.get("intent") or {}).get(
                            "effective_config"
                        ),
                        "generated_state": (launch.get("intent") or {}).get(
                            "generated_state"
                        ),
                        "evaluated_tree_snapshot": (
                            launch.get("intent") or {}
                        ).get("evaluated_tree_snapshot"),
                    }
                )
                if canonical_sha256(normalized_repetition) != repetition_digest:
                    diagnostics.error(
                        "published_repetition_digest_mismatch",
                        display,
                        "evidence_published digest does not match the reconstructible normalized repetition",
                        line,
                    )
            launch["evidence_published"] = event
            launch["published_refs"] = {
                "result_artifact": raw_ref,
                "log": log_ref,
                "repetition_sha256": repetition_digest,
            }
        elif event_name == "plan_tail_cancelled":
            from_ordinal = event.get("from_ordinal")
            expected_from = len(active["launches"])
            expected_ordinals = list(range(expected_from, len(active["launch_plan"])))
            if stop_requested is None:
                diagnostics.error(
                    "tail_cancel_without_stop",
                    display,
                    "plan_tail_cancelled requires a prior stop_requested event",
                    line,
                )
            if active.get("tail_cancelled") is not None:
                diagnostics.error(
                    "duplicate_tail_cancellation",
                    display,
                    "plan tail may be cancelled only once",
                    line,
                )
            if from_ordinal != expected_from or event.get(
                "cancelled_ordinals"
            ) != expected_ordinals or not expected_ordinals:
                diagnostics.error(
                    "invalid_tail_cancellation",
                    display,
                    "plan_tail_cancelled must name the exact unlaunched ordinal suffix",
                    line,
                )
            if any(
                launch.get("state") not in {"finished", "aborted", "unresolved"}
                or (
                    launch.get("state") == "finished"
                    and (launch.get("finished") or {}).get("cleanup_verified") is not True
                    and launch.get("cleanup_confirmed") is None
                    and launch.get("cleanup_unresolved") is None
                )
                for launch in active["launches"]
            ):
                diagnostics.error(
                    "tail_cancel_with_active_launch",
                    display,
                    "plan tail cannot be cancelled while owned work remains active",
                    line,
                )
            require_string(event, "reason", diagnostics, display, line)
            active["tail_cancelled"] = event
        elif event_name == "integrity_cleared":
            if active.get("role") != "candidate" or active.get("phase") != "committed":
                diagnostics.error(
                    "journal_order",
                    display,
                    "integrity_cleared is valid only for a committed candidate before evaluation",
                    line,
                )
            if active.get("integrity_cleared") is not None:
                diagnostics.error(
                    "duplicate_integrity_clearance",
                    display,
                    "candidate integrity may be cleared only once",
                    line,
                )
            if any(
                (launch.get("intent") or {}).get("stage") in protected_stages
                for launch in active.get("launches", [])
            ):
                diagnostics.error(
                    "late_integrity_clearance",
                    display,
                    "integrity clearance must precede every candidate protected-stage intent",
                    line,
                )
            if event.get("candidate_commit") != active.get("candidate_commit"):
                diagnostics.error(
                    "integrity_commit_mismatch",
                    display,
                    "integrity clearance is bound to the wrong candidate commit",
                    line,
                )
            require_hex64(event, "evaluated_tree_sha256", diagnostics, display, line)
            candidate_launches = [
                launch
                for launch in active.get("launches", [])
                if (launch.get("intent") or {}).get("arm") == "candidate"
            ]
            expected_tree_digest = (
                (candidate_launches[-1].get("intent") or {}).get(
                    "evaluated_tree_sha256"
                )
                if candidate_launches
                else None
            )
            if event.get("evaluated_tree_sha256") != expected_tree_digest:
                diagnostics.error(
                    "integrity_tree_mismatch",
                    display,
                    "integrity clearance must bind the latest candidate tree snapshot",
                    line,
                )
            if event.get("scope_audit") != "pass" or event.get(
                "leakage_audit"
            ) != "pass":
                diagnostics.error(
                    "integrity_clearance_failed",
                    display,
                    "integrity clearance requires passing scope and leakage audits",
                    line,
                )
            constraints = event.get("constraint_results")
            expected_constraints = set(
                contract.get("objective", {}).get("hard_constraints", [])
                if contract and isinstance(contract.get("objective"), dict)
                else []
            )
            if not isinstance(constraints, dict) or set(constraints) != expected_constraints or any(
                constraints.get(name) is not True for name in expected_constraints
            ):
                diagnostics.error(
                    "integrity_constraint_mismatch",
                    display,
                    "integrity clearance requires every sealed hard constraint to be true",
                    line,
                )
            active["integrity_cleared"] = event
        elif event_name == "stage_completed":
            stage = event.get("stage")
            if not isinstance(stage, str) or stage not in active["required_stages"]:
                diagnostics.error(
                    "unexpected_stage",
                    display,
                    "stage_completed must name an applicable sealed stage",
                    line,
                )
                continue
            if stage in active["stage_checkpoints"]:
                diagnostics.error(
                    "duplicate_stage_checkpoint",
                    display,
                    f"stage {stage!r} already has a completion receipt",
                    line,
                )
            expected_index = len(active["stage_checkpoints"])
            if (
                expected_index >= len(active["required_stages"])
                or active["required_stages"][expected_index] != stage
            ):
                diagnostics.error(
                    "stage_order",
                    display,
                    "stage completion receipts must follow the sealed stage order",
                    line,
                )
            stage_launches = [
                launch
                for launch in active["launches"]
                if launch.get("intent", {}).get("stage") == stage
            ]
            if not stage_launches:
                diagnostics.error(
                    "stage_without_launch",
                    display,
                    f"stage {stage!r} has no launch receipts",
                    line,
                )
            planned_stage_launches = [
                launch
                for launch in active["launch_plan"]
                if launch.get("stage") == stage
            ]
            stage_plan_incomplete = len(stage_launches) != len(planned_stage_launches)
            if stage_plan_incomplete and active.get("tail_cancelled") is None:
                diagnostics.error(
                    "incomplete_stage_launch_plan",
                    display,
                    f"stage {stage!r} cannot complete before every preregistered launch",
                    line,
                )
            if any(
                launch.get("state") not in {"finished", "aborted", "unresolved"}
                or (
                    launch.get("state") == "finished"
                    and (launch.get("finished") or {}).get("cleanup_verified") is not True
                    and launch.get("cleanup_confirmed") is None
                    and launch.get("cleanup_unresolved") is None
                )
                for launch in stage_launches
            ):
                diagnostics.error(
                    "stage_with_active_launch",
                    display,
                    f"stage {stage!r} cannot complete before every owned launch is terminal",
                    line,
                )
            expected_result_ids = [
                launch["result_id"]
                for launch in stage_launches
                if (launch.get("finished") or {}).get("outcome") == "completed"
                and (
                    (launch.get("finished") or {}).get("cleanup_verified") is True
                    or launch.get("cleanup_confirmed") is not None
                )
                and launch.get("evidence_published") is not None
            ]
            if event.get("result_ids") != expected_result_ids:
                diagnostics.error(
                    "stage_result_ids_mismatch",
                    display,
                    f"stage {stage!r} result_ids must exactly match its admissible launches",
                    line,
                )
            outcome = event.get("outcome")
            if outcome not in ("pass", "fail", "unknown"):
                diagnostics.error(
                    "invalid_stage_outcome",
                    display,
                    "stage_completed outcome must be pass, fail, or unknown",
                    line,
                )
            stage_has_uncertain_work = any(
                launch.get("state") in {"aborted", "unresolved"}
                or launch.get("cleanup_unresolved") is not None
                or (launch.get("finished") or {}).get("outcome") != "completed"
                or launch.get("evidence_published") is None
                for launch in stage_launches
            )
            if (
                stage_has_uncertain_work
                or stage_plan_incomplete
                or not expected_result_ids
            ) and outcome != "unknown":
                diagnostics.error(
                    "conclusive_stage_without_evidence",
                    display,
                    f"stage {stage!r} cannot be conclusive with missing or failed launch evidence",
                    line,
                )
            require_string(event, "rule_evaluation", diagnostics, display, line)
            active["stage_checkpoints"][stage] = event
        elif event_name == "evaluated":
            expected_phase = (
                "committed" if active["role"] == "candidate" else "prepared"
            )
            if active["phase"] != expected_phase or not active["launches"]:
                diagnostics.error("journal_order", display, "evaluated is out of order", line)
            if any(launch["state"] not in {"finished", "aborted", "unresolved"} for launch in active["launches"]):
                diagnostics.error("unfinished_launch", display, "all launch intents must become terminal before evaluated", line)
            if any(
                launch["state"] == "finished"
                and (launch.get("finished") or {}).get("cleanup_verified") is not True
                and launch.get("cleanup_confirmed") is None
                and launch.get("cleanup_unresolved") is None
                for launch in active["launches"]
            ):
                diagnostics.error("cleanup_unverified", display, "all launch cleanup must be verified", line)
            expected_results = [
                launch["result_id"]
                for launch in active["launches"]
                if (launch.get("finished") or {}).get("outcome") == "completed"
                and (
                    (launch.get("finished") or {}).get("cleanup_verified") is True
                    or launch.get("cleanup_confirmed") is not None
                )
                and launch.get("evidence_published") is not None
            ]
            if event.get("result_ids") != expected_results:
                diagnostics.error("evaluated_result_ids", display, "evaluated result_ids do not match launches", line)
            launched_stages = {
                launch.get("intent", {}).get("stage") for launch in active["launches"]
            }
            if not launched_stages.issubset(active["stage_checkpoints"]):
                diagnostics.error(
                    "missing_stage_checkpoint",
                    display,
                    "every launched stage requires a durable stage_completed receipt",
                    line,
                )
            active["evaluated"] = event
            active["phase"] = "evaluated"
        elif event_name == "decided":
            if active["decision"] is not None:
                diagnostics.error("duplicate_decision", display, "decided may occur once", line)
            if active["launches"]:
                if active["phase"] != "evaluated":
                    diagnostics.error("journal_order", display, "decided requires evaluated", line)
            else:
                if event.get("status") not in ("invalid", "cancelled", "interrupted"):
                    diagnostics.error("decision_without_evaluation", display, "status cannot skip evaluation", line)
                require_string(event, "no_evaluation_reason", diagnostics, display, line)
            status = event.get("status")
            lane = event.get("lane")
            maturity = event.get("evidence_maturity")
            accepted = event.get("accepted")
            stage_results = event.get("stage_results")
            validate_outcome(
                status,
                lane,
                maturity,
                accepted,
                stage_results,
                active["required_stages"],
                diagnostics,
                display,
                line,
            )
            available_stage_results = {
                launch["result_id"]: launch["intent"].get("stage")
                for launch in active["launches"]
                if (launch.get("finished") or {}).get("outcome") == "completed"
                and (
                    (launch.get("finished") or {}).get("cleanup_verified") is True
                    or launch.get("cleanup_confirmed") is not None
                )
            }
            validate_stage_evidence(
                stage_results,
                event.get("stage_evidence"),
                active["required_stages"],
                available_stage_results,
                diagnostics,
                display,
                line,
            )
            if isinstance(stage_results, dict) and isinstance(event.get("stage_evidence"), dict):
                for stage in active["required_stages"]:
                    checkpoint = active["stage_checkpoints"].get(stage)
                    expected_outcome = (
                        checkpoint.get("outcome") if checkpoint is not None else "unknown"
                    )
                    expected_evidence = (
                        checkpoint.get("result_ids", [])
                        if expected_outcome in ("pass", "fail")
                        else []
                    )
                    if stage_results.get(stage) != expected_outcome:
                        diagnostics.error(
                            "stage_checkpoint_mismatch",
                            display,
                            f"decision for stage {stage!r} differs from its durable checkpoint",
                            line,
                        )
                    if event["stage_evidence"].get(stage) != expected_evidence:
                        diagnostics.error(
                            "stage_checkpoint_mismatch",
                            display,
                            f"decision evidence for stage {stage!r} differs from its durable checkpoint",
                            line,
                        )
            unresolved_work = any(
                launch.get("state") == "unresolved"
                or launch.get("cleanup_unresolved") is not None
                for launch in active["launches"]
            )
            failed_launch_outcomes = {
                (launch.get("finished") or {}).get("outcome")
                for launch in active["launches"]
                if (launch.get("finished") or {}).get("outcome")
                in ("crash", "timeout", "cancelled", "interrupted")
            }
            if (
                failed_launch_outcomes
                and not unresolved_work
                and status not in failed_launch_outcomes
            ):
                diagnostics.error(
                    "launch_outcome_mismatch",
                    display,
                    "a failed launch forces the matching non-promotional attempt status",
                    line,
                )
            if any(launch.get("state") == "aborted" for launch in active["launches"]) and status not in (
                "cancelled",
                "interrupted",
                "invalid",
            ):
                diagnostics.error(
                    "aborted_launch_promoted",
                    display,
                    "an aborted launch forces a non-promotional attempt outcome",
                    line,
                )
            require_string(event, "reason", diagnostics, display, line)
            require_string(event, "rule_evaluation", diagnostics, display, line)
            incumbent_after = require_git_oid(
                event, "incumbent_after", diagnostics, display, line
            )
            rollback_action = event.get("rollback_action")
            if rollback_action not in ("none", "revert", "preserve_and_stop"):
                diagnostics.error("invalid_rollback_action", display, "unknown rollback_action", line)
            if unresolved_work and (
                status != "interrupted"
                or accepted is not False
                or rollback_action != "preserve_and_stop"
                or any(
                    isinstance(stage_results, dict)
                    and stage_results.get(stage) != "unknown"
                    for stage in active["required_stages"]
                )
            ):
                diagnostics.error(
                    "invalid_unresolved_decision",
                    display,
                    "unresolved work requires interrupted, unknown evidence, and preserve_and_stop",
                    line,
                )
            if status == "keep":
                if len(active["launches"]) != len(active["launch_plan"]):
                    diagnostics.error(
                        "incomplete_launch_plan",
                        display,
                        "keep requires every preregistered launch-plan entry to be executed",
                        line,
                    )
                expected = (
                    active["candidate_commit"]
                    if active["role"] == "candidate"
                    else active["incumbent_commit"]
                )
                if incumbent_after != expected or rollback_action != "none":
                    diagnostics.error("invalid_keep_decision", display, "keep must advance to evaluated candidate without rollback", line)
            else:
                if incumbent_after != active["incumbent_commit"]:
                    diagnostics.error("invalid_incumbent", display, "non-keep must retain prior incumbent", line)
                if active["role"] == "candidate":
                    if (
                        active.get("candidate_commit") is None
                        and rollback_action not in ("none", "preserve_and_stop")
                    ):
                        diagnostics.error(
                            "invalid_precommit_rollback",
                            display,
                            "a pre-commit candidate may only close normally or preserve and stop",
                            line,
                        )
                    elif active.get("candidate_commit") is not None and rollback_action not in (
                        "revert",
                        "preserve_and_stop",
                    ):
                        diagnostics.error(
                            "missing_rollback_action",
                            display,
                            "committed candidate non-keep needs recovery action",
                            line,
                        )
                if active["role"] == "final" and rollback_action != "none":
                    diagnostics.error(
                        "invalid_final_rollback", display, "final verification never rolls back code", line
                    )
                if active["role"] == "final" and lane != "diagnostic":
                    diagnostics.error(
                        "invalid_final_lane", display, "failed final verification is diagnostic", line
                    )
            active["decision"] = event
            active["decision_seq"] = line
            active["phase"] = "decided"
        elif event_name == "rolled_back":
            if active["phase"] != "decided" or active.get("decision", {}).get("rollback_action") != "revert":
                diagnostics.error("journal_order", display, "rolled_back is out of order", line)
            require_git_oid(event, "rollback_commit", diagnostics, display, line)
            restored = require_git_oid(
                event, "restored_incumbent_commit", diagnostics, display, line
            )
            if restored != active["incumbent_commit"]:
                diagnostics.error("rollback_mismatch", display, "rollback restored wrong incumbent", line)
            active["rolled_back"] = event
            active["phase"] = "rolled_back"
        elif event_name == "finalized":
            if active["phase"] not in {"decided", "rolled_back"}:
                diagnostics.error("journal_order", display, "finalized is out of order", line)
            if active.get("decision", {}).get("rollback_action") == "revert" and active["rolled_back"] is None:
                diagnostics.error("rollback_missing", display, "required rollback is not recorded", line)
            if event.get("decision_seq") != active.get("decision_seq"):
                diagnostics.error("decision_reference", display, "finalized decision_seq differs", line)
            if not is_nonnegative_int(event.get("result_index")):
                diagnostics.error("invalid_result_index", display, "result_index must be nonnegative", line)
            require_hex64(event, "result_sha256", diagnostics, display, line)
            expected_boundary = f"boundaries/{active['attempt_id']}.json"
            if event.get("boundary_path") != expected_boundary:
                diagnostics.error("invalid_boundary_path", display, f"boundary_path must be {expected_boundary}", line)
            active["finalized"] = event
            active["finalized_seq"] = line
            active["phase"] = "finalized"
            expected_incumbent = active.get("decision", {}).get(
                "incumbent_after", expected_incumbent
            )
            active = None
    unresolved_recorded = any(
        launch.get("state") == "unresolved" or launch.get("cleanup_unresolved") is not None
        for attempt in attempts
        for launch in attempt.get("launches", [])
    )
    if (
        run_stopped is not None
        and run_stopped.get("unresolved_cleanup") is not unresolved_recorded
    ):
        diagnostics.error(
            "unresolved_cleanup_mismatch",
            display,
            "run_stopped unresolved_cleanup must match unresolved launch records",
            run_stopped.get("seq"),
        )
    return attempts, stop_requested, run_stopped


def validate_result_record(
    result: dict[str, Any],
    line: int,
    attempt: dict[str, Any] | None,
    run_dir: Path,
    contract: dict[str, Any] | None,
    contract_digest: str | None,
    required_stages: list[str],
    budgets: dict[str, tuple[int | None, int, int]],
    cumulative: dict[str, int],
    result_ids_seen: dict[str, tuple[Any, ...]],
    design_digests: dict[str, str],
    confirmed_parents: dict[str, str],
    prior_result_ids: set[str],
    confirmed_primary: dict[str, float],
    diagnostics: Diagnostics,
    path: Path,
) -> None:
    display = str(path)
    if result.get("schema_version") != 1:
        diagnostics.error("schema_version", display, "schema_version must equal 1", line)
    attempt_id = require_id(result, "attempt_id", diagnostics, display, line)
    require_id(result, "experiment_id", diagnostics, display, line)
    if result.get("iteration") != line - 1:
        diagnostics.error("result_iteration", display, f"iteration must equal {line - 1}", line)
    if not isinstance(result.get("role"), str) or result.get("role") not in {"baseline", "candidate", "final"}:
        diagnostics.error(
            "invalid_role", display, "role must be baseline, candidate, or final", line
        )
    commit = require_git_oid(result, "commit", diagnostics, display, line)
    incumbent = require_git_oid(result, "incumbent_commit", diagnostics, display, line)
    pre_attempt_head = require_git_oid(
        result, "pre_attempt_head_commit", diagnostics, display, line
    )
    rollback_commit = result.get("rollback_commit")
    if rollback_commit is not None and (
        not isinstance(rollback_commit, str) or GIT_OID_RE.fullmatch(rollback_commit) is None
    ):
        diagnostics.error("invalid_git_oid", display, "rollback_commit is invalid", line)
    started_at = validate_timestamp(
        result.get("started_at"), diagnostics, display, "started_at", line
    )
    ended_at = validate_timestamp(
        result.get("ended_at"), diagnostics, display, "ended_at", line
    )
    if started_at is not None and ended_at is not None and started_at > ended_at:
        diagnostics.error(
            "result_time_regression",
            display,
            "result started_at must not be after ended_at",
            line,
        )
    if result.get("contract_sha256") != contract_digest:
        diagnostics.error("contract_digest_mismatch", display, "result contract digest differs", line)
    decision = result.get("decision")
    if not isinstance(decision, dict):
        diagnostics.error("invalid_decision", display, "decision must be an object", line)
        decision = {}
    status = result.get("status")
    lane = result.get("lane")
    maturity = result.get("evidence_maturity")
    role = result.get("role")
    operational_overruns: list[str] = []
    objective = contract.get("objective", {}) if contract else {}
    final_test = contract.get("final_test") if contract else None
    final_stage = final_test.get("stage") if isinstance(final_test, dict) else None
    result_required_stages = (
        [final_stage]
        if role == "final" and isinstance(final_stage, str)
        else required_stages
    )
    result_required_telemetry = (
        {final_stage: final_test.get("required_telemetry", [])}
        if role == "final" and isinstance(final_stage, str) and isinstance(final_test, dict)
        else (
            contract.get("promotion", {}).get("required_telemetry", {})
            if contract and isinstance(contract.get("promotion"), dict)
            else {}
        )
    )
    acceptance_stage = (
        final_stage
        if role == "final" and isinstance(final_stage, str)
        else objective.get("acceptance_split")
    )
    protected_stages = protected_stage_ids(contract)
    validate_outcome(
        status,
        lane,
        maturity,
        decision.get("accepted"),
        decision.get("stage_results"),
        result_required_stages,
        diagnostics,
        display,
        line,
    )
    require_string(result, "acceptance_reason", diagnostics, display, line)
    primary_metric = require_string(result, "primary_metric", diagnostics, display, line)
    contracted_metric = objective.get("primary_metric") if isinstance(objective, dict) else None
    if primary_metric != contracted_metric:
        diagnostics.error(
            "primary_metric_mismatch",
            display,
            "result primary_metric differs from the sealed objective",
            line,
        )
    primary_value = result.get("primary_value")
    if primary_value is not None and not is_finite_number(primary_value):
        diagnostics.error("invalid_metric", display, "primary_value must be finite or null", line)
    for key in ("split_metrics", "secondary_metrics", "counterexample_results", "constraint_results"):
        if not isinstance(result.get(key), dict):
            diagnostics.error("invalid_field", display, f"{key} must be an object", line)
        else:
            validate_finite_tree(result[key], diagnostics, display, key, line)
    contracted_counterexamples = (
        contract.get("control_policy", {}).get("counterexamples", {})
        if contract and isinstance(contract.get("control_policy"), dict)
        else {}
    )
    counterexample_results = result.get("counterexample_results")
    if isinstance(counterexample_results, dict):
        expected_counterexamples = (
            set(contracted_counterexamples)
            if isinstance(contracted_counterexamples, dict)
            else set()
        )
        actual_counterexamples = set(counterexample_results)
        missing_counterexamples = sorted(
            expected_counterexamples - actual_counterexamples
        )
        extra_counterexamples = sorted(
            actual_counterexamples - expected_counterexamples
        )
        if missing_counterexamples:
            diagnostics.error(
                "missing_counterexample_result",
                display,
                f"sealed counterexamples are missing: {missing_counterexamples}",
                line,
            )
        if extra_counterexamples:
            diagnostics.error(
                "unexpected_counterexample_result",
                display,
                f"unsealed counterexample result IDs are forbidden: {extra_counterexamples}",
                line,
            )
        for counterexample, outcome in counterexample_results.items():
            if outcome is not None and not isinstance(outcome, bool):
                diagnostics.error(
                    "invalid_counterexample_result",
                    display,
                    f"counterexample result {counterexample!r} must be true, false, or null",
                    line,
                )
    contracted_constraints = (
        objective.get("hard_constraints", []) if isinstance(objective, dict) else []
    )
    constraint_results = result.get("constraint_results")
    if isinstance(constraint_results, dict):
        expected_constraints = set(contracted_constraints)
        actual_constraints = set(constraint_results)
        missing = sorted(expected_constraints - actual_constraints)
        extra = sorted(actual_constraints - expected_constraints)
        if missing:
            diagnostics.error(
                "missing_constraint_result",
                display,
                f"sealed hard constraints are missing: {missing}",
                line,
            )
        if extra:
            diagnostics.error(
                "unexpected_constraint_result",
                display,
                f"unsealed constraint result IDs are forbidden: {extra}",
                line,
            )
        for constraint, outcome in constraint_results.items():
            if outcome is not None and not isinstance(outcome, bool):
                diagnostics.error(
                    "invalid_constraint_result",
                    display,
                    f"constraint result {constraint!r} must be true, false, or null",
                    line,
                )
    if status == "keep" and not is_finite_number(primary_value):
        diagnostics.error("illegal_promotion", display, "keep requires a finite primary_value", line)
    if status == "keep" and (
        not isinstance(constraint_results, dict)
        or any(constraint_results.get(constraint) is not True for constraint in contracted_constraints)
    ):
        diagnostics.error(
            "illegal_promotion", display, "keep requires every recorded constraint to be true", line
        )
    if status == "keep" and (
        not isinstance(counterexample_results, dict)
        or not isinstance(contracted_counterexamples, dict)
        or any(
            counterexample_results.get(counterexample) is not True
            for counterexample in contracted_counterexamples
        )
    ):
        diagnostics.error(
            "illegal_promotion",
            display,
            "keep requires every sealed counterexample to pass",
            line,
        )
    aggregation = require_string(result, "aggregation", diagnostics, display, line)
    measurement = contract.get("measurement", {}) if contract else {}
    contracted_aggregation = (
        measurement.get("aggregation") if isinstance(measurement, dict) else None
    )
    if aggregation != contracted_aggregation:
        diagnostics.error(
            "aggregation_mismatch",
            display,
            "result aggregation differs from the sealed measurement rule",
            line,
        )
    duration = result.get("duration_seconds")
    if not is_finite_number(duration) or duration < 0:
        diagnostics.error("invalid_duration", display, "duration_seconds must be nonnegative and finite", line)
    validation_queries = result.get("validation_queries")
    if not is_nonnegative_int(validation_queries):
        diagnostics.error("invalid_query_count", display, "validation_queries must be nonnegative integer", line)
    if (
        status == "keep"
        and role in ("baseline", "candidate", "final")
        and protected_stages.intersection(result_required_stages)
        and (not is_nonnegative_int(validation_queries) or validation_queries == 0)
    ):
        diagnostics.error(
            "missing_gate_evidence",
            display,
            "a conclusive protected stage requires receipted validation-query use",
            line,
        )
    require_string(result, "hypothesis", diagnostics, display, line)
    require_string(result, "change_summary", diagnostics, display, line)
    if role in ("baseline", "final"):
        if result.get("design") is not None:
            diagnostics.error(
                "noncandidate_design", display, "baseline and final design must be null", line
            )
    else:
        design_ref = validate_artifact_ref(
            result.get("design"),
            run_dir,
            diagnostics,
            display,
            "design",
            line,
            expected_path=f"designs/{result.get('experiment_id')}.json",
        )
        experiment_id = result.get("experiment_id")
        if design_ref is not None and isinstance(experiment_id, str):
            prior_design = design_digests.get(experiment_id)
            if prior_design is not None and prior_design != design_ref[1]:
                diagnostics.error(
                    "experiment_design_drift",
                    display,
                    "one experiment_id references multiple design digests",
                    line,
                )
            design_digests[experiment_id] = design_ref[1]
        validate_design_record(
            design_ref,
            result,
            run_dir,
            contract,
            budgets,
            confirmed_parents,
            prior_result_ids,
            diagnostics,
            display,
            line,
        )
    admission = result.get("admission")
    if not isinstance(admission, dict):
        diagnostics.error("invalid_admission", display, "admission must be an object", line)
        admission = {}
    requested = validate_usage_map(
        admission.get("requested_worst_case"), diagnostics, display, "admission.requested_worst_case", line
    ) or {}
    protected = validate_usage_map(
        admission.get("protected_after_launch"), diagnostics, display, "admission.protected_after_launch", line
    ) or {}
    cumulative_before = validate_usage_map(
        admission.get("cumulative_before"),
        diagnostics,
        display,
        "admission.cumulative_before",
        line,
    ) or {}
    checked_at = validate_timestamp(
        admission.get("checked_at"), diagnostics, display, "admission.checked_at", line
    )
    worst_case_end_at = validate_timestamp(
        admission.get("worst_case_end_at"),
        diagnostics,
        display,
        "admission.worst_case_end_at",
        line,
    )
    if started_at is not None and checked_at is not None and checked_at > started_at:
        diagnostics.error(
            "admission_time_mismatch",
            display,
            "admission cannot follow the recorded attempt start",
            line,
        )
    if attempt is not None:
        prepared_at = parse_timestamp((attempt.get("prepared") or {}).get("at"))
        if prepared_at is not None and started_at is not None and started_at != prepared_at:
            diagnostics.error(
                "result_start_mismatch",
                display,
                "result.started_at must equal the prepared event time",
                line,
            )
        intent_times = [
            timestamp
            for launch in attempt["launches"]
            if (timestamp := parse_timestamp((launch.get("intent") or {}).get("at")))
            is not None
        ]
        if checked_at is not None and intent_times and checked_at > min(intent_times):
            diagnostics.error(
                "admission_after_launch_intent",
                display,
                "admission.checked_at must not follow the first launch intent",
                line,
            )
        terminal_times: list[datetime] = []
        for launch in attempt["launches"]:
            for field in (
                "aborted",
                "unresolved",
                "finished",
                "cleanup_confirmed",
                "cleanup_unresolved",
                "evidence_published",
            ):
                timestamp = parse_timestamp((launch.get(field) or {}).get("at"))
                if timestamp is not None:
                    terminal_times.append(timestamp)
        if ended_at is not None and terminal_times and ended_at < max(terminal_times):
            diagnostics.error(
                "result_before_launch_terminal",
                display,
                "result.ended_at must not precede launch resolution and cleanup receipts",
                line,
            )
    if ended_at is not None and worst_case_end_at is not None and ended_at > worst_case_end_at:
        operational_overruns.append("attempt ended after its admitted worst-case end")
    if not isinstance(admission.get("allowed"), bool) or not isinstance(
        admission.get("deadline_fit"), bool
    ):
        diagnostics.error("invalid_admission", display, "admission flags must be boolean", line)
    budget = result.get("budget")
    if not isinstance(budget, dict):
        diagnostics.error("invalid_budget", display, "budget must be an object", line)
        budget = {}
    unresolved_usage = bool(
        attempt is not None
        and any(
            launch.get("state") == "unresolved"
            or launch.get("cleanup_unresolved") is not None
            for launch in attempt["launches"]
        )
    )
    expected_actual_complete = not unresolved_usage
    if budget.get("actual_complete") is not expected_actual_complete:
        diagnostics.error(
            "budget_completeness_mismatch",
            display,
            "budget.actual_complete must be false exactly when launch usage is unresolved",
            line,
        )
    actual = validate_usage_map(budget.get("actual"), diagnostics, display, "budget.actual", line) or {}
    remaining_after = validate_nullable_usage_map(
        budget.get("remaining_after"), diagnostics, display, "budget.remaining_after", line
    ) or {}
    contract_units = set(budgets)
    require_exact_units(
        requested,
        contract_units,
        diagnostics,
        display,
        "admission.requested_worst_case",
        line,
    )
    require_exact_units(
        protected,
        contract_units,
        diagnostics,
        display,
        "admission.protected_after_launch",
        line,
    )
    require_exact_units(
        cumulative_before,
        contract_units,
        diagnostics,
        display,
        "admission.cumulative_before",
        line,
    )
    require_exact_units(actual, contract_units, diagnostics, display, "budget.actual", line)
    finite_units = {
        unit
        for unit, (total, _operational_reserve, _final_test_reserve) in budgets.items()
        if total is not None
    }
    require_exact_units(
        remaining_after,
        finite_units,
        diagnostics,
        display,
        "budget.remaining_after",
        line,
    )
    if unresolved_usage and any(value is not None for value in remaining_after.values()):
        diagnostics.error(
            "unknown_budget_remaining",
            display,
            "remaining_after values must be null while launch usage is unresolved",
            line,
        )
    if not unresolved_usage and any(value is None for value in remaining_after.values()):
        diagnostics.error(
            "unknown_budget_remaining",
            display,
            "remaining_after values must be exact when all launch usage is resolved",
            line,
        )
    repetitions = result.get("repetitions")
    if not isinstance(repetitions, list):
        diagnostics.error("invalid_repetitions", display, "repetitions must be an array", line)
        repetitions = []
    if attempt is not None and attempt["launches"] and (
        admission.get("allowed") is not True or admission.get("deadline_fit") is not True
    ):
        diagnostics.error("launch_without_admission", display, "launched attempt lacks admission", line)
    deadline = None
    deadline_value = contract.get("deadline_at") if contract else None
    if isinstance(deadline_value, str) and RFC3339_UTC_RE.fullmatch(deadline_value):
        try:
            deadline = datetime.fromisoformat(deadline_value[:-1] + "+00:00")
        except ValueError:
            deadline = None
    if ended_at is not None and deadline is not None and ended_at > deadline:
        operational_overruns.append("attempt ended after the sealed run deadline")
    calculated_deadline_fit = (
        worst_case_end_at is not None
        and (deadline is None or worst_case_end_at <= deadline)
    )
    if checked_at is not None and worst_case_end_at is not None and checked_at > worst_case_end_at:
        diagnostics.error(
            "invalid_admission_window",
            display,
            "admission.checked_at must not be after worst_case_end_at",
            line,
        )
        calculated_deadline_fit = False
    if admission.get("deadline_fit") is not calculated_deadline_fit:
        diagnostics.error(
            "deadline_fit_mismatch",
            display,
            "admission.deadline_fit does not match the sealed deadline",
            line,
        )
    admission_constraints_fit = calculated_deadline_fit
    per_attempt_max = contract.get("per_attempt_max", {}) if contract else {}
    for unit, (total, operational_reserve, final_test_reserve) in budgets.items():
        before = cumulative.get(unit, 0)
        if cumulative_before.get(unit) != before:
            diagnostics.error(
                "cumulative_before_mismatch",
                display,
                f"admission.cumulative_before.{unit} differs from prior receipts",
                line,
            )
            admission_constraints_fit = False
        maximum = per_attempt_max.get(unit) if isinstance(per_attempt_max, dict) else None
        if is_nonnegative_int(maximum) and requested.get(unit, 0) > maximum:
            admission_constraints_fit = False
        actual_amount = actual.get(unit)
        if (
            is_nonnegative_int(actual_amount)
            and (
                actual_amount > requested.get(unit, 0)
                or (is_nonnegative_int(maximum) and actual_amount > maximum)
            )
        ):
            operational_overruns.append(
                f"actual {unit} exceeds the admitted worst case or per-attempt cap"
            )
        required_protection = operational_reserve + (
            final_test_reserve if role != "final" else 0
        )
        if protected.get(unit, 0) < required_protection:
            admission_constraints_fit = False
        if role == "final" and requested.get(unit, 0) > final_test_reserve:
            admission_constraints_fit = False
        if total is not None and requested.get(unit, 0) + protected.get(unit, 0) > total - before:
            admission_constraints_fit = False
    if admission.get("allowed") is not admission_constraints_fit:
        diagnostics.error(
            "admission_decision_mismatch",
            display,
            "admission.allowed does not match deadline, cap, reserve, and remaining-budget checks",
            line,
        )
    if attempt is not None:
        if admission != attempt.get("admission_record"):
            diagnostics.error(
                "admission_snapshot_mismatch",
                display,
                "result admission differs from the sealed pre-launch artifact",
                line,
            )
        admission_digest = (
            attempt.get("admission_ref", {}).get("sha256")
            if isinstance(attempt.get("admission_ref"), dict)
            else None
        )
        allocated = {unit: 0 for unit in contract_units}
        for launch in attempt["launches"]:
            if launch.get("intent", {}).get("admission_sha256") != admission_digest:
                diagnostics.error(
                    "admission_digest_mismatch",
                    display,
                    "launch intent does not bind the attempt admission record",
                    line,
                )
            allocation = launch.get("intent", {}).get("allocation")
            if isinstance(allocation, dict):
                for unit in contract_units:
                    amount = allocation.get(unit)
                    if is_nonnegative_int(amount):
                        allocated[unit] += amount
                    finished_amount = (launch.get("finished") or {}).get(
                        "actual_usage", {}
                    ).get(unit)
                    if (
                        is_nonnegative_int(finished_amount)
                        and is_nonnegative_int(amount)
                        and finished_amount > amount
                    ):
                        operational_overruns.append(
                            f"launch {launch.get('launch_id')!r} actual {unit} exceeds its allocation"
                        )
        for unit in contract_units:
            if allocated[unit] > requested.get(unit, 0):
                diagnostics.error(
                    "allocation_exceeds_admission",
                    display,
                    f"launch allocations exceed admission.requested_worst_case.{unit}",
                    line,
                )
        planned_allocation = {unit: 0 for unit in contract_units}
        for planned_launch in attempt.get("launch_plan", []):
            allocation = planned_launch.get("allocation")
            if not isinstance(allocation, dict):
                continue
            for unit in contract_units:
                amount = allocation.get(unit)
                if is_nonnegative_int(amount):
                    planned_allocation[unit] += amount
        if requested != planned_allocation:
            diagnostics.error(
                "admission_plan_mismatch",
                display,
                "admission.requested_worst_case must equal the sealed launch-plan allocation",
                line,
            )
        planned_timeout_seconds = sum(
            planned_launch.get("timeout_seconds", 0)
            for planned_launch in attempt.get("launch_plan", [])
            if is_positive_int(planned_launch.get("timeout_seconds"))
        )
        if checked_at is not None and worst_case_end_at is not None and (
            worst_case_end_at - checked_at
        ).total_seconds() < planned_timeout_seconds:
            admission_constraints_fit = False
            if admission.get("allowed") is True:
                diagnostics.error(
                    "admission_timeout_window_too_short",
                    display,
                    "allowed admission window cannot cover the serial launch-plan timeouts",
                    line,
                )
        for launch in attempt["launches"]:
            intent_timeout = (launch.get("intent") or {}).get("timeout_seconds")
            launched_at = parse_timestamp((launch.get("launched") or {}).get("at"))
            finished_at = parse_timestamp((launch.get("finished") or {}).get("at"))
            if (
                is_positive_int(intent_timeout)
                and launched_at is not None
                and finished_at is not None
                and (finished_at - launched_at).total_seconds() > intent_timeout
            ):
                operational_overruns.append(
                    f"launch {launch.get('launch_id')!r} exceeded its sealed timeout"
                )
    for unit, (total, _operational_reserve, _final_test_reserve) in budgets.items():
        before = cumulative.get(unit, 0)
        cumulative[unit] = before + actual.get(unit, 0)
        if total is not None:
            if cumulative[unit] > total:
                operational_overruns.append(
                    f"cumulative actual {unit} exceeds the sealed total"
                )
            if not unresolved_usage and remaining_after.get(unit) != total - cumulative[unit]:
                diagnostics.error("budget_remaining_mismatch", display, f"remaining_after.{unit} is incorrect", line)
    if "validation_queries" in budgets and actual.get("validation_queries") != validation_queries:
        diagnostics.error(
            "query_usage_mismatch", display, "validation_queries differs from budget.actual", line
        )
    if attempt is not None and not unresolved_usage and "evaluator_calls" in actual:
        launched_count = sum(1 for launch in attempt["launches"] if launch["launched"] is not None)
        if actual["evaluator_calls"] != launched_count:
            diagnostics.error("call_usage_mismatch", display, "evaluator_calls differs from launched count", line)
    launch_reported_timeout = bool(
        attempt is not None
        and any(
            (launch.get("finished") or {}).get("outcome") == "timeout"
            for launch in attempt["launches"]
        )
    )
    if operational_overruns:
        for message in sorted(set(operational_overruns)):
            diagnostics.warning("operational_overrun", display, message, line)
        if status != "timeout":
            diagnostics.error(
                "unclassified_operational_overrun",
                display,
                "resource or time overrun must close as non-promotional timeout",
                line,
            )
    elif status == "timeout" and not launch_reported_timeout:
        diagnostics.error(
            "unsupported_timeout_status",
            display,
            "timeout status requires a timeout launch receipt or a measured operational overrun",
            line,
        )
    if attempt is not None:
        launch_usage = {unit: 0 for unit in contract_units}
        for launch in attempt["launches"]:
            finished_usage = (
                launch.get("finished", {}).get("actual_usage", {})
                if launch.get("finished") is not None
                else {}
            )
            for unit in contract_units:
                amount = finished_usage.get(unit)
                if is_nonnegative_int(amount):
                    launch_usage[unit] += amount
        if actual != launch_usage:
            diagnostics.error(
                "usage_receipt_mismatch",
                display,
                "budget.actual must equal the sum of launch_finished actual_usage",
                line,
            )
        if protected_stages:
            gate_usage = 0
            offstage_usage = 0
            for launch in attempt["launches"]:
                usage = (
                    launch.get("finished", {}).get("actual_usage", {})
                    if launch.get("finished") is not None
                    else {}
                )
                query_use = usage.get("validation_queries", 0)
                if launch.get("intent", {}).get("stage") in protected_stages:
                    gate_usage += query_use if is_nonnegative_int(query_use) else 0
                else:
                    offstage_usage += query_use if is_nonnegative_int(query_use) else 0
            if offstage_usage != 0 or validation_queries != gate_usage:
                diagnostics.error(
                    "gate_usage_mismatch",
                    display,
                    "validation queries must be receipted exactly by protected-stage launches",
                    line,
                )
    evaluator_digest = (
        contract.get("evaluation", {}).get("evaluator_sha256") if contract else None
    )
    local_result_ids: list[str] = []
    local_result_stages: dict[str, str] = {}
    local_repetitions: dict[str, dict[str, Any]] = {}
    for rep_index, repetition in enumerate(repetitions):
        field = f"repetitions[{rep_index}]"
        if not isinstance(repetition, dict):
            diagnostics.error("invalid_repetition", display, f"{field} must be an object", line)
            continue
        result_id = require_id(repetition, "result_id", diagnostics, display, line)
        if result_id in local_result_ids:
            diagnostics.error("duplicate_result_id", display, f"duplicate result_id {result_id!r}", line)
        if isinstance(result_id, str):
            local_result_ids.append(result_id)
        if not isinstance(repetition.get("arm"), str) or repetition.get("arm") not in {"incumbent", "candidate"}:
            diagnostics.error("invalid_arm", display, f"{field}.arm is invalid", line)
        pair_id = repetition.get("pair_id")
        if pair_id is not None and (
            not isinstance(pair_id, str) or SAFE_ID_RE.fullmatch(pair_id) is None
        ):
            diagnostics.error("invalid_pair_id", display, f"{field}.pair_id is invalid", line)
        if not isinstance(repetition.get("phase"), str) or repetition.get("phase") not in {"deterministic", "exploration", "confirmation"}:
            diagnostics.error("invalid_phase", display, f"{field}.phase is invalid", line)
        repetition_stage = repetition.get("stage")
        if not isinstance(repetition_stage, str) or UNIT_RE.fullmatch(repetition_stage) is None:
            diagnostics.error("invalid_stage", display, f"{field}.stage is invalid", line)
        elif isinstance(result_id, str):
            local_result_stages[result_id] = repetition_stage
            local_repetitions[result_id] = repetition
        evaluated_commit = require_git_oid(
            repetition, "evaluated_commit", diagnostics, display, line
        )
        allowed_commits = {incumbent, commit}
        if evaluated_commit not in allowed_commits:
            diagnostics.error("unexpected_evaluated_commit", display, f"{field} commit is unrelated", line)
        arm = repetition.get("arm")
        expected_arm_commit = commit if role == "candidate" and arm == "candidate" else incumbent
        if role in ("baseline", "final") and arm != "incumbent":
            diagnostics.error(
                "invalid_arm", display, f"{field} must use incumbent arm for {role}", line
            )
        if evaluated_commit != expected_arm_commit:
            diagnostics.error(
                "arm_commit_mismatch", display, f"{field} arm does not match evaluated_commit", line
            )
        rep_primary = repetition.get("primary_value")
        if rep_primary is not None and not is_finite_number(rep_primary):
            diagnostics.error("invalid_metric", display, f"{field}.primary_value must be finite or null", line)
        if not isinstance(repetition.get("metrics"), dict):
            diagnostics.error("invalid_metric", display, f"{field}.metrics must be an object", line)
        else:
            validate_finite_tree(repetition["metrics"], diagnostics, display, f"{field}.metrics", line)
            if (
                contracted_metric not in repetition["metrics"]
                or repetition["metrics"].get(contracted_metric) != rep_primary
            ):
                diagnostics.error(
                    "primary_metric_value_mismatch",
                    display,
                    f"{field}.primary_value must equal metrics[{contracted_metric!r}]",
                    line,
                )
        rep_duration = repetition.get("duration_seconds")
        if not is_finite_number(rep_duration) or rep_duration < 0:
            diagnostics.error("invalid_duration", display, f"{field}.duration_seconds is invalid", line)
        refs: dict[str, tuple[str, str] | None] = {}
        role_paths = {
            "result_artifact": f"evidence/{result_id}.json",
            "log": f"logs/{result_id}.log",
            "effective_config": None,
            "generated_state": None,
            "evaluated_tree_snapshot": None,
        }
        role_prefixes = {
            "result_artifact": "evidence",
            "log": "logs",
            "effective_config": "snapshots",
            "generated_state": "snapshots",
            "evaluated_tree_snapshot": "snapshots",
        }
        for ref_name in (
            "result_artifact",
            "log",
            "effective_config",
            "generated_state",
            "evaluated_tree_snapshot",
        ):
            refs[ref_name] = validate_artifact_ref(
                repetition.get(ref_name),
                run_dir,
                diagnostics,
                display,
                f"{field}.{ref_name}",
                line,
                expected_prefix=role_prefixes[ref_name],
                expected_path=role_paths[ref_name],
            )
        validate_raw_evidence(
            refs.get("result_artifact"),
            repetition,
            run_dir,
            diagnostics,
            display,
            field,
            line,
        )
        validate_effective_config(
            refs.get("effective_config"),
            repetition,
            contract,
            run_dir,
            diagnostics,
            display,
            field,
            line,
        )
        validate_tree_snapshot(
            refs.get("evaluated_tree_snapshot"),
            repetition,
            run_dir,
            diagnostics,
            display,
            field,
            line,
        )
        referenced_paths = [value[0] for value in refs.values() if value is not None]
        if len(referenced_paths) != len(set(referenced_paths)):
            diagnostics.error(
                "artifact_role_collision",
                display,
                f"{field} artifact roles must use distinct paths",
                line,
            )
        if repetition.get("evaluator_sha256") != evaluator_digest:
            diagnostics.error("evaluator_digest_mismatch", display, f"{field} evaluator differs", line)
        launch = None
        if attempt is not None and isinstance(result_id, str):
            launch = next(
                (candidate for candidate in attempt["launches"] if candidate["result_id"] == result_id),
                None,
            )
            if launch is None:
                diagnostics.error("result_without_launch", display, f"{field} has no launch receipt", line)
            elif refs.get("effective_config") is not None and refs["effective_config"][1] != launch["intent"].get("effective_config_sha256"):
                diagnostics.error("config_digest_mismatch", display, f"{field} config differs from intent", line)
            if launch is not None:
                for identity_field in ("arm", "phase", "pair_id", "seed", "input_id"):
                    if repetition.get(identity_field) != launch["intent"].get(identity_field):
                        diagnostics.error(
                            "launch_evidence_identity_mismatch",
                            display,
                            f"{field}.{identity_field} differs from launch intent",
                            line,
                        )
                for artifact_field in (
                    "effective_config",
                    "generated_state",
                    "evaluated_tree_snapshot",
                ):
                    if repetition.get(artifact_field) != launch["intent"].get(
                        artifact_field
                    ):
                        diagnostics.error(
                            "launch_artifact_reference_mismatch",
                            display,
                            f"{field}.{artifact_field} differs from launch intent",
                            line,
                        )
                published = launch.get("evidence_published")
                if published is None:
                    diagnostics.error(
                        "missing_evidence_publication",
                        display,
                        f"{field} lacks an append-only evidence publication receipt",
                        line,
                    )
                else:
                    for published_field in ("result_artifact", "log"):
                        if repetition.get(published_field) != published.get(
                            published_field
                        ):
                            diagnostics.error(
                                "published_evidence_reference_mismatch",
                                display,
                                f"{field}.{published_field} differs from evidence_published",
                                line,
                            )
                    if canonical_sha256(repetition) != published.get(
                        "repetition_sha256"
                    ):
                        diagnostics.error(
                            "published_repetition_digest_mismatch",
                            display,
                            f"{field} differs from the normalized repetition publication digest",
                            line,
                        )
            if launch is not None and repetition_stage != launch["intent"].get("stage"):
                diagnostics.error(
                    "stage_binding_mismatch",
                    display,
                    f"{field} stage differs from launch intent",
                    line,
                )
            if (
                launch is not None
                and refs.get("generated_state") is not None
                and refs["generated_state"][1]
                != launch["intent"].get("generated_state_sha256")
            ):
                diagnostics.error(
                    "generated_state_digest_mismatch",
                    display,
                    f"{field} generated state differs from launch intent",
                    line,
                )
            if (
                launch is not None
                and refs.get("evaluated_tree_snapshot") is not None
                and refs["evaluated_tree_snapshot"][1]
                != launch["intent"].get("evaluated_tree_sha256")
            ):
                diagnostics.error(
                    "evaluated_tree_digest_mismatch",
                    display,
                    f"{field} evaluated tree differs from launch intent",
                    line,
                )
        if isinstance(result_id, str) and all(value is not None for value in refs.values()):
            identity = (
                evaluated_commit,
                repetition.get("arm"),
                repetition.get("phase"),
                repetition.get("stage"),
                repetition.get("pair_id"),
                repetition.get("seed"),
                repetition.get("input_id"),
                repetition.get("evaluator_sha256"),
                refs["effective_config"][1],
                refs["generated_state"][1],
                refs["result_artifact"][1],
                refs["log"][1],
            )
            prior = result_ids_seen.get(result_id)
            if prior is not None and prior != identity:
                diagnostics.error("result_id_conflict", display, f"result_id {result_id!r} changed identity", line)
            result_ids_seen[result_id] = identity
    validate_stage_evidence(
        decision.get("stage_results"),
        decision.get("stage_evidence"),
        result_required_stages,
        local_result_stages,
        diagnostics,
        display,
        line,
    )
    stage_results = decision.get("stage_results") if isinstance(decision, dict) else {}
    stage_evidence = decision.get("stage_evidence") if isinstance(decision, dict) else {}
    if isinstance(stage_results, dict) and isinstance(stage_evidence, dict):
        for stage in result_required_stages:
            citations = stage_evidence.get(stage, [])
            if not isinstance(citations, list):
                continue
            for cited_id in citations:
                cited = local_repetitions.get(cited_id)
                if cited is None:
                    continue
                metrics = cited.get("metrics")
                for metric_id in result_required_telemetry.get(stage, []):
                    if (
                        not isinstance(metrics, dict)
                        or metric_id not in metrics
                        or not is_finite_number(metrics[metric_id])
                    ):
                        diagnostics.error(
                            "missing_required_telemetry",
                            display,
                            f"stage {stage!r} evidence {cited_id!r} lacks finite {metric_id!r}",
                            line,
                        )
            if stage_results.get(stage) in ("pass", "fail"):
                expected_arm = "candidate" if role == "candidate" else "incumbent"
                if not any(
                    local_repetitions.get(cited_id, {}).get("arm") == expected_arm
                    for cited_id in citations
                ):
                    diagnostics.error(
                        "stage_arm_evidence_missing",
                        display,
                        f"conclusive stage {stage!r} lacks {expected_arm}-arm evidence",
                        line,
                    )
            if (
                stage in protected_stages
                and stage_results.get(stage) in ("pass", "fail")
                and attempt is not None
            ):
                for cited_id in citations:
                    cited_launch = next(
                        (
                            launch
                            for launch in attempt["launches"]
                            if launch.get("result_id") == cited_id
                        ),
                        None,
                    )
                    cited_usage = (
                        (cited_launch.get("finished") or {}).get("actual_usage", {})
                        if cited_launch is not None
                        else {}
                    )
                    if not is_positive_int(cited_usage.get("validation_queries")):
                        diagnostics.error(
                            "unreceipted_gate_evidence",
                            display,
                            f"protected-stage evidence {cited_id!r} lacks positive query use",
                            line,
                        )
    target_arm = "candidate" if role == "candidate" else "incumbent"
    acceptance_values = [
        float(repetition["primary_value"])
        for repetition in repetitions
        if isinstance(repetition, dict)
        and repetition.get("stage") == acceptance_stage
        and repetition.get("arm") == target_arm
        and is_finite_number(repetition.get("primary_value"))
    ]
    measurement_mode = measurement.get("mode") if isinstance(measurement, dict) else None
    paired_improvement: float | None = None
    if status == "keep" and measurement_mode == "noisy":
        target_repetitions = [
            repetition
            for repetition in repetitions
            if isinstance(repetition, dict)
            and repetition.get("stage") == acceptance_stage
            and repetition.get("arm") == target_arm
            and is_finite_number(repetition.get("primary_value"))
        ]
        phase_counts = {
            phase: sum(
                1 for repetition in target_repetitions if repetition.get("phase") == phase
            )
            for phase in ("exploration", "confirmation")
        }
        required_exploration = measurement.get("exploration_repetitions")
        required_confirmation = measurement.get("confirmation_repetitions")
        if (
            not is_nonnegative_int(required_exploration)
            or not is_nonnegative_int(required_confirmation)
            or phase_counts["exploration"] < required_exploration
            or phase_counts["confirmation"] < required_confirmation
            or any(
                repetition.get("phase") not in ("exploration", "confirmation")
                for repetition in target_repetitions
            )
        ):
            diagnostics.error(
                "insufficient_noisy_phases",
                display,
                "noisy keep lacks the sealed target-arm exploration and confirmation evidence",
                line,
            )
        seen_target_identities: set[tuple[Any, Any]] = set()
        for repetition in target_repetitions:
            identity = (repetition.get("seed"), repetition.get("input_id"))
            if identity == (None, None):
                diagnostics.error(
                    "missing_noisy_identity",
                    display,
                    "every noisy keep repetition requires a seed or input_id",
                    line,
                )
            elif identity in seen_target_identities:
                diagnostics.error(
                    "reused_noisy_identity",
                    display,
                    "noisy target-arm repetitions must use unique seed/input identities",
                    line,
                )
            seen_target_identities.add(identity)
    if role == "candidate" and measurement_mode == "noisy":
        pairs: dict[str, dict[str, dict[str, Any]]] = {}
        acceptance_repetitions = [
            repetition
            for repetition in repetitions
            if isinstance(repetition, dict)
            and repetition.get("stage") == acceptance_stage
            and repetition.get("arm") in ("incumbent", "candidate")
        ]
        for repetition in acceptance_repetitions:
            pair_id = repetition.get("pair_id")
            if not isinstance(pair_id, str) or SAFE_ID_RE.fullmatch(pair_id) is None:
                diagnostics.error(
                    "unpaired_noisy_evidence",
                    display,
                    "noisy candidate acceptance repetitions require pair_id",
                    line,
                )
                continue
            arm = repetition["arm"]
            pair = pairs.setdefault(pair_id, {})
            if arm in pair:
                diagnostics.error(
                    "duplicate_pair_arm",
                    display,
                    f"pair {pair_id!r} contains duplicate {arm} arms",
                    line,
                )
            pair[arm] = repetition
        direction = objective.get("direction") if isinstance(objective, dict) else None
        phase_deltas: dict[str, list[float]] = {
            "exploration": [],
            "confirmation": [],
        }
        seen_input_identities: set[tuple[Any, Any]] = set()
        for pair_id, pair in pairs.items():
            if set(pair) != {"incumbent", "candidate"}:
                diagnostics.error(
                    "incomplete_pair",
                    display,
                    f"pair {pair_id!r} must contain one incumbent and one candidate arm",
                    line,
                )
                continue
            incumbent_rep = pair["incumbent"]
            candidate_rep = pair["candidate"]
            for ref_name in ("effective_config", "generated_state"):
                incumbent_ref = incumbent_rep.get(ref_name)
                candidate_ref = candidate_rep.get(ref_name)
                incumbent_digest = (
                    incumbent_ref.get("sha256") if isinstance(incumbent_ref, dict) else None
                )
                candidate_digest = (
                    candidate_ref.get("sha256") if isinstance(candidate_ref, dict) else None
                )
                if incumbent_digest != candidate_digest:
                    diagnostics.error(
                        "pair_context_mismatch",
                        display,
                        f"pair {pair_id!r} arms must share the {ref_name} digest",
                        line,
                    )
            if (
                incumbent_rep.get("seed") != candidate_rep.get("seed")
                or incumbent_rep.get("input_id") != candidate_rep.get("input_id")
                or incumbent_rep.get("phase") != candidate_rep.get("phase")
            ):
                diagnostics.error(
                    "pair_identity_mismatch",
                    display,
                    f"pair {pair_id!r} arms must share seed, input_id, and phase",
                    line,
                )
                continue
            phase = candidate_rep.get("phase")
            if phase not in ("exploration", "confirmation"):
                diagnostics.error(
                    "invalid_noisy_phase",
                    display,
                    f"noisy pair {pair_id!r} must be exploration or confirmation",
                    line,
                )
                continue
            input_identity = (
                candidate_rep.get("seed"),
                candidate_rep.get("input_id"),
            )
            if input_identity == (None, None):
                diagnostics.error(
                    "missing_pair_identity",
                    display,
                    f"noisy pair {pair_id!r} requires a seed or input_id",
                    line,
                )
                continue
            if input_identity in seen_input_identities:
                diagnostics.error(
                    "reused_pair_identity",
                    display,
                    f"noisy pair {pair_id!r} reuses a seed/input identity",
                    line,
                )
                continue
            seen_input_identities.add(input_identity)
            incumbent_value = incumbent_rep.get("primary_value")
            candidate_value = candidate_rep.get("primary_value")
            if not (
                is_finite_number(incumbent_value) and is_finite_number(candidate_value)
            ):
                diagnostics.error(
                    "invalid_pair_metric",
                    display,
                    f"pair {pair_id!r} requires finite arm metrics",
                    line,
                )
                continue
            delta = (
                candidate_value - incumbent_value
                if direction == "maximize"
                else incumbent_value - candidate_value
            )
            phase_deltas[phase].append(float(delta))
        phase_means = {
            phase: sum(deltas) / len(deltas)
            for phase, deltas in phase_deltas.items()
            if deltas
        }
        if set(phase_means) == {"exploration", "confirmation"}:
            paired_improvement = min(
                phase_means["exploration"], phase_means["confirmation"]
            )
        required_confirmation = (
            measurement.get("confirmation_repetitions")
            if isinstance(measurement, dict)
            else None
        )
        required_exploration = (
            measurement.get("exploration_repetitions")
            if isinstance(measurement, dict)
            else None
        )
        if status == "keep" and (
            not is_nonnegative_int(required_confirmation)
            or not is_nonnegative_int(required_exploration)
            or len(phase_deltas["confirmation"]) < required_confirmation
            or len(phase_deltas["exploration"]) < required_exploration
        ):
            diagnostics.error(
                "insufficient_noisy_pairs",
                display,
                "noisy keep lacks sealed exploration or fresh-confirmation pairs",
                line,
            )
        if status == "keep" and isinstance(decision.get("stage_evidence"), dict):
            cited = set(decision["stage_evidence"].get(acceptance_stage, []))
            expected = {
                repetition.get("result_id") for repetition in acceptance_repetitions
            }
            if not expected.issubset(cited):
                diagnostics.error(
                    "missing_pair_evidence",
                    display,
                    "acceptance-stage evidence must cite every noisy pair arm",
                    line,
                )
    minimum_repetitions = (
        measurement.get("minimum_repetitions") if isinstance(measurement, dict) else None
    )
    if status == "keep" and (
        not is_positive_int(minimum_repetitions)
        or len(acceptance_values) < minimum_repetitions
    ):
        diagnostics.error(
            "insufficient_repetitions",
            display,
            "keep lacks the sealed minimum acceptance-split repetitions",
            line,
        )
    computed_primary: float | None = None
    if acceptance_values:
        if contracted_aggregation == "single":
            if len(acceptance_values) != 1:
                diagnostics.error(
                    "aggregation_input_mismatch",
                    display,
                    "single aggregation requires exactly one acceptance value",
                    line,
                )
            else:
                computed_primary = acceptance_values[0]
        elif contracted_aggregation == "mean":
            computed_primary = sum(acceptance_values) / len(acceptance_values)
        elif contracted_aggregation == "median":
            ordered = sorted(acceptance_values)
            midpoint = len(ordered) // 2
            computed_primary = (
                ordered[midpoint]
                if len(ordered) % 2
                else (ordered[midpoint - 1] + ordered[midpoint]) / 2
            )
        elif contracted_aggregation == "min":
            computed_primary = min(acceptance_values)
        elif contracted_aggregation == "max":
            computed_primary = max(acceptance_values)
    if computed_primary is None:
        if primary_value is not None:
            diagnostics.error(
                "aggregate_metric_mismatch",
                display,
                "primary_value must be null without acceptance-split evidence",
                line,
            )
    elif not is_finite_number(primary_value) or not math.isclose(
        float(primary_value), computed_primary, rel_tol=1e-12, abs_tol=1e-12
    ):
        diagnostics.error(
            "aggregate_metric_mismatch",
            display,
            "primary_value does not match the sealed aggregation of raw evidence",
            line,
        )
    split_metrics = result.get("split_metrics")
    if (
        computed_primary is not None
        and isinstance(split_metrics, dict)
        and (
                not is_finite_number(split_metrics.get(acceptance_stage))
                or not math.isclose(
                    float(split_metrics[acceptance_stage]),
                computed_primary,
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
        )
    ):
        diagnostics.error(
            "split_metric_mismatch",
            display,
            "acceptance split metric differs from aggregated raw evidence",
            line,
        )
    if status == "keep" and role == "candidate":
        incumbent_primary = (
            confirmed_primary.get(incumbent) if isinstance(incumbent, str) else None
        )
        direction = objective.get("direction") if isinstance(objective, dict) else None
        minimum_delta = objective.get("minimum_delta") if isinstance(objective, dict) else None
        if measurement_mode == "noisy":
            if paired_improvement is None or not is_finite_number(minimum_delta):
                diagnostics.error(
                    "primary_acceptance_failed",
                    display,
                    "noisy candidate keep lacks a valid paired improvement",
                    line,
                )
            elif paired_improvement < minimum_delta:
                diagnostics.error(
                    "primary_acceptance_failed",
                    display,
                    "noisy candidate keep does not satisfy the paired minimum delta",
                    line,
                )
        elif incumbent_primary is None:
            diagnostics.error(
                "missing_incumbent_metric",
                display,
                "candidate promotion requires a confirmed incumbent metric",
                line,
            )
        elif computed_primary is not None and is_finite_number(minimum_delta):
            improvement = (
                computed_primary - incumbent_primary
                if direction == "maximize"
                else incumbent_primary - computed_primary
            )
            if direction not in ("maximize", "minimize") or improvement < minimum_delta:
                diagnostics.error(
                    "primary_acceptance_failed",
                    display,
                    "candidate keep does not satisfy the sealed direction and minimum delta",
                    line,
                )
    if status == "keep" and role == "final":
        direction = objective.get("direction") if isinstance(objective, dict) else None
        minimum_value = (
            final_test.get("minimum_value")
            if isinstance(final_test, dict)
            else None
        )
        maximum_value = (
            final_test.get("maximum_value")
            if isinstance(final_test, dict)
            else None
        )
        threshold_passes = bool(
            computed_primary is not None
            and (
                direction == "maximize"
                and is_finite_number(minimum_value)
                and computed_primary >= minimum_value
                or direction == "minimize"
                and is_finite_number(maximum_value)
                and computed_primary <= maximum_value
            )
        )
        if not threshold_passes:
            diagnostics.error(
                "final_acceptance_failed",
                display,
                "final keep does not satisfy its sealed stage-specific absolute threshold",
                line,
            )
        comparable = (
            final_test.get("comparable_to_acceptance_split") is True
            if isinstance(final_test, dict)
            else False
        )
        maximum_regression = (
            final_test.get("maximum_regression")
            if isinstance(final_test, dict)
            else None
        )
        if comparable and maximum_regression is not None:
            incumbent_primary = (
                confirmed_primary.get(incumbent)
                if isinstance(incumbent, str)
                else None
            )
            if incumbent_primary is None or computed_primary is None:
                diagnostics.error(
                    "final_acceptance_failed",
                    display,
                    "comparable final keep lacks a confirmed acceptance-split reference metric",
                    line,
                )
            else:
                regression = (
                    incumbent_primary - computed_primary
                    if direction == "maximize"
                    else computed_primary - incumbent_primary
                )
                if regression > maximum_regression:
                    diagnostics.error(
                        "final_acceptance_failed",
                        display,
                        "final keep exceeds the sealed cross-split maximum regression",
                        line,
                    )
    if attempt is not None:
        expected_ids = (
            attempt.get("evaluated", {}).get("result_ids", [])
            if attempt.get("evaluated") is not None
            else []
        )
        if local_result_ids != expected_ids:
            diagnostics.error("result_repetition_ids", display, "repetitions do not match evaluated event", line)
        expected_commit = (
            (
                attempt["candidate_commit"]
                if attempt.get("candidate_commit") is not None
                else attempt["pre_attempt_head_commit"]
            )
            if attempt["role"] == "candidate"
            else attempt["incumbent_commit"]
        )
        if attempt_id != attempt["attempt_id"] or result.get("experiment_id") != attempt["experiment_id"]:
            diagnostics.error("result_attempt_mismatch", display, "result identity differs from journal", line)
        if result.get("design") != attempt.get("design"):
            diagnostics.error(
                "design_binding_mismatch",
                display,
                "result design reference differs from the prepared event",
                line,
            )
        if result.get("role") != attempt["role"] or commit != expected_commit or incumbent != attempt["incumbent_commit"]:
            diagnostics.error("result_commit_mismatch", display, "result role or commits differ from journal", line)
        if pre_attempt_head != attempt.get("pre_attempt_head_commit"):
            diagnostics.error(
                "result_commit_mismatch",
                display,
                "result pre-attempt HEAD differs from journal",
                line,
            )
        journal_decision = attempt.get("decision") or {}
        for key in ("status", "lane", "evidence_maturity"):
            if result.get(key) != journal_decision.get(key):
                diagnostics.error("decision_mismatch", display, f"result {key} differs from journal", line)
        if decision.get("decision_seq") != attempt.get("decision_seq"):
            diagnostics.error("decision_reference", display, "result decision_seq differs", line)
        if decision.get("journal_event_sha256") != canonical_sha256(journal_decision):
            diagnostics.error(
                "decision_digest_mismatch", display, "result does not bind the full decided event", line
            )
        if decision.get("accepted") != journal_decision.get("accepted"):
            diagnostics.error("decision_mismatch", display, "result accepted differs", line)
        if decision.get("incumbent_after") != journal_decision.get("incumbent_after"):
            diagnostics.error("decision_mismatch", display, "result incumbent_after differs", line)
        rollback_required = journal_decision.get("rollback_action") == "revert"
        if decision.get("rollback_required") is not rollback_required:
            diagnostics.error("decision_mismatch", display, "rollback_required differs", line)
        if decision.get("stage_results") != journal_decision.get("stage_results"):
            diagnostics.error("decision_mismatch", display, "stage_results differ from journal", line)
        if decision.get("stage_evidence") != journal_decision.get("stage_evidence"):
            diagnostics.error("decision_mismatch", display, "stage_evidence differs from journal", line)
        if decision.get("rule_evaluation") != journal_decision.get("rule_evaluation"):
            diagnostics.error("decision_mismatch", display, "rule_evaluation differs from journal", line)
        if result.get("acceptance_reason") != journal_decision.get("reason"):
            diagnostics.error("decision_mismatch", display, "acceptance_reason differs from journal", line)
        journal_rollback = attempt.get("rolled_back")
        expected_rollback = journal_rollback.get("rollback_commit") if journal_rollback else None
        if rollback_commit != expected_rollback:
            diagnostics.error("rollback_mismatch", display, "result rollback_commit differs", line)
    if status == "keep" and role == "candidate" and not any(
        isinstance(repetition, dict) and repetition.get("arm") == "candidate"
        for repetition in repetitions
    ):
        diagnostics.error(
            "promotion_without_candidate_evidence",
            display,
            "candidate keep requires at least one completed candidate repetition",
            line,
        )
    finding = result.get("finding")
    if not isinstance(finding, dict):
        diagnostics.error("invalid_finding", display, "finding must be an object", line)
    else:
        if not isinstance(finding.get("type"), str) or finding.get("type") not in {"positive", "negative", "diagnostic", "uncertain", "procedural"}:
            diagnostics.error("invalid_finding", display, "finding.type is invalid", line)
        for key in ("summary", "scope", "caveats"):
            require_string(finding, key, diagnostics, display, line)
        if not isinstance(finding.get("next_action"), str) or finding.get("next_action") not in {"reuse", "validate", "avoid", "diagnose", "preserve", "archive"}:
            diagnostics.error("invalid_finding", display, "finding.next_action is invalid", line)
        citations = finding.get("result_ids")
        if not isinstance(citations, list) or not all(item in local_result_ids for item in citations):
            diagnostics.error("invalid_finding", display, "finding.result_ids must cite local results", line)
    if result.get("failure") is not None and not isinstance(result.get("failure"), dict):
        diagnostics.error("invalid_failure", display, "failure must be an object or null", line)
    if not isinstance(result.get("leakage_audit"), str) or result.get("leakage_audit") not in {"pass", "fail", "not_run"}:
        diagnostics.error("invalid_leakage_audit", display, "leakage_audit is invalid", line)
    if role == "candidate" and attempt is not None:
        clearance = attempt.get("integrity_cleared")
        if clearance is not None and (
            result.get("leakage_audit") != clearance.get("leakage_audit")
            or result.get("constraint_results")
            != clearance.get("constraint_results")
        ):
            diagnostics.error(
                "integrity_summary_mismatch",
                display,
                "result integrity summaries differ from the pre-gate clearance",
                line,
            )
    if status == "keep" and result.get("leakage_audit") != "pass":
        diagnostics.error("illegal_promotion", display, "keep requires leakage audit pass", line)


def validate_results(
    records: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    run_dir: Path,
    contract: dict[str, Any] | None,
    contract_digest: str | None,
    required_stages: list[str],
    budgets: dict[str, tuple[int | None, int, int]],
    diagnostics: Diagnostics,
    path: Path,
) -> tuple[dict[str, tuple[int, dict[str, Any], str]], dict[str, int], str | None]:
    by_attempt: dict[str, tuple[int, dict[str, Any], str]] = {}
    cumulative = {unit: 0 for unit in budgets}
    evidence_identities: dict[str, tuple[Any, ...]] = {}
    design_digests: dict[str, str] = {}
    confirmed_parents: dict[str, str] = {}
    prior_result_ids: set[str] = set()
    confirmed_primary: dict[str, float] = {}
    incumbent = contract.get("baseline_commit") if contract else None
    for index, result in enumerate(records):
        line = index + 1
        attempt = attempts[index] if index < len(attempts) else None
        validate_result_record(
            result,
            line,
            attempt,
            run_dir,
            contract,
            contract_digest,
            required_stages,
            budgets,
            cumulative,
            evidence_identities,
            design_digests,
            confirmed_parents,
            prior_result_ids,
            confirmed_primary,
            diagnostics,
            path,
        )
        attempt_id = result.get("attempt_id")
        digest = canonical_sha256(result)
        if isinstance(attempt_id, str):
            if attempt_id in by_attempt:
                diagnostics.error("duplicate_attempt_result", str(path), f"duplicate result for {attempt_id}", line)
            by_attempt[attempt_id] = (index, result, digest)
        if result.get("incumbent_commit") != incumbent:
            diagnostics.error("incumbent_chain", str(path), "incumbent chain is broken", line)
        if result.get("status") == "keep":
            incumbent = result.get("commit")
        repetitions = result.get("repetitions")
        if isinstance(repetitions, list):
            for repetition in repetitions:
                if isinstance(repetition, dict):
                    result_id = repetition.get("result_id")
                    if isinstance(result_id, str) and SAFE_ID_RE.fullmatch(result_id):
                        prior_result_ids.add(result_id)
        if (
            result.get("status") == "keep"
            and result.get("lane") == "confirmed"
            and result.get("role") != "final"
            and isinstance(attempt_id, str)
            and isinstance(result.get("commit"), str)
        ):
            confirmed_parents[attempt_id] = result["commit"]
            if is_finite_number(result.get("primary_value")):
                confirmed_primary[result["commit"]] = float(result["primary_value"])
    return by_attempt, cumulative, incumbent


def validate_boundary(
    boundary: dict[str, Any],
    attempt: dict[str, Any],
    result_info: tuple[int, dict[str, Any], str],
    contract_digest: str | None,
    journal_raw: bytes,
    results_raw: bytes,
    diagnostics: Diagnostics,
    path: Path,
) -> bool:
    index, _result, digest = result_info
    valid = True
    expected = {
        "schema_version": 1,
        "kind": "attempt-boundary",
        "attempt_id": attempt["attempt_id"],
        "experiment_id": attempt["experiment_id"],
        "contract_sha256": contract_digest,
        "decision_seq": attempt.get("decision_seq"),
        "finalized_seq": attempt.get("finalized_seq"),
        "result_index": index,
        "result_sha256": digest,
        "journal_prefix_sha256": jsonl_prefix_sha256(
            journal_raw, attempt.get("finalized_seq", 0)
        ),
        "results_prefix_sha256": jsonl_prefix_sha256(results_raw, index + 1),
    }
    expected_keys = set(expected) | {"publication_nonce", "finalized_at"}
    if set(boundary) != expected_keys:
        diagnostics.error(
            "boundary_schema_mismatch",
            str(path),
            f"attempt boundary keys must be exactly {sorted(expected_keys)}",
        )
        valid = False
    for key, value in expected.items():
        if boundary.get(key) != value:
            diagnostics.error("boundary_mismatch", str(path), f"{key} does not match canonical state")
            valid = False
    if require_hex64(boundary, "publication_nonce", diagnostics, str(path)) is None:
        valid = False
    boundary_at = validate_timestamp(
        boundary.get("finalized_at"), diagnostics, str(path), "finalized_at"
    )
    journal_finalized_at = parse_timestamp(
        (attempt.get("finalized") or {}).get("at")
    )
    if (
        boundary_at is not None
        and journal_finalized_at is not None
        and boundary_at < journal_finalized_at
    ):
        diagnostics.error(
            "boundary_time_regression",
            str(path),
            "attempt boundary cannot predate its finalized journal event",
        )
        valid = False
    return valid


def reconcile_attempts(
    run_dir: Path,
    attempts: list[dict[str, Any]],
    results_by_attempt: dict[str, tuple[int, dict[str, Any], str]],
    contract_digest: str | None,
    journal_raw: bytes,
    results_raw: bytes,
    stop_requested: dict[str, Any] | None,
    allow_incomplete: bool,
    diagnostics: Diagnostics,
) -> tuple[list[str], str | None, str | None, str | None]:
    finalized: list[str] = []
    incomplete: list[tuple[int, dict[str, Any], str, str]] = []
    boundaries_dir = run_dir / "boundaries"
    if boundaries_dir.is_symlink():
        diagnostics.error(
            "symlink_forbidden", str(boundaries_dir), "boundaries must not be a symlink"
        )
    known_boundary_paths: set[Path] = set()
    for index, attempt in enumerate(attempts):
        attempt_id = attempt["attempt_id"]
        result_info = results_by_attempt.get(attempt_id)
        if not isinstance(attempt_id, str) or SAFE_ID_RE.fullmatch(attempt_id) is None:
            continue
        boundary_path = boundaries_dir / f"{attempt_id}.json"
        known_boundary_paths.add(boundary_path)
        boundary = load_json_file(boundary_path, diagnostics, required=False)
        if attempt.get("finalized") is not None:
            if result_info is None:
                diagnostics.error(
                    "finalized_without_result",
                    str(run_dir / "journal.jsonl"),
                    f"{attempt_id} finalized without its result row",
                    attempt.get("finalized_seq"),
                )
                continue
            result_index, _result, digest = result_info
            event = attempt["finalized"]
            if event.get("result_index") != result_index or event.get("result_sha256") != digest:
                diagnostics.error(
                    "finalized_result_mismatch",
                    str(run_dir / "journal.jsonl"),
                    f"{attempt_id} finalization does not bind its result",
                    attempt.get("finalized_seq"),
                )
                continue
            if boundary is None:
                incomplete.append((index, attempt, "finalized", "publish_boundary"))
            elif validate_boundary(
                boundary,
                attempt,
                result_info,
                contract_digest,
                journal_raw,
                results_raw,
                diagnostics,
                boundary_path,
            ):
                finalized.append(attempt_id)
        else:
            if boundary is not None:
                diagnostics.error(
                    "boundary_without_finalization",
                    str(boundary_path),
                    "boundary exists without finalized journal event",
                )
            if result_info is not None:
                if attempt.get("decision") is None:
                    diagnostics.error(
                        "orphan_result",
                        str(run_dir / "results.jsonl"),
                        f"{attempt_id} has a result before decided",
                        result_info[0] + 1,
                    )
                elif attempt.get("decision", {}).get("rollback_action") == "revert" and attempt.get("rolled_back") is None:
                    diagnostics.error(
                        "result_before_rollback",
                        str(run_dir / "results.jsonl"),
                        f"{attempt_id} result precedes required rollback",
                        result_info[0] + 1,
                    )
                else:
                    incomplete.append((index, attempt, "result_published", "append_finalized"))
            elif attempt.get("decision") is not None:
                if attempt.get("decision", {}).get("rollback_action") == "revert" and attempt.get("rolled_back") is None:
                    incomplete.append((index, attempt, "decided", "execute_recorded_rollback"))
                else:
                    incomplete.append((index, attempt, attempt.get("phase", "decided"), "append_result"))
            elif attempt.get("evaluated") is not None:
                incomplete.append((index, attempt, "evaluated", "apply_contract_decision"))
            elif attempt.get("launches"):
                launch_states = {launch.get("state") for launch in attempt["launches"]}
                if "launched" in launch_states:
                    action = "reclaim_or_drain_launch"
                    phase = "launched"
                elif "intent" in launch_states:
                    action = "prove_absence_or_append_launch_unresolved"
                    phase = "launch_intent"
                elif any(
                    launch.get("state") == "finished"
                    and (launch.get("finished") or {}).get("cleanup_verified") is not True
                    and launch.get("cleanup_confirmed") is None
                    and launch.get("cleanup_unresolved") is None
                    for launch in attempt["launches"]
                ):
                    action = "verify_cleanup_or_append_cleanup_unresolved"
                    phase = "launch_finished_cleanup_pending"
                elif any(
                    launch.get("state") == "finished"
                    and (launch.get("finished") or {}).get("outcome") == "completed"
                    and launch.get("cleanup_unresolved") is None
                    and launch.get("evidence_published") is None
                    for launch in attempt["launches"]
                ):
                    action = "normalize_and_publish_evidence"
                    phase = "evidence_publication_pending"
                else:
                    launched_count = len(attempt["launches"])
                    launch_plan = attempt.get("launch_plan") or []
                    current_stage = (
                        (attempt["launches"][-1].get("intent") or {}).get("stage")
                    )
                    next_planned_stage = (
                        launch_plan[launched_count].get("stage")
                        if launched_count < len(launch_plan)
                        and attempt.get("tail_cancelled") is None
                        else None
                    )
                    checkpoint = attempt.get("stage_checkpoints", {}).get(
                        current_stage
                    )
                    if (
                        stop_requested is not None
                        and launched_count < len(launch_plan)
                        and attempt.get("tail_cancelled") is None
                    ):
                        action = "append_plan_tail_cancelled"
                        phase = "plan_tail_cancellation_pending"
                    elif next_planned_stage == current_stage:
                        action = "append_next_launch_intent"
                        phase = "stage_launches_pending"
                    elif checkpoint is None:
                        action = "append_stage_completed"
                        phase = "stage_checkpoint_pending"
                    elif next_planned_stage is not None and checkpoint.get(
                        "outcome"
                    ) == "pass":
                        action = "append_next_launch_intent"
                        phase = "next_stage_launch_pending"
                    else:
                        action = "append_evaluated"
                        phase = "launches_terminal"
                incomplete.append((index, attempt, phase, action))
            else:
                if attempt.get("phase") == "committed":
                    incomplete.append(
                        (index, attempt, "committed", "reconcile_committed_candidate")
                    )
                else:
                    incomplete.append(
                        (index, attempt, "prepared", "reconcile_prepared_attempt")
                    )
    for attempt_id, (index, _result, _digest) in results_by_attempt.items():
        if index >= len(attempts) or attempts[index].get("attempt_id") != attempt_id:
            diagnostics.error(
                "orphan_result",
                str(run_dir / "results.jsonl"),
                f"result {attempt_id!r} has no matching journal attempt",
                index + 1,
            )
    if boundaries_dir.is_dir():
        for boundary_path in boundaries_dir.glob("*.json"):
            if boundary_path not in known_boundary_paths:
                diagnostics.error("orphan_boundary", str(boundary_path), "boundary has no journal attempt")
    resume_action: str | None = None
    active_attempt: str | None = None
    active_phase: str | None = None
    if incomplete:
        if len(incomplete) != 1:
            diagnostics.error("multiple_incomplete_attempts", str(run_dir), "only one trailing attempt may be incomplete")
        else:
            index, attempt, phase, action = incomplete[0]
            if index != len(attempts) - 1:
                diagnostics.error("nontrailing_incomplete", str(run_dir), "incomplete attempt is not last")
            elif not allow_incomplete:
                diagnostics.error(
                    "incomplete_not_allowed",
                    str(run_dir),
                    "rerun with --allow-incomplete to classify the trailing attempt",
                )
            else:
                resume_action = action
                active_attempt = attempt["attempt_id"]
                active_phase = phase
    return finalized, active_attempt, active_phase, resume_action


def validate_pending_designs(
    run_dir: Path,
    attempts: list[dict[str, Any]],
    results_by_attempt: dict[str, tuple[int, dict[str, Any], str]],
    finalized_attempts: list[str],
    contract: dict[str, Any] | None,
    budgets: dict[str, tuple[int | None, int, int]],
    diagnostics: Diagnostics,
) -> None:
    finalized = set(finalized_attempts)
    confirmed_parents: dict[str, str] = {}
    prior_result_ids: set[str] = set()
    for attempt in attempts:
        attempt_id = attempt.get("attempt_id")
        result_info = results_by_attempt.get(attempt_id)
        if attempt.get("role") == "candidate" and result_info is None:
            reference = validate_artifact_ref(
                attempt.get("design"),
                run_dir,
                diagnostics,
                str(run_dir / "journal.jsonl"),
                "design",
                attempt.get("prepared_seq"),
                expected_path=f"designs/{attempt.get('experiment_id')}.json",
            )
            validate_design_record(
                reference,
                {
                    "experiment_id": attempt.get("experiment_id"),
                    "incumbent_commit": attempt.get("incumbent_commit"),
                },
                run_dir,
                contract,
                budgets,
                confirmed_parents,
                prior_result_ids,
                diagnostics,
                str(run_dir / "journal.jsonl"),
                attempt.get("prepared_seq") or 1,
            )
        if result_info is None or attempt_id not in finalized:
            continue
        result = result_info[1]
        repetitions = result.get("repetitions")
        if isinstance(repetitions, list):
            for repetition in repetitions:
                if isinstance(repetition, dict):
                    result_id = repetition.get("result_id")
                    if isinstance(result_id, str) and SAFE_ID_RE.fullmatch(result_id):
                        prior_result_ids.add(result_id)
        if (
            result.get("status") == "keep"
            and result.get("lane") == "confirmed"
            and result.get("role") != "final"
            and isinstance(attempt_id, str)
            and isinstance(result.get("commit"), str)
        ):
            confirmed_parents[attempt_id] = result["commit"]


def validate_git(
    git_root: Path,
    run_dir: Path,
    contract: dict[str, Any] | None,
    attempts: list[dict[str, Any]],
    results: list[dict[str, Any]],
    run_stopped: dict[str, Any] | None,
    diagnostics: Diagnostics,
) -> str | None:
    root = git_root.expanduser().resolve()

    def invoke(*args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", "-C", str(root), *args],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as error:
            diagnostics.error("git_unavailable", str(root), str(error))
            return subprocess.CompletedProcess(args, 127, "", str(error))

    probe = invoke("rev-parse", "--is-inside-work-tree")
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        diagnostics.error(
            "git_repository_required",
            str(root),
            "run state must belong to a readable Git worktree",
        )
        return None
    head_result = invoke("rev-parse", "HEAD")
    if head_result.returncode != 0 or GIT_OID_RE.fullmatch(head_result.stdout.strip()) is None:
        diagnostics.error("git_head_unreadable", str(root), "unable to resolve Git HEAD")
        actual_head = None
    else:
        actual_head = head_result.stdout.strip()
    object_ids: set[str] = set()
    if contract is not None and isinstance(contract.get("baseline_commit"), str):
        object_ids.add(contract["baseline_commit"])
    for attempt in attempts:
        for key in ("incumbent_commit", "pre_attempt_head_commit", "candidate_commit"):
            value = attempt.get(key)
            if isinstance(value, str) and GIT_OID_RE.fullmatch(value):
                object_ids.add(value)
        rollback = attempt.get("rolled_back")
        if isinstance(rollback, dict) and isinstance(rollback.get("rollback_commit"), str):
            object_ids.add(rollback["rollback_commit"])
    for result in results:
        for key in ("commit", "incumbent_commit", "pre_attempt_head_commit", "rollback_commit"):
            value = result.get(key)
            if isinstance(value, str) and GIT_OID_RE.fullmatch(value):
                object_ids.add(value)
    if run_stopped is not None:
        for key in ("final_incumbent_commit", "final_head_commit"):
            value = run_stopped.get(key)
            if isinstance(value, str) and GIT_OID_RE.fullmatch(value):
                object_ids.add(value)
    for object_id in sorted(object_ids):
        check = invoke("cat-file", "-e", f"{object_id}^{{commit}}")
        if check.returncode != 0:
            diagnostics.error(
                "missing_git_commit", str(root), f"referenced commit does not exist: {object_id}"
            )
    def tree_of(commit: Any) -> str | None:
        if not isinstance(commit, str) or GIT_OID_RE.fullmatch(commit) is None:
            return None
        resolved = invoke("rev-parse", f"{commit}^{{tree}}")
        return resolved.stdout.strip() if resolved.returncode == 0 else None

    for result in results:
        repetitions = result.get("repetitions")
        if not isinstance(repetitions, list):
            continue
        for repetition in repetitions:
            if not isinstance(repetition, dict):
                continue
            reference = repetition.get("evaluated_tree_snapshot")
            if not isinstance(reference, dict):
                continue
            relative = validate_relative_syntax(
                reference.get("path"),
                diagnostics,
                str(run_dir / "results.jsonl"),
                "evaluated_tree_snapshot.path",
            )
            if relative is None:
                continue
            target = resolve_regular_file(
                run_dir,
                relative,
                diagnostics,
                str(run_dir / "results.jsonl"),
                "evaluated_tree_snapshot",
            )
            if target is None:
                continue
            snapshot = load_json_file(target, diagnostics)
            if snapshot is None:
                continue
            expected_tree = tree_of(repetition.get("evaluated_commit"))
            if expected_tree is not None and snapshot.get("tree_oid") != expected_tree:
                diagnostics.error(
                    "tree_snapshot_git_mismatch",
                    str(target),
                    "snapshot tree_oid differs from the evaluated Git commit",
                )

    writable_scope = (
        contract.get("scope", {}).get("writable_paths", [])
        if isinstance(contract, dict) and isinstance(contract.get("scope"), dict)
        else []
    )
    if not isinstance(writable_scope, list):
        writable_scope = []
    immutable_scope = (
        contract.get("scope", {}).get("immutable_paths", [])
        if isinstance(contract, dict) and isinstance(contract.get("scope"), dict)
        else []
    )
    if not isinstance(immutable_scope, list):
        immutable_scope = []
    generated_scope = (
        contract.get("scope", {}).get("generated_paths", [])
        if isinstance(contract, dict) and isinstance(contract.get("scope"), dict)
        else []
    )
    if not isinstance(generated_scope, list):
        generated_scope = []
    inspected_modes: set[tuple[str, str]] = set()

    def validate_tree_modes(commit: Any, scopes: list[Any], label: str) -> None:
        if not isinstance(commit, str) or GIT_OID_RE.fullmatch(commit) is None:
            return
        normalized_scopes = [scope for scope in scopes if isinstance(scope, str)]
        if not normalized_scopes:
            return
        listing = invoke("ls-tree", "-r", "-z", commit, "--", *normalized_scopes)
        if listing.returncode != 0:
            diagnostics.error(
                "git_tree_unreadable",
                str(root),
                f"unable to inspect Git modes for {label}",
            )
            return
        for record in listing.stdout.split("\0"):
            if not record:
                continue
            try:
                metadata, entry_path = record.split("\t", 1)
                mode, object_type, _object_id = metadata.split(" ", 2)
            except ValueError:
                diagnostics.error(
                    "git_tree_unreadable",
                    str(root),
                    f"malformed ls-tree record while inspecting {label}",
                )
                continue
            identity = (commit, entry_path)
            if identity in inspected_modes:
                continue
            inspected_modes.add(identity)
            if object_type != "blob" or mode not in {"100644", "100755"}:
                diagnostics.error(
                    "unsafe_git_mode",
                    str(root),
                    f"{label} contains forbidden Git mode {mode} ({object_type}) at {entry_path!r}",
                )

    tree_scopes = list(writable_scope) + list(immutable_scope) + list(generated_scope)
    commits_to_inspect: dict[str, str] = {}
    if isinstance(contract, dict) and isinstance(contract.get("baseline_commit"), str):
        commits_to_inspect[contract["baseline_commit"]] = "sealed baseline"
    for attempt in attempts:
        for field in ("incumbent_commit", "candidate_commit"):
            commit = attempt.get(field)
            if isinstance(commit, str):
                commits_to_inspect.setdefault(
                    commit, f"{attempt.get('attempt_id')} {field}"
                )
    for commit, label in commits_to_inspect.items():
        validate_tree_modes(commit, tree_scopes, label)

    for scope_name in generated_scope:
        if not isinstance(scope_name, str):
            continue
        relative_scope = PurePosixPath(scope_name)
        cursor = root
        escaped = False
        for part in relative_scope.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                diagnostics.error(
                    "symlink_forbidden",
                    str(root),
                    f"generated scope traverses a symlink: {cursor.relative_to(root).as_posix()}",
                )
                escaped = True
                break
        if escaped or not cursor.exists():
            continue
        diagnostics.error(
            "generated_state_not_reset",
            str(root),
            f"reset_each_launch requires an absent generated path: {scope_name}",
        )
        for generated_entry in (
            cursor.rglob("*") if cursor.is_dir() else [cursor]
        ):
            if generated_entry.is_symlink():
                diagnostics.error(
                    "symlink_forbidden",
                    str(root),
                    f"generated scope contains a symlink: {generated_entry.relative_to(root).as_posix()}",
                )
            elif not generated_entry.is_file() and not generated_entry.is_dir():
                diagnostics.error(
                    "invalid_generated_entry",
                    str(root),
                    f"generated scope contains a non-regular entry: {generated_entry.relative_to(root).as_posix()}",
                )

    for attempt in attempts:
        if attempt.get("role") != "candidate":
            continue
        incumbent_commit = attempt.get("incumbent_commit")
        pre_attempt_head = attempt.get("pre_attempt_head_commit")
        candidate_commit = attempt.get("candidate_commit")
        if not (
            isinstance(incumbent_commit, str)
            and isinstance(pre_attempt_head, str)
            and isinstance(candidate_commit, str)
            and GIT_OID_RE.fullmatch(incumbent_commit)
            and GIT_OID_RE.fullmatch(pre_attempt_head)
            and GIT_OID_RE.fullmatch(candidate_commit)
        ):
            continue
        parents = invoke("rev-list", "--parents", "-n", "1", candidate_commit)
        parent_fields = parents.stdout.strip().split()
        if (
            parents.returncode == 0
            and (len(parent_fields) != 2 or parent_fields[1] != pre_attempt_head)
        ):
            diagnostics.error(
                "candidate_parent_mismatch",
                str(root),
                f"candidate {attempt.get('attempt_id')} must be one direct commit over its pre-attempt HEAD",
            )
        changed = invoke(
            "diff",
            "--no-ext-diff",
            "--name-only",
            "-z",
            incumbent_commit,
            candidate_commit,
            "--",
        )
        if changed.returncode != 0:
            diagnostics.error(
                "candidate_diff_unreadable",
                str(root),
                f"unable to inspect candidate {attempt.get('attempt_id')}",
            )
            continue
        changed_paths = [item for item in changed.stdout.split("\0") if item]
        if not changed_paths:
            diagnostics.error(
                "empty_candidate_diff",
                str(root),
                f"candidate {attempt.get('attempt_id')} changes no tracked path",
            )
        design = None
        design_ref = attempt.get("design")
        if isinstance(design_ref, dict) and isinstance(design_ref.get("path"), str):
            design = load_json_file(run_dir / design_ref["path"], diagnostics)
        allowed_files = design.get("allowed_files", []) if isinstance(design, dict) else []
        for changed_path in changed_paths:
            relative = validate_relative_syntax(
                changed_path,
                diagnostics,
                str(root),
                f"candidate {attempt.get('attempt_id')} changed path",
            )
            if relative is None:
                continue
            normalized = relative.as_posix()
            protected = any(
                part in {".git", ".research-loop"} for part in relative.parts
            )
            in_writable = any(
                isinstance(scope_path, str) and path_within_scope(normalized, scope_path)
                for scope_path in writable_scope
            )
            in_allowed = any(
                isinstance(scope_path, str) and path_within_scope(normalized, scope_path)
                for scope_path in allowed_files
            )
            in_immutable = any(
                isinstance(scope_path, str) and path_within_scope(normalized, scope_path)
                for scope_path in immutable_scope
            )
            if protected or in_immutable or not in_writable or not in_allowed:
                diagnostics.error(
                    "candidate_scope_violation",
                    str(root),
                    f"candidate {attempt.get('attempt_id')} changed unauthorized path {normalized!r}",
                )

    expected_head = contract.get("baseline_commit") if isinstance(contract, dict) else None
    for attempt in attempts:
        pre_attempt_head = attempt.get("pre_attempt_head_commit")
        if expected_head is not None and pre_attempt_head != expected_head:
            diagnostics.error(
                "pre_attempt_head_chain",
                str(root),
                f"pre-attempt HEAD for {attempt.get('attempt_id')} does not continue the recorded Git history",
            )
        pre_head_tree = tree_of(attempt.get("pre_attempt_head_commit"))
        incumbent_tree = tree_of(attempt.get("incumbent_commit"))
        if (
            pre_head_tree is not None
            and incumbent_tree is not None
            and pre_head_tree != incumbent_tree
        ):
            diagnostics.error(
                "pre_attempt_tree_mismatch",
                str(root),
                f"pre-attempt HEAD for {attempt.get('attempt_id')} differs from its incumbent tree",
            )
        rollback = attempt.get("rolled_back")
        if isinstance(rollback, dict):
            rollback_commit = rollback.get("rollback_commit")
            candidate_commit = attempt.get("candidate_commit")
            rollback_parents = (
                invoke("rev-list", "--parents", "-n", "1", rollback_commit)
                if isinstance(rollback_commit, str)
                else None
            )
            rollback_fields = (
                rollback_parents.stdout.strip().split()
                if rollback_parents is not None and rollback_parents.returncode == 0
                else []
            )
            if len(rollback_fields) != 2 or rollback_fields[1] != candidate_commit:
                diagnostics.error(
                    "rollback_lineage_mismatch",
                    str(root),
                    f"rollback for {attempt.get('attempt_id')} must be one direct inverse commit over its candidate",
                )
            rollback_tree = tree_of(rollback_commit)
            if (
                rollback_tree is not None
                and incumbent_tree is not None
                and rollback_tree != incumbent_tree
            ):
                diagnostics.error(
                    "rollback_tree_mismatch",
                    str(root),
                    f"rollback for {attempt.get('attempt_id')} does not restore the incumbent tree",
                )
            expected_head = rollback_commit
        elif attempt.get("role") == "candidate" and attempt.get("candidate_commit") is not None:
            expected_head = attempt.get("candidate_commit")
        else:
            expected_head = pre_attempt_head
    if actual_head is not None and expected_head is not None and actual_head != expected_head:
        diagnostics.error(
            "head_chain_mismatch",
            str(root),
            "actual HEAD does not match the last recorded Git transition",
        )
    if (
        run_stopped is not None
        and run_stopped.get("unresolved_cleanup") is False
        and run_stopped.get("unresolved_recovery") is False
    ):
        head_tree = tree_of(actual_head)
        incumbent_tree = tree_of(run_stopped.get("final_incumbent_commit"))
        if head_tree is not None and incumbent_tree is not None and head_tree != incumbent_tree:
            diagnostics.error(
                "final_tree_mismatch",
                str(root),
                "final HEAD tree differs from the final incumbent while cleanup and recovery are resolved",
            )
    dirty_paths: set[str] = set()
    index_listing = invoke("ls-files", "-v", "-z")
    if index_listing.returncode != 0:
        diagnostics.error(
            "git_status_unreadable",
            str(root),
            "unable to inspect supervisor index flags",
        )
    else:
        unsafe_index_entries = [
            record[2:] if len(record) >= 3 else record
            for record in index_listing.stdout.split("\0")
            if record
            and (len(record) < 3 or record[1] != " " or record[0] != "H")
        ]
        if unsafe_index_entries:
            diagnostics.error(
                "unsafe_git_index_flags",
                str(root),
                "supervisor worktree contains assume-unchanged, skip-worktree, or non-normal index entries: "
                f"{sorted(unsafe_index_entries)[:8]}",
            )
    for args in (
        ("diff", "--no-ext-diff", "--name-only", "-z"),
        ("diff", "--cached", "--no-ext-diff", "--name-only", "-z"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
        ("ls-files", "--others", "--ignored", "--exclude-standard", "-z"),
    ):
        listed = invoke(*args)
        if listed.returncode != 0:
            diagnostics.error("git_status_unreadable", str(root), "unable to inspect worktree state")
            continue
        dirty_paths.update(path for path in listed.stdout.split("\0") if path)
    unexpected_dirty = []
    for dirty_path in sorted(dirty_paths):
        relative = PurePosixPath(dirty_path)
        if relative.parts and relative.parts[0] == ".research-loop":
            continue
        unexpected_dirty.append(dirty_path)
    active_precommit_candidate = bool(
        attempts
        and attempts[-1].get("role") == "candidate"
        and attempts[-1].get("phase") == "prepared"
        and attempts[-1].get("finalized") is None
    )
    permitted_precommit_dirty: list[str] = []
    if active_precommit_candidate:
        active_attempt = attempts[-1]
        design = None
        design_ref = active_attempt.get("design")
        if isinstance(design_ref, dict) and isinstance(design_ref.get("path"), str):
            design = load_json_file(run_dir / design_ref["path"], diagnostics)
        allowed_files = design.get("allowed_files", []) if isinstance(design, dict) else []
        for dirty_path in unexpected_dirty:
            relative = PurePosixPath(dirty_path)
            protected = any(part in {".git", ".research-loop"} for part in relative.parts)
            in_writable = any(
                isinstance(scope_path, str) and path_within_scope(dirty_path, scope_path)
                for scope_path in writable_scope
            )
            in_allowed = any(
                isinstance(scope_path, str) and path_within_scope(dirty_path, scope_path)
                for scope_path in allowed_files
            )
            in_immutable = any(
                isinstance(scope_path, str) and path_within_scope(dirty_path, scope_path)
                for scope_path in immutable_scope
            )
            if not protected and in_writable and in_allowed and not in_immutable:
                permitted_precommit_dirty.append(dirty_path)
    unauthorized_dirty = [
        path for path in unexpected_dirty if path not in permitted_precommit_dirty
    ]
    if unauthorized_dirty:
        diagnostics.error(
            "dirty_worktree",
            str(root),
            f"uncommitted or unexpected paths remain: {unauthorized_dirty}",
        )
    return actual_head


def find_partial_files(run_dir: Path) -> list[Path]:
    quarantine = (run_dir / "quarantine").resolve()
    found: list[Path] = []
    for path in run_dir.rglob("*"):
        if not path.is_file() or not path.name.endswith((".partial", ".tmp")):
            continue
        try:
            path.resolve().relative_to(quarantine)
        except ValueError:
            found.append(path)
    return found


def validate_canonical_artifacts(
    run_dir: Path,
    attempts: list[dict[str, Any]],
    results: list[dict[str, Any]],
    diagnostics: Diagnostics,
) -> list[dict[str, str]]:
    referenced: set[str] = set()

    def add_reference(value: Any) -> None:
        if isinstance(value, dict) and isinstance(value.get("path"), str):
            relative = PurePosixPath(value["path"])
            if not relative.is_absolute() and ".." not in relative.parts:
                referenced.add(relative.as_posix())

    for attempt in attempts:
        add_reference(attempt.get("design"))
        add_reference(attempt.get("launch_plan_ref"))
        add_reference(attempt.get("admission_ref"))
        for planned_launch in attempt.get("launch_plan", []):
            if isinstance(planned_launch, dict):
                add_reference(planned_launch.get("workspace"))
        for launch in attempt.get("launches", []):
            if not isinstance(launch, dict):
                continue
            for artifact_field in (
                "workspace",
                "effective_config",
                "generated_state",
                "evaluated_tree_snapshot",
            ):
                add_reference((launch.get("intent") or {}).get(artifact_field))
            publication = launch.get("evidence_published") or {}
            add_reference(publication.get("result_artifact"))
            add_reference(publication.get("log"))
        attempt_id = attempt.get("attempt_id")
        if isinstance(attempt_id, str) and SAFE_ID_RE.fullmatch(attempt_id):
            referenced.add(f"boundaries/{attempt_id}.json")
    for result in results:
        add_reference(result.get("design"))
        repetitions = result.get("repetitions")
        if not isinstance(repetitions, list):
            continue
        for repetition in repetitions:
            if not isinstance(repetition, dict):
                continue
            for artifact_field in (
                "result_artifact",
                "log",
                "effective_config",
                "generated_state",
                "evaluated_tree_snapshot",
            ):
                add_reference(repetition.get(artifact_field))

    manifest: list[dict[str, str]] = []
    for directory_name in ("designs", "evidence", "logs", "snapshots", "boundaries"):
        directory = run_dir / directory_name
        if not directory.exists():
            continue
        for artifact in sorted(directory.rglob("*")):
            relative = artifact.relative_to(run_dir).as_posix()
            if artifact.is_symlink():
                diagnostics.error(
                    "symlink_forbidden",
                    str(artifact),
                    "canonical artifact must not be a symlink",
                )
                continue
            if artifact.is_dir():
                continue
            if not artifact.is_file():
                diagnostics.error(
                    "invalid_canonical_artifact",
                    str(artifact),
                    "canonical artifact must be a regular file",
                )
                continue
            if artifact.name.endswith((".partial", ".tmp")):
                continue
            if relative not in referenced:
                diagnostics.error(
                    "orphan_canonical_artifact",
                    str(artifact),
                    "finalized canonical artifact is not referenced by run state",
                )
            manifest.append({"path": relative, "sha256": file_sha256(artifact)})
    manifest.sort(key=lambda item: item["path"])
    return manifest


def validate_run_boundary(
    run_dir: Path,
    marker: dict[str, Any] | None,
    contract: dict[str, Any] | None,
    contract_digest: str | None,
    journal_records: list[dict[str, Any]],
    journal_raw: bytes,
    results_records: list[dict[str, Any]],
    results_raw: bytes,
    run_stopped: dict[str, Any] | None,
    terminal_required: bool,
    cumulative: dict[str, int],
    final_incumbent: str | None,
    actual_head: str | None,
    results_by_attempt: dict[str, tuple[int, dict[str, Any], str]],
    incomplete_action: str | None,
    allow_incomplete: bool,
    require_final: bool,
    canonical_artifacts: list[dict[str, str]],
    diagnostics: Diagnostics,
) -> tuple[bool, str | None]:
    marker_path = run_dir / "run.finalized.json"
    actual_budget_complete = all(
        isinstance(result.get("budget"), dict)
        and result["budget"].get("actual_complete") is True
        for result in results_records
    )
    final_test_configured = bool(
        isinstance(contract, dict) and isinstance(contract.get("final_test"), dict)
    )
    confirmed_nonfinal = any(
        result.get("status") == "keep"
        and result.get("lane") == "confirmed"
        and result.get("role") != "final"
        for result in results_records
    )
    final_results = [result for result in results_records if result.get("role") == "final"]
    authorized_interruption = any(
        record.get("event") == "stop_requested"
        and record.get("stop_kind") == "user_interruption"
        and record.get("source") == "user"
        and record.get("final_test_skip_authorized") is True
        for record in journal_records
    )
    resolved_stop = bool(
        run_stopped is not None
        and run_stopped.get("unresolved_cleanup") is False
        and run_stopped.get("unresolved_recovery") is False
    )
    final_required = (
        final_test_configured
        and confirmed_nonfinal
        and resolved_stop
        and not authorized_interruption
    )
    if final_required:
        if len(final_results) != 1:
            diagnostics.error(
                "final_test_missing",
                str(run_dir / "results.jsonl"),
                "ordinary resolved closure with a confirmed incumbent requires exactly one final-test attempt",
            )
        else:
            final_actual = (
                final_results[0].get("budget", {}).get("actual", {})
                if isinstance(final_results[0].get("budget"), dict)
                else {}
            )
            if not is_positive_int(final_actual.get("evaluator_calls")):
                diagnostics.error(
                    "final_test_not_launched",
                    str(run_dir / "results.jsonl"),
                    "ordinary resolved closure requires the final attempt to have actually launched the configured final stage",
                )
    if marker is None:
        if require_final:
            diagnostics.error("missing_run_boundary", str(marker_path), "final run boundary is required")
        if run_stopped is not None and incomplete_action is None:
            if allow_incomplete:
                return False, "publish_run_boundary"
            diagnostics.error(
                "incomplete_run_finalization",
                str(marker_path),
                "run_stopped exists without run.finalized.json",
            )
        if run_stopped is None and terminal_required and incomplete_action is None:
            if allow_incomplete:
                return False, "append_run_stopped"
            diagnostics.error(
                "run_stop_record_missing",
                str(run_dir / "journal.jsonl"),
                "terminal run state requires run_stopped",
            )
        return False, incomplete_action
    if incomplete_action is not None:
        diagnostics.error("finalized_with_incomplete_attempt", str(marker_path), "run boundary has incomplete attempt")
    if run_stopped is None:
        diagnostics.error("run_boundary_without_stop", str(marker_path), "run_stopped event is missing")
    last_attempt_boundary_sha256: str | None = None
    last_attempt_boundary_at: datetime | None = None
    if results_records:
        last_attempt_id = results_records[-1].get("attempt_id")
        if isinstance(last_attempt_id, str) and SAFE_ID_RE.fullmatch(last_attempt_id):
            last_boundary_path = run_dir / "boundaries" / f"{last_attempt_id}.json"
            if last_boundary_path.is_file() and not last_boundary_path.is_symlink():
                last_attempt_boundary_sha256 = file_sha256(last_boundary_path)
                last_boundary = load_json_file(last_boundary_path, diagnostics)
                if last_boundary is not None:
                    last_attempt_boundary_at = parse_timestamp(
                        last_boundary.get("finalized_at")
                    )
    expected = {
        "schema_version": 1,
        "kind": "run-boundary",
        "run_tag": contract.get("run_tag") if contract else None,
        "contract_sha256": contract_digest,
        "final_incumbent_commit": final_incumbent,
        "final_head_commit": actual_head,
        "result_count": len(results_records),
        "last_journal_seq": len(journal_records),
        "results_sha256": hashlib.sha256(results_raw).hexdigest(),
        "journal_sha256": hashlib.sha256(journal_raw).hexdigest(),
        "last_attempt_boundary_sha256": last_attempt_boundary_sha256,
        "canonical_artifacts": canonical_artifacts,
        "canonical_artifacts_sha256": canonical_sha256(canonical_artifacts),
        "actual_budget": cumulative,
        "actual_budget_complete": actual_budget_complete,
    }
    expected_keys = set(expected) | {
        "best_confirmed_attempt",
        "best_confirmed_commit",
        "unresolved_cleanup",
        "unresolved_recovery",
        "stop_reason",
        "finalized_at",
    }
    if set(marker) != expected_keys:
        diagnostics.error(
            "run_boundary_schema_mismatch",
            str(marker_path),
            f"run boundary keys must be exactly {sorted(expected_keys)}",
        )
    for key, value in expected.items():
        if marker.get(key) != value:
            diagnostics.error("run_boundary_mismatch", str(marker_path), f"{key} differs from canonical state")
    marker_at = validate_timestamp(
        marker.get("finalized_at"), diagnostics, str(marker_path), "finalized_at"
    )
    last_journal_at = (
        parse_timestamp(journal_records[-1].get("at")) if journal_records else None
    )
    if marker_at is not None and last_journal_at is not None and marker_at < last_journal_at:
        diagnostics.error(
            "run_boundary_time_regression",
            str(marker_path),
            "run boundary cannot predate the final journal event",
        )
    if (
        marker_at is not None
        and last_attempt_boundary_at is not None
        and marker_at < last_attempt_boundary_at
    ):
        diagnostics.error(
            "run_boundary_time_regression",
            str(marker_path),
            "run boundary cannot predate the terminal attempt boundary it seals",
        )
    require_string(marker, "stop_reason", diagnostics, str(marker_path))
    if run_stopped is not None:
        if marker.get("stop_reason") != run_stopped.get("reason"):
            diagnostics.error("run_boundary_mismatch", str(marker_path), "stop_reason differs from journal")
        if marker.get("unresolved_cleanup") is not run_stopped.get("unresolved_cleanup"):
            diagnostics.error("run_boundary_mismatch", str(marker_path), "cleanup state differs from journal")
        if marker.get("unresolved_recovery") is not run_stopped.get(
            "unresolved_recovery"
        ):
            diagnostics.error(
                "run_boundary_mismatch",
                str(marker_path),
                "recovery state differs from journal",
            )
        if run_stopped.get("final_incumbent_commit") != final_incumbent:
            diagnostics.error(
                "run_boundary_mismatch", str(marker_path), "journal final incumbent differs"
            )
        if run_stopped.get("final_head_commit") != actual_head:
            diagnostics.error(
                "run_boundary_mismatch", str(marker_path), "journal final HEAD differs from Git"
            )
    best_attempt = marker.get("best_confirmed_attempt")
    best_commit = marker.get("best_confirmed_commit")
    confirmed = [
        (attempt_id, result)
        for attempt_id, (_index, result, _digest) in results_by_attempt.items()
        if result.get("status") == "keep"
        and result.get("lane") == "confirmed"
        and result.get("role") != "final"
        and result.get("commit") == final_incumbent
    ]
    if not confirmed:
        if best_attempt is not None or best_commit is not None:
            diagnostics.error(
                "invalid_best_attempt",
                str(marker_path),
                "best fields must be null when no confirmed result exists",
            )
    elif not isinstance(best_attempt, str) or best_attempt not in results_by_attempt:
        diagnostics.error("invalid_best_attempt", str(marker_path), "best confirmed attempt is unknown")
    else:
        result = results_by_attempt[best_attempt][1]
        if result.get("status") != "keep" or result.get("lane") != "confirmed":
            diagnostics.error("invalid_best_attempt", str(marker_path), "best attempt is not confirmed")
        if best_commit != result.get("commit"):
            diagnostics.error("invalid_best_attempt", str(marker_path), "best commit differs from result")
        expected_best_attempt, _expected_best_result = confirmed[-1]
        if best_attempt != expected_best_attempt or best_commit != final_incumbent:
            diagnostics.error(
                "invalid_best_attempt",
                str(marker_path),
                "best fields must name the latest confirmed result for the final incumbent",
            )
    for partial in find_partial_files(run_dir):
        diagnostics.error("partial_after_finalization", str(partial), "partial file remains outside quarantine")
    return True, incomplete_action


def _validate_run(
    run_dir: Path,
    *,
    trusted_root: Path | None = None,
    git_root: Path | None = None,
    allow_incomplete: bool = False,
    require_run_finalized: bool = False,
    verify_git: bool = True,
) -> tuple[dict[str, Any], int]:
    diagnostics = Diagnostics()
    run_dir = run_dir.expanduser().absolute()
    if run_dir.is_symlink():
        diagnostics.error(
            "symlink_forbidden", str(run_dir), "run directory must not be a symlink"
        )
    run_dir = run_dir.resolve()
    if not run_dir.is_dir():
        diagnostics.error("missing_run_directory", str(run_dir), "run directory does not exist")
    inferred_git_root = (
        git_root
        if git_root is not None
        else (
            run_dir.parent.parent
            if run_dir.parent.name == ".research-loop"
            else run_dir.parent
        )
    )
    for directory_name in (
        "boundaries",
        "designs",
        "evidence",
        "logs",
        "snapshots",
        "quarantine",
    ):
        directory = run_dir / directory_name
        if directory.is_symlink():
            diagnostics.error(
                "symlink_forbidden", str(directory), "canonical directory must not be a symlink"
            )
        elif not directory.is_dir():
            diagnostics.error(
                "invalid_canonical_directory",
                str(directory),
                "canonical namespace must be a real directory",
            )
    contract, contract_digest, budgets, required_stages = validate_contract(
        run_dir, trusted_root, diagnostics
    )
    journal_records, journal_raw = load_jsonl(run_dir / "journal.jsonl", diagnostics)
    results_records, results_raw = load_jsonl(run_dir / "results.jsonl", diagnostics)
    attempts, stop_requested, run_stopped = validate_journal(
        journal_records,
        contract,
        contract_digest,
        required_stages,
        diagnostics,
        run_dir / "journal.jsonl",
        supervisor_git_root=inferred_git_root,
        verify_git=verify_git,
    )
    results_by_attempt, cumulative, final_incumbent = validate_results(
        results_records,
        attempts,
        run_dir,
        contract,
        contract_digest,
        required_stages,
        budgets,
        diagnostics,
        run_dir / "results.jsonl",
    )
    actual_head = (
        validate_git(
            inferred_git_root,
            run_dir,
            contract,
            attempts,
            results_records,
            run_stopped,
            diagnostics,
        )
        if verify_git
        else (
            run_stopped.get("final_head_commit")
            if run_stopped is not None
            else None
        )
    )
    finalized_attempts, active_attempt, active_phase, resume_action = reconcile_attempts(
        run_dir,
        attempts,
        results_by_attempt,
        contract_digest,
        journal_raw,
        results_raw,
        stop_requested,
        allow_incomplete,
        diagnostics,
    )
    validate_pending_designs(
        run_dir,
        attempts,
        results_by_attempt,
        finalized_attempts,
        contract,
        budgets,
        diagnostics,
    )
    marker = load_json_file(run_dir / "run.finalized.json", diagnostics, required=False)
    terminal_required = stop_requested is not None or any(
        attempt.get("role") == "final"
        or (attempt.get("decision") or {}).get("rollback_action") == "preserve_and_stop"
        or (
            attempt.get("role") == "baseline"
            and attempt.get("decision") is not None
            and (attempt.get("decision") or {}).get("status") != "keep"
        )
        for attempt in attempts
    )
    canonical_artifacts = validate_canonical_artifacts(
        run_dir, attempts, results_records, diagnostics
    )
    closed, resume_action = validate_run_boundary(
        run_dir,
        marker,
        contract,
        contract_digest,
        journal_records,
        journal_raw,
        results_records,
        results_raw,
        run_stopped,
        terminal_required,
        cumulative,
        final_incumbent,
        actual_head,
        results_by_attempt,
        resume_action,
        allow_incomplete,
        require_run_finalized,
        canonical_artifacts,
        diagnostics,
    )
    partials = find_partial_files(run_dir)
    if partials and not closed:
        for partial in partials:
            diagnostics.warning(
                "partial_artifact", str(partial), "partial output is non-evidence; reconcile or quarantine it"
            )
    if diagnostics.errors:
        verdict = "invalid"
        exit_code = EXIT_INVALID
        resume_action = None
    elif resume_action is not None:
        verdict = "recoverable"
        exit_code = EXIT_RECOVERABLE
    else:
        verdict = "valid"
        exit_code = EXIT_VALID
    if closed:
        last_durable_phase = "run_finalized"
    elif run_stopped is not None:
        last_durable_phase = "run_stopped"
    elif active_phase is not None:
        last_durable_phase = active_phase
    elif finalized_attempts:
        last_durable_phase = "attempt_boundary"
    elif contract_digest is not None:
        last_durable_phase = "contract_sealed"
    else:
        last_durable_phase = None
    output = {
        "verdict": verdict,
        "closed": closed,
        "contract_sha256": contract_digest,
        "finalized_attempts": finalized_attempts,
        "active_attempt": active_attempt,
        "last_durable_phase": last_durable_phase,
        "resume_action": resume_action,
        "result_count": len(results_records),
        "journal_event_count": len(journal_records),
        "actual_budget": cumulative,
        "actual_budget_complete": all(
            isinstance(result.get("budget"), dict)
            and result["budget"].get("actual_complete") is True
            for result in results_records
        ),
        "errors": diagnostics.sorted_errors(),
        "warnings": diagnostics.sorted_warnings(),
    }
    return output, exit_code


def validate_run(
    run_dir: Path,
    *,
    trusted_root: Path | None = None,
    git_root: Path | None = None,
    allow_incomplete: bool = False,
    require_run_finalized: bool = False,
    verify_git: bool = True,
) -> tuple[dict[str, Any], int]:
    try:
        return _validate_run(
            run_dir,
            trusted_root=trusted_root,
            git_root=git_root,
            allow_incomplete=allow_incomplete,
            require_run_finalized=require_run_finalized,
            verify_git=verify_git,
        )
    except Exception as error:
        display = str(run_dir)
        output = {
            "verdict": "invalid",
            "closed": False,
            "contract_sha256": None,
            "finalized_attempts": [],
            "active_attempt": None,
            "last_durable_phase": "internal_error",
            "resume_action": None,
            "result_count": 0,
            "journal_event_count": 0,
            "actual_budget": {},
            "actual_budget_complete": False,
            "errors": [
                {
                    "code": "internal_error",
                    "path": display,
                    "message": f"validator rejected malformed state after {type(error).__name__}: {error}",
                }
            ],
            "warnings": [],
        }
        return output, EXIT_INVALID


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    payload = "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n" for record in records
    )
    path.write_text(payload, encoding="utf-8")


class Fixture:
    BASE = "a" * 40
    CANDIDATE = "b" * 40
    REVERT = "c" * 40

    def __init__(self, root: Path) -> None:
        self.worktree = root / "worktree"
        self.run = self.worktree / ".research-loop" / "fixture-run"
        self.run.mkdir(parents=True)
        (self.worktree / "eval.py").write_text("print('ok')\n", encoding="utf-8")
        executable_path = str(Path(sys.executable).resolve(strict=True))
        environment = {
            "inherit_parent": False,
            "variables": {"LANG": "C.UTF-8"},
            "secret_variable_names": [],
            "dependency_fingerprints": {},
        }
        evaluation = {
            "argv": [executable_path, "eval.py"],
            "cwd": ".",
            "extractor_path": "eval.py",
            "executable_path": executable_path,
            "executable_sha256": file_sha256(Path(executable_path)),
            "environment": environment,
            "environment_sha256": canonical_sha256(environment),
            "timeout_seconds": 10,
        }
        evaluation["invocation_sha256"] = canonical_sha256(
            invocation_fingerprint_payload(evaluation)
        )
        immutable_files = [
            {"path": "eval.py", "sha256": file_sha256(self.worktree / "eval.py")}
        ]
        evaluation["evaluator_sha256"] = canonical_sha256(
            {
                "invocation_sha256": evaluation["invocation_sha256"],
                "immutable_files": immutable_files,
            }
        )
        self.contract = {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "protocol": {
                "version": PROTOCOL_VERSION,
                "validator_sha256": file_sha256(Path(__file__).resolve(strict=True)),
            },
            "run_tag": "fixture-run",
            "feedback_mode": "sealed_gate",
            "baseline_commit": self.BASE,
            "deadline_at": None,
            "objective": {
                "goal": "increase the fixture score",
                "intended_population": "fixture inputs",
                "primary_metric": "score",
                "direction": "maximize",
                "target": None,
                "minimum_delta": 0.05,
                "acceptance_split": "validation",
                "hard_constraints": ["integrity"],
            },
            "measurement": {
                "mode": "deterministic",
                "aggregation": "single",
                "minimum_repetitions": 1,
                "exploration_repetitions": 0,
                "confirmation_repetitions": 0,
                "uncertainty_rule": "none",
            },
            "scope": {
                "writable_paths": ["candidate.txt"],
                "immutable_paths": ["eval.py"],
                "generated_paths": ["runtime-state"],
            },
            "generated_state_policy": {
                "mode": "reset_each_launch",
                "initial_state_sha256": EMPTY_GENERATED_STATE_SHA256,
            },
            "control_policy": {
                "authorization": "bounded",
                "stop_conditions": [
                    "target reached",
                    "budget cannot admit another attempt",
                    "user interruption",
                    "integrity failure",
                ],
                "plateau_trigger": {
                    "consecutive_non_improvements": 3,
                    "action": "refresh_candidate_pool",
                },
                "counterexamples": {
                    "fixture_integrity": "preserve the fixture integrity invariant"
                },
                "cleanup_grace_seconds": 30,
                "actual_usage_sources": {
                    "evaluator_calls": "supervisor launch receipts",
                    "validation_queries": "protected-stage access receipts",
                },
                "hard_constraint_rules": {
                    "integrity": "all sealed scope and provenance checks pass"
                },
            },
            "evaluation": evaluation,
            "immutable_files": immutable_files,
            "budgets": {
                "evaluator_calls": {
                    "total": 10,
                    "operational_reserve": 1,
                    "final_test_reserve": 0,
                },
                "validation_queries": {
                    "total": 10,
                    "operational_reserve": 1,
                    "final_test_reserve": 0,
                },
            },
            "per_attempt_max": {
                "evaluator_calls": 2,
                "validation_queries": 1,
            },
            "promotion": {
                "required_stages": ["development", "validation"],
                "required_telemetry": {
                    "development": ["score"],
                    "validation": ["score"],
                },
                "acceptance_rule": "meet the minimum primary delta and pass every required stage",
            },
            "stage_resources": {
                stage: {
                    "resource_manifest_sha256": hashlib.sha256(
                        f"fixture-resource:{stage}".encode("utf-8")
                    ).hexdigest(),
                    "capability_sha256": hashlib.sha256(
                        f"fixture-capability:{stage}".encode("utf-8")
                    ).hexdigest(),
                    "visibility": "visible" if stage == "development" else "protected",
                }
                for stage in ("development", "validation")
            },
            "final_test": None,
        }
        self.contract["stage_access_sha256"] = {
            stage: canonical_sha256(resource)
            for stage, resource in self.contract["stage_resources"].items()
        }
        self.contract["stage_config_sha256"] = {
            stage: hashlib.sha256(
                (
                    json.dumps(
                        {
                            "schema_version": 1,
                            "stage": stage,
                            "stage_access_sha256": access_digest,
                            "settings": {},
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
            ).hexdigest()
            for stage, access_digest in self.contract["stage_access_sha256"].items()
        }
        _write_json(self.run / "contract.json", self.contract)
        (self.run / "contract.sha256").write_text(
            file_sha256(self.run / "contract.json") + "\n", encoding="ascii"
        )
        for directory in ("boundaries", "designs", "evidence", "logs", "snapshots", "quarantine"):
            (self.run / directory).mkdir()
        self.contract_digest = file_sha256(self.run / "contract.json")
        self.events: list[dict[str, Any]] = []
        self.results: list[dict[str, Any]] = []
        self.clock_seconds = 0

    def timestamp(self, *, advance: bool = False, offset_seconds: int = 0) -> str:
        instant = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(
            seconds=self.clock_seconds + offset_seconds
        )
        if advance:
            self.clock_seconds += 1
        return instant.isoformat().replace("+00:00", "Z")

    def event(self, name: str, **fields: Any) -> dict[str, Any]:
        record = {
            "schema_version": 1,
            "seq": len(self.events) + 1,
            "at": self.timestamp(advance=True),
            "event": name,
            "contract_sha256": self.contract_digest,
            **fields,
        }
        self.events.append(record)
        return record

    def artifact(self, path: str, content: str) -> dict[str, str]:
        target = self.run / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": path, "sha256": file_sha256(target)}

    def reseal_contract(self) -> None:
        _write_json(self.run / "contract.json", self.contract)
        self.contract_digest = file_sha256(self.run / "contract.json")
        (self.run / "contract.sha256").write_text(
            self.contract_digest + "\n", encoding="ascii"
        )

    def tree_oid(self, commit: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(self.worktree), "rev-parse", f"{commit}^{{tree}}"],
            check=False,
            capture_output=True,
            text=True,
        )
        resolved = completed.stdout.strip()
        return resolved if completed.returncode == 0 and GIT_OID_RE.fullmatch(resolved) else commit

    def workspace_record(
        self, attempt_id: str, iteration: int, arm: str, commit: str
    ) -> dict[str, Any]:
        root = (
            self.worktree.parent
            / "fixture-evaluation-worktrees"
            / f"{attempt_id}-{arm}"
        )
        probe = subprocess.run(
            ["git", "-C", str(self.worktree), "rev-parse", "--is-inside-work-tree"],
            check=False,
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0 and probe.stdout.strip() == "true":
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.worktree),
                    "worktree",
                    "add",
                    "--detach",
                    "--quiet",
                    str(root),
                    commit,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        else:
            root.mkdir(parents=True)
        root = root.resolve(strict=True)
        root_stat = root.stat()
        root_identity = canonical_sha256(
            {
                "realpath": str(root),
                "device": root_stat.st_dev,
                "inode": root_stat.st_ino,
            }
        )
        git_dir_identity = None
        git_common_identity = None
        if probe.returncode == 0 and probe.stdout.strip() == "true":
            git_dir_value = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--absolute-git-dir"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            common_dir_value = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--git-common-dir"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            git_dir = Path(git_dir_value).resolve(strict=True)
            common_dir = Path(common_dir_value)
            if not common_dir.is_absolute():
                common_dir = root / common_dir
            common_dir = common_dir.resolve(strict=True)
            git_dir_stat = git_dir.stat()
            common_dir_stat = common_dir.stat()
            git_dir_identity = canonical_sha256(
                {
                    "realpath": str(git_dir),
                    "device": git_dir_stat.st_dev,
                    "inode": git_dir_stat.st_ino,
                }
            )
            git_common_identity = canonical_sha256(
                {
                    "realpath": str(common_dir),
                    "device": common_dir_stat.st_dev,
                    "inode": common_dir_stat.st_ino,
                }
            )
        return {
            "schema_version": 1,
            "workspace_id": f"ws-{iteration}-{arm}",
            "arm": arm,
            "isolation_mode": "dedicated_git_worktree",
            "root_path": str(root),
            "root_identity_sha256": root_identity,
            "git_dir_identity_sha256": git_dir_identity,
            "git_common_dir_identity_sha256": git_common_identity,
        }

    def design_artifact(
        self, experiment_id: str, incumbent: str
    ) -> dict[str, str]:
        if incumbent == self.contract["baseline_commit"]:
            implementation_parent = {
                "kind": "baseline",
                "attempt_id": None,
                "commit": incumbent,
            }
        else:
            parent_result = next(
                result
                for result in reversed(self.results)
                if result["status"] == "keep" and result["commit"] == incumbent
            )
            implementation_parent = {
                "kind": "confirmed_attempt",
                "attempt_id": parent_result["attempt_id"],
                "commit": incumbent,
            }
        evidence_sources = [
            repetition["result_id"]
            for prior in self.results
            for repetition in prior["repetitions"]
        ]
        payload = {
            "schema_version": 1,
            "experiment_id": experiment_id,
            "created_at": "2026-01-01T00:00:00Z",
            "implementation_parent": implementation_parent,
            "evidence_sources": evidence_sources,
            "mechanism_family": "fixture mechanism",
            "intervention_surface": "candidate file",
            "intent": "exercise candidate validation",
            "hypothesis": "fixture hypothesis",
            "general_failure_class": "fixture failure",
            "supporting_metric_signature": "score is nondecreasing",
            "weakening_metric_signature": "score decreases",
            "predicted_unseen_behavior": "fixture behavior remains stable",
            "invariant_or_counterexample": "integrity remains true",
            "falsifier_or_ablation": "evaluate the unchanged arm",
            "fail_fast_condition": "integrity failure",
            "allowed_files": ["candidate.txt"],
            "forbidden_changes": ["do not change eval.py"],
            "cost_estimate": dict(self.contract["per_attempt_max"]),
            "risks": ["fixture risk"],
            "rejected_alternatives": [],
            "amendment": None,
        }
        return self.artifact(
            f"designs/{experiment_id}.json",
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        )

    def add_attempt(
        self,
        *,
        candidate: bool,
        keep: bool,
        finalize: bool = True,
        final: bool = False,
        cleanup_via_confirmation: bool = False,
        abort_launch: bool = False,
    ) -> dict[str, Any]:
        if candidate and final:
            raise ValueError("an attempt cannot be both candidate and final")
        if abort_launch and (candidate or keep):
            raise ValueError("the fixture only aborts a non-keep baseline or final launch")
        iteration = len(self.results)
        attempt_id = f"a-{iteration:03d}"
        experiment_id = (
            f"exp-{iteration:03d}" if candidate else ("final" if final else "baseline")
        )
        role = "candidate" if candidate else ("final" if final else "baseline")
        incumbent = (
            self.results[-1]["decision"]["incumbent_after"]
            if self.results
            else self.BASE
        )
        commit = self.CANDIDATE if candidate else incumbent
        pre_attempt_head = (
            self.results[-1].get("rollback_commit")
            or self.results[-1]["decision"]["incumbent_after"]
            if self.results
            else self.BASE
        )
        measurement_mode = self.contract["measurement"]["mode"]
        required_stages = self.contract["promotion"]["required_stages"]
        configured_final_stage = (
            self.contract["final_test"]["stage"]
            if isinstance(self.contract.get("final_test"), dict)
            else "final_test"
        )
        acceptance_stage = (
            configured_final_stage
            if final
            else self.contract["objective"]["acceptance_split"]
        )
        protected_stages = protected_stage_ids(self.contract)
        if abort_launch:
            launch_specs = [
                {
                    "stage": stage,
                    "arm": "incumbent",
                    "phase": "deterministic",
                    "pair_id": None,
                    "seed": None,
                    "input_id": None,
                }
                for stage in ([configured_final_stage] if final else required_stages)
            ]
        elif final and measurement_mode == "noisy":
            exploration_count = self.contract["measurement"]["exploration_repetitions"]
            confirmation_count = self.contract["measurement"]["confirmation_repetitions"]
            confirmation_count += max(
                0,
                self.contract["measurement"]["minimum_repetitions"]
                - exploration_count
                - confirmation_count,
            )
            launch_specs = [
                {
                    "stage": configured_final_stage,
                    "arm": "incumbent",
                    "phase": phase,
                    "pair_id": None,
                    "seed": f"final-{phase}-{index}",
                    "input_id": f"final-{phase}-{index}",
                }
                for phase, count in (
                    ("exploration", exploration_count),
                    ("confirmation", confirmation_count),
                )
                for index in range(count)
            ]
        elif final:
            launch_specs = [
                {
                    "stage": configured_final_stage,
                    "arm": "incumbent",
                    "phase": "deterministic",
                    "pair_id": None,
                    "seed": None,
                    "input_id": None,
                }
            ]
        elif measurement_mode == "noisy" and candidate:
            exploration_count = self.contract["measurement"]["exploration_repetitions"]
            confirmation_count = self.contract["measurement"]["confirmation_repetitions"]
            confirmation_count += max(
                0,
                self.contract["measurement"]["minimum_repetitions"]
                - exploration_count
                - confirmation_count,
            )
            paired_specs: list[dict[str, Any]] = []
            pair_ordinal = 0
            for phase, count in (
                ("exploration", exploration_count),
                ("confirmation", confirmation_count),
            ):
                for index in range(count):
                    arm_order = (
                        ("incumbent", "candidate")
                        if pair_ordinal % 2 == 0
                        else ("candidate", "incumbent")
                    )
                    paired_specs.extend(
                        {
                            "stage": "validation",
                            "arm": arm,
                            "phase": phase,
                            "pair_id": f"pair-{phase}-{index}",
                            "seed": f"{phase}-{index}",
                            "input_id": f"input-{phase}-{index}",
                        }
                        for arm in arm_order
                    )
                    pair_ordinal += 1
            launch_specs = [
                {
                    "stage": "development",
                    "arm": "candidate",
                    "phase": "exploration",
                    "pair_id": None,
                    "seed": "dev",
                    "input_id": "dev",
                },
                *paired_specs,
            ]
        elif measurement_mode == "noisy":
            exploration_count = self.contract["measurement"]["exploration_repetitions"]
            confirmation_count = self.contract["measurement"]["confirmation_repetitions"]
            confirmation_count += max(
                0,
                self.contract["measurement"]["minimum_repetitions"]
                - exploration_count
                - confirmation_count,
            )
            launch_specs = [
                {
                    "stage": "development",
                    "arm": "incumbent",
                    "phase": "exploration",
                    "pair_id": None,
                    "seed": "dev",
                    "input_id": "dev",
                },
                *[
                    {
                        "stage": "validation",
                        "arm": "incumbent",
                        "phase": phase,
                        "pair_id": None,
                        "seed": f"baseline-{phase}-{index}",
                        "input_id": f"baseline-{phase}-{index}",
                    }
                    for phase, count in (
                        ("exploration", exploration_count),
                        ("confirmation", confirmation_count),
                    )
                    for index in range(count)
                ],
            ]
        else:
            launch_specs = [
                {
                    "stage": stage,
                    "arm": "candidate" if candidate else "incumbent",
                    "phase": "deterministic",
                    "pair_id": None,
                    "seed": None,
                    "input_id": None,
                }
                for stage in required_stages
            ]
        planned_queries = sum(
            1 for spec in launch_specs if spec["stage"] in protected_stages
        )
        def effective_config_text(stage: str) -> str:
            return (
                json.dumps(
                    {
                        "schema_version": 1,
                        "stage": stage,
                        "stage_access_sha256": self.contract["stage_access_sha256"][stage],
                        "settings": {},
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )

        workspace_refs: dict[str, dict[str, str]] = {}
        for arm in {spec["arm"] for spec in launch_specs}:
            workspace_commit = commit if arm == "candidate" else incumbent
            workspace_record = self.workspace_record(
                attempt_id, iteration, arm, workspace_commit
            )
            workspace_refs[arm] = self.artifact(
                f"snapshots/{attempt_id}-ws-{iteration}-{arm}.json",
                json.dumps(
                    workspace_record,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
            )
        launch_plan_entries = []
        for ordinal, spec in enumerate(launch_specs):
            stage = spec["stage"]
            launch_plan_entries.append(
                {
                    "ordinal": ordinal,
                    "stage": stage,
                    "arm": spec["arm"],
                    "phase": spec["phase"],
                    "pair_id": spec["pair_id"],
                    "seed": spec["seed"],
                    "input_id": spec["input_id"],
                    "workspace_id": f"ws-{iteration}-{spec['arm']}",
                    "workspace": workspace_refs[spec["arm"]],
                    "allocation": {
                        "evaluator_calls": 1,
                        "validation_queries": 1 if stage in protected_stages else 0,
                    },
                    "timeout_seconds": self.contract["evaluation"]["timeout_seconds"],
                    "stage_access_sha256": self.contract["stage_access_sha256"][stage],
                    "effective_config_sha256": hashlib.sha256(
                        effective_config_text(stage).encode("utf-8")
                    ).hexdigest(),
                    "generated_state_sha256": self.contract[
                        "generated_state_policy"
                    ]["initial_state_sha256"],
                }
            )
        launch_plan = self.artifact(
            f"snapshots/{attempt_id}-launch-plan.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "attempt_id": attempt_id,
                    "launches": launch_plan_entries,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
        )
        design = self.design_artifact(experiment_id, incumbent) if candidate else None
        common = {
            "attempt_id": attempt_id,
            "experiment_id": experiment_id,
            "iteration": iteration,
            "role": role,
        }
        prior_calls = sum(
            prior["budget"]["actual"]["evaluator_calls"] for prior in self.results
        )
        prior_queries = sum(
            prior["budget"]["actual"]["validation_queries"] for prior in self.results
        )
        admission = {
            "allowed": True,
            "deadline_fit": True,
            "checked_at": self.timestamp(),
            "worst_case_end_at": self.timestamp(offset_seconds=60),
            "cumulative_before": {
                "evaluator_calls": prior_calls,
                "validation_queries": prior_queries,
            },
            "requested_worst_case": {
                "evaluator_calls": len(launch_specs),
                "validation_queries": planned_queries,
            },
            "protected_after_launch": {
                unit: budget["operational_reserve"]
                + (0 if final else budget["final_test_reserve"])
                for unit, budget in self.contract["budgets"].items()
            },
        }
        admission_ref = self.artifact(
            f"snapshots/{attempt_id}-admission.json",
            json.dumps(admission, sort_keys=True, separators=(",", ":")) + "\n",
        )
        prior_boundary = None
        if self.results:
            previous_result = self.results[-1]
            previous_attempt_id = previous_result["attempt_id"]
            previous_boundary_path = (
                self.run / "boundaries" / f"{previous_attempt_id}.json"
            )
            previous_finalized = next(
                event
                for event in reversed(self.events)
                if event["event"] == "finalized"
                and event["attempt_id"] == previous_attempt_id
            )
            prior_boundary = {
                "path": f"boundaries/{previous_attempt_id}.json",
                "sha256": file_sha256(previous_boundary_path),
                "attempt_id": previous_attempt_id,
                "finalized_seq": previous_finalized["seq"],
                "result_sha256": canonical_sha256(previous_result),
            }
        prepared = self.event(
            "prepared",
            incumbent_commit=incumbent,
            pre_attempt_head_commit=pre_attempt_head,
            design=design,
            launch_plan=launch_plan,
            admission=admission_ref,
            prior_boundary=prior_boundary,
            **common,
        )
        if candidate:
            self.event("committed", candidate_commit=commit, **common)
        repetitions: list[dict[str, Any]] = []
        result_ids: list[str] = []
        attempt_stages = [configured_final_stage] if final else list(required_stages)
        stage_evidence = {stage: [] for stage in attempt_stages}
        stage_counts = {
            stage: sum(1 for spec in launch_specs if spec["stage"] == stage)
            for stage in attempt_stages
        }
        integrity_emitted = False
        for ordinal, spec in enumerate(launch_specs):
            stage = spec["stage"]
            if (
                candidate
                and stage in protected_stages
                and not integrity_emitted
            ):
                prior_candidate_intent = next(
                    event
                    for event in reversed(self.events)
                    if event.get("event") == "launch_intent"
                    and event.get("attempt_id") == attempt_id
                    and event.get("arm") == "candidate"
                )
                self.event(
                    "integrity_cleared",
                    candidate_commit=commit,
                    evaluated_tree_sha256=prior_candidate_intent[
                        "evaluated_tree_sha256"
                    ],
                    scope_audit="pass",
                    leakage_audit="pass",
                    constraint_results={
                        constraint: True
                        for constraint in self.contract["objective"][
                            "hard_constraints"
                        ]
                    },
                    **common,
                )
                integrity_emitted = True
            identity_suffix = (
                stage if stage_counts.get(stage) == 1 else f"{stage}-{ordinal:02d}"
            )
            result_id = f"r-{iteration:03d}-{identity_suffix}"
            launch_id = f"l-{iteration:03d}-{identity_suffix}"
            evaluated_commit = (
                commit if candidate and spec["arm"] == "candidate" else incumbent
            )
            stage_access_sha256 = self.contract["stage_access_sha256"][stage]
            config = self.artifact(
                f"snapshots/{result_id}-config.json",
                effective_config_text(stage),
            )
            generated = self.artifact(
                f"snapshots/{result_id}-state.json",
                EMPTY_GENERATED_STATE_BYTES.decode("utf-8"),
            )
            snapshot = self.artifact(
                f"snapshots/{result_id}-tree.json",
                json.dumps(
                    {
                        "schema_version": 1,
                        "result_id": result_id,
                        "commit": evaluated_commit,
                        "tree_oid": self.tree_oid(evaluated_commit),
                        "untracked_writable_paths": [],
                        "unexpected_paths": [],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
            )
            query_use = 1 if stage in protected_stages else 0
            self.event(
                "launch_intent",
                launch_id=launch_id,
                result_id=result_id,
                launch_ordinal=ordinal,
                stage=stage,
                arm=spec["arm"],
                phase=spec["phase"],
                pair_id=spec["pair_id"],
                seed=spec["seed"],
                input_id=spec["input_id"],
                workspace_id=f"ws-{iteration}-{spec['arm']}",
                workspace=workspace_refs[spec["arm"]],
                stage_access_sha256=stage_access_sha256,
                evaluated_commit=evaluated_commit,
                invocation_sha256=self.contract["evaluation"]["invocation_sha256"],
                timeout_seconds=self.contract["evaluation"]["timeout_seconds"],
                effective_config_sha256=config["sha256"],
                generated_state_sha256=generated["sha256"],
                evaluated_tree_sha256=snapshot["sha256"],
                effective_config=config,
                generated_state=generated,
                evaluated_tree_snapshot=snapshot,
                admission_sha256=admission_ref["sha256"],
                allocation={
                    "evaluator_calls": 1,
                    "validation_queries": query_use,
                },
                cwd=".",
                log_partial_path=f"logs/{result_id}.log.partial",
                **common,
            )
            if abort_launch:
                self.event(
                    "launch_aborted",
                    launch_id=launch_id,
                    result_id=result_id,
                    launch_ordinal=ordinal,
                    absence_verified=True,
                    reason="fixture proved the process never started",
                    **common,
                )
                self.event(
                    "stage_completed",
                    stage=stage,
                    outcome="unknown",
                    result_ids=[],
                    rule_evaluation="launch was proven absent",
                    **common,
                )
                break
            self.event(
                "launched",
                launch_id=launch_id,
                result_id=result_id,
                launch_ordinal=ordinal,
                pid=1000 + iteration * 100 + ordinal,
                pgid=1000 + iteration * 100 + ordinal,
                process_start_id=f"fixture-start-{iteration}-{ordinal}",
                external_job_id=None,
                **common,
            )
            self.event(
                "launch_finished",
                launch_id=launch_id,
                result_id=result_id,
                launch_ordinal=ordinal,
                outcome="completed",
                actual_usage={"evaluator_calls": 1, "validation_queries": query_use},
                cleanup_verified=not cleanup_via_confirmation,
                **common,
            )
            if cleanup_via_confirmation:
                self.event(
                    "cleanup_confirmed",
                    launch_id=launch_id,
                    result_id=result_id,
                    launch_ordinal=ordinal,
                    verification="fixture process identity is absent",
                    **common,
                )
            score = (
                0.0
                if final and not keep
                else (
                    1.1
                    if candidate
                    and keep
                    and stage == acceptance_stage
                    and spec["arm"] == "candidate"
                    else (
                        1.0
                        if spec["arm"] == "incumbent"
                        or keep
                        or stage != acceptance_stage
                        else 0.0
                    )
                )
            )
            measurement = {
                "result_id": result_id,
                "pair_id": spec["pair_id"],
                "arm": spec["arm"],
                "phase": spec["phase"],
                "stage": stage,
                "evaluated_commit": evaluated_commit,
                "seed": spec["seed"],
                "input_id": spec["input_id"],
                "metrics": {"score": score},
                "primary_value": score,
                "duration_seconds": 1.0,
                "evaluator_sha256": self.contract["evaluation"]["evaluator_sha256"],
            }
            evaluator_output = (
                json.dumps(
                    {"schema_version": 1, **measurement},
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
            raw = self.artifact(
                f"evidence/{result_id}.json", evaluator_output
            )
            log = self.artifact(f"logs/{result_id}.log", evaluator_output)
            repetition = {
                **measurement,
                "result_artifact": raw,
                "log": log,
                "effective_config": config,
                "generated_state": generated,
                "evaluated_tree_snapshot": snapshot,
            }
            self.event(
                "evidence_published",
                launch_id=launch_id,
                result_id=result_id,
                launch_ordinal=ordinal,
                result_artifact=raw,
                log=log,
                repetition_sha256=canonical_sha256(repetition),
                **common,
            )
            repetitions.append(repetition)
            result_ids.append(result_id)
            stage_evidence[stage].append(result_id)
            next_stage = (
                launch_specs[ordinal + 1]["stage"]
                if ordinal + 1 < len(launch_specs)
                else None
            )
            if next_stage != stage:
                stage_outcome = (
                    "pass" if keep or stage != acceptance_stage else "fail"
                )
                self.event(
                    "stage_completed",
                    stage=stage,
                    outcome=stage_outcome,
                    result_ids=list(stage_evidence[stage]),
                    rule_evaluation="fixture stage rule",
                    **common,
                )
        evaluated_event = self.event("evaluated", result_ids=result_ids, **common)
        status = "cancelled" if abort_launch else ("keep" if keep else "discard")
        lane = (
            "confirmed"
            if keep
            else ("candidate" if candidate and not abort_launch else "diagnostic")
        )
        maturity = "unknown" if abort_launch else "complete"
        stage_results = {
            stage: (
                "unknown"
                if abort_launch
                else ("pass" if keep or stage != acceptance_stage else "fail")
            )
            for stage in attempt_stages
        }
        rollback_action = "none" if keep or not candidate else "revert"
        incumbent_after = commit if keep else incumbent
        decided = self.event(
            "decided",
            status=status,
            lane=lane,
            evidence_maturity=maturity,
            accepted=keep,
            stage_results=stage_results,
            stage_evidence=stage_evidence,
            reason="fixture decision",
            rule_evaluation="fixture",
            incumbent_after=incumbent_after,
            rollback_action=rollback_action,
            **common,
        )
        rollback_commit = None
        if rollback_action == "revert":
            rollback_commit = self.REVERT
            self.event(
                "rolled_back",
                rollback_commit=rollback_commit,
                restored_incumbent_commit=incumbent,
                **common,
            )
        actual_calls = 0 if abort_launch else len(launch_specs)
        actual_queries = 0 if abort_launch else planned_queries
        aggregate_values = [
            repetition["primary_value"]
            for repetition in repetitions
            if repetition["stage"] == acceptance_stage
            and repetition["arm"] == ("candidate" if candidate else "incumbent")
        ]
        aggregate_primary = (
            sum(aggregate_values) / len(aggregate_values)
            if aggregate_values
            else None
        )
        split_metrics = {
            stage: (
                None
                if abort_launch
                else (
                    sum(values) / len(values)
                    if (
                        values := [
                            repetition["primary_value"]
                            for repetition in repetitions
                            if repetition["stage"] == stage
                            and repetition["arm"]
                            == ("candidate" if candidate else "incumbent")
                        ]
                    )
                    else None
                )
            )
            for stage in attempt_stages
        }
        result = {
            "schema_version": 1,
            **common,
            "commit": commit,
            "incumbent_commit": incumbent,
            "pre_attempt_head_commit": pre_attempt_head,
            "rollback_commit": rollback_commit,
            "started_at": prepared["at"],
            "ended_at": evaluated_event["at"],
            "contract_sha256": self.contract_digest,
            "status": status,
            "lane": lane,
            "evidence_maturity": maturity,
            "acceptance_reason": "fixture decision",
            "decision": {
                "decision_seq": decided["seq"],
                "journal_event_sha256": canonical_sha256(decided),
                "accepted": keep,
                "incumbent_after": incumbent_after,
                "rollback_required": rollback_action == "revert",
                "stage_results": decided["stage_results"],
                "stage_evidence": decided["stage_evidence"],
                "rule_evaluation": "fixture",
            },
            "primary_metric": "score",
            "primary_value": aggregate_primary,
            "split_metrics": split_metrics,
            "aggregation": self.contract["measurement"]["aggregation"],
            "secondary_metrics": {},
            "repetitions": repetitions,
            "duration_seconds": float(len(repetitions)),
            "validation_queries": actual_queries,
            "hypothesis": "fixture hypothesis",
            "change_summary": "fixture change" if candidate else "none",
            "design": design,
            "admission": admission,
            "budget": {
                "actual_complete": True,
                "actual": {
                    "evaluator_calls": actual_calls,
                    "validation_queries": actual_queries,
                },
                "remaining_after": {
                    "evaluator_calls": self.contract["budgets"]["evaluator_calls"]["total"]
                    - prior_calls
                    - actual_calls,
                    "validation_queries": self.contract["budgets"]["validation_queries"]["total"]
                    - prior_queries
                    - actual_queries,
                },
            },
            "leakage_audit": "not_run" if abort_launch else "pass",
            "counterexample_results": {
                counterexample: None if abort_launch else True
                for counterexample in self.contract["control_policy"][
                    "counterexamples"
                ]
            },
            "constraint_results": {
                constraint: None if abort_launch else True
                for constraint in self.contract["objective"]["hard_constraints"]
            },
            "finding": {
                "type": "diagnostic" if abort_launch else ("positive" if keep else "negative"),
                "summary": "fixture finding",
                "scope": "fixture",
                "caveats": "none",
                "next_action": "diagnose" if abort_launch else ("reuse" if keep else "avoid"),
                "revive_if": None,
                "result_ids": result_ids,
            },
            "failure": {"kind": "never_launched"} if abort_launch else None,
        }
        self.results.append(result)
        if finalize:
            digest = canonical_sha256(result)
            finalized_event = self.event(
                "finalized",
                decision_seq=decided["seq"],
                result_index=len(self.results) - 1,
                result_sha256=digest,
                boundary_path=f"boundaries/{attempt_id}.json",
                **common,
            )
            _write_json(
                self.run / "boundaries" / f"{attempt_id}.json",
                {
                    "schema_version": 1,
                    "kind": "attempt-boundary",
                    "attempt_id": attempt_id,
                    "experiment_id": experiment_id,
                    "contract_sha256": self.contract_digest,
                    "decision_seq": decided["seq"],
                    "finalized_seq": finalized_event["seq"],
                    "result_index": len(self.results) - 1,
                    "result_sha256": digest,
                    "journal_prefix_sha256": hashlib.sha256(
                        canonical_jsonl_bytes(self.events[: finalized_event["seq"]])
                    ).hexdigest(),
                    "results_prefix_sha256": hashlib.sha256(
                        canonical_jsonl_bytes(self.results)
                    ).hexdigest(),
                    "publication_nonce": hashlib.sha256(
                        f"fixture-boundary:{attempt_id}".encode("utf-8")
                    ).hexdigest(),
                    "finalized_at": self.timestamp(advance=True),
                },
            )
        return result

    def close_run(
        self, *, reason: str = "fixture_stop", unresolved_cleanup: bool = False
    ) -> None:
        final_incumbent = (
            self.results[-1]["decision"]["incumbent_after"]
            if self.results
            else self.BASE
        )
        unresolved_recovery = bool(
            self.events
            and next(
                (
                    event
                    for event in reversed(self.events)
                    if event.get("event") == "decided"
                ),
                {},
            ).get("rollback_action")
            == "preserve_and_stop"
        )
        final_head = final_incumbent
        if unresolved_recovery and self.results:
            final_head = self.results[-1]["commit"]
        elif self.results and self.results[-1].get("rollback_commit") is not None:
            final_head = self.results[-1]["rollback_commit"]
        self.event(
            "run_stopped",
            reason=reason,
            final_incumbent_commit=final_incumbent,
            final_head_commit=final_head,
            unresolved_cleanup=unresolved_cleanup,
            unresolved_recovery=unresolved_recovery,
        )

        def publish_run_boundary() -> None:
            confirmed = [
                result
                for result in self.results
                if result["status"] == "keep"
                and result["lane"] == "confirmed"
                and result["role"] != "final"
            ]
            best = confirmed[-1] if confirmed else None
            actual_budget = {
                unit: sum(result["budget"]["actual"][unit] for result in self.results)
                for unit in self.contract["budgets"]
            }
            canonical_artifacts = []
            for directory_name in (
                "designs",
                "evidence",
                "logs",
                "snapshots",
                "boundaries",
            ):
                for artifact in sorted((self.run / directory_name).rglob("*")):
                    if artifact.is_file() and not artifact.name.endswith((".partial", ".tmp")):
                        canonical_artifacts.append(
                            {
                                "path": artifact.relative_to(self.run).as_posix(),
                                "sha256": file_sha256(artifact),
                            }
                        )
            canonical_artifacts.sort(key=lambda item: item["path"])
            _write_json(
                self.run / "run.finalized.json",
                {
                    "schema_version": 1,
                    "kind": "run-boundary",
                    "run_tag": self.contract["run_tag"],
                    "contract_sha256": self.contract_digest,
                    "final_incumbent_commit": final_incumbent,
                    "final_head_commit": final_head,
                    "best_confirmed_attempt": best["attempt_id"] if best else None,
                    "best_confirmed_commit": best["commit"] if best else None,
                    "result_count": len(self.results),
                    "last_journal_seq": len(self.events),
                    "results_sha256": file_sha256(self.run / "results.jsonl"),
                    "journal_sha256": file_sha256(self.run / "journal.jsonl"),
                    "last_attempt_boundary_sha256": (
                        file_sha256(
                            self.run
                            / "boundaries"
                            / f"{self.results[-1]['attempt_id']}.json"
                        )
                        if self.results
                        else None
                    ),
                    "canonical_artifacts": canonical_artifacts,
                    "canonical_artifacts_sha256": canonical_sha256(
                        canonical_artifacts
                    ),
                    "actual_budget": actual_budget,
                    "actual_budget_complete": all(
                        result["budget"]["actual_complete"] for result in self.results
                    ),
                    "unresolved_cleanup": unresolved_cleanup,
                    "unresolved_recovery": unresolved_recovery,
                    "stop_reason": reason,
                    "finalized_at": self.timestamp(advance=True),
                },
            )

        self.post_flush = publish_run_boundary

    def flush(self) -> None:
        _append_jsonl(self.run / "journal.jsonl", self.events)
        _append_jsonl(self.run / "results.jsonl", self.results)

    def rebind_attempt_boundary(self, attempt_id: str) -> None:
        result_index = next(
            index
            for index, result in enumerate(self.results)
            if result.get("attempt_id") == attempt_id
        )
        result = self.results[result_index]
        finalized = next(
            event
            for event in self.events
            if event.get("event") == "finalized"
            and event.get("attempt_id") == attempt_id
        )
        result_digest = canonical_sha256(result)
        finalized["result_sha256"] = result_digest
        boundary_path = self.run / "boundaries" / f"{attempt_id}.json"
        boundary = load_json_file(boundary_path, Diagnostics())
        assert boundary is not None
        boundary["result_sha256"] = result_digest
        boundary["journal_prefix_sha256"] = hashlib.sha256(
            canonical_jsonl_bytes(self.events[: finalized["seq"]])
        ).hexdigest()
        boundary["results_prefix_sha256"] = hashlib.sha256(
            canonical_jsonl_bytes(self.results[: result_index + 1])
        ).hexdigest()
        _write_json(boundary_path, boundary)

    def prune_unreferenced_artifacts(self) -> None:
        referenced: set[str] = set()

        def add_ref(value: Any) -> None:
            if isinstance(value, dict) and isinstance(value.get("path"), str):
                referenced.add(value["path"])

        for event in self.events:
            if event.get("event") == "prepared":
                add_ref(event.get("design"))
                add_ref(event.get("launch_plan"))
                add_ref(event.get("admission"))
                add_ref(event.get("prior_boundary"))
                launch_plan_ref = event.get("launch_plan")
                if isinstance(launch_plan_ref, dict) and isinstance(
                    launch_plan_ref.get("path"), str
                ):
                    plan = load_json_file(
                        self.run / launch_plan_ref["path"], Diagnostics()
                    )
                    if isinstance(plan, dict):
                        for planned_launch in plan.get("launches", []):
                            if isinstance(planned_launch, dict):
                                add_ref(planned_launch.get("workspace"))
            elif event.get("event") == "launch_intent":
                for field in (
                    "workspace",
                    "effective_config",
                    "generated_state",
                    "evaluated_tree_snapshot",
                ):
                    add_ref(event.get(field))
            elif event.get("event") == "evidence_published":
                add_ref(event.get("result_artifact"))
                add_ref(event.get("log"))
            elif event.get("event") == "finalized" and isinstance(
                event.get("boundary_path"), str
            ):
                referenced.add(event["boundary_path"])
        for result in self.results:
            add_ref(result.get("design"))
            for repetition in result.get("repetitions", []):
                if not isinstance(repetition, dict):
                    continue
                for field in (
                    "result_artifact",
                    "log",
                    "effective_config",
                    "generated_state",
                    "evaluated_tree_snapshot",
                ):
                    add_ref(repetition.get(field))
        for directory_name in (
            "designs",
            "evidence",
            "logs",
            "snapshots",
            "boundaries",
        ):
            for artifact in (self.run / directory_name).rglob("*"):
                if (
                    artifact.is_file()
                    and not artifact.is_symlink()
                    and not artifact.name.endswith((".partial", ".tmp"))
                    and artifact.relative_to(self.run).as_posix() not in referenced
                ):
                    artifact.unlink()


def run_self_test() -> tuple[dict[str, Any], int]:
    cases: list[dict[str, str]] = []

    def check(
        name: str,
        expected: str,
        builder: Any,
        expected_action: str | None = None,
        expected_phase: str | None = None,
        expected_error: str | None = None,
        expected_closed: bool | None = None,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="research-loop-validator-") as temp:
            fixture = Fixture(Path(temp))
            builder(fixture)
            fixture.flush()
            post_flush = getattr(fixture, "post_flush", None)
            if post_flush is not None:
                post_flush()
            output, _exit = validate_run(
                fixture.run, allow_incomplete=True, verify_git=False
            )
            actual = output["verdict"]
            action = output["resume_action"]
            phase = output["last_durable_phase"]
            if (
                actual != expected
                or action != expected_action
                or (expected_phase is not None and phase != expected_phase)
            ):
                raise AssertionError(
                    f"{name}: expected {expected}/{expected_phase}/{expected_action}, got {actual}/{phase}/{action}: {output['errors']}"
                )
            if expected_error is not None and expected_error not in {
                item["code"] for item in output["errors"]
            }:
                raise AssertionError(
                    f"{name}: missing expected error {expected_error}: {output['errors']}"
                )
            if expected_closed is not None and output["closed"] is not expected_closed:
                raise AssertionError(
                    f"{name}: expected closed={expected_closed}, got {output['closed']}"
                )
            cases.append({"name": name, "verdict": actual})

    def rebind_evaluation(fixture: Fixture, *, environment: bool = False) -> None:
        evaluation = fixture.contract["evaluation"]
        if environment:
            evaluation["environment_sha256"] = canonical_sha256(
                evaluation["environment"]
            )
        evaluation["invocation_sha256"] = canonical_sha256(
            invocation_fingerprint_payload(evaluation)
        )
        evaluation["evaluator_sha256"] = canonical_sha256(
            {
                "invocation_sha256": evaluation["invocation_sha256"],
                "immutable_files": fixture.contract["immutable_files"],
            }
        )
        fixture.reseal_contract()

    def rebind_stage_identity(fixture: Fixture, stage: str) -> None:
        access_digest = canonical_sha256(
            fixture.contract["stage_resources"][stage]
        )
        fixture.contract["stage_access_sha256"][stage] = access_digest
        config_bytes = (
            json.dumps(
                {
                    "schema_version": 1,
                    "stage": stage,
                    "stage_access_sha256": access_digest,
                    "settings": {},
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        fixture.contract["stage_config_sha256"][stage] = hashlib.sha256(
            config_bytes
        ).hexdigest()
        fixture.reseal_contract()

    check("valid_keep", "valid", lambda fixture: fixture.add_attempt(candidate=False, keep=True))

    def valid_closed(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        fixture.close_run()

    check("valid_closed", "valid", valid_closed, expected_closed=True)

    def valid_empty_cancelled(fixture: Fixture) -> None:
        fixture.event(
            "stop_requested",
            source="user",
            stop_kind="user_interruption",
            final_test_skip_authorized=True,
            reason="cancelled before baseline",
            rule_evaluation="explicit pre-baseline cancellation",
        )
        fixture.close_run(reason="cancelled_before_baseline")

    check(
        "valid_empty_cancelled_run",
        "valid",
        valid_empty_cancelled,
        expected_closed=True,
    )

    check(
        "invalid_empty_run_without_stop_request",
        "invalid",
        lambda fixture: fixture.close_run(reason="unexplained_empty_close"),
        expected_error="empty_run_without_stop_request",
    )

    def valid_failed_baseline_close(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=False)
        fixture.close_run(reason="baseline_failed")

    check(
        "valid_failed_baseline_close",
        "valid",
        valid_failed_baseline_close,
        expected_closed=True,
    )

    def valid_discard(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        fixture.add_attempt(candidate=True, keep=False)

    check("valid_discard_rollback", "valid", valid_discard)

    def missing_prior_boundary_binding(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        fixture.add_attempt(candidate=True, keep=False)
        prepared = next(
            event
            for event in fixture.events
            if event["event"] == "prepared" and event["attempt_id"] == "a-001"
        )
        prepared["prior_boundary"] = None

    check(
        "invalid_missing_prior_boundary_binding",
        "invalid",
        missing_prior_boundary_binding,
        expected_error="missing_prior_boundary",
    )

    def unsafe_attempt_id(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        fixture.event(
            "prepared",
            attempt_id="../escape",
            experiment_id="unsafe",
            iteration=1,
            role="candidate",
            incumbent_commit=fixture.BASE,
        )

    check(
        "invalid_unsafe_attempt_id",
        "invalid",
        unsafe_attempt_id,
        expected_error="invalid_id",
    )

    def wrong_incomplete_incumbent(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        fixture.event(
            "prepared",
            attempt_id="a-001",
            experiment_id="exp-001",
            iteration=1,
            role="candidate",
            incumbent_commit="d" * 40,
        )

    check(
        "invalid_incomplete_incumbent",
        "invalid",
        wrong_incomplete_incumbent,
        expected_error="incumbent_chain",
    )

    def keep_without_candidate_arm(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        result = fixture.add_attempt(candidate=True, keep=True)
        for repetition in result["repetitions"]:
            repetition["arm"] = "incumbent"
            repetition["evaluated_commit"] = fixture.BASE

    check(
        "invalid_keep_without_candidate_arm",
        "invalid",
        keep_without_candidate_arm,
        expected_error="promotion_without_candidate_evidence",
    )

    def omitted_budget_unit(fixture: Fixture) -> None:
        result = fixture.add_attempt(candidate=False, keep=True)
        result["budget"]["actual"].pop("validation_queries")

    check(
        "invalid_omitted_budget_unit",
        "invalid",
        omitted_budget_unit,
        expected_error="budget_units_mismatch",
    )

    def close_preflight_failure(fixture: Fixture, reason: str) -> None:
        fixture.event(
            "stop_requested",
            source="fixture",
            stop_kind="integrity",
            final_test_skip_authorized=False,
            reason=reason,
            rule_evaluation="contract rejected before launch",
        )
        fixture.close_run(reason=reason)

    def missing_evaluator_budget(fixture: Fixture) -> None:
        fixture.contract["budgets"].pop("evaluator_calls")
        fixture.contract["per_attempt_max"].pop("evaluator_calls")
        fixture.contract["control_policy"]["actual_usage_sources"].pop(
            "evaluator_calls"
        )
        fixture.reseal_contract()
        close_preflight_failure(fixture, "missing evaluator budget")

    check(
        "invalid_missing_evaluator_budget",
        "invalid",
        missing_evaluator_budget,
        expected_error="missing_required_budget_unit",
    )

    def missing_protected_query_budget(fixture: Fixture) -> None:
        fixture.contract["budgets"].pop("validation_queries")
        fixture.contract["per_attempt_max"].pop("validation_queries")
        fixture.contract["control_policy"]["actual_usage_sources"].pop(
            "validation_queries"
        )
        fixture.reseal_contract()
        close_preflight_failure(fixture, "missing protected query budget")

    check(
        "invalid_missing_protected_query_budget",
        "invalid",
        missing_protected_query_budget,
        expected_error="missing_required_budget_unit",
    )

    def insufficient_per_attempt_budget(fixture: Fixture) -> None:
        fixture.contract["per_attempt_max"]["evaluator_calls"] = 1
        fixture.reseal_contract()
        close_preflight_failure(fixture, "insufficient attempt cap")

    check(
        "invalid_insufficient_per_attempt_budget",
        "invalid",
        insufficient_per_attempt_budget,
        expected_error="insufficient_per_attempt_budget",
    )

    def insufficient_run_budget(fixture: Fixture) -> None:
        fixture.contract["budgets"]["evaluator_calls"]["total"] = 4
        fixture.reseal_contract()
        close_preflight_failure(fixture, "insufficient total budget")

    check(
        "invalid_insufficient_run_budget",
        "invalid",
        insufficient_run_budget,
        expected_error="insufficient_run_budget",
    )

    def mismatched_raw_evidence(fixture: Fixture) -> None:
        result = fixture.add_attempt(candidate=False, keep=True)
        repetition = next(
            item for item in result["repetitions"] if item["stage"] == "validation"
        )
        raw_path = fixture.run / repetition["result_artifact"]["path"]
        raw = strict_loads(raw_path.read_text(encoding="utf-8"))
        raw["metrics"] = {"score": 0.0}
        raw["primary_value"] = 0.0
        _write_json(raw_path, raw)
        repetition["result_artifact"]["sha256"] = file_sha256(raw_path)

    check(
        "invalid_raw_evidence_mismatch",
        "invalid",
        mismatched_raw_evidence,
        expected_error="evidence_content_mismatch",
    )

    def forged_aggregate(fixture: Fixture) -> None:
        result = fixture.add_attempt(candidate=False, keep=True)
        result["primary_value"] = 999.0
        result["split_metrics"]["validation"] = 999.0

    check(
        "invalid_forged_aggregate",
        "invalid",
        forged_aggregate,
        expected_error="aggregate_metric_mismatch",
    )

    def missing_hard_constraint(fixture: Fixture) -> None:
        result = fixture.add_attempt(candidate=False, keep=True)
        result["constraint_results"].clear()

    check(
        "invalid_missing_hard_constraint",
        "invalid",
        missing_hard_constraint,
        expected_error="missing_constraint_result",
    )

    def malformed_constraint_outcome(fixture: Fixture) -> None:
        result = fixture.add_attempt(candidate=False, keep=True)
        result["constraint_results"]["integrity"] = {}

    check(
        "invalid_nonnumeric_constraint_container",
        "invalid",
        malformed_constraint_outcome,
        expected_error="invalid_constraint_result",
    )

    def missing_counterexample(fixture: Fixture) -> None:
        result = fixture.add_attempt(candidate=False, keep=True)
        result["counterexample_results"].clear()

    check(
        "invalid_missing_counterexample",
        "invalid",
        missing_counterexample,
        expected_error="missing_counterexample_result",
    )

    def failed_counterexample_promotion(fixture: Fixture) -> None:
        result = fixture.add_attempt(candidate=False, keep=True)
        result["counterexample_results"]["fixture_integrity"] = False

    check(
        "invalid_failed_counterexample_promotion",
        "invalid",
        failed_counterexample_promotion,
        expected_error="illegal_promotion",
    )

    def valid_without_hard_constraints(fixture: Fixture) -> None:
        fixture.contract["objective"]["hard_constraints"] = []
        fixture.contract["control_policy"]["hard_constraint_rules"] = {}
        fixture.reseal_contract()
        fixture.add_attempt(candidate=False, keep=True)

    check(
        "valid_empty_hard_constraints",
        "valid",
        valid_without_hard_constraints,
    )

    def malformed_hard_constraints(fixture: Fixture) -> None:
        fixture.contract["objective"]["hard_constraints"] = 7
        fixture.reseal_contract()
        fixture.event(
            "stop_requested",
            source="fixture",
            stop_kind="integrity",
            final_test_skip_authorized=False,
            reason="malformed contract fixture",
            rule_evaluation="stop",
        )
        fixture.close_run(reason="malformed_contract")

    check(
        "invalid_malformed_hard_constraints",
        "invalid",
        malformed_hard_constraints,
        expected_error="invalid_field",
    )

    def overflowing_json_number(fixture: Fixture) -> None:
        contract_path = fixture.run / "contract.json"
        raw = contract_path.read_text(encoding="utf-8")
        raw = raw.replace('"minimum_delta":0.05', '"minimum_delta":1e999')
        contract_path.write_text(raw, encoding="utf-8")
        (fixture.run / "contract.sha256").write_text(
            file_sha256(contract_path) + "\n", encoding="ascii"
        )

    check(
        "invalid_overflowing_json_number",
        "invalid",
        overflowing_json_number,
        expected_error="invalid_json",
    )

    def extra_contract_claim(fixture: Fixture) -> None:
        fixture.contract["unvalidated_claim"] = "trusted by an alternate consumer"
        fixture.reseal_contract()
        fixture.event(
            "stop_requested",
            source="fixture",
            stop_kind="integrity",
            final_test_skip_authorized=False,
            reason="invalid contract fixture",
            rule_evaluation="reject unknown contract fields",
        )
        fixture.close_run(reason="invalid_contract")

    check(
        "invalid_extra_contract_claim",
        "invalid",
        extra_contract_claim,
        expected_error="contract_schema_mismatch",
    )

    def mismatched_protocol_version(fixture: Fixture) -> None:
        fixture.contract["protocol"]["version"] = "0.0.0"
        fixture.reseal_contract()
        fixture.add_attempt(candidate=False, keep=True)

    check(
        "invalid_protocol_version_mismatch",
        "invalid",
        mismatched_protocol_version,
        expected_error="protocol_version_mismatch",
    )

    def mismatched_validator_version(fixture: Fixture) -> None:
        fixture.contract["protocol"]["validator_sha256"] = "0" * 64
        fixture.reseal_contract()
        fixture.add_attempt(candidate=False, keep=True)

    check(
        "invalid_validator_version_mismatch",
        "invalid",
        mismatched_validator_version,
        expected_error="validator_version_mismatch",
    )

    def unsealed_hard_constraint_rule(fixture: Fixture) -> None:
        fixture.contract["control_policy"]["hard_constraint_rules"].clear()
        fixture.reseal_contract()
        close_preflight_failure(fixture, "missing hard constraint rule")

    check(
        "invalid_unsealed_hard_constraint_rule",
        "invalid",
        unsealed_hard_constraint_rule,
        expected_error="invalid_hard_constraint_rules",
    )

    def malformed_counterexample_rules(fixture: Fixture) -> None:
        fixture.contract["control_policy"]["counterexamples"] = [
            "unkeyed counterexample"
        ]
        fixture.reseal_contract()
        close_preflight_failure(fixture, "malformed counterexample rules")

    check(
        "invalid_malformed_counterexample_rules",
        "invalid",
        malformed_counterexample_rules,
        expected_error="invalid_counterexamples",
    )

    def unbounded_without_authorization(fixture: Fixture) -> None:
        fixture.contract["budgets"]["evaluator_calls"]["total"] = None
        fixture.reseal_contract()
        close_preflight_failure(fixture, "unbounded without authorization")

    check(
        "invalid_unbounded_without_authorization",
        "invalid",
        unbounded_without_authorization,
        expected_error="missing_open_ended_authorization",
    )

    def authorized_unbounded_contract(fixture: Fixture) -> None:
        fixture.contract["budgets"]["evaluator_calls"]["total"] = None
        fixture.contract["control_policy"][
            "authorization"
        ] = "run_until_interrupted"
        fixture.reseal_contract()
        close_preflight_failure(fixture, "authorized interruption")

    check(
        "valid_authorized_unbounded_contract",
        "valid",
        authorized_unbounded_contract,
        expected_closed=True,
    )

    def invalid_cleanup_grace(fixture: Fixture) -> None:
        fixture.contract["control_policy"]["cleanup_grace_seconds"] = 0
        fixture.reseal_contract()
        close_preflight_failure(fixture, "invalid cleanup grace")

    check(
        "invalid_cleanup_grace",
        "invalid",
        invalid_cleanup_grace,
        expected_error="invalid_cleanup_grace",
    )

    def altered_environment_preimage(fixture: Fixture) -> None:
        fixture.contract["evaluation"]["environment"]["variables"]["LANG"] = "C"
        fixture.reseal_contract()
        fixture.add_attempt(candidate=False, keep=True)

    check(
        "invalid_altered_environment_preimage",
        "invalid",
        altered_environment_preimage,
        expected_error="environment_hash_mismatch",
    )

    def secret_in_environment_preimage(fixture: Fixture) -> None:
        fixture.contract["evaluation"]["environment"]["variables"][
            "SERVICE_API_KEY"
        ] = "must-not-be-recorded"
        rebind_evaluation(fixture, environment=True)
        fixture.add_attempt(candidate=False, keep=True)

    check(
        "invalid_secret_environment_value",
        "invalid",
        secret_in_environment_preimage,
        expected_error="secret_environment_value",
    )

    def secret_execution_control_environment(fixture: Fixture) -> None:
        fixture.contract["evaluation"]["environment"][
            "secret_variable_names"
        ] = ["PYTHONPATH"]
        rebind_evaluation(fixture, environment=True)
        fixture.add_attempt(candidate=False, keep=True)

    check(
        "invalid_secret_execution_control_environment",
        "invalid",
        secret_execution_control_environment,
        expected_error="execution_control_environment_variable",
    )

    def sealed_execution_control_environment(fixture: Fixture) -> None:
        fixture.contract["evaluation"]["environment"]["variables"][
            "NODE_OPTIONS"
        ] = "--require=/tmp/unsealed.js"
        rebind_evaluation(fixture, environment=True)
        fixture.add_attempt(candidate=False, keep=True)

    check(
        "invalid_sealed_execution_control_environment",
        "invalid",
        sealed_execution_control_environment,
        expected_error="execution_control_environment_variable",
    )

    def unresolved_executable_argv(fixture: Fixture) -> None:
        fixture.contract["evaluation"]["argv"][0] = "python3"
        rebind_evaluation(fixture)
        fixture.add_attempt(candidate=False, keep=True)

    check(
        "invalid_unresolved_executable_argv",
        "invalid",
        unresolved_executable_argv,
        expected_error="executable_argv_mismatch",
    )

    def altered_executable_digest(fixture: Fixture) -> None:
        fixture.contract["evaluation"]["executable_sha256"] = "0" * 64
        rebind_evaluation(fixture)
        fixture.add_attempt(candidate=False, keep=True)

    check(
        "invalid_altered_executable_digest",
        "invalid",
        altered_executable_digest,
        expected_error="executable_hash_mismatch",
    )

    def reused_stage_resource_manifest(fixture: Fixture) -> None:
        fixture.contract["stage_resources"]["validation"][
            "resource_manifest_sha256"
        ] = fixture.contract["stage_resources"]["development"][
            "resource_manifest_sha256"
        ]
        rebind_stage_identity(fixture, "validation")
        fixture.add_attempt(candidate=False, keep=True)

    check(
        "invalid_reused_stage_resource_manifest",
        "invalid",
        reused_stage_resource_manifest,
        expected_error="stage_resource_manifests_not_disjoint",
    )

    def reused_stage_capability(fixture: Fixture) -> None:
        fixture.contract["stage_resources"]["validation"][
            "capability_sha256"
        ] = fixture.contract["stage_resources"]["development"][
            "capability_sha256"
        ]
        rebind_stage_identity(fixture, "validation")
        fixture.add_attempt(candidate=False, keep=True)

    check(
        "invalid_reused_stage_capability",
        "invalid",
        reused_stage_capability,
        expected_error="stage_capabilities_not_disjoint",
    )

    def configure_third_protected_stage(fixture: Fixture) -> None:
        fixture.contract["promotion"]["required_stages"].append("robustness")
        fixture.contract["promotion"]["required_telemetry"]["robustness"] = [
            "score"
        ]
        fixture.contract["stage_resources"]["robustness"] = {
            "resource_manifest_sha256": hashlib.sha256(
                b"fixture-resource:robustness"
            ).hexdigest(),
            "capability_sha256": hashlib.sha256(
                b"fixture-capability:robustness"
            ).hexdigest(),
            "visibility": "protected",
        }
        fixture.contract["per_attempt_max"]["evaluator_calls"] = 3
        fixture.contract["per_attempt_max"]["validation_queries"] = 2
        rebind_stage_identity(fixture, "robustness")

    def valid_third_protected_stage(fixture: Fixture) -> None:
        configure_third_protected_stage(fixture)
        fixture.add_attempt(candidate=False, keep=True)

    check(
        "valid_third_protected_stage",
        "valid",
        valid_third_protected_stage,
    )

    def uncharged_third_protected_stage(fixture: Fixture) -> None:
        configure_third_protected_stage(fixture)
        result = fixture.add_attempt(candidate=False, keep=True)
        finish = next(
            event
            for event in fixture.events
            if event["event"] == "launch_finished"
            and event["result_id"].endswith("robustness")
        )
        finish["actual_usage"]["validation_queries"] = 0
        result["validation_queries"] -= 1
        result["budget"]["actual"]["validation_queries"] -= 1
        result["budget"]["remaining_after"]["validation_queries"] += 1

    check(
        "invalid_uncharged_third_protected_stage",
        "invalid",
        uncharged_third_protected_stage,
        expected_error="unreceipted_gate_evidence",
    )

    def protected_control_scope(fixture: Fixture) -> None:
        fixture.contract["scope"]["writable_paths"] = [".research-loop"]
        fixture.reseal_contract()
        fixture.add_attempt(candidate=False, keep=True)

    check(
        "invalid_protected_control_scope",
        "invalid",
        protected_control_scope,
        expected_error="protected_control_scope",
    )

    def symlinked_evaluation_cwd(fixture: Fixture) -> None:
        (fixture.worktree / "eval-link").symlink_to(".")
        evaluation = fixture.contract["evaluation"]
        evaluation["cwd"] = "eval-link"
        evaluation["invocation_sha256"] = canonical_sha256(
            invocation_fingerprint_payload(evaluation)
        )
        evaluation["evaluator_sha256"] = canonical_sha256(
            {
                "invocation_sha256": evaluation["invocation_sha256"],
                "immutable_files": fixture.contract["immutable_files"],
            }
        )
        fixture.reseal_contract()
        fixture.event(
            "stop_requested",
            source="fixture",
            stop_kind="integrity",
            final_test_skip_authorized=False,
            reason="invalid evaluator cwd",
            rule_evaluation="reject before launch",
        )
        fixture.close_run(reason="invalid_evaluator_cwd")

    check(
        "invalid_symlinked_evaluation_cwd",
        "invalid",
        symlinked_evaluation_cwd,
        expected_error="symlink_forbidden",
    )

    def incomplete_immutable_manifest(fixture: Fixture) -> None:
        (fixture.worktree / "additional-eval.txt").write_text("trusted\n", encoding="utf-8")
        fixture.contract["scope"]["immutable_paths"].append("additional-eval.txt")
        fixture.reseal_contract()
        fixture.add_attempt(candidate=False, keep=True)

    check(
        "invalid_incomplete_immutable_manifest",
        "invalid",
        incomplete_immutable_manifest,
        expected_error="immutable_coverage_mismatch",
    )

    def missing_gate_receipt(fixture: Fixture) -> None:
        result = fixture.add_attempt(candidate=False, keep=True)
        validation_finish = next(
            event
            for event in fixture.events
            if event["event"] == "launch_finished"
            and event["result_id"].endswith("-validation")
        )
        validation_finish["actual_usage"]["validation_queries"] = 0
        result["validation_queries"] = 0
        result["budget"]["actual"]["validation_queries"] = 0
        result["budget"]["remaining_after"]["validation_queries"] = 10

    check(
        "invalid_missing_gate_receipt",
        "invalid",
        missing_gate_receipt,
        expected_error="missing_gate_evidence",
    )

    def weak_candidate_promotion(fixture: Fixture) -> None:
        fixture.contract["objective"]["minimum_delta"] = 0.2
        fixture.reseal_contract()
        fixture.add_attempt(candidate=False, keep=True)
        fixture.add_attempt(candidate=True, keep=True)

    check(
        "invalid_primary_acceptance",
        "invalid",
        weak_candidate_promotion,
        expected_error="primary_acceptance_failed",
    )

    def missing_integrity_clearance(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        fixture.add_attempt(candidate=True, keep=True)
        fixture.events = [
            event
            for event in fixture.events
            if not (
                event["event"] == "integrity_cleared"
                and event["attempt_id"] == "a-001"
            )
        ]
        for sequence, event in enumerate(fixture.events, start=1):
            event["seq"] = sequence

    check(
        "invalid_missing_integrity_clearance",
        "invalid",
        missing_integrity_clearance,
        expected_error="protected_stage_without_integrity_clearance",
    )

    def configure_noisy(fixture: Fixture) -> None:
        fixture.contract["measurement"] = {
            "mode": "noisy",
            "aggregation": "mean",
            "minimum_repetitions": 2,
            "exploration_repetitions": 1,
            "confirmation_repetitions": 1,
            "uncertainty_rule": "paired_mean_delta",
        }
        fixture.contract["budgets"] = {
            "evaluator_calls": {
                "total": 30,
                "operational_reserve": 1,
                "final_test_reserve": 0,
            },
            "validation_queries": {
                "total": 20,
                "operational_reserve": 1,
                "final_test_reserve": 0,
            },
        }
        fixture.contract["per_attempt_max"] = {
            "evaluator_calls": 5,
            "validation_queries": 4,
        }
        fixture.reseal_contract()

    def rewrite_raw_repetition(fixture: Fixture, repetition: dict[str, Any]) -> None:
        raw_path = fixture.run / repetition["result_artifact"]["path"]
        raw = {"schema_version": 1}
        for key in (
            "result_id",
            "pair_id",
            "arm",
            "phase",
            "stage",
            "evaluated_commit",
            "seed",
            "input_id",
            "metrics",
            "primary_value",
            "duration_seconds",
            "evaluator_sha256",
        ):
            raw[key] = repetition[key]
        _write_json(raw_path, raw)
        repetition["result_artifact"]["sha256"] = file_sha256(raw_path)

    def valid_noisy_candidate(fixture: Fixture) -> None:
        configure_noisy(fixture)
        fixture.add_attempt(candidate=False, keep=True)
        fixture.add_attempt(candidate=True, keep=True)

    check("valid_noisy_candidate", "valid", valid_noisy_candidate)

    def noisy_baseline_without_confirmation(fixture: Fixture) -> None:
        configure_noisy(fixture)
        result = fixture.add_attempt(candidate=False, keep=True)
        repetition = next(
            item
            for item in result["repetitions"]
            if item["stage"] == "validation" and item["phase"] == "confirmation"
        )
        repetition["phase"] = "exploration"
        rewrite_raw_repetition(fixture, repetition)

    check(
        "invalid_noisy_baseline_without_confirmation",
        "invalid",
        noisy_baseline_without_confirmation,
        expected_error="insufficient_noisy_phases",
    )

    def noisy_pair_context_drift(fixture: Fixture) -> None:
        configure_noisy(fixture)
        fixture.add_attempt(candidate=False, keep=True)
        result = fixture.add_attempt(candidate=True, keep=True)
        repetition = next(
            item
            for item in result["repetitions"]
            if item["stage"] == "validation"
            and item["phase"] == "confirmation"
            and item["arm"] == "candidate"
        )
        replacement = fixture.artifact(
            "snapshots/pair-context-drift.json", '{"different":true}\n'
        )
        repetition["effective_config"] = replacement
        launch = next(
            event
            for event in fixture.events
            if event["event"] == "launch_intent"
            and event["result_id"] == repetition["result_id"]
        )
        launch["effective_config_sha256"] = replacement["sha256"]

    check(
        "invalid_noisy_pair_context_drift",
        "invalid",
        noisy_pair_context_drift,
        expected_error="pair_context_mismatch",
    )

    def noisy_confirmation_regression(fixture: Fixture) -> None:
        configure_noisy(fixture)
        fixture.add_attempt(candidate=False, keep=True)
        result = fixture.add_attempt(candidate=True, keep=True)
        repetition = next(
            item
            for item in result["repetitions"]
            if item["stage"] == "validation"
            and item["phase"] == "confirmation"
            and item["arm"] == "candidate"
        )
        repetition["metrics"]["score"] = 0.9
        repetition["primary_value"] = 0.9
        rewrite_raw_repetition(fixture, repetition)
        candidate_values = [
            item["primary_value"]
            for item in result["repetitions"]
            if item["stage"] == "validation" and item["arm"] == "candidate"
        ]
        result["primary_value"] = sum(candidate_values) / len(candidate_values)
        result["split_metrics"]["validation"] = result["primary_value"]

    check(
        "invalid_noisy_confirmation_regression",
        "invalid",
        noisy_confirmation_regression,
        expected_error="primary_acceptance_failed",
    )

    def noisy_reused_identity(fixture: Fixture) -> None:
        configure_noisy(fixture)
        fixture.add_attempt(candidate=False, keep=True)
        result = fixture.add_attempt(candidate=True, keep=True)
        for repetition in result["repetitions"]:
            if repetition["stage"] == "validation" and repetition["phase"] == "confirmation":
                repetition["seed"] = "exploration-0"
                repetition["input_id"] = "input-exploration-0"
                rewrite_raw_repetition(fixture, repetition)

    check(
        "invalid_noisy_reused_identity",
        "invalid",
        noisy_reused_identity,
        expected_error="reused_pair_identity",
    )

    def noisy_shared_workspace_root(fixture: Fixture) -> None:
        configure_noisy(fixture)
        fixture.add_attempt(candidate=False, keep=True)
        fixture.add_attempt(candidate=True, keep=True)
        prepared = next(
            event
            for event in fixture.events
            if event["event"] == "prepared" and event["attempt_id"] == "a-001"
        )
        plan_path = fixture.run / prepared["launch_plan"]["path"]
        plan = load_json_file(plan_path, Diagnostics())
        assert plan is not None
        incumbent_ref = next(
            launch["workspace"]
            for launch in plan["launches"]
            if launch["arm"] == "incumbent"
        )
        candidate_ref = next(
            launch["workspace"]
            for launch in plan["launches"]
            if launch["arm"] == "candidate"
        )
        incumbent_workspace = load_json_file(
            fixture.run / incumbent_ref["path"], Diagnostics()
        )
        candidate_path = fixture.run / candidate_ref["path"]
        candidate_workspace = load_json_file(candidate_path, Diagnostics())
        assert incumbent_workspace is not None and candidate_workspace is not None
        for field in (
            "root_path",
            "root_identity_sha256",
            "git_dir_identity_sha256",
            "git_common_dir_identity_sha256",
        ):
            candidate_workspace[field] = incumbent_workspace[field]
        _write_json(candidate_path, candidate_workspace)
        candidate_digest = file_sha256(candidate_path)
        for launch in plan["launches"]:
            if launch["arm"] == "candidate":
                launch["workspace"]["sha256"] = candidate_digest
        _write_json(plan_path, plan)
        prepared["launch_plan"]["sha256"] = file_sha256(plan_path)
        for event in fixture.events:
            if (
                event["event"] == "launch_intent"
                and event["attempt_id"] == "a-001"
                and event["arm"] == "candidate"
            ):
                event["workspace"]["sha256"] = candidate_digest
        fixture.rebind_attempt_boundary("a-001")

    check(
        "invalid_noisy_shared_workspace_root",
        "invalid",
        noisy_shared_workspace_root,
        expected_error="shared_arm_workspace_root",
    )

    def forged_workspace_root_identity(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        prepared = next(
            event for event in fixture.events if event["event"] == "prepared"
        )
        plan_path = fixture.run / prepared["launch_plan"]["path"]
        plan = load_json_file(plan_path, Diagnostics())
        assert plan is not None
        workspace_ref = plan["launches"][0]["workspace"]
        workspace_path = fixture.run / workspace_ref["path"]
        workspace = load_json_file(workspace_path, Diagnostics())
        assert workspace is not None
        workspace["root_identity_sha256"] = "0" * 64
        _write_json(workspace_path, workspace)
        workspace_digest = file_sha256(workspace_path)
        for launch in plan["launches"]:
            launch["workspace"]["sha256"] = workspace_digest
        _write_json(plan_path, plan)
        prepared["launch_plan"]["sha256"] = file_sha256(plan_path)
        for event in fixture.events:
            if event["event"] == "launch_intent":
                event["workspace"]["sha256"] = workspace_digest
        fixture.rebind_attempt_boundary("a-000")

    check(
        "invalid_forged_workspace_root_identity",
        "invalid",
        forged_workspace_root_identity,
        expected_error="workspace_root_identity_mismatch",
    )

    def noisy_uncounterbalanced_order(fixture: Fixture) -> None:
        configure_noisy(fixture)
        fixture.add_attempt(candidate=False, keep=True)
        fixture.add_attempt(candidate=True, keep=True)
        prepared = next(
            event
            for event in fixture.events
            if event["event"] == "prepared" and event["attempt_id"] == "a-001"
        )
        plan_path = fixture.run / prepared["launch_plan"]["path"]
        plan = load_json_file(plan_path, Diagnostics())
        assert plan is not None
        confirmation_indexes = [
            index
            for index, launch in enumerate(plan["launches"])
            if launch["stage"] == "validation"
            and launch["phase"] == "confirmation"
        ]
        confirmation = [plan["launches"][index] for index in confirmation_indexes]
        confirmation.sort(key=lambda launch: launch["arm"] != "incumbent")
        for index, launch in zip(confirmation_indexes, confirmation):
            plan["launches"][index] = launch
        for ordinal, launch in enumerate(plan["launches"]):
            launch["ordinal"] = ordinal
        _write_json(plan_path, plan)
        prepared["launch_plan"]["sha256"] = file_sha256(plan_path)
        fixture.rebind_attempt_boundary("a-001")

    check(
        "invalid_uncounterbalanced_arm_order",
        "invalid",
        noisy_uncounterbalanced_order,
        expected_error="uncounterbalanced_arm_order",
    )

    def stop_requires_plan_tail_receipt(fixture: Fixture) -> None:
        configure_noisy(fixture)
        fixture.add_attempt(candidate=False, keep=True)
        fixture.add_attempt(candidate=True, keep=True)
        cutoff = next(
            index
            for index, event in enumerate(fixture.events)
            if event.get("event") == "stage_completed"
            and event.get("attempt_id") == "a-001"
            and event.get("stage") == "development"
        )
        fixture.events = fixture.events[: cutoff + 1]
        fixture.results = fixture.results[:1]
        candidate_boundary = fixture.run / "boundaries" / "a-001.json"
        candidate_boundary.unlink()
        fixture.event(
            "stop_requested",
            source="user",
            stop_kind="user_interruption",
            final_test_skip_authorized=True,
            reason="stop before the remaining preregistered launches",
            rule_evaluation="fence plan tail",
        )
        fixture.prune_unreferenced_artifacts()

    check(
        "recoverable_plan_tail_cancellation",
        "recoverable",
        stop_requires_plan_tail_receipt,
        "append_plan_tail_cancelled",
        expected_phase="plan_tail_cancellation_pending",
    )

    def mismatched_admission_digest(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        intent = next(event for event in fixture.events if event["event"] == "launch_intent")
        intent["admission_sha256"] = "0" * 64

    check(
        "invalid_admission_binding",
        "invalid",
        mismatched_admission_digest,
        expected_error="admission_digest_mismatch",
    )

    def launch_with_denied_admission(fixture: Fixture) -> None:
        fixture.contract["budgets"]["evaluator_calls"]["total"] = 2
        fixture.contract["budgets"]["validation_queries"]["total"] = 1
        fixture.reseal_contract()
        fixture.add_attempt(candidate=False, keep=True)
        prepared = next(
            event for event in fixture.events if event["event"] == "prepared"
        )
        admission_path = fixture.run / prepared["admission"]["path"]
        admission = load_json_file(admission_path, Diagnostics())
        assert admission is not None
        admission["allowed"] = False
        _write_json(admission_path, admission)
        admission_digest = file_sha256(admission_path)
        prepared["admission"]["sha256"] = admission_digest
        first_intent_index = next(
            index
            for index, event in enumerate(fixture.events)
            if event["event"] == "launch_intent"
        )
        first_intent = fixture.events[first_intent_index]
        first_intent["admission_sha256"] = admission_digest
        fixture.events = fixture.events[: first_intent_index + 1]
        fixture.results.clear()
        (fixture.run / "boundaries" / "a-000.json").unlink()
        fixture.prune_unreferenced_artifacts()

    check(
        "invalid_launch_with_denied_admission",
        "invalid",
        launch_with_denied_admission,
        expected_error="launch_without_admission",
    )

    def mismatched_initial_state(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        intent = next(
            event for event in fixture.events if event["event"] == "launch_intent"
        )
        intent["generated_state_sha256"] = "0" * 64

    check(
        "invalid_initial_state_binding",
        "invalid",
        mismatched_initial_state,
        expected_error="generated_state_digest_mismatch",
    )

    def overlapping_launches(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        validation_index = next(
            index
            for index, event in enumerate(fixture.events)
            if event["event"] == "launch_intent" and event["stage"] == "validation"
        )
        validation_intent = fixture.events.pop(validation_index)
        first_launched_index = next(
            index
            for index, event in enumerate(fixture.events)
            if event["event"] == "launched"
        )
        fixture.events.insert(first_launched_index + 1, validation_intent)
        for sequence, event in enumerate(fixture.events, start=1):
            event["seq"] = sequence

    check(
        "invalid_overlapping_launches",
        "invalid",
        overlapping_launches,
        expected_error="overlapping_launch",
    )

    def missing_local_process_group(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        launched = next(event for event in fixture.events if event["event"] == "launched")
        launched["pgid"] = None

    check(
        "invalid_missing_local_process_group",
        "invalid",
        missing_local_process_group,
        expected_error="missing_process_identity",
    )

    def reused_process_identity(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        launched = [
            event for event in fixture.events if event["event"] == "launched"
        ]
        for field in ("pid", "pgid", "process_start_id"):
            launched[1][field] = launched[0][field]

    check(
        "invalid_reused_process_identity",
        "invalid",
        reused_process_identity,
        expected_error="reused_process_identity",
    )

    def launch_cwd_drift(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        intent = next(event for event in fixture.events if event["event"] == "launch_intent")
        intent["cwd"] = "different-directory"

    check(
        "invalid_launch_cwd_drift",
        "invalid",
        launch_cwd_drift,
        expected_error="launch_cwd_mismatch",
    )

    def launch_timeout_drift(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        intent = next(
            event for event in fixture.events if event["event"] == "launch_intent"
        )
        intent["timeout_seconds"] += 1

    check(
        "invalid_launch_timeout_drift",
        "invalid",
        launch_timeout_drift,
        expected_error="launch_timeout_mismatch",
    )

    def insufficient_admission_timeout_window(fixture: Fixture) -> None:
        evaluation = fixture.contract["evaluation"]
        evaluation["timeout_seconds"] = 40
        evaluation["invocation_sha256"] = canonical_sha256(
            invocation_fingerprint_payload(evaluation)
        )
        evaluation["evaluator_sha256"] = canonical_sha256(
            {
                "invocation_sha256": evaluation["invocation_sha256"],
                "immutable_files": fixture.contract["immutable_files"],
            }
        )
        fixture.reseal_contract()
        fixture.add_attempt(candidate=False, keep=True)

    check(
        "invalid_admission_timeout_window",
        "invalid",
        insufficient_admission_timeout_window,
        expected_error="admission_timeout_window_too_short",
    )

    def delayed_launch_lacks_remaining_window(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        intent_index = next(
            index
            for index, event in enumerate(fixture.events)
            if event["event"] == "launch_intent"
        )
        fixture.events[intent_index]["at"] = "2026-01-01T00:00:50Z"
        fixture.events = fixture.events[: intent_index + 1]
        fixture.results.clear()
        (fixture.run / "boundaries" / "a-000.json").unlink()
        fixture.prune_unreferenced_artifacts()

    check(
        "invalid_insufficient_remaining_timeout_window",
        "invalid",
        delayed_launch_lacks_remaining_window,
        expected_error="insufficient_remaining_timeout_window",
    )

    def delayed_spawn_lacks_timeout_window(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        launched_index = next(
            index
            for index, event in enumerate(fixture.events)
            if event["event"] == "launched"
        )
        fixture.events[launched_index]["at"] = "2026-01-01T00:00:59Z"
        fixture.events = fixture.events[: launched_index + 1]
        fixture.results.clear()
        (fixture.run / "boundaries" / "a-000.json").unlink()
        fixture.prune_unreferenced_artifacts()

    check(
        "invalid_spawn_lacks_timeout_window",
        "invalid",
        delayed_spawn_lacks_timeout_window,
        expected_error="insufficient_spawn_timeout_window",
    )

    def completed_launch_exceeds_timeout(fixture: Fixture) -> None:
        result = fixture.add_attempt(candidate=False, keep=True)
        finish_index = next(
            index
            for index, event in enumerate(fixture.events)
            if event["event"] == "launch_finished"
        )
        for event in fixture.events[finish_index:]:
            timestamp = parse_timestamp(event["at"])
            assert timestamp is not None
            event["at"] = (timestamp + timedelta(seconds=20)).isoformat().replace(
                "+00:00", "Z"
            )
        evaluated = next(
            event for event in fixture.events if event["event"] == "evaluated"
        )
        decided = next(
            event for event in fixture.events if event["event"] == "decided"
        )
        finalized = next(
            event for event in fixture.events if event["event"] == "finalized"
        )
        result["ended_at"] = evaluated["at"]
        result["decision"]["journal_event_sha256"] = canonical_sha256(decided)
        fixture.rebind_attempt_boundary("a-000")
        boundary_path = fixture.run / "boundaries" / "a-000.json"
        boundary = load_json_file(boundary_path, Diagnostics())
        assert boundary is not None
        boundary["finalized_at"] = finalized["at"]
        _write_json(boundary_path, boundary)

    check(
        "invalid_completed_launch_exceeds_timeout",
        "invalid",
        completed_launch_exceeds_timeout,
        expected_error="unclassified_operational_overrun",
    )

    def actual_usage_overrun(fixture: Fixture) -> None:
        result = fixture.add_attempt(candidate=False, keep=True)
        validation_finish = next(
            event
            for event in fixture.events
            if event["event"] == "launch_finished"
            and event["result_id"].endswith("-validation")
        )
        validation_finish["actual_usage"]["validation_queries"] = 2
        result["validation_queries"] = 2
        result["budget"]["actual"]["validation_queries"] = 2
        result["budget"]["remaining_after"]["validation_queries"] = 8
        fixture.rebind_attempt_boundary("a-000")

    check(
        "invalid_actual_usage_overrun",
        "invalid",
        actual_usage_overrun,
        expected_error="unclassified_operational_overrun",
    )

    def truthful_usage_overrun(fixture: Fixture) -> None:
        result = fixture.add_attempt(candidate=False, keep=False)
        validation_finish = next(
            event
            for event in fixture.events
            if event["event"] == "launch_finished"
            and event["result_id"].endswith("-validation")
        )
        validation_finish["actual_usage"]["validation_queries"] = 2
        decided = next(
            event for event in fixture.events if event["event"] == "decided"
        )
        decided["status"] = "timeout"
        result["status"] = "timeout"
        result["decision"]["journal_event_sha256"] = canonical_sha256(decided)
        result["validation_queries"] = 2
        result["budget"]["actual"]["validation_queries"] = 2
        result["budget"]["remaining_after"]["validation_queries"] = 8
        fixture.rebind_attempt_boundary("a-000")
        fixture.close_run(reason="baseline_timed_out")

    check(
        "valid_truthful_usage_overrun_timeout",
        "valid",
        truthful_usage_overrun,
        expected_closed=True,
    )

    def admission_after_intent(fixture: Fixture) -> None:
        result = fixture.add_attempt(candidate=False, keep=True)
        result["admission"]["checked_at"] = "2026-01-01T23:59:59Z"
        result["admission"]["worst_case_end_at"] = "2026-01-02T00:01:00Z"
        admission_digest = canonical_sha256(result["admission"])
        for event in fixture.events:
            if event["event"] == "launch_intent":
                event["admission_sha256"] = admission_digest
        fixture.rebind_attempt_boundary("a-000")

    check(
        "invalid_admission_after_launch_intent",
        "invalid",
        admission_after_intent,
        expected_error="admission_after_launch_intent",
    )

    def result_before_launch_terminal(fixture: Fixture) -> None:
        result = fixture.add_attempt(candidate=False, keep=True)
        finished_index = next(
            index
            for index, event in enumerate(fixture.events)
            if event["event"] == "launch_finished"
        )
        for event in fixture.events[finished_index:]:
            event["at"] = "2027-01-01T00:00:00Z"
        result["ended_at"] = "2026-01-01T00:00:01Z"
        fixture.rebind_attempt_boundary("a-000")

    check(
        "invalid_result_before_launch_terminal",
        "invalid",
        result_before_launch_terminal,
        expected_error="result_before_launch_terminal",
    )

    def gate_after_failed_development(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        development_checkpoint = next(
            event
            for event in fixture.events
            if event["event"] == "stage_completed" and event["stage"] == "development"
        )
        development_checkpoint["outcome"] = "fail"

    check(
        "invalid_gate_after_failed_development",
        "invalid",
        gate_after_failed_development,
        expected_error="protected_stage_without_pass",
    )

    def malformed_journal_event(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        fixture.events[0]["event"] = []

    check(
        "invalid_malformed_journal_event",
        "invalid",
        malformed_journal_event,
        expected_error="unknown_event",
    )

    def malformed_result_status(fixture: Fixture) -> None:
        result = fixture.add_attempt(candidate=False, keep=True)
        result["status"] = []

    check(
        "invalid_malformed_result_status",
        "invalid",
        malformed_result_status,
        expected_error="invalid_status",
    )

    def malformed_pending_design(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        fixture.add_attempt(candidate=True, keep=False)
        prepared = next(
            event
            for event in fixture.events
            if event["event"] == "prepared" and event["attempt_id"] == "a-001"
        )
        design_path = fixture.run / prepared["design"]["path"]
        _write_json(design_path, {})
        prepared["design"]["sha256"] = file_sha256(design_path)
        fixture.events = fixture.events[: prepared["seq"]]
        fixture.results.pop()
        (fixture.run / "boundaries" / "a-001.json").unlink()
        fixture.prune_unreferenced_artifacts()

    check(
        "invalid_pending_design_schema",
        "invalid",
        malformed_pending_design,
        expected_error="schema_version",
    )

    def impossible_prepared_launch_plan(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        prepared = next(
            event for event in fixture.events if event["event"] == "prepared"
        )
        plan_path = fixture.run / prepared["launch_plan"]["path"]
        plan = load_json_file(plan_path, Diagnostics())
        assert plan is not None
        plan["launches"][0]["phase"] = "exploration"
        _write_json(plan_path, plan)
        prepared["launch_plan"]["sha256"] = file_sha256(plan_path)
        fixture.events = fixture.events[: prepared["seq"]]
        fixture.results.clear()
        (fixture.run / "boundaries" / "a-000.json").unlink()
        fixture.prune_unreferenced_artifacts()

    check(
        "invalid_frozen_launch_plan_before_spawn",
        "invalid",
        impossible_prepared_launch_plan,
        expected_error="invalid_deterministic_plan",
    )

    def malformed_config_before_spawn(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        intent_index = next(
            index
            for index, event in enumerate(fixture.events)
            if event["event"] == "launch_intent"
        )
        intent = fixture.events[intent_index]
        config_path = fixture.run / intent["effective_config"]["path"]
        config = load_json_file(config_path, Diagnostics())
        assert config is not None
        config["stage"] = "validation"
        _write_json(config_path, config)
        digest = file_sha256(config_path)
        intent["effective_config"]["sha256"] = digest
        intent["effective_config_sha256"] = digest
        prepared = next(
            event for event in fixture.events if event["event"] == "prepared"
        )
        plan_path = fixture.run / prepared["launch_plan"]["path"]
        plan = load_json_file(plan_path, Diagnostics())
        assert plan is not None
        plan["launches"][0]["effective_config_sha256"] = digest
        _write_json(plan_path, plan)
        prepared["launch_plan"]["sha256"] = file_sha256(plan_path)
        fixture.events = fixture.events[: intent_index + 1]
        fixture.results.clear()
        (fixture.run / "boundaries" / "a-000.json").unlink()
        fixture.prune_unreferenced_artifacts()

    check(
        "invalid_config_semantics_before_spawn",
        "invalid",
        malformed_config_before_spawn,
        expected_error="effective_config_stage_mismatch",
    )

    check(
        "valid_cleanup_confirmation",
        "valid",
        lambda fixture: fixture.add_attempt(
            candidate=False, keep=True, cleanup_via_confirmation=True
        ),
    )

    def cleanup_pending(fixture: Fixture) -> None:
        fixture.add_attempt(
            candidate=False, keep=True, cleanup_via_confirmation=True
        )
        finished_index = next(
            index
            for index, event in enumerate(fixture.events)
            if event["event"] == "launch_finished"
        )
        fixture.events = fixture.events[: finished_index + 1]
        fixture.results.clear()
        (fixture.run / "boundaries" / "a-000.json").unlink()
        fixture.prune_unreferenced_artifacts()

    check(
        "recoverable_cleanup_pending",
        "recoverable",
        cleanup_pending,
        "verify_cleanup_or_append_cleanup_unresolved",
        "launch_finished_cleanup_pending",
    )

    def after_evidence_publication(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        publication_index = next(
            index
            for index, event in enumerate(fixture.events)
            if event["event"] == "evidence_published"
            and event["result_id"].endswith("-development")
        )
        fixture.events = fixture.events[: publication_index + 1]
        fixture.results.clear()
        (fixture.run / "boundaries" / "a-000.json").unlink()
        fixture.prune_unreferenced_artifacts()

    check(
        "recoverable_after_evidence_publication",
        "recoverable",
        after_evidence_publication,
        "append_stage_completed",
        "stage_checkpoint_pending",
    )

    def corrupt_evidence_publication_tail(fixture: Fixture) -> None:
        after_evidence_publication(fixture)
        publication = next(
            event
            for event in reversed(fixture.events)
            if event["event"] == "evidence_published"
        )
        publication["repetition_sha256"] = "0" * 64

    check(
        "invalid_evidence_publication_digest_at_crash_tail",
        "invalid",
        corrupt_evidence_publication_tail,
        expected_error="published_repetition_digest_mismatch",
    )

    def after_passed_stage_checkpoint(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        checkpoint_index = next(
            index
            for index, event in enumerate(fixture.events)
            if event["event"] == "stage_completed"
            and event["stage"] == "development"
        )
        fixture.events = fixture.events[: checkpoint_index + 1]
        fixture.results.clear()
        (fixture.run / "boundaries" / "a-000.json").unlink()
        fixture.prune_unreferenced_artifacts()

    check(
        "recoverable_after_passed_stage_checkpoint",
        "recoverable",
        after_passed_stage_checkpoint,
        "append_next_launch_intent",
        "next_stage_launch_pending",
    )

    def cleanup_unresolved_stop(fixture: Fixture, *, launch_after_fence: bool = False) -> None:
        result = fixture.add_attempt(
            candidate=False, keep=True, cleanup_via_confirmation=True
        )
        first_finish_index = next(
            index
            for index, event in enumerate(fixture.events)
            if event["event"] == "launch_finished"
        )
        finished = fixture.events[first_finish_index]
        first_intent = next(
            event
            for event in fixture.events[: first_finish_index + 1]
            if event["event"] == "launch_intent"
        )
        common = {
            key: finished[key]
            for key in ("attempt_id", "experiment_id", "iteration", "role")
        }
        fixture.events = fixture.events[: first_finish_index + 1]
        fixture.results.clear()
        (fixture.run / "boundaries" / "a-000.json").unlink()
        fixture.event(
            "cleanup_unresolved",
            launch_id=finished["launch_id"],
            result_id=finished["result_id"],
            launch_ordinal=finished["launch_ordinal"],
            fence_applied=True,
            reason="fixture cannot prove process-tree cleanup",
            **common,
        )
        fixture.prune_unreferenced_artifacts()
        if launch_after_fence:
            fixture.event(
                "launch_intent",
                launch_id="l-after-fence",
                result_id="r-after-fence",
                launch_ordinal=1,
                stage="development",
                arm=first_intent["arm"],
                phase=first_intent["phase"],
                pair_id=first_intent["pair_id"],
                seed=first_intent["seed"],
                input_id=first_intent["input_id"],
                workspace_id=first_intent["workspace_id"],
                workspace=first_intent["workspace"],
                stage_access_sha256=first_intent["stage_access_sha256"],
                evaluated_commit=fixture.BASE,
                invocation_sha256=fixture.contract["evaluation"]["invocation_sha256"],
                timeout_seconds=first_intent["timeout_seconds"],
                effective_config_sha256=first_intent["effective_config_sha256"],
                generated_state_sha256=first_intent["generated_state_sha256"],
                evaluated_tree_sha256=first_intent["evaluated_tree_sha256"],
                effective_config=first_intent["effective_config"],
                generated_state=first_intent["generated_state"],
                evaluated_tree_snapshot=first_intent["evaluated_tree_snapshot"],
                admission_sha256=first_intent["admission_sha256"],
                allocation=dict(first_intent["allocation"]),
                cwd=".",
                log_partial_path="logs/r-after-fence.log.partial",
                **common,
            )
            fixture.event(
                "launch_aborted",
                launch_id="l-after-fence",
                result_id="r-after-fence",
                launch_ordinal=1,
                absence_verified=True,
                reason="fixture did not spawn after the fence",
                **common,
            )
        fixture.event(
            "stage_completed",
            stage="development",
            outcome="unknown",
            result_ids=[],
            rule_evaluation="cleanup ownership is unresolved",
            **common,
        )
        evaluated_event = fixture.event("evaluated", result_ids=[], **common)
        decided = fixture.event(
            "decided",
            status="interrupted",
            lane="diagnostic",
            evidence_maturity="unknown",
            accepted=False,
            stage_results={"development": "unknown", "validation": "unknown"},
            stage_evidence={"development": [], "validation": []},
            reason="cleanup ownership is unresolved",
            rule_evaluation="no evidence is admissible",
            incumbent_after=fixture.BASE,
            rollback_action="preserve_and_stop",
            **common,
        )
        result.update(
            {
                "status": "interrupted",
                "lane": "diagnostic",
                "evidence_maturity": "unknown",
                "acceptance_reason": decided["reason"],
                "decision": {
                    "decision_seq": decided["seq"],
                    "journal_event_sha256": canonical_sha256(decided),
                    "accepted": False,
                    "incumbent_after": fixture.BASE,
                    "rollback_required": False,
                    "stage_results": decided["stage_results"],
                    "stage_evidence": decided["stage_evidence"],
                    "rule_evaluation": decided["rule_evaluation"],
                },
                "primary_value": None,
                "ended_at": evaluated_event["at"],
                "split_metrics": {"development": None, "validation": None},
                "repetitions": [],
                "duration_seconds": 1.0,
                "validation_queries": 0,
                "leakage_audit": "not_run",
                "constraint_results": {"integrity": None},
                "finding": {
                    "type": "diagnostic",
                    "summary": "cleanup remains unresolved",
                    "scope": "fixture",
                    "caveats": "usage is a lower bound",
                    "next_action": "preserve",
                    "revive_if": None,
                    "result_ids": [],
                },
                "failure": {"kind": "cleanup_unresolved"},
                "budget": {
                    "actual_complete": False,
                    "actual": {"evaluator_calls": 1, "validation_queries": 0},
                    "remaining_after": {
                        "evaluator_calls": None,
                        "validation_queries": None,
                    },
                },
            }
        )
        fixture.results.append(result)
        result_digest = canonical_sha256(result)
        finalized = fixture.event(
            "finalized",
            decision_seq=decided["seq"],
            result_index=0,
            result_sha256=result_digest,
            boundary_path="boundaries/a-000.json",
            **common,
        )
        _write_json(
            fixture.run / "boundaries" / "a-000.json",
            {
                "schema_version": 1,
                "kind": "attempt-boundary",
                "attempt_id": "a-000",
                "experiment_id": "baseline",
                "contract_sha256": fixture.contract_digest,
                "decision_seq": decided["seq"],
                "finalized_seq": finalized["seq"],
                "result_index": 0,
                "result_sha256": result_digest,
                "journal_prefix_sha256": hashlib.sha256(
                    canonical_jsonl_bytes(fixture.events[: finalized["seq"]])
                ).hexdigest(),
                "results_prefix_sha256": hashlib.sha256(
                    canonical_jsonl_bytes(fixture.results)
                ).hexdigest(),
                "publication_nonce": hashlib.sha256(
                    b"fixture-boundary:a-000"
                ).hexdigest(),
                "finalized_at": fixture.timestamp(advance=True),
            },
        )
        fixture.close_run(reason="cleanup_unresolved", unresolved_cleanup=True)

    check(
        "valid_cleanup_unresolved_stop",
        "valid",
        cleanup_unresolved_stop,
        expected_closed=True,
    )
    check(
        "invalid_launch_after_unresolved_fence",
        "invalid",
        lambda fixture: cleanup_unresolved_stop(fixture, launch_after_fence=True),
        expected_error="launch_after_fence",
    )

    def intent_then_stop(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        intent_index = next(
            index
            for index, event in enumerate(fixture.events)
            if event["event"] == "launch_intent"
        )
        fixture.events = fixture.events[: intent_index + 1]
        fixture.results.clear()
        (fixture.run / "boundaries" / "a-000.json").unlink()
        fixture.event(
            "stop_requested",
            source="user",
            stop_kind="user_interruption",
            final_test_skip_authorized=True,
            reason="fixture stop",
            rule_evaluation="stop now",
        )
        fixture.prune_unreferenced_artifacts()

    check(
        "recoverable_aborted_intent",
        "recoverable",
        intent_then_stop,
        "prove_absence_or_append_launch_unresolved",
        "launch_intent",
    )

    def corrupt_admission_digest_at_intent_tail(fixture: Fixture) -> None:
        intent_then_stop(fixture)
        intent = next(
            event for event in fixture.events if event["event"] == "launch_intent"
        )
        intent["admission_sha256"] = "0" * 64

    check(
        "invalid_admission_digest_at_intent_tail",
        "invalid",
        corrupt_admission_digest_at_intent_tail,
        expected_error="admission_digest_mismatch",
    )

    def valid_aborted_run(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=False, abort_launch=True)
        fixture.close_run(reason="launch_aborted")

    check(
        "valid_aborted_run",
        "valid",
        valid_aborted_run,
        expected_closed=True,
    )

    def promote_after_aborted_launch(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=False, abort_launch=True)
        decided = next(event for event in fixture.events if event["event"] == "decided")
        decided["status"] = "keep"

    check(
        "invalid_promotion_after_aborted_launch",
        "invalid",
        promote_after_aborted_launch,
        expected_error="aborted_launch_promoted",
    )

    def mismatched_final_head(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        fixture.close_run()
        publish = fixture.post_flush

        def publish_then_corrupt() -> None:
            publish()
            marker_path = fixture.run / "run.finalized.json"
            marker = strict_loads(marker_path.read_text(encoding="utf-8"))
            marker["final_head_commit"] = fixture.CANDIDATE
            _write_json(marker_path, marker)

        fixture.post_flush = publish_then_corrupt

    check(
        "invalid_final_head",
        "invalid",
        mismatched_final_head,
        expected_error="run_boundary_mismatch",
    )

    def extra_run_boundary_claim(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        fixture.close_run()
        publish = fixture.post_flush

        def publish_then_extend() -> None:
            publish()
            marker_path = fixture.run / "run.finalized.json"
            marker = load_json_file(marker_path, Diagnostics())
            assert marker is not None
            marker["unvalidated_claim"] = "misleading"
            _write_json(marker_path, marker)

        fixture.post_flush = publish_then_extend

    check(
        "invalid_extra_run_boundary_claim",
        "invalid",
        extra_run_boundary_claim,
        expected_error="run_boundary_schema_mismatch",
    )

    def ghost_artifact_after_run_boundary(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        fixture.close_run()
        publish = fixture.post_flush

        def publish_then_add_ghost() -> None:
            publish()
            fixture.artifact("evidence/ghost.json", '{"schema_version":1}\n')

        fixture.post_flush = publish_then_add_ghost

    check(
        "invalid_unmanifested_artifact_after_close",
        "invalid",
        ghost_artifact_after_run_boundary,
        expected_error="orphan_canonical_artifact",
    )

    def run_boundary_predates_attempt_boundary(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        fixture.close_run()
        publish = fixture.post_flush

        def publish_then_shift_attempt_boundary() -> None:
            publish()
            boundary_path = fixture.run / "boundaries" / "a-000.json"
            boundary = load_json_file(boundary_path, Diagnostics())
            assert boundary is not None
            boundary["finalized_at"] = "2099-01-01T00:00:00Z"
            _write_json(boundary_path, boundary)
            boundary_digest = file_sha256(boundary_path)
            marker_path = fixture.run / "run.finalized.json"
            marker = load_json_file(marker_path, Diagnostics())
            assert marker is not None
            marker["last_attempt_boundary_sha256"] = boundary_digest
            for artifact in marker["canonical_artifacts"]:
                if artifact["path"] == "boundaries/a-000.json":
                    artifact["sha256"] = boundary_digest
            marker["canonical_artifacts_sha256"] = canonical_sha256(
                marker["canonical_artifacts"]
            )
            _write_json(marker_path, marker)

        fixture.post_flush = publish_then_shift_attempt_boundary

    check(
        "invalid_run_boundary_predates_attempt_boundary",
        "invalid",
        run_boundary_predates_attempt_boundary,
        expected_error="run_boundary_time_regression",
    )

    def configure_final_test(fixture: Fixture) -> None:
        fixture.contract["final_test"] = {
            "stage": "final_test",
            "required_telemetry": ["score"],
            "minimum_value": 1.0,
            "maximum_value": None,
            "comparable_to_acceptance_split": False,
            "maximum_regression": None,
        }
        fixture.contract["stage_resources"]["final_test"] = {
            "resource_manifest_sha256": hashlib.sha256(
                b"fixture-resource:final_test"
            ).hexdigest(),
            "capability_sha256": hashlib.sha256(
                b"fixture-capability:final_test"
            ).hexdigest(),
            "visibility": "protected",
        }
        fixture.contract["stage_access_sha256"]["final_test"] = canonical_sha256(
            fixture.contract["stage_resources"]["final_test"]
        )
        fixture.contract["stage_config_sha256"]["final_test"] = hashlib.sha256(
            (
                json.dumps(
                    {
                        "schema_version": 1,
                        "stage": "final_test",
                        "stage_access_sha256": fixture.contract[
                            "stage_access_sha256"
                        ]["final_test"],
                        "settings": {},
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        ).hexdigest()
        final_repetitions = (
            fixture.contract["measurement"]["minimum_repetitions"]
            if fixture.contract["measurement"]["mode"] == "noisy"
            else 1
        )
        for budget in fixture.contract["budgets"].values():
            budget["final_test_reserve"] = final_repetitions
        fixture.reseal_contract()

    def valid_final_failure(fixture: Fixture) -> None:
        configure_final_test(fixture)
        fixture.add_attempt(candidate=False, keep=True)
        fixture.add_attempt(candidate=False, keep=False, final=True)

    check(
        "final_requires_stop_record",
        "recoverable",
        valid_final_failure,
        "append_run_stopped",
    )

    def valid_closed_final_failure(fixture: Fixture) -> None:
        configure_final_test(fixture)
        fixture.add_attempt(candidate=False, keep=True)
        fixture.add_attempt(candidate=False, keep=False, final=True)
        fixture.close_run(reason="final_test_failed")

    check(
        "valid_closed_final_failure",
        "valid",
        valid_closed_final_failure,
        expected_closed=True,
    )

    def valid_final_success(fixture: Fixture) -> None:
        configure_final_test(fixture)
        fixture.add_attempt(candidate=False, keep=True)
        fixture.add_attempt(candidate=False, keep=True, final=True)
        fixture.close_run(reason="final_test_passed")

    check(
        "valid_final_test_success",
        "valid",
        valid_final_success,
        expected_closed=True,
    )

    def final_below_stage_threshold(fixture: Fixture) -> None:
        configure_final_test(fixture)
        fixture.contract["final_test"]["minimum_value"] = 2.0
        fixture.reseal_contract()
        fixture.add_attempt(candidate=False, keep=True)
        fixture.add_attempt(candidate=False, keep=True, final=True)

    check(
        "invalid_final_below_stage_threshold",
        "invalid",
        final_below_stage_threshold,
        expected_error="final_acceptance_failed",
    )

    def final_exceeds_typed_reserve(fixture: Fixture) -> None:
        configure_final_test(fixture)
        for budget in fixture.contract["budgets"].values():
            budget["final_test_reserve"] = 0
        fixture.reseal_contract()
        fixture.add_attempt(candidate=False, keep=True)
        fixture.add_attempt(candidate=False, keep=True, final=True)

    check(
        "invalid_final_exceeds_typed_reserve",
        "invalid",
        final_exceeds_typed_reserve,
        expected_error="insufficient_final_test_reserve",
    )

    def cross_split_regression_without_comparability(fixture: Fixture) -> None:
        configure_final_test(fixture)
        fixture.contract["final_test"]["maximum_regression"] = 0.0
        fixture.reseal_contract()
        fixture.event(
            "stop_requested",
            source="fixture",
            stop_kind="integrity",
            final_test_skip_authorized=False,
            reason="invalid final contract",
            rule_evaluation="reject before launch",
        )
        fixture.close_run(reason="invalid_final_contract")

    check(
        "invalid_cross_split_regression_without_comparability",
        "invalid",
        cross_split_regression_without_comparability,
        expected_error="invalid_final_test",
    )

    def skipped_configured_final(fixture: Fixture) -> None:
        configure_final_test(fixture)
        fixture.add_attempt(candidate=False, keep=True)
        fixture.close_run(reason="ordinary_completion")

    check(
        "invalid_skipped_configured_final",
        "invalid",
        skipped_configured_final,
        expected_error="final_test_missing",
    )

    def interrupted_before_configured_final(fixture: Fixture) -> None:
        configure_final_test(fixture)
        fixture.add_attempt(candidate=False, keep=True)
        fixture.event(
            "stop_requested",
            source="user",
            stop_kind="user_interruption",
            final_test_skip_authorized=True,
            reason="interrupt before final verification",
            rule_evaluation="stop immediately",
        )
        fixture.close_run(reason="user_interrupted")

    check(
        "valid_explicit_stop_without_final",
        "valid",
        interrupted_before_configured_final,
        expected_closed=True,
    )

    def ordinary_stop_before_configured_final(fixture: Fixture) -> None:
        configure_final_test(fixture)
        fixture.add_attempt(candidate=False, keep=True)
        fixture.event(
            "stop_requested",
            source="controller",
            stop_kind="ordinary",
            final_test_skip_authorized=False,
            reason="ordinary completion",
            rule_evaluation="configured terminal rule fired",
        )
        fixture.close_run(reason="ordinary_completion")

    check(
        "invalid_ordinary_stop_cannot_skip_final",
        "invalid",
        ordinary_stop_before_configured_final,
        expected_error="final_test_missing",
    )

    def ordinary_stop_then_final(fixture: Fixture) -> None:
        configure_final_test(fixture)
        fixture.add_attempt(candidate=False, keep=True)
        fixture.event(
            "stop_requested",
            source="controller",
            stop_kind="ordinary",
            final_test_skip_authorized=False,
            reason="ordinary completion",
            rule_evaluation="configured terminal rule fired",
        )
        fixture.add_attempt(candidate=False, keep=True, final=True)
        fixture.close_run(reason="final_test_passed_after_stop")

    check(
        "valid_ordinary_stop_then_final",
        "valid",
        ordinary_stop_then_final,
        expected_closed=True,
    )

    def authorized_skip_then_illegal_final(fixture: Fixture) -> None:
        configure_final_test(fixture)
        fixture.add_attempt(candidate=False, keep=True)
        fixture.event(
            "stop_requested",
            source="user",
            stop_kind="user_interruption",
            final_test_skip_authorized=True,
            reason="stop without final",
            rule_evaluation="user authorized immediate stop",
        )
        fixture.add_attempt(candidate=False, keep=True, final=True)

    check(
        "invalid_final_after_authorized_skip",
        "invalid",
        authorized_skip_then_illegal_final,
        expected_error="launch_after_stop",
    )

    def valid_noisy_final(fixture: Fixture) -> None:
        configure_noisy(fixture)
        configure_final_test(fixture)
        fixture.add_attempt(candidate=False, keep=True)
        fixture.add_attempt(candidate=False, keep=True, final=True)
        fixture.close_run(reason="noisy_final_passed")

    check(
        "valid_noisy_final_test",
        "valid",
        valid_noisy_final,
        expected_closed=True,
    )

    def premature_final_test_access(fixture: Fixture) -> None:
        configure_final_test(fixture)
        fixture.add_attempt(candidate=False, keep=True)
        intent = next(event for event in fixture.events if event["event"] == "launch_intent")
        intent["stage"] = "final_test"

    check(
        "invalid_premature_final_test_access",
        "invalid",
        premature_final_test_access,
        expected_error="premature_final_test",
    )

    def after_decision(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        fixture.add_attempt(candidate=True, keep=False, finalize=False)
        fixture.results.pop()
        fixture.events = [event for event in fixture.events if event["event"] != "rolled_back"]
        for index, event in enumerate(fixture.events, start=1):
            event["seq"] = index

    check(
        "recoverable_after_decision",
        "recoverable",
        after_decision,
        "execute_recorded_rollback",
    )

    def after_result(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        fixture.add_attempt(candidate=True, keep=True, finalize=False)

    check("recoverable_after_result", "recoverable", after_result, "append_finalized")

    def invalid_boundary(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        boundary = load_json_file(
            fixture.run / "boundaries" / "a-000.json", Diagnostics()
        )
        assert boundary is not None
        boundary["result_sha256"] = "0" * 64
        _write_json(fixture.run / "boundaries" / "a-000.json", boundary)

    check("invalid_boundary", "invalid", invalid_boundary)

    def boundary_publication_target_is_directory(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        boundary_path = fixture.run / "boundaries" / "a-000.json"
        boundary_path.unlink()
        boundary_path.mkdir()

    check(
        "invalid_boundary_publication_directory",
        "invalid",
        boundary_publication_target_is_directory,
        expected_error="invalid_publication_target",
    )

    def missing_boundary_nonce(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        boundary_path = fixture.run / "boundaries" / "a-000.json"
        boundary = load_json_file(boundary_path, Diagnostics())
        assert boundary is not None
        boundary.pop("publication_nonce")
        _write_json(boundary_path, boundary)

    check(
        "invalid_missing_boundary_nonce",
        "invalid",
        missing_boundary_nonce,
        expected_error="invalid_digest",
    )

    def rewritten_journal_prefix(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)
        intent = next(event for event in fixture.events if event["event"] == "launch_intent")
        intent["allocation"] = {"mutated": True}

    check(
        "invalid_rewritten_journal_prefix",
        "invalid",
        rewritten_journal_prefix,
        expected_error="boundary_mismatch",
    )

    def torn_tail(fixture: Fixture) -> None:
        fixture.add_attempt(candidate=False, keep=True)

        def append_torn_tail() -> None:
            with (fixture.run / "journal.jsonl").open("ab") as handle:
                handle.write(b'{"torn":')

        fixture.post_flush = append_torn_tail

    check("invalid_torn_tail", "invalid", torn_tail)

    def git_tree_case(
        name: str,
        restore_tree: bool,
        expected: str,
        error_code: str | None,
        *,
        mutate_immutable: bool = False,
        symlink_candidate: bool = False,
        nested_control_dirty: bool = False,
        generated_symlink_baseline: bool = False,
        dirty_evaluation_workspace: bool = False,
        generated_state_contamination: bool = False,
        hidden_evaluation_workspace_drift: bool = False,
        preserve_candidate: bool = False,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="research-loop-git-validator-") as temp:
            root = Path(temp)
            worktree = root / "worktree"
            worktree.mkdir()

            def git(*args: str) -> str:
                completed = subprocess.run(
                    ["git", "-C", str(worktree), *args],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return completed.stdout.strip()

            git("init", "-q")
            git("config", "user.name", "Research Loop Test")
            git("config", "user.email", "research-loop@example.invalid")
            (worktree / "eval.py").write_text("print('ok')\n", encoding="utf-8")
            (worktree / "candidate.txt").write_text("base\n", encoding="utf-8")
            baseline_paths = ["eval.py", "candidate.txt"]
            if generated_symlink_baseline:
                (worktree / "runtime-state").symlink_to("/etc/passwd")
                baseline_paths.append("runtime-state")
            git("add", *baseline_paths)
            git("commit", "-qm", "baseline")
            baseline = git("rev-parse", "HEAD")
            changed_name = "eval.py" if mutate_immutable else "candidate.txt"
            changed_content = "print('tampered')\n" if mutate_immutable else "candidate\n"
            if symlink_candidate:
                (worktree / changed_name).unlink()
                (worktree / changed_name).symlink_to("/etc/passwd")
            else:
                (worktree / changed_name).write_text(changed_content, encoding="utf-8")
            git("add", changed_name)
            git("commit", "-qm", "candidate")
            candidate = git("rev-parse", "HEAD")
            if preserve_candidate:
                rollback = candidate
            elif restore_tree:
                git("revert", "--no-edit", candidate)
                rollback = git("rev-parse", "HEAD")
            else:
                (worktree / "candidate.txt").write_text("wrong rollback\n", encoding="utf-8")
                git("add", "candidate.txt")
                git("commit", "-qm", "wrong rollback")
                rollback = git("rev-parse", "HEAD")
            fixture = Fixture(root)
            fixture.BASE = baseline
            fixture.CANDIDATE = candidate
            fixture.REVERT = rollback
            fixture.contract["baseline_commit"] = baseline
            _write_json(fixture.run / "contract.json", fixture.contract)
            fixture.contract_digest = file_sha256(fixture.run / "contract.json")
            (fixture.run / "contract.sha256").write_text(
                fixture.contract_digest + "\n", encoding="ascii"
            )
            fixture.add_attempt(candidate=False, keep=True)
            preserved_result = fixture.add_attempt(candidate=True, keep=False)
            if preserve_candidate:
                decided = next(
                    event
                    for event in fixture.events
                    if event.get("event") == "decided"
                    and event.get("attempt_id") == "a-001"
                )
                decided.update(
                    {
                        "status": "invalid",
                        "lane": "diagnostic",
                        "evidence_maturity": "unknown",
                        "reason": "safe recovery is uncertain",
                        "rule_evaluation": "preserve candidate state without promotion",
                        "rollback_action": "preserve_and_stop",
                    }
                )
                fixture.events = [
                    event
                    for event in fixture.events
                    if not (
                        event.get("event") == "rolled_back"
                        and event.get("attempt_id") == "a-001"
                    )
                ]
                for sequence, event in enumerate(fixture.events, start=1):
                    event["seq"] = sequence
                decided = next(
                    event
                    for event in fixture.events
                    if event.get("event") == "decided"
                    and event.get("attempt_id") == "a-001"
                )
                finalized = next(
                    event
                    for event in fixture.events
                    if event.get("event") == "finalized"
                    and event.get("attempt_id") == "a-001"
                )
                finalized["decision_seq"] = decided["seq"]
                preserved_result.update(
                    {
                        "status": "invalid",
                        "lane": "diagnostic",
                        "evidence_maturity": "unknown",
                        "acceptance_reason": decided["reason"],
                        "rollback_commit": None,
                        "failure": {"kind": "recovery_uncertain"},
                    }
                )
                preserved_result["decision"].update(
                    {
                        "decision_seq": decided["seq"],
                        "journal_event_sha256": canonical_sha256(decided),
                        "rollback_required": False,
                        "rule_evaluation": decided["rule_evaluation"],
                    }
                )
                preserved_result["finding"].update(
                    {
                        "type": "diagnostic",
                        "summary": "candidate state preserved for recovery",
                        "next_action": "preserve",
                    }
                )
                boundary_path = fixture.run / "boundaries" / "a-001.json"
                boundary = load_json_file(boundary_path, Diagnostics())
                assert boundary is not None
                boundary["decision_seq"] = decided["seq"]
                boundary["finalized_seq"] = finalized["seq"]
                _write_json(boundary_path, boundary)
                fixture.rebind_attempt_boundary("a-001")
            fixture.close_run()
            fixture.flush()
            fixture.post_flush()
            if nested_control_dirty:
                nested = worktree / "nested" / ".research-loop"
                nested.mkdir(parents=True)
                (nested / "payload").write_text("unexpected\n", encoding="utf-8")
            if (
                dirty_evaluation_workspace
                or generated_state_contamination
                or hidden_evaluation_workspace_drift
            ):
                workspace_snapshot_path = next(
                    fixture.run.glob("snapshots/a-000-ws-*.json")
                )
                workspace_snapshot = load_json_file(
                    workspace_snapshot_path, Diagnostics()
                )
                assert workspace_snapshot is not None
                evaluation_root = Path(workspace_snapshot["root_path"])
                if dirty_evaluation_workspace:
                    (evaluation_root / "eval.py").write_text(
                        "print('runtime tamper')\n", encoding="utf-8"
                    )
                if generated_state_contamination:
                    generated_root = evaluation_root / "runtime-state"
                    generated_root.mkdir()
                    (generated_root / "cache").write_text(
                        "cross-arm state\n", encoding="utf-8"
                    )
                if hidden_evaluation_workspace_drift:
                    subprocess.run(
                        [
                            "git",
                            "-C",
                            str(evaluation_root),
                            "update-index",
                            "--assume-unchanged",
                            "candidate.txt",
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    (evaluation_root / "candidate.txt").write_text(
                        "hidden runtime drift\n", encoding="utf-8"
                    )
            output, _exit = validate_run(fixture.run)
            if output["verdict"] != expected:
                raise AssertionError(
                    f"{name}: expected {expected}, got {output['verdict']}: {output['errors']}"
                )
            if error_code is not None and error_code not in {
                item["code"] for item in output["errors"]
            }:
                raise AssertionError(
                    f"{name}: missing expected error {error_code}: {output['errors']}"
                )
            cases.append({"name": name, "verdict": output["verdict"]})

    git_tree_case("valid_git_rollback_tree", True, "valid", None)
    git_tree_case(
        "valid_unresolved_recovery_preserves_candidate",
        False,
        "valid",
        None,
        preserve_candidate=True,
    )
    git_tree_case(
        "invalid_git_rollback_tree",
        False,
        "invalid",
        "rollback_tree_mismatch",
    )
    git_tree_case(
        "invalid_candidate_scope",
        True,
        "invalid",
        "candidate_scope_violation",
        mutate_immutable=True,
    )
    git_tree_case(
        "invalid_candidate_symlink",
        True,
        "invalid",
        "unsafe_git_mode",
        symlink_candidate=True,
    )
    git_tree_case(
        "invalid_nested_control_dirty_path",
        True,
        "invalid",
        "dirty_worktree",
        nested_control_dirty=True,
    )
    git_tree_case(
        "invalid_generated_scope_symlink",
        True,
        "invalid",
        "unsafe_git_mode",
        generated_symlink_baseline=True,
    )
    git_tree_case(
        "invalid_dirty_evaluation_workspace",
        True,
        "invalid",
        "dirty_evaluation_workspace",
        dirty_evaluation_workspace=True,
    )
    git_tree_case(
        "invalid_generated_state_contamination",
        True,
        "invalid",
        "generated_state_not_reset",
        generated_state_contamination=True,
    )
    git_tree_case(
        "invalid_hidden_evaluation_workspace_drift",
        True,
        "invalid",
        "unsafe_workspace_index_flags",
        hidden_evaluation_workspace_drift=True,
    )
    return {"self_test": "passed", "cases": cases}, EXIT_VALID


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = UsageParser(description=__doc__)
    parser.add_argument("run_dir", nargs="?", type=Path)
    parser.add_argument("--trusted-root", type=Path)
    parser.add_argument("--git-root", type=Path)
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--require-run-finalized", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        if args.run_dir is not None or args.trusted_root is not None or args.git_root is not None:
            parser.error("--self-test does not accept run_dir, --trusted-root, or --git-root")
    elif args.run_dir is None:
        parser.error("run_dir is required unless --self-test is used")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.self_test:
        try:
            output, exit_code = run_self_test()
        except Exception as error:  # self-test failures must be visible and nonzero
            output = {"self_test": "failed", "error": f"{type(error).__name__}: {error}"}
            exit_code = EXIT_INVALID
    else:
        output, exit_code = validate_run(
            args.run_dir,
            trusted_root=args.trusted_root,
            git_root=args.git_root,
            allow_incomplete=args.allow_incomplete,
            require_run_finalized=args.require_run_finalized,
        )
    print(json.dumps(output, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
