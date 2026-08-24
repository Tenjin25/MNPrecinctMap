#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


LOWERCASE_WORDS = {"and", "de", "del", "for", "la", "of", "the", "van", "von", "with"}
SUFFIXES = {
    "JR": "Jr.",
    "SR": "Sr.",
    "II": "II",
    "III": "III",
    "IV": "IV",
    "V": "V",
}


def title_word(raw: str, word_index: int) -> str:
    prefix_match = re.match(r"^([^A-Za-z]*)(.*?)([^A-Za-z.]*)$", raw)
    if not prefix_match:
        return raw
    prefix, core, suffix = prefix_match.groups()
    if not core:
        return raw

    core_upper = core.upper().rstrip(".")
    if re.fullmatch(r"(?:[A-Z]\.){2,}", core.upper()):
        titled = core.upper()
    elif core_upper in SUFFIXES:
        titled = SUFFIXES[core_upper]
    elif len(core_upper) == 1:
        titled = core_upper + ("." if core.endswith(".") else "")
    else:
        apostrophe_parts = core.lower().split("'")

        def title_apostrophe_part(part: str) -> str:
            if not part:
                return part
            if part.startswith("mc") and len(part) > 2:
                return "Mc" + part[2].upper() + part[3:]
            return part[0].upper() + part[1:]

        titled = "'".join(title_apostrophe_part(part) for part in apostrophe_parts)
        if word_index > 0 and core.lower() in LOWERCASE_WORDS:
            titled = core.lower()

    return f"{prefix}{titled}{suffix}"


def normalize_candidate_name(value: str) -> str:
    if not value or not any(char.isalpha() for char in value):
        return value
    value = re.sub(r"\b(?:[A-Za-z]\.){2,}", lambda match: match.group(0).upper(), value)
    if value != value.upper():
        return value

    if re.fullmatch(r"WRITE[- ]IN\**", value.strip(), flags=re.IGNORECASE):
        stars = "*" * (len(value) - len(value.rstrip("*")))
        return f"Write-in{stars}"

    parts = re.split(r"(\s+|-)", value)
    word_index = 0
    normalized: list[str] = []
    for part in parts:
        if not part or part.isspace() or part == "-":
            normalized.append(part)
            continue
        normalized.append(title_word(part, word_index))
        word_index += 1
    return "".join(normalized)


def normalize_node(node: object) -> int:
    changes = 0
    if isinstance(node, dict):
        for key, value in node.items():
            if "candidate" in str(key).lower() and isinstance(value, str):
                normalized = normalize_candidate_name(value)
                if normalized != value:
                    node[key] = normalized
                    changes += 1
            else:
                changes += normalize_node(value)
    elif isinstance(node, list):
        for value in node:
            changes += normalize_node(value)
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize all-uppercase candidate fields in generated contest JSON files."
    )
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--check", action="store_true", help="Report changes without writing files.")
    args = parser.parse_args()

    files: list[Path] = []
    for path in args.paths:
        files.extend(sorted(path.glob("*.json")) if path.is_dir() else [path])

    changed_files = 0
    changed_values = 0
    for path in files:
        original_text = path.read_text(encoding="utf-8-sig")
        payload = json.loads(original_text)
        changes = normalize_node(payload)
        if not changes:
            continue
        changed_files += 1
        changed_values += changes
        print(f"{path}: {changes} candidate value(s)")
        if not args.check:
            if "\n" in original_text.strip():
                rendered = json.dumps(payload, indent=2)
            else:
                rendered = json.dumps(payload, separators=(",", ":"))
            if original_text.endswith("\n"):
                rendered += "\n"
            path.write_text(rendered, encoding="utf-8")

    print(f"{'Would update' if args.check else 'Updated'} {changed_values} values in {changed_files} files.")


if __name__ == "__main__":
    main()
