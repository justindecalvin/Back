#!/usr/bin/env python3
"""Build the single Stanbax first/second-test bulk import file.

Tytlt.csv is the only authority for registration number, display name, and class.
The score sources may use a different name spelling, CSV/JSON layouts, and may
contain a third test. The output intentionally contains only the first two tests.
"""
from __future__ import annotations

import csv
import difflib
import json
import re
import unicodedata
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ROSTER = ROOT / "Tytlt.csv"
OUTPUT = ROOT / "stanbax_tests_bulk_import.csv"
REPORT = ROOT / "stanbax_tests_bulk_import_report.txt"

CSV_SOURCES = [
    "Jss1.csv", "Jss3.csv", "Kg1.csv", "Kg2.csv", "Pry1.csv",
    "Ss2_science.csv", "Ss2art.csv",
]
JSON_SOURCES = ["Pry.json", "jss2_scores.json", "ss1_scores.json", "ss3_scores.json"]

# Corrections that cannot be safely inferred from spelling alone.
NAME_ALIASES = {
    "Oladiipo Darasimi Esther": "Oladipo Darasimi",
    "Yekinni Zainab Ojuolape": "Yekinni Zainab",
    "Yekinni Omotoyosi Oreduwa": "Yekinni Omotoyosi",
    "Adeniyi Moses Ipepemi": "Adeniyi Moses",
    "Alibawo Jamiloju": "Ajibowo Jomiloju Ruth",
    "Abayomi Tamara": "Tamara Abayomi",
}

# Subject names used in the JSON sources are shorthand for the same subjects
# represented in the tabular sources/site. The output uses stable display names.
SUBJECT_ALIASES = {
    "english": "English Language",
    "english studies": "English Language",
    "english language": "English Language",
    "basic science": "Basic Science",
    "b.s.": "Basic Science",
    "bse": "Basic Science",
    "social citizenship": "Social & Citizenship Studies",
    "social studies": "Social Studies",
    "intermediate science": "Intermediate Science",
    "digital tech": "Digital Technology",
    "d/tech": "Digital Technology",
    "computer": "Computer Studies",
    "crs": "Cristian Religious studies",
    "c.r.s": "Cristian Religious studies",
    "civic": "Civic",
    "cca": "Cultural and Creative Arts",
    "phe": "Physical & Health Education",
    "pvs": "Prevocational Studies",
    "pre-vocational": "Prevocational Studies",
    "livestock": "Livestock Farming",
    "l.s.f": "Livestock Farming",
    "c.h.s": "Citizenship and Heritage Studies",
    "government": "Government",
    "literature": "Literature",
}


