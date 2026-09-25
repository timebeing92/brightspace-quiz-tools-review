#!/usr/bin/env python3
"""Render the local-only Quiz Binder Stage 1 station from JSON artifacts."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import sys
from typing import Any

from quiz_binder_strings import LABELS, render_claim, value_label
from quiz_review_queues import project_review_queues


FORMAT = "quiz-binder-local-station-v0"
ACTIVATION_BASE_COMMIT = "051180117ecc097cf5fe27c248977bab34a69246"


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _load_optional(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _exclusion_next_action(reason: str) -> str:
    if reason == "target_question_not_found":
        return "Verify that the decision overlay is paired with the intended source model."
    if reason == "question_instance_not_build_approved":
        return (
            "Keep this instance in review; Stage 1 cannot grant platform evidence. "
            "A separately authorized build-trial route is required."
        )
    if reason.startswith("question_kind_"):
        return "Keep this question in review until its kind has registry-backed build support."
    if "Library-location revisions require" in reason:
        return "Name and approve a stable target entity key before retrying promotion."
    return "Resolve the stated structural blocker, then rerun promotion."


def _promotion_exclusions(
    promotion_receipt: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if promotion_receipt is None:
        return []
    return [
        {
            "annotation_id": row.get("annotation_id"),
            "target_entity_key": row.get("target_entity_key"),
            "field_path": row.get("field_path"),
            "reason": str(row.get("reason") or "unspecified_exclusion"),
            "next_action": _exclusion_next_action(
                str(row.get("reason") or "unspecified_exclusion")
            ),
        }
        for row in promotion_receipt.get("excluded", [])
        if isinstance(row, dict)
    ]


def _fallback_identifier(entity_key: str) -> str:
    suffix = entity_key.rsplit(":", 1)[-1]
    return suffix if suffix else entity_key


def build_station_data(
    model: dict[str, Any],
    registry: dict[str, Any],
    *,
    overlay: dict[str, Any] | None = None,
    promotion_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    queues = project_review_queues(model, registry)
    questions = [
        {
            "entity_key": row["entity_key"],
            "permanent_code": row.get("identity", {}).get("permanent_code"),
            "display_identifier": row.get("identity", {}).get("permanent_code")
            or _fallback_identifier(row["entity_key"]),
            "title": row.get("title") or "Untitled question",
            "kind": row.get("kind"),
            "build_support": row.get("build_support", {}).get("level"),
            "match_state": row.get("extensions", {}).get(
                "coursecraft.binder.match_state", "not_recorded"
            ),
            "match_state_label": value_label(
                row.get("extensions", {}).get(
                    "coursecraft.binder.match_state", "not_recorded"
                )
            ),
            "build_support_label": value_label(
                row.get("build_support", {}).get("level")
            ),
        }
        for row in model.get("questions", [])
        if not (row.get("extensions") or {}).get("library_only")
    ]
    decision_summary = (
        overlay.get("content_minimized_summary", {}) if overlay is not None else {}
    )
    promotion_summary = (
        promotion_receipt.get("summary", {}) if promotion_receipt is not None else {}
    )
    return {
        "format": FORMAT,
        "activation_base_commit": ACTIVATION_BASE_COMMIT,
        "route": "strict_model_stage1",
        "route_status": "local_only",
        "model_id": model.get("model_id"),
        "source_fingerprint": model.get("source", {}).get("fingerprint", {}).get("digest"),
        "registry_schema": registry.get("schema"),
        "registry_updated_at": registry.get("updated_at"),
        "questions": questions,
        "queues": queues,
        "decision_summary": decision_summary,
        "promotion_summary": promotion_summary,
        "promotion_exclusions": _promotion_exclusions(promotion_receipt),
        "content_minimized": True,
    }


def _queue_list(rows: list[dict[str, Any]], empty: str) -> str:
    if not rows:
        return f'<p class="empty">{_e(empty)}</p>'
    items = []
    for row in rows:
        code = row.get("permanent_code") or row.get("entity_key") or row.get("diagnostic_id")
        detail = row.get("reason") or row.get("code") or row.get("state") or row.get("status")
        items.append(f"<li><code>{_e(code)}</code><span>{_e(detail)}</span></li>")
    return '<ul class="queue-list">' + "".join(items) + "</ul>"


def render_station_html(data: dict[str, Any]) -> str:
    queue_data = data["queues"]["queues"]
    summary = data["queues"]["summary"]
    question_rows = "".join(
        "<tr>"
        f"<td><code>{_e(row['display_identifier'])}</code></td>"
        f"<td>{_e(row['title'])}</td>"
        f"<td>{_e(value_label(row['kind']))}</td>"
        f"<td>{_e(row['match_state_label'])}<br><code>{_e(row['match_state'])}</code></td>"
        f"<td>{_e(row['build_support_label'])}<br><code>{_e(row['build_support'])}</code></td>"
        "</tr>"
        for row in data["questions"]
    )
    claim_rows: list[str] = []
    for claim in data["queues"]["capability_claims"]:
        if claim.get("renderable") is False:
            text = f"Claim withheld: {claim['reason']}"
            state = "withheld"
        else:
            text = render_claim(claim)
            state = claim["route_status"]
        claim_rows.append(
            f'<li><span class="claim-state">{_e(state)}</span><span>{_e(text)}</span></li>'
        )
    decision = data.get("decision_summary", {})
    promotion = data.get("promotion_summary", {})
    exclusion_rows = "".join(
        "<li>"
        f"<code>{_e(row['target_entity_key'])}</code>"
        f"<span><strong>{_e(row['reason'])}</strong><br>{_e(row['next_action'])}</span>"
        "</li>"
        for row in data.get("promotion_exclusions", [])
    )
    exclusion_detail = (
        '<h3>Excluded changes and next actions</h3><ul class="queue-list">'
        + exclusion_rows
        + "</ul>"
        if exclusion_rows
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(LABELS['station_title'])} — Local Stage 1</title>
<style>
:root{{--ink:#16202a;--muted:#52606d;--paper:#fbfaf7;--card:#fff;--line:#c7d0d9;--accent:#174f78;--focus:#b84a00;--soft:#eaf2f7;--warn:#8a4b08}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
a{{color:var(--accent)}}a:focus-visible,button:focus-visible{{outline:3px solid var(--focus);outline-offset:3px}}
.skip{{position:absolute;left:1rem;top:-5rem;background:#fff;padding:.75rem;z-index:5}}.skip:focus{{top:1rem}}
header,main,footer{{width:min(1120px,calc(100% - 2rem));margin-inline:auto}}header{{padding:2.5rem 0 1rem}}h1{{font-size:clamp(2rem,5vw,3.5rem);line-height:1;margin:.2rem 0}}h2{{margin-top:0}}.eyebrow{{font-weight:700;color:var(--accent);text-transform:uppercase;letter-spacing:.08em}}.lede{{max-width:70ch;color:var(--muted)}}
nav ul{{display:flex;flex-wrap:wrap;gap:.6rem;list-style:none;padding:0}}nav a,button{{border:1px solid var(--line);background:#fff;border-radius:.4rem;padding:.55rem .75rem;font:inherit}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:1rem}}.card,section{{background:var(--card);border:1px solid var(--line);border-radius:.7rem;padding:1rem;box-shadow:0 2px 8px #16202a12}}section{{margin:1rem 0 1.5rem}}.metric{{font-size:2rem;font-weight:750;line-height:1}}.label,.empty{{color:var(--muted)}}
.queue-list,.claims{{padding:0;list-style:none;display:grid;gap:.5rem}}.queue-list li,.claims li{{display:flex;gap:.75rem;justify-content:space-between;border-bottom:1px solid var(--line);padding:.5rem 0}}.claim-state{{font-weight:700;color:var(--warn)}}
.table-wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;min-width:720px}}th,td{{padding:.65rem;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}}th{{background:var(--soft)}}code{{overflow-wrap:anywhere}}
.boundary{{border-left:5px solid var(--warn)}}footer{{padding:1rem 0 3rem;color:var(--muted)}}body.plain section,body.plain .card{{box-shadow:none;border-radius:0}}body.plain .decorative{{display:none}}
@media (max-width:600px){{header{{padding-top:1.5rem}}.queue-list li,.claims li{{display:grid;gap:.2rem}}}}
@media (prefers-reduced-motion:reduce){{html{{scroll-behavior:auto}}*,*::before,*::after{{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}}}}
</style>
</head>
<body>
<a class="skip" href="#main">Skip to review station</a>
<header>
  <p class="eyebrow">Local-only translation and review lane</p>
  <h1>{_e(LABELS['station_title'])}</h1>
  <p class="lede">This station reads the normalized model and current capability registry. It does not parse XML, contact Brightspace, or claim that the strict-model route has L4 evidence.</p>
  <button id="plain-toggle" type="button" aria-pressed="false">{_e(LABELS['plain_mode'])}: off</button>
  <nav aria-label="Station sections"><ul><li><a href="#snapshot">Snapshot</a></li><li><a href="#queues">Queues</a></li><li><a href="#questions">Questions</a></li><li><a href="#claims">Claims</a></li><li><a href="#promotion">Promotion</a></li></ul></nav>
</header>
<main id="main" tabindex="-1">
<section id="snapshot" aria-labelledby="snapshot-title"><h2 id="snapshot-title">{_e(LABELS['source_snapshot'])}</h2><div class="grid">
<div class="card"><div class="metric">{len(data['questions'])}</div><div class="label">Questions represented</div></div>
<div class="card"><div class="metric">{summary['blocker_count']}</div><div class="label">Open blockers</div></div>
<div class="card"><div class="metric">{summary['settings_review_count']}</div><div class="label">Settings to resolve</div></div>
<div class="card"><div class="metric">{summary['asset_review_count']}</div><div class="label">Assets to resolve</div></div>
</div><p><strong>Model:</strong> <code>{_e(data['model_id'])}</code><br><strong>Registry:</strong> {_e(data['registry_schema'])}, updated {_e(data['registry_updated_at'])}</p></section>
<section id="queues" aria-labelledby="queues-title"><h2 id="queues-title">Review queues</h2><div class="grid">
<article><h3>{_e(LABELS['no_safe_match'])}</h3>{_queue_list(queue_data['no_safe_match'],'No items in this queue.')}</article>
<article><h3>{_e(LABELS['probable_match'])}</h3>{_queue_list(queue_data['probable_match'],'No items in this queue.')}</article>
<article><h3>{_e(LABELS['settings_review'])}</h3>{_queue_list(queue_data['settings_review'],'No settings need review.')}</article>
<article><h3>{_e(LABELS['asset_review'])}</h3>{_queue_list(queue_data['asset_review'],'No assets need review.')}</article>
</div><h3>Blockers</h3>{_queue_list(queue_data['blockers'],'No blockers recorded.')}</section>
<section id="questions" aria-labelledby="questions-title"><h2 id="questions-title">Question ledger</h2><p class="lede">Stable identifiers, type, match state, and support only. Question stems, options, and answers are omitted from this station ledger. A question kind can be verified in general while this course's extracted instance remains review-only until an exact build trial is separately authorized.</p><div class="table-wrap" tabindex="0" aria-label="Scrollable question ledger"><table><thead><tr><th>Code or source identifier</th><th>Title</th><th>Kind</th><th>Match state</th><th>Instance support</th></tr></thead><tbody>{question_rows}</tbody></table></div></section>
<section id="claims" aria-labelledby="claims-title"><h2 id="claims-title">Evidence-aware claims</h2><ul class="claims">{''.join(claim_rows)}</ul></section>
<section id="promotion" class="boundary" aria-labelledby="promotion-title"><h2 id="promotion-title">{_e(LABELS['promotion_preview'])}</h2><div class="grid"><div class="card"><div class="metric">{_e(decision.get('accepted_change_count',0))}</div><div class="label">Accepted workbook changes</div></div><div class="card"><div class="metric">{_e(promotion.get('applied_change_count',0))}</div><div class="label">Applied to model copy</div></div><div class="card"><div class="metric">{_e(promotion.get('excluded_change_count',0))}</div><div class="label">Loud exclusions</div></div></div>{exclusion_detail}<p><strong>Boundary:</strong> The strict-model route is local-only. Phase 5, live Brightspace, hosting, and Catalog work remain unauthorized.</p></section>
</main>
<footer>Activation base <code>{_e(data['activation_base_commit'])}</code>. No network operation is performed.</footer>
<script>
const toggle=document.getElementById('plain-toggle');
toggle.addEventListener('click',()=>{{const active=document.body.classList.toggle('plain');toggle.setAttribute('aria-pressed',String(active));toggle.textContent='Plain mode: '+(active?'on':'off');}});
</script>
</body>
</html>
"""


def write_station(
    model_path: Path,
    registry_path: Path,
    output_dir: Path,
    *,
    overlay_path: Path | None = None,
    promotion_receipt_path: Path | None = None,
) -> dict[str, Any]:
    model = json.loads(model_path.read_text(encoding="utf-8"))
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    data = build_station_data(
        model,
        registry,
        overlay=_load_optional(overlay_path),
        promotion_receipt=_load_optional(promotion_receipt_path),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "station.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "index.html").write_text(render_station_html(data), encoding="utf-8")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the local Quiz Binder station.")
    parser.add_argument("--model", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--overlay")
    parser.add_argument("--promotion-receipt")
    args = parser.parse_args()
    try:
        data = write_station(
            Path(args.model),
            Path(args.registry),
            Path(args.output_dir),
            overlay_path=Path(args.overlay) if args.overlay else None,
            promotion_receipt_path=(
                Path(args.promotion_receipt) if args.promotion_receipt else None
            ),
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"format": data["format"], "output": "index.html"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
