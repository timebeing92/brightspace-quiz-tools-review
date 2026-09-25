#!/usr/bin/env python3
"""Contract-aware authoring projection and receipts for the quiz builder."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import mimetypes
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any
from urllib.parse import unquote, urlsplit

from quiz_contracts import SCHEMA_REGISTRY, validate_contract
from quiz_phase5_authorization import (
    verify_candidate_authorization,
    verify_promotion_chain,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CAPABILITY_REGISTRY_PATH = (
    REPO_ROOT / "workspace" / "reference" / "schemas" / "quiz" / "quiz_build_capabilities.json"
)
SAFE_BUILD_LEVELS = {"roundtrip_verified", "import_verified"}
QUIZ_SETTING_DEFAULTS: dict[str, Any] = {
    "is_active": False,
    "attempts_allowed": 1,
    "time_limit": 0,
    "show_clock": False,
    "enforce_time_limit": False,
    "is_forward_only": False,
}
SUPPORTED_QUIZ_SETTINGS = set(QUIZ_SETTING_DEFAULTS)


def safe_ident(value: str, prefix: str = "ID") -> str:
    """Retain the historical target spelling; callers must check collisions."""
    ident = "_".join(part for part in "".join(
        char if char.isalnum() else "_" for char in value.upper()
    ).split("_") if part) or prefix
    return f"{prefix}_{ident}" if ident[0].isdigit() else ident


def projected_bank_id(pool: dict[str, Any], draw_index: int) -> str:
    code = pool["identity"].get("permanent_code") or pool["entity_key"].rsplit(":", 1)[-1]
    return re.sub(r"[^A-Za-z0-9._-]+", "_", code).strip("_") or f"POOL_{draw_index}"


def target_identifier_issues(
    question_codes: list[tuple[str, str]],
    bank_codes: list[tuple[str, str]],
    draws: list[tuple[int, str, str]],
) -> list[tuple[str, str, str]]:
    """Check the exact serialized namespaces, including normalization collisions.

    Each tuple carries its source owner so repeated references to one bank are
    allowed, but two source entities can never merge into one package object.
    Canonical identities are neither rewritten nor inferred here.
    """
    candidates = [("question", f"QUES_{safe_ident(code, 'Q')}", owner) for code, owner in question_codes]
    candidates += [("pool", f"SECT_{safe_ident(code, 'BANK')}", owner) for code, owner in bank_codes]
    candidates += [("draw", f"RAND_{safe_ident(str(order), 'SECTION')}_{safe_ident(bank, 'BANK')}", owner)
                   for order, bank, owner in draws]
    owners: dict[tuple[str, str], set[str]] = {}
    for kind, target, owner in candidates:
        owners.setdefault((kind, target), set()).add(owner)
    return [(f"target_{kind}_identifier_collision",
             f"Target {kind} identifier {target!r} represents {len(values)} distinct source entities; review colliding codes before building.",
             sorted(values)[0])
            for (kind, target), values in sorted(owners.items()) if len(values) > 1]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_capability_registry() -> dict[str, Any]:
    return json.loads(CAPABILITY_REGISTRY_PATH.read_text(encoding="utf-8"))


def _contract_errors(record: dict[str, Any]) -> list[str]:
    return [issue.render() for issue in validate_contract(record, mode="transform") if issue.severity == "error"]


def _safe_package_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value.replace("\\", "/"))
    if not value or path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"Unsafe asset package_path: {value!r}")
    return path


def package_member_path(value: str) -> PurePosixPath:
    """Map a package URI path to its physical archive member safely.

    Brightspace resolves percent-encoded HTML/manifest references as URLs, but
    the ZIP member itself must use the decoded file name. Encoded separators
    are rejected because they make the URI and archive hierarchy ambiguous.
    """
    uri_path = _safe_package_path(value)
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        raise ValueError(f"Invalid percent escape in asset package_path: {value!r}")
    if re.search(r"%(?:2f|5c|00)", value, re.IGNORECASE):
        raise ValueError(f"Encoded separator or NUL is unsafe in asset package_path: {value!r}")
    decoded = unquote(uri_path.as_posix(), encoding="utf-8", errors="strict")
    return _safe_package_path(decoded)


def resolve_model_asset(
    asset: dict[str, Any],
    *,
    asset_root: Path,
) -> dict[str, Any]:
    """Resolve one approved model asset without allowing source/package escape."""
    if asset["status"] != "resolved" or not asset["source_path"] or not asset["package_path"]:
        raise ValueError(f"Asset {asset['entity_key']} is not resolved with source_path and package_path.")
    package_path = _safe_package_path(asset["package_path"])
    archive_path = package_member_path(asset["package_path"])
    root = asset_root.resolve()
    candidate_values = [
        asset["source_path"],
        asset.get("extensions", {}).get("review_copy_path"),
        asset["package_path"],
    ]
    source = None
    for candidate_value in candidate_values:
        if not candidate_value:
            continue
        candidate_path = Path(candidate_value)
        candidate = (
            candidate_path.resolve()
            if candidate_path.is_absolute()
            else (root / candidate_path).resolve()
        )
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file():
            source = candidate
            break
    if source is None:
        raise ValueError(
            f"Asset source does not resolve inside --asset-root for {asset['entity_key']}: "
            f"{[value for value in candidate_values if value]}"
        )
    expected = asset.get("fingerprint", {}).get("digest") if asset.get("fingerprint") else None
    actual = sha256_file(source)
    if expected and expected != actual:
        raise ValueError(
            f"Asset checksum mismatch for {asset['entity_key']}: expected {expected}, got {actual}."
        )
    return {
        "entity_key": asset["entity_key"],
        "source": source,
        "package_path": package_path,
        "archive_path": archive_path,
        "media_type": asset["media_type"]
        or mimetypes.guess_type(str(package_path))[0]
        or "application/octet-stream",
        "sha256": actual,
    }


class _QuestionReferences(HTMLParser):
    """Read references without rewriting authored HTML or resolving the network."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[tuple[str, str, str]] = []
        self.unsupported: list[str] = []
        self.in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.in_style = self.in_style or tag == "style"
        seen: set[str] = set()
        for name, value in attrs:
            if name == "xml:base" or (tag == "object" and name == "codebase"):
                self.unsupported.append(f"{tag}.{name} cannot change package reference resolution")
            if name in {"srcset", "imagesrcset", "srcdoc", "archive"} and value:
                self.unsupported.append(f"{tag}.{name} needs a reviewed reference parser")
            if name == "style" and value and re.search(r"url\s*\(|@import|\\", value, re.I):
                self.unsupported.append("CSS references need a reviewed reference parser")
            if name not in {"src", "href", "xlink:href", "poster", "data", "background", "action", "formaction", "altimg"}:
                continue
            if name == "data" and tag != "object":
                continue
            if name in seen:
                self.unsupported.append(f"duplicate {tag}.{name} is ambiguous")
            seen.add(name)
            if tag == "base":
                self.unsupported.append("base URLs cannot change package reference resolution")
            self.references.append((tag, name, value or ""))

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self.in_style = False

    def handle_data(self, value: str) -> None:
        if self.in_style and re.search(r"url\s*\(|@import|\\", value, re.I):
            self.unsupported.append("CSS references need a reviewed reference parser")


