"""
merge_results.py — union any number of inventory JSON files (as emitted by
``search_inventory.py`` / ``crawl_autotrader.py``) into one, deduping by
VIN, and optionally drop listings in blacklisted states.

Record-merging strategy per VIN:
  • The base record is the one with the most *authoritative* fields
    populated (``daysOnLot``, ``dateInStock``, ``carfaxUrl``, ``dealerUrl``,
    ``dealerPhone``, ``extColor``, ``interiorColor``, ``stockNumber``,
    ``internetPrice``). Ties go to the earlier ``--inputs`` entry.
  • Empty fields on the base record are then filled from other records
    that have them, so we don't lose a price-only source or a CarFax-only
    source.
  • Aggregator dealer URLs (``cars.com``, ``edmunds.com``, …) get
    overwritten with a real dealer domain when another record has one.

Usage
-----
  # merge Autotrader results into results.json, drop CA:
  uv run python merge_results.py \
      --inputs results.json autotrader_results.json \
      --out results.json \
      --exclude-states CA

  # dry-run to preview:
  uv run python merge_results.py \
      --inputs results.json autotrader_results.json \
      --exclude-states CA --dry-run
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

from carfax_utils import merge_carfax


# Authoritative fields used to (a) pick the "best" base record when a VIN
# shows up in multiple sources, and (b) backfill still-empty fields from
# other sources after the base is chosen.
AUTHORITATIVE_FIELDS = (
    "daysOnLot",
    "dateInStock",
    "carfaxUrl",
    "carfaxBadge",
    "ownerCount",
    "dealerUrl",
    "dealerPhone",
    "dealerCity",
    "dealerState",
    "dealerName",
    "extColor",
    "interiorColor",
    "stockNumber",
    "internetPrice",
    "odometer",
    "packageSignals",
)


# Aggregator/marketplace dealerUrls we'd rather overwrite with a real
# dealer domain if we have one in the extra record.
BAD_DEALER_HOSTS = {
    "cars.com",
    "edmunds.com",
    "autotrader.com",
    "cargurus.com",
    "carfax.com",
    "truecar.com",
}


def _is_empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str) and not v.strip():
        return True
    if isinstance(v, (list, dict)) and not v:
        return True
    return False


def _is_bad_dealer_url(url: str | None) -> bool:
    """True if *url*'s host is a marketplace aggregator we don't want to keep
    in the base record when a better URL is available."""
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return False
    return any(host == h or host.endswith("." + h) for h in BAD_DEALER_HOSTS)


def _score(rec: dict) -> int:
    """Count how many authoritative fields are populated on *rec*."""
    return sum(1 for k in AUTHORITATIVE_FIELDS if not _is_empty(rec.get(k)))


def _pick_base(records: list[dict]) -> int:
    """Return the index of the best base record.

    Ranking, in priority order:
      1. More authoritative fields populated (higher ``_score``).
      2. Tie-break: prefer records whose ``link`` is on a real dealer domain
         (not an aggregator like autotrader.com / cars.com). This keeps the
         visualizer's "VDP" link pointing at the actual dealership listing
         rather than an aggregator redirect.
      3. Tie-break: earlier ``--inputs`` entry.
    """
    def rank(r: dict) -> tuple[int, int]:
        not_aggregator = 0 if _is_bad_dealer_url(r.get("link")) else 1
        return (_score(r), not_aggregator)

    best = 0
    best_rank = rank(records[0])
    for i in range(1, len(records)):
        r = rank(records[i])
        if r > best_rank:
            best, best_rank = i, r
    return best


def _merge_records(records: list[dict]) -> dict:
    """Pick the best base record then backfill empty fields from the rest."""
    base_idx = _pick_base(records)
    out = dict(records[base_idx])

    for i, extra in enumerate(records):
        if i == base_idx:
            continue
        for k in AUTHORITATIVE_FIELDS:
            if _is_empty(out.get(k)) and not _is_empty(extra.get(k)):
                out[k] = extra[k]
        # Prefer dealer-site SVG badge slugs over Autotrader VHR composites.
        if extra.get("carfaxBadge") or extra.get("carfaxUrl") or extra.get("ownerCount") is not None:
            merged = merge_carfax(
                {k: out.get(k) for k in ("carfaxUrl", "carfaxBadge", "ownerCount")},
                {k: extra.get(k) for k in ("carfaxUrl", "carfaxBadge", "ownerCount")},
            )
            out.update({k: v for k, v in merged.items() if v is not None})
        # Replace aggregator dealer URLs with a real one when available
        if (
            _is_bad_dealer_url(out.get("dealerUrl"))
            and not _is_empty(extra.get("dealerUrl"))
            and not _is_bad_dealer_url(extra.get("dealerUrl"))
        ):
            out["dealerUrl"] = extra["dealerUrl"]
            if not _is_empty(extra.get("vinLink")):
                out["vinLink"] = extra["vinLink"]
        # Same idea for the VDP link itself: if the base's `link` points at an
        # aggregator (autotrader.com etc.) and another source has a real
        # dealer VDP, swap both `link` and `resolvedLink` in. Without this the
        # visualizer's "VDP" button drops the user on the Autotrader redirect
        # page instead of the actual BMW dealership listing.
        if (
            _is_bad_dealer_url(out.get("link"))
            and not _is_empty(extra.get("link"))
            and not _is_bad_dealer_url(extra.get("link"))
        ):
            out["link"] = extra["link"]
            # Only carry over `resolvedLink` if the extra's resolvedLink
            # actually corresponds to a real dealer (not an aggregator).
            if (
                not _is_empty(extra.get("resolvedLink"))
                and not _is_bad_dealer_url(extra.get("resolvedLink"))
            ):
                out["resolvedLink"] = extra["resolvedLink"]
            else:
                # Force re-resolution downstream — base's `resolvedLink`
                # likely belonged to the old aggregator URL.
                out["resolvedLink"] = None
        # Preserve Autotrader-specific fields if any source has them
        for k in ("autotraderId", "autotraderSearchUrl"):
            if k in extra and k not in out:
                out[k] = extra[k]

    # Option packages are positive evidence: a source that saw "M Sport
    # Package" on the VDP outranks one that fetched a page without it (or
    # never fetched one at all). Union the signals rather than letting the
    # base record's empty/False value win.
    signals: list[str] = []
    for r in records:
        for sig in r.get("packageSignals") or []:
            if sig not in signals:
                signals.append(sig)
    if signals:
        out["packageSignals"] = signals
    if any(r.get("mSport") for r in records):
        out["mSport"] = True
    elif any(r.get("mSport") is False for r in records):
        out["mSport"] = False

    # Remember every source platform this record was seen on
    platforms = {r.get("platform") for r in records if r.get("platform")}
    if len(platforms) > 1:
        out["platform"] = ",".join(sorted(platforms))
    return out


def _parse_state_list(raw: list[str]) -> set[str]:
    """``--exclude-states CA,HI`` or ``--exclude-states CA --exclude-states HI``."""
    out: set[str] = set()
    for entry in raw:
        for s in entry.split(","):
            s = s.strip().upper()
            if s:
                out.add(s)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument(
        "--inputs",
        nargs="+",
        required=True,
        help="Inventory JSON files to union",
    )
    ap.add_argument(
        "--out",
        default=None,
        help="Output path (default: overwrite the first --inputs entry)",
    )
    ap.add_argument(
        "--exclude-states",
        action="append",
        default=[],
        help="Drop records whose dealerState matches (two-letter code). "
        "Comma-separated and/or repeatable. Example: --exclude-states CA,HI",
    )
    ap.add_argument(
        "--exclude-trim",
        action="append",
        default=[],
        metavar="TERM",
        help="Drop records whose trim or model contains TERM (case- and "
        "punctuation-insensitive). Comma-separated and/or repeatable. Applied "
        "to every input, so it is the one place to strip a drivetrain the "
        "aggregators can't filter out server-side "
        "(e.g. --exclude-trim xDrive for a RWD-only search).",
    )
    ap.add_argument(
        "--only-type",
        default=None,
        choices=["new", "used", "cpo"],
        help="Keep only records whose `type` field matches exactly. Needed "
        "because the aggregator crawlers (Autotrader/Cars.com/TrueCar) have no "
        "condition filter of their own — search_inventory.py's --type covers "
        "the four dealer platforms, but a merge of aggregator output has to "
        "drop non-matching records here.",
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="Print summary without writing"
    )
    args = ap.parse_args()

    drop_states = _parse_state_list(args.exclude_states)
    only_type = args.only_type.lower() if args.only_type else None
    drop_trims = {
        t.strip().lower()
        for entry in args.exclude_trim
        for t in entry.split(",")
        if t.strip()
    }

    inputs: list[pathlib.Path] = [pathlib.Path(p) for p in args.inputs]
    for p in inputs:
        if not p.exists():
            sys.exit(f"error: {p} not found")

    out_path = pathlib.Path(args.out) if args.out else inputs[0]

    buckets: dict[str, list[dict]] = {}
    stats = {
        "total_read": 0,
        "dropped_state": 0,
        "dropped_no_vin": 0,
        "dropped_trim": 0,
        "dropped_type": 0,
    }

    for path in inputs:
        data = json.loads(path.read_text())
        stats["total_read"] += len(data)
        print(f"read {len(data)} records ← {path}")
        for rec in data:
            vin = rec.get("vin")
            if not vin:
                stats["dropped_no_vin"] += 1
                continue
            if (rec.get("dealerState") or "").upper() in drop_states:
                stats["dropped_state"] += 1
                continue
            if drop_trims:
                haystack = f"{rec.get('trim') or ''} {rec.get('model') or ''}".lower()
                if any(term in haystack for term in drop_trims):
                    stats["dropped_trim"] += 1
                    continue
            if only_type and (rec.get("type") or "").lower() != only_type:
                stats["dropped_type"] += 1
                continue
            buckets.setdefault(vin, []).append(rec)

    # Merge each bucket
    by_vin: dict[str, dict] = {
        vin: (recs[0] if len(recs) == 1 else _merge_records(recs))
        for vin, recs in buckets.items()
    }
    merged_count = sum(1 for recs in buckets.values() if len(recs) > 1)

    merged_list = list(by_vin.values())
    # Sort deterministically: state, then odometer asc (nulls last), then VIN
    def _sort_key(r: dict) -> tuple:
        return (
            r.get("dealerState") or "",
            r.get("odometer") if r.get("odometer") is not None else 10**9,
            r.get("vin") or "",
        )

    merged_list.sort(key=_sort_key)

    print(
        f"\nresult: {len(merged_list)} unique VINs  "
        f"(read {stats['total_read']}, {merged_count} merged across sources, "
        f"{stats['dropped_state']} dropped-state, "
        f"{stats['dropped_trim']} dropped-trim, "
        f"{stats['dropped_type']} dropped-type, "
        f"{stats['dropped_no_vin']} dropped-no-vin)"
    )

    if args.dry_run:
        return

    out_path.write_text(json.dumps(merged_list, indent=2))
    print(f"wrote → {out_path}")


if __name__ == "__main__":
    main()
