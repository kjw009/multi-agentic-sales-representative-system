"""Vision-based condition analysis for intake photos."""

from __future__ import annotations

import json
import logging
from typing import Any, cast

import openai
from langsmith import traceable

from packages.config import settings
from packages.db.models import Item, ItemCondition, ItemImage

logger = logging.getLogger(__name__)

_MAX_IMAGES = 6
_VALID_CONDITIONS = {condition.value for condition in ItemCondition}
_VISION_SYSTEM = """\
You inspect seller-uploaded marketplace photos to identify visible item condition.
Return only valid JSON. Be conservative and factual: mention only defects visible in
the photos, do not infer hidden faults, and do not invent accessories or damage.
Use these condition grades only: new, like_new, good, fair, poor.
"""


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    return "\n".join(line for line in lines if not line.strip().startswith("```")).strip()


def _minimal_report(error: str, *, photo_quality: str = "poor") -> dict[str, Any]:
    return {
        "condition_grade": None,
        "confidence": 0.0,
        "visible_defects": [],
        "visual_descriptors": [],
        "photo_quality": photo_quality,
        "description_addendum": "",
        "descriptor_addendum": "",
        "pricing_signals": [],
        "comparable_include_terms": [],
        "comparable_exclude_terms": [],
        "analysis_error": error,
    }


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _normalise_descriptors(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    descriptors: list[dict[str, Any]] = []
    for entry in value[:20]:
        if isinstance(entry, str):
            text = entry.strip()
            if text:
                descriptors.append({"name": "visible_detail", "value": text, "confidence": 0.5})
            continue
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("type") or "visible_detail").strip()
        descriptor: dict[str, Any] = {
            "name": name,
            "value": str(entry.get("value") or "").strip(),
        }
        if not descriptor["value"]:
            continue
        if entry.get("confidence") is not None:
            try:
                descriptor["confidence"] = max(0.0, min(float(entry["confidence"]), 1.0))
            except (TypeError, ValueError):
                descriptor["confidence"] = 0.5
        if entry.get("evidence"):
            descriptor["evidence"] = str(entry["evidence"]).strip()
        descriptors.append(descriptor)
    return descriptors


def _normalise_report(parsed: Any) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        raise ValueError("vision response must be a JSON object")

    grade = parsed.get("condition_grade")
    if grade is not None:
        grade = str(grade).strip()
    if grade not in _VALID_CONDITIONS:
        grade = None

    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(confidence, 1.0))

    defects = parsed.get("visible_defects")
    if not isinstance(defects, list):
        defects = []
    normalised_defects: list[dict[str, str]] = []
    for defect in defects[:12]:
        if not isinstance(defect, dict):
            continue
        normalised_defects.append(
            {
                "type": str(defect.get("type") or "visible_wear").strip(),
                "location": str(defect.get("location") or "unknown").strip(),
                "severity": str(defect.get("severity") or "minor").strip(),
                "evidence": str(defect.get("evidence") or "").strip(),
            }
        )

    photo_quality = str(parsed.get("photo_quality") or "usable").strip().lower()
    if photo_quality not in {"clear", "usable", "poor"}:
        photo_quality = "usable"

    return {
        "condition_grade": grade,
        "confidence": confidence,
        "visible_defects": normalised_defects,
        "visual_descriptors": _normalise_descriptors(parsed.get("visual_descriptors")),
        "photo_quality": photo_quality,
        "description_addendum": str(parsed.get("description_addendum") or "").strip(),
        "descriptor_addendum": str(parsed.get("descriptor_addendum") or "").strip(),
        "pricing_signals": _coerce_string_list(parsed.get("pricing_signals")),
        "comparable_include_terms": _coerce_string_list(parsed.get("comparable_include_terms")),
        "comparable_exclude_terms": _coerce_string_list(parsed.get("comparable_exclude_terms")),
    }


def parse_visual_condition_response(text: str) -> dict[str, Any]:
    """Parse and normalise the model's JSON condition report."""
    parsed = json.loads(_strip_json_fence(text))
    return _normalise_report(parsed)


def image_urls_for_analysis(images: list[ItemImage]) -> list[str]:
    """Return ordered image URLs capped to the vision budget."""
    ordered = sorted(images, key=lambda image: image.position)
    return [image.url for image in ordered[:_MAX_IMAGES] if image.url]