def question_asset_reference_issues(
    questions: list[dict[str, Any]],
    resolved_assets: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> list[tuple[str, str, str]]:
    """Require each local HTML reference to join one selected, verified asset.

    The match uses the exact package URI -> archive-member mapping used when
    copying assets. Source-path resemblance is not a rewrite or a valid join.
    External HTTP(S), mail links and fragments retain the validator's existing
    non-packaged-reference semantics; availability/rendering is not asserted.
    """
    assets_by_path: dict[str, list[str]] = {}
    for asset in resolved_assets:
        assets_by_path.setdefault(asset["archive_path"].as_posix(), []).append(asset["entity_key"])
    bound: dict[str, set[str]] = {}
    for relation in relationships:
        if relation["kind"] == "uses_asset" and relation["status"] == "resolved":
            bound.setdefault(relation["from_entity_key"], set()).add(relation["to_entity_key"])
    issues: list[tuple[str, str, str]] = []
    for question in questions:
        key = question["entity_key"]
        payload = question["type_payload"]
        fields = [("prompt", question.get("prompt")), ("manual_answer_key", payload.get("manual_answer_key"))]
        fields += [(f"option[{index}]", row["content"]) for index, row in enumerate(payload["options"], 1)]
        fields += [(f"feedback[{index}]", row["content"]) for index, row in enumerate(question.get("feedback", []), 1)]
        for location, field in fields:
            if not field or field["format"] not in {"html", "xhtml"}:
                continue
            parser = _QuestionReferences()
            parser.feed(field["content"])
            parser.close()
            for reason in sorted(set(parser.unsupported)):
                issues.append(("html_reference_projection_not_supported", f"Question {key} {location}: {reason}.", key))
            for index, (tag, attribute, original) in enumerate(parser.references, 1):
                where = f"Question {key} {location} reference {index} ({tag}.{attribute})"
                try:
                    if any(ord(char) < 32 or ord(char) == 127 for char in original) or "\\" in original:
                        raise ValueError("control characters or backslashes make the reference unsafe")
                    # The supported URL profile trims ASCII space only; other
                    # ASCII controls are refused above. Unicode whitespace is
                    # part of the path and must never alias a clean filename.
                    value = original.strip(" ")
                    if not value:
                        if tag == "a" and attribute == "href":
                            continue
                        raise ValueError("empty media/resource reference")
                    url = urlsplit(value)
                    if url.scheme in {"http", "https"} or value.startswith("//"):
                        if (not url.hostname or any(char.isspace() for char in url.hostname)
                                or url.username is not None or url.password is not None):
                            raise ValueError("external reference needs an unambiguous host without credentials")
                        _ = url.port  # Access validates numeric range/syntax without network I/O.
                        continue
                    if attribute in {"href", "xlink:href"} and (value.startswith("#") or url.scheme == "mailto"):
                        continue
                    if url.scheme or url.netloc or url.path.startswith("/") or not url.path:
                        raise ValueError("this scheme or tenant-relative reference needs an explicit supported delivery profile")
                    member = package_member_path(url.path).as_posix()
                except (UnicodeError, ValueError) as exc:
                    issues.append(("unsafe_or_unsupported_html_reference", f"{where}: {exc}.", key))
                    continue
                matches = [owner for owner in assets_by_path.get(member, []) if owner in bound.get(key, set())]
                if not matches:
                    issues.append(("html_asset_reference_not_bound", f"{where} has no matching selected asset and resolved uses_asset relationship at its package path.", key))
                elif len(matches) != 1:
                    issues.append(("html_asset_reference_ambiguous", f"{where} matches multiple selected asset entities; resolve the asset binding explicitly.", key))
    return issues


def _as_bool(value: Any, setting: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"Setting {setting!r} requires a JSON boolean, got {value!r}.")


def _as_int(value: Any, setting: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "positive" if minimum == 1 else "nonnegative"
        raise ValueError(f"Setting {setting!r} requires a {qualifier} integer, got {value!r}.")
    return value


def coerce_supported_setting(name: str, value: Any) -> Any:
    if name in {"is_active", "show_clock", "enforce_time_limit", "is_forward_only", "randomize_answers"}:
        return _as_bool(value, name)
    if name == "attempts_allowed":
        return _as_int(value, name, 1)
    if name == "time_limit":
        return _as_int(value, name, 0)
    raise ValueError(f"No approved builder projection for known setting {name!r}.")


def question_projection_issues(
    question: dict[str, Any],
    *,
    randomize_answers: bool,
) -> list[tuple[str, str]]:
    """Return builder-specific blockers without changing the evidence model."""
    key = question["entity_key"]
    issues: list[tuple[str, str]] = []
    prompt = question.get("prompt")
    if prompt is None or not str(prompt.get("content", "")).strip():
        issues.append(("missing_question_prompt", f"Question {key} needs nonempty prompt content."))
    try:
        maximum_points = float(question["scoring"]["maximum_points"])
        if maximum_points <= 0:
            raise ValueError
    except (TypeError, ValueError):
        issues.append(("invalid_question_points", f"Question {key} needs a positive numeric maximum_points value."))

    kind = question["kind"]
    payload = question["type_payload"]
    options = payload["options"]
    if payload.get("extensions", {}).get("coursecraft.source_choice_projection", {}).get("state") == "unresolved":
        issues.append(("source_choice_projection_unresolved", f"Question {key} has unresolved source choice evidence; populated keys or build-support flags cannot authorize this projection."))
    # The only represented feedback slot is one Written Response evaluator key.
    # Count records, not distinct text: combining duplicates would also erase
    # provenance/targeting. A manual key must not silently shadow feedback.
    feedback = question.get("feedback", [])
    answer_feedback = [row for row in feedback if row["channel"] == "answer_key"]
    manual_key = payload.get("manual_answer_key")
    if any(row["channel"] != "answer_key" for row in feedback):
        issues.append(("feedback_projection_not_supported", f"Question {key} has feedback the current builder cannot preserve."))
    if answer_feedback and (kind != "long_answer" or len(answer_feedback) != 1 or manual_key is not None):
        issues.append(("answer_key_feedback_projection_not_supported", f"Question {key} requires one Written Response answer-key source; feedback cannot be duplicated, shadowed by a manual key, or attached to another kind."))
    if manual_key is not None and kind != "long_answer":
        issues.append(("manual_answer_key_projection_not_supported", f"Question {key} has a manual answer key outside Written Response."))
    if any(not str(field.get("content", "")).strip() for field in
           ([manual_key] if manual_key is not None else []) + [row["content"] for row in answer_feedback]):
        issues.append(("empty_evaluator_answer_key", f"Question {key} has an empty evaluator key that the serializer would replace with fallback text."))
    rich_fields = [prompt, payload.get("manual_answer_key"), *[row["content"] for row in options],
                   *[row["content"] for row in question.get("feedback", [])]]
    for field in filter(None, rich_fields):
        if field["format"] not in {"plain_text", "html", "xhtml"} or field.get("extensions", {}).get("coursecraft.source_encoding") == "latex":
            issues.append(("content_encoding_requires_conversion", f"Question {key} contains an explicit encoding needing a reviewed package conversion."))
            break
    if kind in {"multiple_choice", "multi_select"}:
        if not 2 <= len(options) <= 5:
            issues.append(
                (
                    "unsupported_option_count",
                    f"Question {key} has {len(options)} options; the proven builder supports two through five.",
                )
            )
        expected_keys = [chr(ord("A") + index) for index in range(len(options))]
        actual_keys = [str(row["option_key"]).upper() for row in options]
        if actual_keys != expected_keys:
            issues.append(
                (
                    "unsupported_option_keys",
                    f"Question {key} option keys must be sequential {expected_keys}, found {actual_keys}.",
                )
            )
        if any(not str(row["content"]["content"]).strip() for row in options):
            issues.append(("empty_option_content", f"Question {key} has an empty answer option."))
    elif kind == "true_false":
        if len(options) != 2:
            issues.append(("invalid_true_false_options", f"Question {key} needs exactly two modeled True/False options."))
        elif ([str(row["option_key"]).upper() for row in options] not in
              [["A", "B"], ["T", "F"], ["TRUE", "FALSE"]]
              or [(row["content"]["format"], row["content"]["content"]) for row in options]
              != [("plain_text", "True"), ("plain_text", "False")]):
            issues.append(("true_false_option_projection_not_supported", f"Question {key} must preserve the proven plain-text True then False labels and corresponding keys; custom or formatted labels cannot be discarded."))

    correct_keys = [str(row["option_key"]).upper() for row in options if row["correct"] is True]
    if kind in {"multiple_choice", "true_false"} and len(correct_keys) != 1:
        issues.append(("invalid_single_answer_key", f"Question {key} requires exactly one correct option."))
    if kind == "true_false" and correct_keys and correct_keys[0] not in {"A", "B", "T", "F", "TRUE", "FALSE"}:
        issues.append(("invalid_true_false_key", f"Question {key} has unsupported True/False key {correct_keys[0]!r}."))
    if kind == "multi_select":
        if not correct_keys:
            issues.append(("missing_multi_select_key", f"Question {key} requires at least one correct option."))
        if question["scoring"]["mode"] != "all_or_nothing":
            issues.append(("unsupported_multi_select_scoring", f"Question {key} multi-select scoring must be all_or_nothing."))
    if kind == "true_false" and randomize_answers:
        issues.append(
            (
                "unsupported_true_false_randomization",
                f"Question {key} cannot randomize True/False answer order in the proven projection.",
            )
        )
    return issues


def default_settings_receipt(quiz_key: str, run_id: str) -> dict[str, Any]:
    inputs = []
    resolutions = []
    for name, value in QUIZ_SETTING_DEFAULTS.items():
        input_id = f"in.{name}.hard-default"
        inputs.append(
            {
                "input_id": input_id,
                "setting": name,
                "layer": "hard_default",
                "target_entity_key": quiz_key,
                "state": "known",
                "value": value,
                "source_reference": "quiz builder safe defaults v1",
                "source_evidence_keys": [],
                "extensions": {"unit": "minutes"} if name == "time_limit" else {},
            }
        )
        resolutions.append(
            {
                "setting": name,
                "target_entity_key": quiz_key,
                "state": "known",
                "effective_value": value,
                "winner_input_id": input_id,
                "considered_input_ids": [input_id],
                "defaulted": True,
                "coerced": False,
                "coercion_note": None,
                "source_evidence_keys": [],
                "extensions": {"unit": "minutes"} if name == "time_limit" else {},
            }
        )
    return {
        "schema": "coursecraft.quiz_settings/1",
        "receipt_id": f"cc:settings-receipt:{hashlib.sha256((run_id + quiz_key).encode()).hexdigest()[:24]}",
        "run_id": run_id,
        "target_quiz_key": quiz_key,
        "profile_key": None,
        "inputs": inputs,
        "resolutions": resolutions,
        "diagnostics": [],
        "extensions": {"coursecraft.authoring_projection": "quiz-builder-v1"},
    }


def load_settings_receipt(path: Path | None, quiz_key: str, run_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, bool]]:
    receipt = default_settings_receipt(quiz_key, run_id) if path is None else json.loads(path.read_text(encoding="utf-8"))
    errors = _contract_errors(receipt)
    if errors:
        raise ValueError("Settings receipt is not transform-safe:\n- " + "\n- ".join(errors))
    if receipt["target_quiz_key"] != quiz_key:
        raise ValueError(
            f"Settings receipt targets {receipt['target_quiz_key']!r}, not selected quiz {quiz_key!r}."
        )

    quiz_settings = dict(QUIZ_SETTING_DEFAULTS)
    question_randomize: dict[str, bool] = {}
    for resolution in receipt["resolutions"]:
        if resolution["state"] != "known":
            continue
        name = resolution["setting"]
        target = resolution["target_entity_key"]
        value = coerce_supported_setting(name, resolution["effective_value"])
        if target == quiz_key:
            if name not in SUPPORTED_QUIZ_SETTINGS:
                raise ValueError(f"Setting {name!r} is not valid at quiz scope.")
            quiz_settings[name] = value
        elif name == "randomize_answers":
            question_randomize[target] = value
        else:
            raise ValueError(f"Setting {name!r} is not valid at question scope.")
    return receipt, quiz_settings, question_randomize


def materialize_effective_settings_receipt(
    receipt: dict[str, Any],
    *,
    quiz_key: str,
    run_id: str,
    question_keys: set[str],
) -> dict[str, Any]:
    """Add explicit hard-default winners for every setting the builder applies."""
    result = deepcopy(receipt)
    source_receipt_id = result["receipt_id"]
    result["run_id"] = run_id
    result["receipt_id"] = f"cc:settings-receipt:{hashlib.sha256((run_id + quiz_key).encode()).hexdigest()[:24]}"
    result["extensions"] = {
        **result.get("extensions", {}),
        "coursecraft.source_settings_receipt_id": source_receipt_id,
        "coursecraft.authoring_projection": "quiz-builder-v1",
    }
    existing = {(row["target_entity_key"], row["setting"]) for row in result["resolutions"]}

    defaults = [(quiz_key, name, value) for name, value in QUIZ_SETTING_DEFAULTS.items()]
    defaults.extend((key, "randomize_answers", False) for key in sorted(question_keys))
    for target, name, value in defaults:
        if (target, name) in existing:
            continue
        token = hashlib.sha256(f"{target}|{name}".encode()).hexdigest()[:12]
        input_id = f"in.{name}.{token}.hard-default"
        result["inputs"].append(
            {
                "input_id": input_id,
                "setting": name,
                "layer": "hard_default",
                "target_entity_key": target,
                "state": "known",
                "value": value,
                "source_reference": "quiz builder safe defaults v1",
                "source_evidence_keys": [],
                "extensions": {"unit": "minutes"} if name == "time_limit" else {},
            }
        )
        result["resolutions"].append(
            {
                "setting": name,
                "target_entity_key": target,
                "state": "known",
                "effective_value": value,
                "winner_input_id": input_id,
                "considered_input_ids": [input_id],
                "defaulted": True,
                "coerced": False,
                "coercion_note": None,
                "source_evidence_keys": [],
                "extensions": {"unit": "minutes"} if name == "time_limit" else {},
            }
        )
    errors = _contract_errors(result)
    if errors:
        raise ValueError("Materialized settings receipt is invalid:\n- " + "\n- ".join(errors))
    return result


def _choose_quiz(model: dict[str, Any], requested_key: str) -> dict[str, Any]:
    quizzes = model["quizzes"]
    if requested_key:
        matches = [row for row in quizzes if row["entity_key"] == requested_key]
        if not matches:
            raise ValueError(f"Quiz entity key not found in authoring model: {requested_key}")
        return matches[0]
    if len(quizzes) != 1:
        raise ValueError("Authoring model must contain exactly one quiz or use --quiz-entity-key.")
    return quizzes[0]


def resolve_projection_relationships(
    model: dict[str, Any],
    quiz_key: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Return resolved relations plus narrowly derived source-observed joins.

    Real extraction preserves a random section as a ``draw`` and its direct
    question-library references as ``itemref`` + ``member_of`` edges. It does
    not manufacture the authoring-only ``contains`` or ``draws_from`` edges
    used by the strict projection fixture. For a selected quiz, these two joins
    may be derived only when the source evidence is exact and unambiguous:

    * a draw shares source evidence with the selected quiz and has direct,
      resolved itemrefs; and
    * every direct itemref question has a source-evidence membership in one
      and the same pool.

    The model is not changed. Each derivation is returned for the build receipt.
    Inferred/similarity matches never qualify.
    """
    resolved = [
        dict(row) for row in model["relationships"] if row["status"] == "resolved"
    ]
    structures = {row["entity_key"]: row for row in model["structures"]}
    questions = {row["entity_key"]: row for row in model["questions"]}
    quiz = next(row for row in model["quizzes"] if row["entity_key"] == quiz_key)
    derivations: list[dict[str, str]] = []

    reachable = {quiz_key}
    changed = True
    while changed:
        changed = False
        for relation in resolved:
            if relation["kind"] == "contains" and relation["from_entity_key"] in reachable:
                target = relation["to_entity_key"]
                if target and target not in reachable:
                    reachable.add(target)
                    changed = True

    if not any(
        key in structures and structures[key]["kind"] == "draw" for key in reachable
    ):
        quiz_evidence = set(quiz.get("source_evidence_keys", []))
        for draw in model["structures"]:
            if draw["kind"] != "draw" or not quiz_evidence.intersection(
                draw.get("source_evidence_keys", [])
            ):
                continue
            direct_itemrefs = [
                row
                for row in resolved
                if row["kind"] == "itemref"
                and row["from_entity_key"] == draw["entity_key"]
                and row["to_entity_key"] in questions
            ]
            if not direct_itemrefs:
                continue
            relation_key = f"cc:relationship:projection:{hashlib.sha256((quiz_key + '|' + draw['entity_key']).encode()).hexdigest()[:24]}"
            resolved.append(
                {
                    "relationship_key": relation_key,
                    "kind": "contains",
                    "source_kind": "source_observed_projection",
                    "from_entity_key": quiz_key,
                    "to_entity_key": draw["entity_key"],
                    "status": "resolved",
                    "ordinal": draw.get("ordinal"),
                    "attributes": {},
                    "candidates": [],
                    "source_evidence_keys": sorted(
                        quiz_evidence.intersection(draw.get("source_evidence_keys", []))
                    ),
                    "diagnostic_ids": [],
                    "extensions": {"coursecraft.projection_only": True},
                }
            )
            derivations.append(
                {
                    "kind": "quiz_contains_draw",
                    "from_entity_key": quiz_key,
                    "to_entity_key": draw["entity_key"],
                    "basis": "shared_source_evidence_and_direct_itemrefs",
                }
            )

    reachable = {quiz_key}
    changed = True
    while changed:
        changed = False
        for relation in resolved:
            if relation["kind"] == "contains" and relation["from_entity_key"] in reachable:
                target = relation["to_entity_key"]
                if target and target not in reachable:
                    reachable.add(target)
                    changed = True
    draw_keys = {
        key
        for key in reachable
        if key in structures and structures[key]["kind"] == "draw"
    }
    for draw_key in sorted(draw_keys):
        if any(
            row["kind"] == "draws_from" and row["from_entity_key"] == draw_key
            for row in resolved
        ):
            continue
        itemref_question_keys = {
            row["to_entity_key"]
            for row in resolved
            if row["kind"] == "itemref"
            and row["from_entity_key"] == draw_key
            and row["to_entity_key"] in questions
        }
        if not itemref_question_keys:
            continue
        pools_by_question: list[set[str]] = []
        for question_key in sorted(itemref_question_keys):
            pool_keys = {
                row["to_entity_key"]
                for row in resolved
                if row["kind"] == "member_of"
                and row["from_entity_key"] == question_key
                and row["to_entity_key"] in structures
                and structures[row["to_entity_key"]]["kind"] in {"bank", "pool"}
                and row.get("attributes", {}).get("evidence_level") == "source_evidence"
            }
            pools_by_question.append(pool_keys)
        common_pools = set.intersection(*pools_by_question) if pools_by_question else set()
        if len(common_pools) != 1:
            continue
        pool_key = next(iter(common_pools))
        relation_key = f"cc:relationship:projection:{hashlib.sha256((draw_key + '|' + pool_key).encode()).hexdigest()[:24]}"
        resolved.append(
            {
                "relationship_key": relation_key,
                "kind": "draws_from",
                "source_kind": "source_observed_projection",
                "from_entity_key": draw_key,
                "to_entity_key": pool_key,
                "status": "resolved",
                "ordinal": 1,
                "attributes": {},
                "candidates": [],
                "source_evidence_keys": [],
                "diagnostic_ids": [],
                "extensions": {"coursecraft.projection_only": True},
            }
        )
        derivations.append(
            {
                "kind": "draw_draws_from_pool",
                "from_entity_key": draw_key,
                "to_entity_key": pool_key,
                "basis": "direct_itemrefs_share_one_source_evidence_pool",
            }
        )
    return resolved, derivations


def load_authoring_projection(
    model_path: Path,
    *,
    quiz_entity_key: str = "",
    settings_path: Path | None = None,
    asset_root: Path | None = None,
    promotion_receipt_path: Path | None = None,
    trial_authorization_path: Path | None = None,
) -> dict[str, Any]:
    """Translate one reviewed quiz model into the current proven builder vocabulary.

    This is deliberately strict. It does not promote extraction-only questions,
    infer unresolved relationships, or silently ignore known settings.
    """
    model = json.loads(model_path.read_text(encoding="utf-8"))
    errors = _contract_errors(model)
    if errors:
        raise ValueError("Authoring model is not transform-safe:\n- " + "\n- ".join(errors))
    quiz = _choose_quiz(model, quiz_entity_key)
    quiz_key = quiz["entity_key"]
    promotion_receipt = None
    trial_authorization = None
    authorized_question_keys: set[str] = set()
    if trial_authorization_path is not None and promotion_receipt_path is None:
        raise ValueError("A Phase 5 candidate authorization requires --promotion-receipt.")
    if promotion_receipt_path is not None:
        promotion_receipt = json.loads(
            promotion_receipt_path.read_text(encoding="utf-8")
        )
        verify_promotion_chain(model, promotion_receipt)
    if trial_authorization_path is not None:
        trial_authorization = verify_candidate_authorization(
            model_path=model_path,
            promotion_receipt_path=promotion_receipt_path,
            registry_path=CAPABILITY_REGISTRY_PATH,
            authorization_path=trial_authorization_path,
            quiz_entity_key=quiz_key,
        )
        authorized_question_keys = set(
            trial_authorization["scope"]["question_entity_keys"]
        )
    run_id = f"cc:run:quiz-build:{hashlib.sha256((str(model_path) + utc_now()).encode()).hexdigest()[:24]}"
    settings_receipt, quiz_settings, question_randomize = load_settings_receipt(settings_path, quiz_key, run_id)

    registry = load_capability_registry()
    kind_caps = registry["question_kinds"]
    structures = {row["entity_key"]: row for row in model["structures"]}
    questions = {row["entity_key"]: row for row in model["questions"]}
    assets = {row["entity_key"]: row for row in model["assets"]}
    resolved, route_derivations = resolve_projection_relationships(model, quiz_key)
    unresolved_from_quiz = [
        row for row in model["relationships"]
        if row["from_entity_key"] == quiz_key and row["status"] != "resolved"
    ]
    if unresolved_from_quiz:
        raise ValueError("Selected quiz has unresolved direct relationships; review them before building.")

    reachable = {quiz_key}
    changed = True
    while changed:
        changed = False
        for relation in resolved:
            if relation["kind"] == "contains" and relation["from_entity_key"] in reachable:
                target = relation["to_entity_key"]
                if target and target not in reachable:
                    reachable.add(target)
                    changed = True
    draws = sorted(
        (structures[key] for key in reachable if key in structures and structures[key]["kind"] == "draw"),
        key=lambda row: (row["ordinal"] is None, row["ordinal"] or 0, row["entity_key"]),
    )
    if not draws:
        raise ValueError("Selected quiz has no resolved, reachable random draw structure.")

    projected_questions: list[dict[str, Any]] = []
    projected_sections: list[dict[str, Any]] = []
    projected_banks: list[tuple[str, str]] = []
    selected_question_keys: set[str] = set()
    for draw_index, draw in enumerate(draws, start=1):
        if draw["selection"]["mode"] != "random":
            raise ValueError(f"Draw {draw['entity_key']} must use selection.mode='random'.")
        pool_edges = [
            row for row in resolved
            if row["kind"] == "draws_from" and row["from_entity_key"] == draw["entity_key"]
        ]
        if len(pool_edges) != 1 or pool_edges[0]["to_entity_key"] not in structures:
            raise ValueError(f"Draw {draw['entity_key']} needs exactly one resolved draws_from pool.")
        pool = structures[pool_edges[0]["to_entity_key"]]
        if pool["kind"] not in {"bank", "pool"}:
            raise ValueError(f"Draw {draw['entity_key']} targets non-pool structure {pool['entity_key']}.")
        members = sorted(
            (
                questions[row["from_entity_key"]]
                for row in resolved
                if row["kind"] == "member_of"
                and row["to_entity_key"] == pool["entity_key"]
                and row["from_entity_key"] in questions
            ),
            key=lambda row: row["entity_key"],
        )
        if not members:
            raise ValueError(f"Pool {pool['entity_key']} has no resolved question members.")
        requested = draw["selection"]["requested_count"]
        if requested is None or requested < 1 or requested > len(members):
            raise ValueError(
                f"Draw {draw['entity_key']} requested_count must be between 1 and {len(members)}."
            )
        point_value = draw["selection"]["extensions"].get("points_per_question")
        if point_value is None:
            points = {str(row["scoring"]["maximum_points"]) for row in members}
            if len(points) != 1:
                raise ValueError(
                    f"Draw {draw['entity_key']} has mixed question points; set selection.extensions.points_per_question."
                )
            point_value = members[0]["scoring"]["maximum_points"]
        point_value = float(point_value)
        if point_value <= 0:
            raise ValueError(f"Draw {draw['entity_key']} points_per_question must be positive.")

        bank_id = projected_bank_id(pool, draw_index)
        projected_banks.append((bank_id, pool["entity_key"]))
        projected_sections.append(
            {
                "section_order": draw["ordinal"] if draw["ordinal"] is not None else draw_index,
                "section_title": draw["title"] or pool["title"] or bank_id,
                "source_bank_id": bank_id,
                "recommended_draw_count": requested,
                "points_per_question": point_value,
                "notes": f"Projected from {draw['entity_key']} via {pool['entity_key']}",
                "entity_key": draw["entity_key"],
            }
        )

        for question in members:
            key = question["entity_key"]
            if key in selected_question_keys:
                raise ValueError(f"Question {key} is selected by more than one draw; duplicate placement is not projected.")
            selected_question_keys.add(key)
            capability = kind_caps.get(question["kind"], {"status": "unsupported"})
            if capability.get("status") != "roundtrip_verified":
                raise ValueError(
                    f"Question {key} kind {question['kind']!r} is {capability.get('status', 'unsupported')}, not build-supported."
                )
            if question["build_support"]["level"] not in SAFE_BUILD_LEVELS:
                if (
                    trial_authorization is None
                    or question["build_support"]["level"] != "extraction_only"
                    or key not in authorized_question_keys
                ):
                    raise ValueError(
                        f"Question {key} declares build_support {question['build_support']['level']!r}; explicit import/round-trip evidence or an exact Phase 5 candidate authorization is required."
                    )
            permanent_code = question["identity"].get("permanent_code")
            if not permanent_code:
                raise ValueError(f"Question {key} needs identity.permanent_code before authoring projection.")
            projection_issues = question_projection_issues(
                question,
                randomize_answers=question_randomize.get(key, False),
            )
            if projection_issues:
                raise ValueError(projection_issues[0][1])
            payload = question["type_payload"]
            options = payload["options"]
            correct_keys = [row["option_key"] for row in options if row["correct"] is True]
            answer_content = payload.get("manual_answer_key")
            if answer_content is None:
                answer_content = next((row["content"] for row in question["feedback"] if row["channel"] == "answer_key"), None)
            projected_questions.append(
                {
                    "bank_id": bank_id,
                    "bank_title": pool["title"] or bank_id,
                    # Model question.title is already the canonical complete title.
                    # Workbook rows retain their separate week/scope prefix.
                    "source_label": "",
                    "question_code": permanent_code,
                    "question_title": question["title"] or permanent_code,
                    "target_question_type": capability["target_question_type"],
                    "scoring_policy": "ALL_OR_NOTHING" if question["kind"] == "multi_select" else "",
                    "scoring_policy_value": "",
                    "question_text": question["prompt"]["content"] if question["prompt"] else "",
                    "question_text_format": question["prompt"]["format"] if question["prompt"] else None,
                    "points": float(question["scoring"]["maximum_points"] or point_value),
                    "randomize_answers": question_randomize.get(key, False),
                    "options": [row["content"]["content"] for row in options],
                    "option_formats": [row["content"]["format"] for row in options],
                    "correct_option_key": ";".join(correct_keys),
                    "correct_option_text": "",
                    "evaluator_answer_key": answer_content["content"] if answer_content else "",
                    "evaluator_answer_key_format": answer_content["format"] if answer_content else None,
                    "notes": "",
                    "label": "",
                    "entity_key": key,
                }
            )

    identifier_issues = target_identifier_issues(
        [(row["question_code"], row["entity_key"]) for row in projected_questions],
        projected_banks,
        [(row["section_order"], row["source_bank_id"], row["entity_key"]) for row in projected_sections],
    )
    if identifier_issues:
        raise ValueError(identifier_issues[0][1])

    projected_assets_by_key: dict[str, dict[str, Any]] = {}
    root = (asset_root or model_path.parent).resolve()
    unresolved_asset_edges = [
        row
        for row in model["relationships"]
        if row["kind"] == "uses_asset"
        and row["from_entity_key"] in selected_question_keys
        and row["status"] != "resolved"
    ]
    if unresolved_asset_edges:
        raise ValueError("Selected questions have unresolved asset relationships; review them before building.")
    for relation in resolved:
        if relation["kind"] != "uses_asset" or relation["from_entity_key"] not in selected_question_keys:
            continue
        asset = assets.get(relation["to_entity_key"])
        if asset is None:
            raise ValueError(f"Resolved uses_asset relationship {relation['relationship_key']} lacks a buildable asset.")
        projected_assets_by_key[asset["entity_key"]] = resolve_model_asset(
            asset,
            asset_root=root,
        )

    package_path_owners: dict[str, dict[str, Any]] = {}
    for asset in projected_assets_by_key.values():
        path_key = asset["archive_path"].as_posix()
        previous = package_path_owners.get(path_key)
        if previous and previous["sha256"] != asset["sha256"]:
            raise ValueError(f"Different assets target the same archive member path: {path_key}")
        package_path_owners[path_key] = asset

    reference_issues = question_asset_reference_issues(
        [questions[key] for key in sorted(selected_question_keys)],
        list(projected_assets_by_key.values()),
        resolved,
    )
    if reference_issues:
        raise ValueError(reference_issues[0][1])

    if trial_authorization is not None and selected_question_keys != authorized_question_keys:
        missing = sorted(selected_question_keys - authorized_question_keys)
        extra = sorted(authorized_question_keys - selected_question_keys)
        raise ValueError(
            "Phase 5 candidate authorization question scope must exactly match the selected "
            f"authoring projection (missing={missing}, extra={extra})."
        )
    settings_receipt = materialize_effective_settings_receipt(
        settings_receipt,
        quiz_key=quiz_key,
        run_id=run_id,
        question_keys=selected_question_keys,
    )

    return {
        "model": model,
        "model_path": model_path,
        "quiz": quiz,
        "quiz_key": quiz_key,
        "quiz_title": quiz["title"] or model_path.stem,
        "run_id": run_id,
        "settings_receipt": settings_receipt,
        "quiz_settings": quiz_settings,
        "questions": projected_questions,
        "sections": projected_sections,
        "assets": list(projected_assets_by_key.values()),
        "capability_registry": registry,
        "promotion_receipt": promotion_receipt,
        "phase5_candidate_authorization": trial_authorization,
        "route_derivations": route_derivations,
    }


def git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True, capture_output=True, check=False
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def artifact_row(path: Path, role: str, *, display_path: str | None = None, status: str = "emitted") -> dict[str, Any]:
    exists = path.is_file()
    return {
        "path": display_path or str(path),
        "role": role,
        "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "sha256": sha256_file(path) if exists else None,
        "bytes": path.stat().st_size if exists else None,
        "status": status if exists else "missing",
        "extensions": {},
    }


def build_run_receipt(
    *,
    run_id: str,
    started_at: str,
    source_path: Path,
    source_kind: str,
    source_scope: str,
    inputs: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    capability_names: list[str],
    settings_receipt_path: str,
    source_lineage_key: str | None = None,
) -> dict[str, Any]:
    finished_at = utc_now()
    if source_kind == "composite":
        digest = hashlib.sha256()
        for row in sorted(inputs, key=lambda item: item["path"]):
            digest.update(
                json.dumps(
                    [row["path"], row["sha256"], row["bytes"]],
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            digest.update(b"\n")
        fingerprint = digest.hexdigest()
        source_scope = "file_set"
    else:
        fingerprint = sha256_file(source_path)
    contracts = ["coursecraft.quiz_run/1", "coursecraft.quiz_settings/1"]
    if source_kind != "workbook":
        contracts.append("coursecraft.quiz/1")
    receipt = {
        "schema": "coursecraft.quiz_run/1",
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "producer": {
            "name": "scripts.build_quiz_package_from_workbook",
            "version": "phase-4",
            "commit": git_commit(),
            "release": None,
            "command": None,
            "extensions": {},
        },
        "source": {
            "source_key": f"cc:source:{fingerprint}",
            "source_lineage_key": source_lineage_key or f"cc:lineage:quiz-authoring:{fingerprint[:24]}",
            "source_kind": source_kind,
            "fingerprint": {"algorithm": "sha256", "digest": fingerprint, "scope": source_scope},
            "references": [row["path"] for row in inputs],
            "extensions": {},
        },
        "contract_versions": [
            {"schema": schema, "schema_sha256": sha256_file(SCHEMA_REGISTRY[schema]), "extensions": {}}
            for schema in contracts
        ],
        "inputs": inputs,
        "artifacts": artifacts,
        "capabilities": [
            {
                "name": name,
                "status": "completed",
                "started_at": started_at,
                "finished_at": finished_at,
                "artifact_paths": [row["path"] for row in artifacts],
                "diagnostic_ids": [],
                "notes": ["Local or previously receipt-backed capability; no Phase 5 live operation performed."],
                "extensions": {},
            }
            for name in capability_names
        ],
        "diagnostics": [],
        "extensions": {
            "coursecraft.settings_receipt": settings_receipt_path,
            "coursecraft.live_brightspace_operations": "not_performed",
            "coursecraft.capability_registry_sha256": sha256_file(CAPABILITY_REGISTRY_PATH),
        },
    }
    errors = _contract_errors(receipt)
    if errors:
        raise ValueError("Generated build run receipt is invalid:\n- " + "\n- ".join(errors))
    return receipt
