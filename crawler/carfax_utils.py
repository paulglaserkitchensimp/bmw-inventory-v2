"""Shared CarFax badge / VHR signal extraction for all inventory crawlers.

Call ``extract_carfax(source)`` on any HTML string, JSON blob, or listing dict
whenever vehicle information is parsed. Use ``merge_carfax`` / ``apply_carfax``
to fold signals into a vehicle record without clobbering a better badge already
present (dealer SVG slugs beat Autotrader ``vhr:`` composites).
"""

from __future__ import annotations

import json
import re
from typing import Any

CFX_RE = re.compile(r"https://www\.carfax\.com/vehiclehistory/ar20/[^\s\"'<>&]+")
# Any CarFax value-badge image URL (partnerstatic CDN, dealer embeds, lazy-load).
BADGE_SLUG_RE = re.compile(
    r"(?:partnerstatic\.carfax\.com/img/valuebadge|valuebadge)[/\\](\w+)\.(?:svg|png|jpe?g)",
    re.IGNORECASE,
)
OWNER_RE = re.compile(r"(?:^|_)(\d+)own", re.IGNORECASE)

_VHR_JSON_KEYS = frozenset({
    "vhrSummary", "vhrPreview", "vehicleHistoryReport",
    "historyReport", "vehicleHistory", "carfaxSummary",
})


def parse_badge_slug(slug: str) -> dict[str, Any]:
    """Return ownerCount + carfaxBadge from a badge filename slug."""
    om = OWNER_RE.search(slug)
    return {
        "ownerCount": int(om.group(1)) if om else None,
        "carfaxBadge": slug,
    }


def _pick_best_badge(current: str | None, candidate: str | None) -> str | None:
    """Prefer dealer SVG badge slugs over Autotrader ``vhr:`` flag strings."""
    if not candidate:
        return current or None
    if not current:
        return candidate
    cur_vhr = str(current).startswith("vhr:")
    cand_vhr = str(candidate).startswith("vhr:")
    if cur_vhr and not cand_vhr:
        return candidate
    if cand_vhr and not cur_vhr:
        return current
    if OWNER_RE.search(candidate) and not OWNER_RE.search(current):
        return candidate
    return current


