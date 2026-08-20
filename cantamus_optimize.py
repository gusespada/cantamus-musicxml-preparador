#!/usr/bin/env python3
"""Audit and safely optimize a MusicXML score for Cantamus.

The optimizer focuses on Cantamus' documented constraints:
- unroll simple repeats and first/second endings;
- turn simultaneous lyric verses into one explicit lyric line per pass;
- remove duplicate lyric nodes produced by some exporters;
- preserve accepted underscore elisions;
- report unsupported or ambiguous constructs instead of inventing music/text.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


CANTAMUS_GUIDE = (
    "https://voicemod.notion.site/"
    "Score-preparation-guidelines-for-Cantamus-9c73c966a82c443bb4064f498b1a4e37"
)
CANTAMUS_MANUAL = (
    "https://voicemod.notion.site/"
    "Cant-mus-Manual-7872a77102834187beedb57314689683"
)
DOCTYPE = (
    '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.0 Partwise//EN" '
    '"http://www.musicxml.org/dtds/partwise.dtd">'
)
STANDARD_PART_NAMES = {
    "soprano",
    "sop",
    "sopranos",
    "alto",
    "altos",
    "contralto",
    "tenor",
    "tenores",
    "bass",
    "bajo",
    "bajos",
    "baritone",
    "barítono",
}
BEAT_UNIT_QUARTERS = {
    "whole": 4.0,
    "half": 2.0,
    "quarter": 1.0,
    "eighth": 0.5,
    "16th": 0.25,
}


@dataclass(frozen=True)
class PlaybackMeasure:
    source_index: int
    repeat_pass: int


@dataclass
class TransformStats:
    duplicate_lyrics_removed: int = 0
    alternate_lyrics_removed: int = 0
    stanza_prefixes_removed: int = 0
    repeat_markers_removed: int = 0
    phonetic_candidates: int = 0
    phonetic_converted: int = 0
    phonetic_unresolved: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare a MusicXML file for Cantamus and write an audit report."
    )
    parser.add_argument("input", type=Path, help="Source .musicxml or .xml file")
    parser.add_argument("output", type=Path, help="Optimized uncompressed MusicXML")
    parser.add_argument(
        "--report",
        type=Path,
        help="Markdown audit report (default: OUTPUT-report.md)",
    )
    parser.add_argument(
        "--bilingual-config",
        type=Path,
        help=(
            "Optional JSON file with primary_language, secondary_language, "
            "replacements and optional secondary-language passages"
        ),
    )
    parser.add_argument(
        "--phonetic-preview",
        type=Path,
        help="Write an editable CSV preview; no phonetic replacement is applied",
    )
    parser.add_argument(
        "--apply-phonetics",
        type=Path,
        help="Apply only rows marked confirmed=yes in a previously reviewed CSV",
    )
    return parser.parse_args()


def parse_tree(path: Path) -> ET.ElementTree:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    return ET.parse(path, parser=parser)


def parse_ending_numbers(value: str | None) -> set[int]:
    if not value:
        return set()
    numbers: set[int] = set()
    for token in re.split(r"\s*[,;]\s*|\s+", value.strip()):
        if token.isdigit():
            numbers.add(int(token))
    return numbers


def ending_memberships(measures: list[ET.Element]) -> list[set[int]]:
    active: set[int] = set()
    memberships: list[set[int]] = []
    for measure in measures:
        endings = measure.findall("barline/ending")
        starts = [ending for ending in endings if ending.get("type") == "start"]
        for ending in starts:
            active.update(parse_ending_numbers(ending.get("number")))
        membership = set(active)
        if not membership:
            for ending in endings:
                membership.update(parse_ending_numbers(ending.get("number")))
        memberships.append(membership)
        for ending in endings:
            if ending.get("type") in {"stop", "discontinue"}:
                numbers = parse_ending_numbers(ending.get("number"))
                active.difference_update(numbers or active)
    return memberships


def repeat_direction(measure: ET.Element, direction: str) -> ET.Element | None:
    for repeat in measure.findall("barline/repeat"):
        if repeat.get("direction") == direction:
            return repeat
    return None


def has_ending_close_for_pass(measure: ET.Element, repeat_pass: int) -> bool:
    for ending in measure.findall("barline/ending"):
        if ending.get("type") not in {"stop", "discontinue"}:
            continue
        numbers = parse_ending_numbers(ending.get("number"))
        if not numbers or repeat_pass in numbers:
            return True
    return False


def build_playback_order(measures: list[ET.Element]) -> list[PlaybackMeasure]:
    """Simulate simple sequential repeats and numbered endings."""
    memberships = ending_memberships(measures)
    output: list[PlaybackMeasure] = []
    index = 0
    repeat_start = 0
    repeat_pass = 1
    safety_steps = 0

    while index < len(measures):
        safety_steps += 1
        if safety_steps > len(measures) * 12:
            raise ValueError("Repeat structure is too complex or circular to unroll safely")

        membership = memberships[index]
        if membership and repeat_pass not in membership:
            index += 1
            continue

        measure = measures[index]
        forward = repeat_direction(measure, "forward")
        if forward is not None and not (index == repeat_start and repeat_pass > 1):
            repeat_start = index
            repeat_pass = 1

        output.append(PlaybackMeasure(index, repeat_pass))

        backward = repeat_direction(measure, "backward")
        if backward is not None:
            times = int(backward.get("times", "2"))
            if times < 2:
                times = 2
            if repeat_pass < times:
                repeat_pass += 1
                index = repeat_start
                continue
            repeat_start = index + 1
            repeat_pass = 1
            index += 1
            continue

        if repeat_pass > 1 and has_ending_close_for_pass(measure, repeat_pass):
            repeat_start = index + 1
            repeat_pass = 1
        index += 1

    return output


def lyric_verse(lyric: ET.Element) -> int:
    number = lyric.get("number", "")
    match = re.search(r"verse\s*(\d+)$", number, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)$", number)
    if match:
        return int(match.group(1))
    return 1


def lyric_signature(lyric: ET.Element) -> tuple[str, str]:
    return lyric.findtext("text", ""), lyric.findtext("elision", "")


def choose_single_lyric(
    note: ET.Element,
    desired_verse: int,
    stats: TransformStats,
) -> None:
    lyrics = note.findall("lyric")
    if not lyrics:
        return

    groups: dict[int, list[ET.Element]] = defaultdict(list)
    for lyric in lyrics:
        groups[lyric_verse(lyric)].append(lyric)

    selected_verse = desired_verse if desired_verse in groups else 1
    if selected_verse not in groups:
        selected_verse = sorted(groups)[0]
    candidates = groups[selected_verse]
    selected = candidates[0]

    signatures = {lyric_signature(candidate) for candidate in candidates}
    if len(candidates) > 1:
        if len(signatures) != 1:
            texts = ", ".join(repr(item[0]) for item in sorted(signatures))
            raise ValueError(
                "Ambiguous duplicate lyrics in one verse on a single note: " + texts
            )
        stats.duplicate_lyrics_removed += len(candidates) - 1

    verse_one = groups.get(1, [])
    if selected_verse != 1 and verse_one:
        if verse_one[0].get("default-y") is not None:
            selected.set("default-y", verse_one[0].get("default-y", ""))
    selected.set("number", "1")

    text = selected.find("text")
    if text is not None and text.text:
        prefix = re.compile(rf"^\s*{desired_verse}\s*[.)]\s*")
        cleaned = prefix.sub("", text.text)
        if cleaned != text.text:
            text.text = cleaned
            stats.stanza_prefixes_removed += 1

    for lyric in lyrics:
        if lyric is not selected:
            note.remove(lyric)
            stats.alternate_lyrics_removed += 1


def clean_navigation(measure: ET.Element, stats: TransformStats) -> None:
    for barline in list(measure.findall("barline")):
        navigation = [
            child for child in list(barline) if child.tag in {"repeat", "ending"}
        ]
        if not navigation:
            continue
        stats.repeat_markers_removed += len(navigation)
        for child in navigation:
            barline.remove(child)

        remaining = list(barline)
        if not remaining or all(child.tag == "bar-style" for child in remaining):
            measure.remove(barline)
            continue
        for child in list(barline):
            if child.tag == "bar-style" and child.text in {"heavy-light", "light-heavy"}:
                barline.remove(child)


def rewrite_parts(
    root: ET.Element,
    playback: list[PlaybackMeasure],
) -> TransformStats:
    stats = TransformStats()
    source_parts = root.findall("part")
    expected_count = len(source_parts[0].findall("measure"))
    for part in source_parts:
        measures = part.findall("measure")
        if len(measures) != expected_count:
            raise ValueError("Parts do not have aligned measure counts")
        unexpected = [
            child
            for child in list(part)
            if child.tag is not ET.Comment and child.tag != "measure"
        ]
        if unexpected:
            raise ValueError("A score part contains unexpected non-measure children")

        for child in list(part):
            part.remove(child)
        part.text = "\n  "
        part_id = part.get("id", "unknown")

        for output_number, item in enumerate(playback, start=1):
            measure = copy.deepcopy(measures[item.source_index])
            clean_navigation(measure, stats)
            for note in measure.findall("note"):
                choose_single_lyric(note, item.repeat_pass, stats)
            measure.set("number", str(output_number))

            comment = ET.Comment(
                f"============== Part: {part_id}, Measure: {output_number} =============="
            )
            comment.tail = "\n  "
            part.append(comment)
            measure.tail = "\n " if output_number == len(playback) else "\n  "
            part.append(measure)
    return stats


def part_name_map(root: ET.Element) -> dict[str, str]:
    return {
        score_part.get("id", ""): score_part.findtext("part-name", "")
        for score_part in root.findall("part-list/score-part")
    }


def displayed_lyric_text(lyric: ET.Element) -> str:
    value = ""
    for child in lyric:
        if child.tag == "text":
            value += child.text or ""
        elif child.tag == "elision":
            value += (child.text or "").strip() or "_"
    return re.sub(r"\s+", " ", value).strip()


PHONETIC_FIELDS = (
    "primary_language",
    "secondary_language",
    "part_id",
    "part",
    "measure",
    "note",
    "verse",
    "original",
    "phonetic",
    "confirmed",
    "status",
)


def load_bilingual_config(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Bilingual config must be a JSON object")
    primary = str(data.get("primary_language", "")).strip().lower()
    secondary = str(data.get("secondary_language", "")).strip().lower()
    if not primary or not secondary or primary == secondary:
        raise ValueError(
            "Bilingual config needs different primary_language and secondary_language"
        )
    replacements = data.get("replacements", {})
    passages = data.get("passages", [])
    if not isinstance(replacements, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in replacements.items()
    ):
        raise ValueError("Bilingual replacements must be a string-to-string object")
    if not isinstance(passages, list) or not all(
        isinstance(item, dict) for item in passages
    ):
        raise ValueError("Bilingual passages must be a list of objects")
    return {
        "primary_language": primary,
        "secondary_language": secondary,
        "replacements": {
            key.strip().casefold(): value.strip()
            for key, value in replacements.items()
            if key.strip() and value.strip()
        },
        "passages": passages,
    }


def passage_matches(
    passages: list[dict[str, object]],
    part_id: str,
    part_name: str,
    measure_number: str,
) -> bool:
    try:
        measure = int(measure_number)
    except ValueError:
        return False
    for passage in passages:
        wanted_part = str(passage.get("part", "*")).strip().casefold()
        if wanted_part not in {"", "*", part_id.casefold(), part_name.casefold()}:
            continue
        start = int(passage.get("measure_start", passage.get("measure", measure)))
        end = int(passage.get("measure_end", passage.get("measure", start)))
        if start <= measure <= end:
            return True
    return False


def phonetic_candidates(
    root: ET.Element,
    config: dict[str, object],
) -> list[dict[str, str]]:
    names = part_name_map(root)
    replacements = config["replacements"]
    passages = config["passages"]
    assert isinstance(replacements, dict) and isinstance(passages, list)
    rows: list[dict[str, str]] = []
    for part in root.findall("part"):
        part_id = part.get("id", "")
        part_name = names.get(part_id, part_id or "Parte")
        for measure in part.findall("measure"):
            measure_number = measure.get("number", "?")
            marked = passage_matches(passages, part_id, part_name, measure_number)
            for note_index, note in enumerate(measure.findall("note"), start=1):
                for lyric in note.findall("lyric"):
                    original = displayed_lyric_text(lyric)
                    suggested = str(replacements.get(original.casefold(), ""))
                    if not marked and not suggested:
                        continue
                    text_elements = lyric.findall("text")
                    native_elisions = lyric.findall("elision")
                    if len(text_elements) != 1 or native_elisions:
                        status = "ambiguous_structure"
                        suggested = ""
                    elif suggested:
                        status = "suggested"
                    else:
                        status = "unresolved"
                    rows.append(
                        {
                            "primary_language": str(config["primary_language"]),
                            "secondary_language": str(config["secondary_language"]),
                            "part_id": part_id,
                            "part": part_name,
                            "measure": measure_number,
                            "note": str(note_index),
                            "verse": str(lyric_verse(lyric)),
                            "original": original,
                            "phonetic": suggested,
                            "confirmed": "no",
                            "status": status,
                        }
                    )
    return rows


def write_phonetic_preview(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=PHONETIC_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def confirmed_value(value: str) -> bool:
    return value.strip().casefold() in {"yes", "y", "true", "1", "si", "sí"}


def apply_confirmed_phonetics(root: ET.Element, path: Path) -> int:
    names = part_name_map(root)
    parts = {part.get("id", ""): part for part in root.findall("part")}
    converted = 0
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        missing = set(PHONETIC_FIELDS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                "Phonetic CSV is missing columns: " + ", ".join(sorted(missing))
            )
        for line_number, row in enumerate(reader, start=2):
            if not confirmed_value(row["confirmed"]):
                continue
            part_id = row["part_id"]
            part = parts.get(part_id)
            if part is None or names.get(part_id, part_id) != row["part"]:
                raise ValueError(f"Phonetic CSV line {line_number}: part mismatch")
            measure = next(
                (
                    candidate
                    for candidate in part.findall("measure")
                    if candidate.get("number", "?") == row["measure"]
                ),
                None,
            )
            if measure is None:
                raise ValueError(f"Phonetic CSV line {line_number}: measure not found")
            notes = measure.findall("note")
            note_index = int(row["note"])
            if note_index < 1 or note_index > len(notes):
                raise ValueError(f"Phonetic CSV line {line_number}: note not found")
            lyrics = [
                lyric
                for lyric in notes[note_index - 1].findall("lyric")
                if lyric_verse(lyric) == int(row["verse"])
            ]
            if len(lyrics) != 1:
                raise ValueError(
                    f"Phonetic CSV line {line_number}: lyric is missing or ambiguous"
                )
            lyric = lyrics[0]
            current = displayed_lyric_text(lyric)
            if current != row["original"]:
                raise ValueError(
                    f"Phonetic CSV line {line_number}: found {current!r}, "
                    f"expected {row['original']!r}"
                )
            text_elements = lyric.findall("text")
            if len(text_elements) != 1 or lyric.findall("elision"):
                raise ValueError(
                    f"Phonetic CSV line {line_number}: lyric structure is ambiguous"
                )
            replacement = row["phonetic"].strip()
            if not replacement:
                raise ValueError(
                    f"Phonetic CSV line {line_number}: confirmed replacement is empty"
                )
            text_elements[0].text = replacement
            converted += 1
    return converted


def set_primary_lyric_language(root: ET.Element, language: str) -> None:
    defaults = root.find("defaults")
    if defaults is None:
        defaults = ET.Element("defaults")
        insertion = 1 if root.find("work") is not None else 0
        root.insert(insertion, defaults)
    lyric_language = defaults.find("lyric-language")
    if lyric_language is None:
        lyric_language = ET.SubElement(defaults, "lyric-language")
    lyric_language.set("{http://www.w3.org/XML/1998/namespace}lang", language)


def analyze_elisions(
    root: ET.Element,
    names: dict[str, str],
) -> dict[str, object]:
    """Report explicit elisions and cross-part syllabic inconsistencies.

    The check is deliberately conservative: it never rewrites lyrics because an
    underscore may be a valid Cantamus elision. Positions are compared in quarter
    notes so parts with different MusicXML division values still align.
    """
    entries: dict[
        tuple[str, Fraction, int, str], list[dict[str, object]]
    ] = defaultdict(list)
    malformed: list[str] = []
    elision_count = 0

    for part in root.findall("part"):
        part_name = names.get(part.get("id", ""), part.get("id", "Parte"))
        divisions = 1
        previous_was_elision: dict[int, bool] = {}

        for measure in part.findall("measure"):
            measure_number = measure.get("number", "?")
            cursor = 0
            last_note_onset = 0
            note_index = 0

            for child in measure:
                if child.tag == "attributes":
                    new_divisions = int(child.findtext("divisions", "0"))
                    if new_divisions > 0:
                        divisions = new_divisions
                    continue
                if child.tag == "backup":
                    cursor -= int(child.findtext("duration", "0"))
                    continue
                if child.tag == "forward":
                    cursor += int(child.findtext("duration", "0"))
                    continue
                if child.tag != "note":
                    continue

                note_index += 1
                is_chord = child.find("chord") is not None
                onset = last_note_onset if is_chord else cursor
                if not is_chord:
                    last_note_onset = onset

                for lyric in child.findall("lyric"):
                    verse = lyric_verse(lyric)
                    text_elements = lyric.findall("text")
                    native_elisions = lyric.findall("elision")
                    underscore_count = sum(
                        (text.text or "").count("_") for text in text_elements
                    )
                    explicit = underscore_count + len(native_elisions) > 0
                    elision_count += underscore_count + len(native_elisions)

                    reasons: list[str] = []
                    for text_element in text_elements:
                        raw_text = text_element.text or ""
                        if re.search(r"^_|_$", raw_text.strip()):
                            reasons.append("guion bajo al comienzo o al final")
                        if "__" in raw_text:
                            reasons.append("dos guiones bajos seguidos")
                        if re.search(r"\s_|_\s", raw_text):
                            reasons.append("espacio junto al guion bajo")

                    lyric_children = [
                        item for item in lyric if item.tag in {"text", "elision"}
                    ]
                    for index, item in enumerate(lyric_children):
                        previous_tag = (
                            lyric_children[index - 1].tag if index > 0 else None
                        )
                        next_tag = (
                            lyric_children[index + 1].tag
                            if index + 1 < len(lyric_children)
                            else None
                        )
                        if item.tag == "elision" and (
                            previous_tag != "text" or next_tag != "text"
                        ):
                            reasons.append("elemento de elisión sin texto a ambos lados")

                    lyric_text = displayed_lyric_text(lyric) or "(sin texto)"
                    if reasons:
                        malformed.append(
                            f"{part_name} m.{measure_number}, nota {note_index}, "
                            f"«{lyric_text}»: {', '.join(dict.fromkeys(reasons))}"
                        )

                    entry = {
                        "part": part_name,
                        "measure": measure_number,
                        "text": lyric_text,
                        "syllabic": lyric.findtext("syllabic", "single"),
                        "in_elision_context": explicit
                        or previous_was_elision.get(verse, False),
                    }
                    key = (measure_number, Fraction(onset, divisions), verse, lyric_text)
                    entries[key].append(entry)
                    previous_was_elision[verse] = explicit

                if not is_chord and child.find("grace") is None:
                    cursor += int(child.findtext("duration", "0"))

    syllabic_mismatches: list[str] = []
    for group in entries.values():
        parts = {str(entry["part"]) for entry in group}
        values = {str(entry["syllabic"]) for entry in group}
        if (
            len(parts) < 2
            or len(values) < 2
            or not any(bool(entry["in_elision_context"]) for entry in group)
        ):
            continue
        by_part: dict[str, set[str]] = defaultdict(set)
        for entry in group:
            by_part[str(entry["part"])].add(str(entry["syllabic"]))
        sample = group[0]
        details = "; ".join(
            f"{part}={'/'.join(sorted(part_values))}"
            for part, part_values in by_part.items()
        )
        syllabic_mismatches.append(
            f"m.{sample['measure']}, «{sample['text']}»: {details}"
        )

    return {
        "elision_count": elision_count,
        "malformed_elisions": malformed,
        "syllabic_mismatches": syllabic_mismatches,
    }


def measure_length(measure: ET.Element, state: dict[str, int]) -> int | None:
    attributes = measure.find("attributes")
    if attributes is not None:
        if attributes.find("divisions") is not None:
            state["divisions"] = int(attributes.findtext("divisions", "0"))
        time = attributes.find("time")
        if time is not None:
            state["beats"] = int(time.findtext("beats", "0"))
            state["beat_type"] = int(time.findtext("beat-type", "0"))
    if not all(state.get(key) for key in ("divisions", "beats", "beat_type")):
        return None
    numerator = state["divisions"] * state["beats"] * 4
    if numerator % state["beat_type"]:
        return None
    return numerator // state["beat_type"]


def note_duration_by_voice(measure: ET.Element) -> Counter[str]:
    durations: Counter[str] = Counter()
    for note in measure.findall("note"):
        if note.find("chord") is not None or note.find("grace") is not None:
            continue
        voice = note.findtext("voice", "1")
        durations[voice] += int(note.findtext("duration", "0"))
    return durations


def max_chord_size(part: ET.Element) -> int:
    maximum = 1
    current = 0
    for measure in part.findall("measure"):
        for note in measure.findall("note"):
            if note.find("rest") is not None:
                current = 0
            elif note.find("chord") is not None:
                current += 1
                maximum = max(maximum, current)
            else:
                current = 1
    return maximum


def lyric_coverage(part: ET.Element) -> tuple[int, int, int]:
    sounding = 0
    explicit_or_extended = 0
    ah_notes = 0
    extension_active = False
    for measure in part.findall("measure"):
        for note in measure.findall("note"):
            if note.find("rest") is not None or note.find("chord") is not None:
                continue
            sounding += 1
            lyrics = note.findall("lyric")
            tied_from_previous = any(
                tie.get("type") == "stop" for tie in note.findall("tie")
            )
            if lyrics:
                explicit_or_extended += 1
                extension_active = any(lyric.find("extend") is not None for lyric in lyrics)
            elif extension_active or tied_from_previous:
                explicit_or_extended += 1
            else:
                ah_notes += 1
        if all(note.find("rest") is not None for note in measure.findall("note")):
            extension_active = False
    return sounding, explicit_or_extended, ah_notes


def detect_time_changes_mid_measure(part: ET.Element) -> list[str]:
    problems: list[str] = []
    for measure in part.findall("measure"):
        cursor = 0
        for child in measure:
            if child.tag == "attributes" and child.find("time") is not None and cursor != 0:
                problems.append(measure.get("number", "?"))
            elif child.tag == "backup":
                cursor -= int(child.findtext("duration", "0"))
            elif child.tag == "forward":
                cursor += int(child.findtext("duration", "0"))
            elif child.tag == "note" and child.find("chord") is None:
                cursor += int(child.findtext("duration", "0"))
    return problems


def unsupported_tuplets(root: ET.Element) -> list[int]:
    values: list[int] = []
    for modification in root.findall(".//time-modification"):
        actual = int(modification.findtext("actual-notes", "0"))
        if actual >= 5:
            values.append(actual)
    return values


def navigation_instructions(root: ET.Element) -> list[str]:
    attributes = ("dacapo", "dalsegno", "tocoda", "fine", "segno", "coda")
    return [
        attribute
        for sound in root.findall(".//sound")
        for attribute in attributes
        if sound.get(attribute) is not None
    ]


def first_tempo(root: ET.Element) -> tuple[float, str] | None:
    for metronome in root.findall(".//metronome"):
        per_minute = metronome.findtext("per-minute")
        beat_unit = metronome.findtext("beat-unit")
        if per_minute and beat_unit in BEAT_UNIT_QUARTERS:
            bpm = float(per_minute)
            if metronome.find("beat-unit-dot") is not None:
                bpm *= 1.5
            quarter_bpm = bpm * BEAT_UNIT_QUARTERS[beat_unit]
            return quarter_bpm, f"{beat_unit}={per_minute}"
    for sound in root.findall(".//sound"):
        if sound.get("tempo"):
            return float(sound.get("tempo", "120")), f"quarter={sound.get('tempo')}"
    return None


def audit(root: ET.Element) -> dict[str, object]:
    names = part_name_map(root)
    elisions = analyze_elisions(root, names)
    parts = root.findall("part")
    part_rows: list[dict[str, object]] = []
    incomplete_voices: list[str] = []
    total_quarters = 0.0

    for part_index, part in enumerate(parts):
        state: dict[str, int] = {}
        voices: set[str] = set()
        for measure in part.findall("measure"):
            expected = measure_length(measure, state)
            by_voice = note_duration_by_voice(measure)
            voices.update(by_voice)
            if expected is not None and len(by_voice) > 1:
                for voice, duration in by_voice.items():
                    if duration != expected:
                        incomplete_voices.append(
                            f"{names.get(part.get('id', ''), part.get('id', '?'))} "
                            f"m.{measure.get('number')} voz {voice}: {duration}/{expected}"
                        )
            if part_index == 0 and expected is not None:
                total_quarters += expected / state["divisions"]

        sounding, covered, ah_notes = lyric_coverage(part)
        lyric_counts = Counter(
            lyric_verse(lyric) for lyric in part.findall(".//lyric")
        )
        multiple_lyric_notes = sum(
            1 for note in part.findall(".//note") if len(note.findall("lyric")) > 1
        )
        duplicate_same_verse = sum(
            max(0, count - 1)
            for note in part.findall(".//note")
            for count in Counter(lyric_verse(item) for item in note.findall("lyric")).values()
        )
        part_rows.append(
            {
                "id": part.get("id", ""),
                "name": names.get(part.get("id", ""), ""),
                "measures": len(part.findall("measure")),
                "voices": sorted(voices),
                "max_chord": max_chord_size(part),
                "sounding_notes": sounding,
                "covered_notes": covered,
                "ah_notes": ah_notes,
                "lyric_verses": dict(sorted(lyric_counts.items())),
                "multiple_lyric_notes": multiple_lyric_notes,
                "duplicate_same_verse": duplicate_same_verse,
            }
        )

    invalid_names = [
        name
        for name in names.values()
        if name.strip().lower() not in STANDARD_PART_NAMES
        or re.search(r"[\s/@*]", name.strip())
    ]
    tempo_without_bpm = sum(
        1
        for metronome in root.findall(".//metronome")
        if metronome.find("per-minute") is None
    )
    tempo = first_tempo(root)
    duration_seconds = None
    voice_minutes = None
    if tempo and tempo[0] > 0:
        duration_seconds = total_quarters * 60 / tempo[0]
        voice_minutes = duration_seconds / 60 * len(parts)

    language = root.find("defaults/lyric-language")
    language_value = ""
    if language is not None:
        language_value = language.get("{http://www.w3.org/XML/1998/namespace}lang", "")

    return {
        "parts": part_rows,
        "repeat_count": len(root.findall(".//repeat")),
        "ending_count": len(root.findall(".//ending")),
        "invalid_part_names": invalid_names,
        "time_changes_mid_measure": detect_time_changes_mid_measure(parts[0]) if parts else [],
        "unsupported_tuplets": unsupported_tuplets(root),
        "tempo_without_bpm": tempo_without_bpm,
        "tempo": tempo[1] if tempo else "default quarter=120",
        "language": language_value,
        "incomplete_divisi_voices": incomplete_voices,
        "duration_seconds": duration_seconds,
        "voice_minutes": voice_minutes,
        **elisions,
        "numbered_stanza_prefixes": sum(
            1
            for lyric in root.findall(".//lyric")
            if re.match(r"^\s*\d+[.)]", lyric.findtext("text", ""))
        ),
    }


def format_seconds(value: float | None) -> str:
    if value is None:
        return "no calculada"
    minutes = int(value // 60)
    seconds = int(round(value - minutes * 60))
    return f"{minutes}:{seconds:02d}"


def status(ok: bool) -> str:
    return "OK" if ok else "REVISAR"


def write_report(
    path: Path,
    input_path: Path,
    output_path: Path,
    before: dict[str, object],
    after: dict[str, object],
    playback: list[PlaybackMeasure],
    stats: TransformStats,
    bilingual_config: dict[str, object] | None = None,
) -> None:
    before_parts = before["parts"]
    after_parts = after["parts"]
    assert isinstance(before_parts, list) and isinstance(after_parts, list)
    voice_minutes = after["voice_minutes"]
    voice_minutes_text = (
        f"≈ {voice_minutes:.1f} voice-minutes"
        if isinstance(voice_minutes, (int, float))
        else "no calculado"
    )
    lines = [
        f"# Auditoría Cantamus — {input_path.name}",
        "",
        "## Resultado",
        "",
        f"- Archivo optimizado: `{output_path.name}`.",
        f"- Compases por parte: {before_parts[0]['measures']} → {after_parts[0]['measures']}.",
        f"- Orden de reproducción desplegado: {len(playback)} compases.",
        f"- Marcadores de repetición/casillas eliminados: {stats.repeat_markers_removed}.",
        f"- Letras alternativas eliminadas después de asignarlas a su pasada: {stats.alternate_lyrics_removed}.",
        f"- Letras duplicadas con el mismo identificador eliminadas: {stats.duplicate_lyrics_removed}.",
        f"- Prefijos de estrofa retirados (por ejemplo, `2.`): {stats.stanza_prefixes_removed}.",
        f"- Reemplazos fonéticos confirmados: {stats.phonetic_converted}.",
        f"- Candidatos fonéticos sin resolver o con estructura ambigua: {stats.phonetic_unresolved}.",
        "- Los guiones bajos se conservaron: Cantamus los admite como elisión.",
        "",
        "## Comprobación contra la guía",
        "",
        "| Criterio | Estado | Evidencia |",
        "|---|---|---|",
        f"| MusicXML sin comprimir | OK | Salida `.musicxml` |",
        f"| Repeticiones desplegadas | {status(after['repeat_count'] == 0 and after['ending_count'] == 0)} | "
        f"{after['repeat_count']} repeticiones, {after['ending_count']} casillas restantes |",
        f"| Una sola estrofa simultánea | {status(all(row['multiple_lyric_notes'] == 0 for row in after_parts))} | "
        f"Máximo una letra por nota |",
        f"| Sinalefas consistentes | {status(not after['malformed_elisions'] and not after['syllabic_mismatches'])} | "
        f"{after['elision_count']} elisiones explícitas; "
        f"{len(after['malformed_elisions']) + len(after['syllabic_mismatches'])} incidencias |",
        f"| Nombres de partes estándar | {status(not after['invalid_part_names'])} | "
        + ", ".join(row["name"] for row in after_parts)
        + " |",
        f"| Tempo interpretable | {status(after['tempo_without_bpm'] == 0)} | {after['tempo']} |",
        f"| Cambios de compás al inicio de compás | {status(not after['time_changes_mid_measure'])} | "
        f"{len(after['time_changes_mid_measure'])} cambios internos |",
        f"| Idioma compatible | {status(after['language'] in {'es', 'ca', 'en', 'la', 'de'})} | "
        f"`{after['language'] or 'no indicado'}` |",
        f"| Divisi máximo de dos voces | {status(all(row['max_chord'] <= 2 and len(row['voices']) <= 2 for row in after_parts))} | "
        f"Sin divisi problemático |",
        f"| Voces de divisi completas | {status(not after['incomplete_divisi_voices'])} | "
        f"{len(after['incomplete_divisi_voices'])} incidencias |",
        f"| Sin quintillos/sextillos/septillos | {status(not after['unsupported_tuplets'])} | "
        f"{after['unsupported_tuplets'] or 'ninguno'} |",
        f"| Menos de 60 voice-minutes | {status(isinstance(voice_minutes, (int, float)) and voice_minutes <= 60)} | "
        f"{voice_minutes_text}, duración {format_seconds(after['duration_seconds'])} |",
        "",
        "## Cobertura de letra por parte",
        "",
        "Cantamus sintetiza con «ah» las notas que no tienen letra, prolongación explícita "
        "ni continuidad mediante ligadura. La tabla permite decidir si esos sonidos son intencionales.",
        "",
        "| Parte | Notas cantadas | Con letra/prolongación | Sonarán «ah» |",
        "|---|---:|---:|---:|",
    ]
    for row in after_parts:
        lines.append(
            f"| {row['name']} | {row['sounding_notes']} | {row['covered_notes']} | {row['ah_notes']} |"
        )
    lines.extend(
        [
            "",
            "## Segunda letra",
            "",
            "La estrofa 2 ya no está superpuesta. En cada repetición musical, el archivo contiene "
            "una única línea de letra: primera estrofa en la primera pasada y segunda estrofa en "
            "la segunda. Cuando la segunda estrofa no contiene una sílaba alternativa —por ejemplo, "
            "en un estribillo común— se conserva la letra de la estrofa 1.",
            "",
            "## Sinalefas",
            "",
            f"{after['elision_count']} elisiones explícitas detectadas. El control conserva los "
            "guiones bajos admitidos por Cantamus y solamente informa separadores mal formados "
            "o diferencias de silabeo entre voces.",
            "",
        ]
    )
    malformed_elisions = after["malformed_elisions"]
    syllabic_mismatches = after["syllabic_mismatches"]
    assert isinstance(malformed_elisions, list)
    assert isinstance(syllabic_mismatches, list)
    if not malformed_elisions and not syllabic_mismatches:
        lines.extend(["No se detectaron inconsistencias de sinalefa.", ""])
    else:
        lines.extend(
            f"- Separador: {problem}." for problem in malformed_elisions
        )
        lines.extend(
            f"- Silabeo entre voces: {problem}." for problem in syllabic_mismatches
        )
        lines.append("")
    lines.extend(
        [
            "## Partitura bilingüe",
            "",
            (
                f"Idioma de la voz de Cantamus: `{bilingual_config['primary_language']}`; "
                f"idioma secundario: `{bilingual_config['secondary_language']}`. "
                f"Se aplicaron {stats.phonetic_converted} reemplazos confirmados y "
                f"quedaron {stats.phonetic_unresolved} candidatos sin resolver."
                if bilingual_config
                else "No se solicitó conversión fonética bilingüe."
            ),
            "",
            "La conversión fonética es aproximada y optativa. El script sólo cambia filas "
            "marcadas como confirmadas y comprueba el texto original antes de escribirlas; "
            "si la partitura ya no coincide, se detiene sin aplicar silenciosamente el caso incierto.",
            "",
            "## Uso del script",
            "",
            "```bash",
            "python3 cantamus_optimize.py entrada.musicxml salida.musicxml --report auditoria.md",
            "```",
            "",
            "## Validación pendiente del flujo completo",
            "",
            "La guía exige abrir y reproducir el resultado en MuseScore antes de subirlo. El script "
            "comprueba la estructura MusicXML; la importación y reproducción en MuseScore deben "
            "registrarse después de ejecutar esta auditoría.",
            "",
            "## Fuentes",
            "",
            f"- [Guía de preparación de partituras para Cantamus]({CANTAMUS_GUIDE})",
            f"- [Manual de Cantamus]({CANTAMUS_MANUAL})",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_musicxml(tree: ET.ElementTree, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree, space=" ")
    with destination.open("wb") as output:
        output.write(b'<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n')
        output.write(DOCTYPE.encode("utf-8") + b"\n")
        tree.write(output, encoding="utf-8", xml_declaration=False)
        output.write(b"\n")


def main() -> None:
    args = parse_args()
    if (args.phonetic_preview or args.apply_phonetics) and not args.bilingual_config:
        raise ValueError(
            "--phonetic-preview and --apply-phonetics require --bilingual-config"
        )
    bilingual_config = (
        load_bilingual_config(args.bilingual_config)
        if args.bilingual_config
        else None
    )
    report_path = args.report or args.output.with_name(args.output.stem + "-report.md")
    tree = parse_tree(args.input)
    root = tree.getroot()
    parts = root.findall("part")
    if not parts:
        raise ValueError("No score parts found")

    before = audit(root)
    navigation = navigation_instructions(root)
    if navigation:
        raise ValueError(
            "Found D.C., D.S., Coda, Segno or Fine navigation; unfold that "
            "route manually before using this script"
        )
    reference_measures = parts[0].findall("measure")
    playback = build_playback_order(reference_measures)
    max_verse = max(
        (lyric_verse(lyric) for lyric in root.findall(".//lyric")),
        default=1,
    )
    max_pass = max((item.repeat_pass for item in playback), default=1)
    if max_verse > max_pass:
        raise ValueError(
            f"Found {max_verse} lyric verses but only {max_pass} repeat passes; "
            "cannot place every verse safely"
        )

    stats = rewrite_parts(root, playback)
    if bilingual_config:
        rows = phonetic_candidates(root, bilingual_config)
        stats.phonetic_candidates = len(rows)
        stats.phonetic_unresolved = sum(
            row["status"] != "suggested" for row in rows
        )
        set_primary_lyric_language(
            root, str(bilingual_config["primary_language"])
        )
        if args.phonetic_preview:
            write_phonetic_preview(args.phonetic_preview, rows)
        if args.apply_phonetics:
            stats.phonetic_converted = apply_confirmed_phonetics(
                root, args.apply_phonetics
            )
    encoding = root.find("identification/encoding")
    if encoding is not None:
        software = ET.SubElement(encoding, "software")
        software.text = "cantamus_optimize.py"

    after = audit(root)
    if after["repeat_count"] or after["ending_count"]:
        raise ValueError("Repeat navigation remains after optimization")
    if any(row["multiple_lyric_notes"] for row in after["parts"]):
        raise ValueError("Multiple simultaneous lyrics remain after optimization")

    write_musicxml(tree, args.output)
    write_report(
        report_path,
        args.input,
        args.output,
        before,
        after,
        playback,
        stats,
        bilingual_config,
    )
    print(
        f"parts={len(parts)} measures_before={len(reference_measures)} "
        f"measures_after={len(playback)} repeats_after={after['repeat_count']} "
        f"multi_lyric_notes_after={sum(row['multiple_lyric_notes'] for row in after['parts'])} "
        f"elisions={after['elision_count']} "
        f"elision_issues={len(after['malformed_elisions']) + len(after['syllabic_mismatches'])} "
        f"phonetic_candidates={stats.phonetic_candidates} "
        f"phonetic_converted={stats.phonetic_converted} "
        f"phonetic_unresolved={stats.phonetic_unresolved}"
    )
    print(f"output={args.output}")
    print(f"report={report_path}")


if __name__ == "__main__":
    main()