def fold(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def token_sort(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(sorted(re.findall(r"[a-z0-9]+", text)))


def canonical_subject(raw: str) -> str:
    value = re.sub(r"\s+", " ", str(raw or "").strip())
    return SUBJECT_ALIASES.get(value.lower(), value)


def number(value: object) -> str:
    """Return a clean score string, preserving a real zero and blanks."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"/\s*\d+(?:\.\d+)?$", "", text).strip()
    try:
        val = float(text)
    except ValueError:
        return ""
    if not 0 <= val <= 100:
        raise ValueError(f"score outside 0..100: {value!r}")
    return str(int(val)) if val.is_integer() else str(val)


def complete_pair(first: object, second: object) -> tuple[str, str] | None:
    a, b = number(first), number(second)
    if not a and not b:
        return None
    # A single supplied test gets the requested default second score.
    if a and not b:
        b = "8"
    elif b and not a:
        a, b = "8", b
    return a, b


def load_roster() -> tuple[list[dict[str, str]], dict[str, dict[str, str]]]:
    with ROSTER.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    roster = []
    for row in rows[6:]:
        if len(row) >= 3 and row[1].strip() and row[2].strip():
            roster.append({"grade": row[0].strip(), "name": row[1].strip(), "reg": row[2].strip()})
    by_name = {fold(r["name"]): r for r in roster}
    return roster, by_name


def match_student(source_name: str, source_reg: str, roster: list[dict[str, str]], by_name: dict[str, dict[str, str]]) -> tuple[dict[str, str], str]:
    if source_reg:
        by_reg = next((r for r in roster if fold(r["reg"]) == fold(source_reg)), None)
        if by_reg:
            return by_reg, "registration number"
    alias = NAME_ALIASES.get(source_name, source_name)
    exact = by_name.get(fold(alias))
    if exact:
        return exact, "exact/alias name"
    query = token_sort(alias)
    ranked = sorted(
        ((difflib.SequenceMatcher(None, query, token_sort(r["name"])).ratio(), r) for r in roster),
        key=lambda item: item[0], reverse=True,
    )
    score, candidate = ranked[0]
    if score < 0.68:
        raise ValueError(f"could not confidently match {source_name!r}; best={candidate['name']!r} ({score:.2f})")
    return candidate, f"fuzzy name ({score:.2f})"


def add_record(records: OrderedDict, match: dict[str, str], subject: str, first: object, second: object, source: str, audit: list[str]) -> None:
    pair = complete_pair(first, second)
    if not pair:
        return
    subject = canonical_subject(subject)
    key = (match["reg"].lower(), subject.lower())
    if key in records:
        old = records[key]
        # Prefer the first non-empty source pair; identical duplicates are harmless.
        if (old["ca1"], old["ca2"]) != pair:
            audit.append(f"CONFLICT {match['reg']} {subject}: kept {old['ca1']},{old['ca2']} from {old['source']}; ignored {pair[0]},{pair[1]} from {source}")
        return
    records[key] = {
        "registration_no": match["reg"], "full_name": match["name"], "grade_level": match["grade"],
        "term": "1st Term", "session": "2026/2027", "subject": subject,
        "ca1": pair[0], "ca2": pair[1], "source": source,
    }


def parse_wide_csv(path: Path, roster, by_name, records, audit) -> None:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        source_name, source_reg = row.get("full_name", "").strip(), row.get("registration_no", "").strip()
        match, how = match_student(source_name, source_reg, roster, by_name)
        audit.append(f"MATCH {path.name}: {source_name} -> {match['name']} / {match['reg']} ({how})")
        headers = list(row)
        i = 3
        while i < len(headers):
            first_header = headers[i]
            if "(1st Test)" not in first_header:
                i += 1
                continue
            subject = first_header.replace("(1st Test)", "").strip()
            second_header = headers[i + 1] if i + 1 < len(headers) and "(2nd Test)" in headers[i + 1] else None
            add_record(records, match, subject, row.get(first_header), row.get(second_header, "") if second_header else "", path.name, audit)
            i += 2 if second_header else 1


def parse_json(path: Path, roster, by_name, records, audit) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    for student in data.get("students", []):
        source_name = str(student.get("name", "")).strip()
        match, how = match_student(source_name, "", roster, by_name)
        audit.append(f"MATCH {path.name}: {source_name} -> {match['name']} / {match['reg']} ({how})")
        for subject, scores in (student.get("scores") or {}).items():
            # Explicitly ignore 3rd_test and any later values.
            add_record(records, match, subject, scores.get("1st_test"), scores.get("2nd_test"), path.name, audit)


def main() -> None:
    roster, by_name = load_roster()
    records: OrderedDict[tuple[str, str], dict[str, str]] = OrderedDict()
    audit: list[str] = []
    for filename in CSV_SOURCES:
        parse_wide_csv(ROOT / filename, roster, by_name, records, audit)
    for filename in JSON_SOURCES:
        parse_json(ROOT / filename, roster, by_name, records, audit)

    fields = ["registration_no", "full_name", "grade_level", "term", "session", "subject", "ca1", "ca2"]
    with OUTPUT.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in sorted(records.values(), key=lambda x: (x["grade_level"].lower(), x["full_name"].lower(), x["subject"].lower())):
            writer.writerow({k: row[k] for k in fields})

    matches = [x for x in audit if x.startswith("MATCH ")]
    conflicts = [x for x in audit if x.startswith("CONFLICT ")]
    report = [
        "Stanbax first/second-test bulk import report",
        f"Authoritative roster: {ROSTER.name} ({len(roster)} students)",
        f"Source students matched: {len(matches)} rows across {len(CSV_SOURCES) + len(JSON_SOURCES)} files",
        f"Output records: {len(records)} student-subject rows",
        "Rules: Tytlt.csv supplies registration_no/full_name/grade_level; only 1st_test and 2nd_test are used; a missing member of a pair is set to 8; 3rd_test is ignored.",
        f"Conflicts ignored after first source wins: {len(conflicts)}",
        "",
        "Match audit:",
        *matches,
        "",
        "Conflict audit:",
        *(conflicts or ["None"]),
    ]
    REPORT.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT.name}: {len(records)} rows")
    print(f"Wrote {REPORT.name}")


if __name__ == "__main__":
    main()