def append_description_addendum(description: str, addendum: str) -> str:
    """Append a short visible-condition note without duplicating it."""
    clean = addendum.strip()
    if not clean:
        return description
    if clean.lower() in (description or "").lower():
        return description
    prefix = "Visible condition note: "
    note = clean if clean.lower().startswith(prefix.lower()) else f"{prefix}{clean}"
    if description and description.strip():
        return f"{description.rstrip()}\n\n{note}"
    return note


def apply_visual_report_to_item(item: Item, report: dict[str, Any]) -> None:
    """Persist normalised vision output onto an Item instance."""
    item.visual_condition_report = report

    attrs = dict(item.attributes or {})
    existing_condition = dict(attrs.get("visual_condition") or {})
    existing_condition.update(
        {
            "condition_grade": report.get("condition_grade"),
            "confidence": report.get("confidence", 0.0),
            "photo_quality": report.get("photo_quality"),
            "visible_defects": report.get("visible_defects", []),
            "pricing_signals": report.get("pricing_signals", []),
            "comparable_include_terms": report.get("comparable_include_terms", []),
            "comparable_exclude_terms": report.get("comparable_exclude_terms", []),
        }
    )
    attrs["visual_condition"] = existing_condition
    attrs["visual_descriptors"] = report.get("visual_descriptors", [])
    item.attributes = attrs

    description = append_description_addendum(
        item.description or "", str(report.get("description_addendum") or "")
    )
    item.description = append_description_addendum(
        description, str(report.get("descriptor_addendum") or "")
    )


def build_tool_summary(report: dict[str, Any]) -> str:
    descriptors = report.get("visual_descriptors") or []
    descriptor_bits = [
        f"{d.get('name')}: {d.get('value')}" for d in descriptors if isinstance(d, dict)
    ][:8]
    defects = report.get("visible_defects") or []
    defect_bits = [
        f"{d.get('severity', 'visible')} {d.get('type', 'wear')} at {d.get('location', 'unknown')}"
        for d in defects
        if isinstance(d, dict)
    ][:6]

    lines = [
        "Visual analysis saved.",
        f"Suggested condition: {report.get('condition_grade') or 'unknown'} "
        f"(confidence {report.get('confidence', 0.0)}).",
    ]
    if descriptor_bits:
        lines.append("Visible descriptors: " + "; ".join(descriptor_bits))
    if defect_bits:
        lines.append("Visible defects: " + "; ".join(defect_bits))
    if report.get("description_addendum") or report.get("descriptor_addendum"):
        lines.append("Description was enriched with factual visual notes.")
    return "\n".join(lines)


@traceable(name="intake_visual_condition_analysis", run_type="llm")
async def analyse_item_images(item: Item, images: list[ItemImage]) -> dict[str, Any]:
    """Call the vision model and return a normalised condition report."""
    image_urls = image_urls_for_analysis(images)
    if not image_urls:
        return _minimal_report("no_images")

    detail = "low" if item.condition == ItemCondition.like_new else "high"
    content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": (
                "Analyze these marketplace listing photos for visible listing descriptors "
                "and visible condition only. "
                f"Seller-stated condition: {item.condition}. "
                f"Title: {item.name or ''}. Category: {item.category or ''}. "
                "Return JSON with keys: condition_grade, confidence, visible_defects, "
                "visual_descriptors, photo_quality, description_addendum, descriptor_addendum, "
                "pricing_signals, comparable_include_terms, comparable_exclude_terms. "
                "For descriptors, include visible attributes such as colour, shape, style, "
                "pattern, markings, accessories, and distinguishing features. Use cautious "
                "language like silver-tone or clear stones unless material/authenticity is "
                "visibly proven."
            ),
        }
    ]
    content.extend(
        {"type": "input_image", "image_url": url, "detail": detail} for url in image_urls
    )

    try:
        client = openai.AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
            timeout=30.0,
        )
        response_input = cast(
            Any,
            [
                {"role": "system", "content": _VISION_SYSTEM},
                {"role": "user", "content": content},
            ],
        )
        response = await client.responses.create(
            model=settings.model_intake_vision,
            input=response_input,
            temperature=0.1,
        )
        text = getattr(response, "output_text", "") or ""
        return parse_visual_condition_response(text)
    except Exception as exc:
        logger.exception("Intake vision analysis failed")
        return _minimal_report(type(exc).__name__)