def merge_carfax(existing: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    """Merge carfaxUrl / ownerCount / carfaxBadge without downgrading badge quality."""
    out: dict[str, Any] = dict(existing or {})
    if not new:
        return out
    if new.get("carfaxUrl") and not out.get("carfaxUrl"):
        out["carfaxUrl"] = new["carfaxUrl"]
    prev_badge = out.get("carfaxBadge")
    if new.get("carfaxBadge"):
        out["carfaxBadge"] = _pick_best_badge(prev_badge, new["carfaxBadge"])
    if new.get("ownerCount") is not None:
        upgraded = (
            prev_badge
            and str(prev_badge).startswith("vhr:")
            and out.get("carfaxBadge")
            and not str(out["carfaxBadge"]).startswith("vhr:")
        )
        if out.get("ownerCount") is None or upgraded:
            out["ownerCount"] = new["ownerCount"]
    return out


def extract_from_text(text: str) -> dict[str, Any]:
    """Parse CarFax-style history hints from visible page text."""
    low = text.lower()
    flags: list[str] = []
    owner_count: int | None = None

    if re.search(r"\b(?:one|1)[\-\s]owner\b", low):
        flags.append("ONE_OWNER")
        owner_count = 1
    if re.search(r"\bno[\-\s]accidents?\b", low):
        flags.append("NO_ACCIDENTS_REPORTED")
    if "free carfax" in low or "free report" in low:
        flags.append("FREE_REPORT")
    if "no salvage" in low:
        flags.append("NO_SALVAGE_TITLE")

    if not flags:
        return {}

    normalized = sorted(set(flags))
    out: dict[str, Any] = {"carfaxBadge": "vhr:" + ",".join(normalized)}
    if owner_count is not None:
        out["ownerCount"] = owner_count
    return out


def _badge_slugs_from_text(text: str) -> dict[str, Any]:
    """Collect the best CarFax SVG badge slug embedded in arbitrary text/JSON."""
    out: dict[str, Any] = {}
    for slug in BADGE_SLUG_RE.findall(text):
        info = parse_badge_slug(slug)
        out = merge_carfax(out, info)
    return out


def _vhr_flags(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        return [s.strip() for s in raw.split(",") if s.strip()]
    return []


def _vhr_from_flags(flags: list[str]) -> dict[str, Any]:
    if not flags:
        return {}
    normalized = sorted({f.upper().replace(" ", "_") for f in flags})
    out: dict[str, Any] = {"carfaxBadge": "vhr:" + ",".join(normalized)}
    if any("ONE_OWNER" in f or "1_OWNER" in f for f in normalized):
        out["ownerCount"] = 1
    return out


def extract_from_json(data: Any, *, _depth: int = 0) -> dict[str, Any]:
    """Walk JSON/dict/list payloads for badge image URLs and VHR flag fields."""
    if _depth > 12:
        return {}

    out: dict[str, Any] = {}

    if isinstance(data, dict):
        if isinstance(data.get("carfaxBadge"), str) and data["carfaxBadge"]:
            slug = data["carfaxBadge"]
            if str(slug).startswith("vhr:"):
                out = merge_carfax(out, {"carfaxBadge": slug})
            else:
                out = merge_carfax(out, parse_badge_slug(slug))
        for key in _VHR_JSON_KEYS:
            if key in data:
                out = merge_carfax(out, _vhr_from_flags(_vhr_flags(data[key])))
        if data.get("ownerCount") is not None and not out.get("ownerCount"):
            out["ownerCount"] = data["ownerCount"]
        if isinstance(data.get("carfaxUrl"), str):
            out = merge_carfax(out, {"carfaxUrl": data["carfaxUrl"]})
        out = merge_carfax(out, _badge_slugs_from_text(json.dumps(data)))
        for v in data.values():
            if isinstance(v, (dict, list)):
                out = merge_carfax(out, extract_from_json(v, _depth=_depth + 1))
        return out

    if isinstance(data, list):
        out = merge_carfax(out, _badge_slugs_from_text(json.dumps(data)))
        for item in data:
            if isinstance(item, (dict, list)):
                out = merge_carfax(out, extract_from_json(item, _depth=_depth + 1))
        return out

    if isinstance(data, str):
        out = merge_carfax(out, _badge_slugs_from_text(data))
        if "<" not in data:
            out = merge_carfax(out, extract_from_text(data))
        return out

    return out


def extract_from_html(html: str) -> dict[str, Any]:
    """Parse CarFax URL + value-badge slug (+ text hints) from page HTML."""
    out: dict[str, Any] = {}
    cfx = CFX_RE.search(html)
    if cfx:
        out["carfaxUrl"] = cfx.group(0)
    out = merge_carfax(out, _badge_slugs_from_text(html))
    if not out.get("carfaxBadge"):
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        out = merge_carfax(out, extract_from_text(text))
    return out


def extract_from_autotrader_listing(listing: dict[str, Any]) -> dict[str, Any]:
    """CarFax signals from an Autotrader SRP listing blob."""
    return extract_from_json(listing)


def extract_carfax(source: Any) -> dict[str, Any]:
    """Extract CarFax signals from HTML, JSON, listing dict, or plain text."""
    if source is None:
        return {}
    if isinstance(source, str):
        if "<" in source and ">" in source:
            return extract_from_html(source)
        return merge_carfax(extract_from_text(source), _badge_slugs_from_text(source))
    if isinstance(source, (dict, list)):
        return extract_from_json(source)
    return {}


def apply_carfax(record: dict[str, Any], *sources: Any) -> dict[str, Any]:
    """Merge CarFax fields from one or more HTML/JSON sources into *record*."""
    carfax: dict[str, Any] = {}
    for src in sources:
        carfax = merge_carfax(carfax, extract_carfax(src))
    if not carfax:
        return record
    out = dict(record)
    if carfax.get("carfaxUrl") and not out.get("carfaxUrl"):
        out["carfaxUrl"] = carfax["carfaxUrl"]
    if carfax.get("carfaxBadge"):
        out["carfaxBadge"] = _pick_best_badge(out.get("carfaxBadge"), carfax["carfaxBadge"])
    if carfax.get("ownerCount") is not None and out.get("ownerCount") is None:
        out["ownerCount"] = carfax["ownerCount"]
    elif (
        carfax.get("ownerCount") is not None
        and carfax.get("carfaxBadge")
        and not str(carfax["carfaxBadge"]).startswith("vhr:")
        and str(out.get("carfaxBadge") or "").startswith("vhr:")
    ):
        out["ownerCount"] = carfax["ownerCount"]
    return out


def badge_log_suffix(info: dict[str, Any]) -> str:
    """Compact suffix for crawler progress lines, e.g. `` badge=1own_great_black 1owner``."""
    parts: list[str] = []
    badge = info.get("carfaxBadge")
    if badge:
        parts.append(f"badge={badge}")
    if info.get("ownerCount") is not None:
        parts.append(f"{info['ownerCount']}owner")
    return (" " + " ".join(parts)) if parts else ""
