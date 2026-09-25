#!/usr/bin/env python3
"""Plain terminal adapter for real-export Quiz Binder Unbind runs.

This bundle-owned module contributes terminal presentation only. The
bundle-contained, Workbench-pinned ``quiz_unbind`` and
``verify_quiz_unbind_run`` modules remain the producer and verification
authorities. No Workbench checkout or network access is required at runtime.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import quiz_unbind
from quiz_assessment_review import LAYOUT, WORKING, prepare_review_packet, validate_packet
from verify_quiz_unbind_run import UnbindVerificationError, verify_unbind_run


def _line(event: dict[str, Any]) -> str:
    sequence = event.get("sequence", "?")
    stage = str(event.get("stage") or "unknown")
    status = str(event.get("status") or "unknown")
    code = str(event.get("code") or "unknown")
    return f"unbind {sequence}: {stage} — {status} ({code})"


def render_verified_unbind(run_dir: Path) -> str:
    """Re-verify one Unbind run, then return its content-minimized summary."""
    run_dir = run_dir.expanduser().resolve()
    verification = verify_unbind_run(run_dir)
    review_packet = run_dir / "review" / "assessment"
    packet_profile = None
    if (review_packet / LAYOUT).is_file():
        packet_profile = validate_packet(review_packet).get("profile")
    summary_path = run_dir / "UNBIND_SUMMARY.md"
    summary = summary_path.read_text(encoding="utf-8").rstrip()
    header = [
        "QUIZ BINDER — VERIFIED UNBIND RUN",
        "",
        "The receipt, extraction artifacts, and Reading Room shards were re-hashed",
        "before this summary was shown.",
        "",
        f"verification: {verification['status']}",
        f"verified references: {verification['total_reference_count']}",
        "Brightspace import/re-export: not performed",
        "",
    ]
    if (review_packet / LAYOUT).is_file():
        label = "Question inventory workbook" if packet_profile == "inventory_only" else "Editable assessment workbook"
        header.extend([f"{label}: {review_packet / WORKING}",
                       "Keep the complete assessment folder with its images and import companions.", ""])
    return "\n".join(header) + summary + "\n"


def run_real_unbind(
    *,
    export_path: Path,
    output_dir: Path,
    source_lineage_key: str = "",
    quiz_entity_key: str = "",
    asset_mode: str = "self_contained",
    registry_path: Path | None = None,
    allow_tracked_output: bool = False,
) -> int:
    """Run canonical Unbind with a content-minimized terminal progress record."""
    print("QUIZ BINDER — REAL EXPORT UNBIND")
    print("Extraction and review only; no package build, import, or network operation.")
    print()

    def sink(event: dict[str, Any]) -> None:
        print(_line(event), flush=True)

    try:
        quiz_unbind.run_unbind(
            export_path=export_path,
            output_dir=output_dir,
            source_lineage_key=source_lineage_key,
            quiz_entity_key=quiz_entity_key,
            asset_mode=asset_mode,
            registry_path=registry_path,
            event_sinks=(sink,),
            allow_tracked_output=allow_tracked_output,
        )
        if asset_mode == "self_contained":
            extraction = output_dir / "extraction"
            prepare_review_packet(next(extraction.glob("*__quiz_pool_review_reviewer.xlsx")),
                                  next(extraction.glob("*.model.json")),
                                  output_dir / "review" / "assessment")
        print()
        print(render_verified_unbind(output_dir), end="")
        return 0
    except KeyboardInterrupt:
        print(
            "CANCELLED: Unbind cancelled; no output was promoted",
            file=sys.stderr,
        )
        return 130
    except quiz_unbind.UnbindRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
    except quiz_unbind.UnbindFailed as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
    except (OSError, ValueError, UnbindVerificationError) as exc:
        print(f"ERROR: Unbind terminal could not verify the run ({exc})", file=sys.stderr)
    except Exception:  # noqa: BLE001 - content-minimizing terminal boundary
        print("ERROR: Unbind failed unexpectedly", file=sys.stderr)
    return 2


def read_real_unbind(run_dir: Path) -> int:
    """Print a completed run only after full in-process verification."""
    try:
        print(render_verified_unbind(run_dir), end="")
        return 0
    except (OSError, ValueError, json.JSONDecodeError, UnbindVerificationError) as exc:
        print(f"ERROR: completed Unbind run was refused ({exc})", file=sys.stderr)
        return 2
