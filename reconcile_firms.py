#!/usr/bin/env python3
"""Reconcile an external firm list against the live monitor registry.

Short marketing names and absorbed firms are handled by explicit aliases in the
source YAML. That makes mergers reviewable and prevents silent fuzzy matching or
duplicate polling.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

from fetchers import SUPPORTED_ATS_TYPES

HERE = Path(__file__).resolve().parent
DEFAULT_SOURCE = HERE / "sources" / "yue_combined_vault_am_law.yaml"

_SUFFIXES = re.compile(
    r"\b(?:llp|pllc|pc|pa|plc|l\.?l\.?p\.?|p\.?c\.?|p\.?a\.?)\b",
    re.IGNORECASE,
)


def canonical(name: str) -> str:
    name = name.replace("&amp;", "&").replace("+", " and ")
    name = name.replace("&", " and ")
    name = _SUFFIXES.sub(" ", name)
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def reconcile(registry_path: Path, source_path: Path) -> dict:
    registry_data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    source_data = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {}
    registry_names = [row["name"] for row in registry_data.get("firms", [])]
    registry_rows = list(registry_data.get("firms", []))
    source_names = list(source_data.get("firms", []))
    aliases = dict(source_data.get("aliases", {}))

    registry_by_key = {canonical(name): name for name in registry_names}
    missing: list[str] = []
    resolved: dict[str, str] = {}
    alias_errors: list[str] = []
    for name in source_names:
        target = aliases.get(name, name)
        registry_name = registry_by_key.get(canonical(target))
        if registry_name is None:
            missing.append(name)
            if name in aliases:
                alias_errors.append(f"{name} -> {target}")
        else:
            resolved[name] = registry_name

    duplicates = sorted(
        {
            name
            for name in source_names
            if sum(canonical(other) == canonical(name) for other in source_names) > 1
        }
    )
    source_targets = {canonical(aliases.get(name, name)) for name in source_names}
    registry_only = [
        name for name in registry_names if canonical(name) not in source_targets
    ]
    ats_polled = sum(
        row.get("ats_type", "unknown").lower() in SUPPORTED_ATS_TYPES
        for row in registry_rows
    )
    entry_page_firms = sum(bool(row.get("entry_pages")) for row in registry_rows)
    effectively_polled = sum(
        row.get("ats_type", "unknown").lower() in SUPPORTED_ATS_TYPES
        or bool(row.get("entry_pages"))
        for row in registry_rows
    )
    return {
        "registry_count": len(registry_names),
        "source_count": len(source_names),
        "resolved": resolved,
        "missing": missing,
        "alias_errors": alias_errors,
        "duplicates": duplicates,
        "registry_only": registry_only,
        "ats_polled": ats_polled,
        "entry_page_firms": entry_page_firms,
        "effectively_polled": effectively_polled,
    }


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=HERE / "firms.yaml")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit nonzero for unresolved source names, broken aliases, or duplicates.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    result = reconcile(args.registry, args.source)
    print(
        f"Source: {result['source_count']} names | registry: "
        f"{result['registry_count']} names | resolved: {len(result['resolved'])}"
    )
    print(
        f"Coverage: {result['ats_polled']} ATS-polled + "
        f"{result['entry_page_firms']} entry-page firms = "
        f"{result['effectively_polled']} firms with an active source"
    )
    if result["missing"]:
        print("\nUnresolved source names:")
        for name in result["missing"]:
            print(f"  - {name}")
    if result["alias_errors"]:
        print("\nAliases whose targets are absent from the registry:")
        for alias in result["alias_errors"]:
            print(f"  - {alias}")
    if result["duplicates"]:
        print("\nDuplicate source names:")
        for name in result["duplicates"]:
            print(f"  - {name}")
    print(f"\nRegistry-only firms retained: {len(result['registry_only'])}")

    invalid = bool(
        result["missing"] or result["alias_errors"] or result["duplicates"]
    )
    return 1 if args.check and invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
