import importlib.util
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "cantamus_optimize.py"
SPEC = importlib.util.spec_from_file_location("cantamus_prepare", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def part(part_id: str, name: str, hidden_rest_case: str = "") -> str:
    if hidden_rest_case == "extra":
        notes = """
          <note><pitch><step>C</step><octave>4</octave></pitch><duration>8</duration><voice>1</voice><type>half</type></note>
          <note print-object="no"><rest/><duration>1</duration><voice>1</voice><type>16th</type></note>
        """
    elif hidden_rest_case == "required":
        notes = """
          <note><pitch><step>C</step><octave>4</octave></pitch><duration>7</duration><voice>1</voice><type>half</type></note>
          <note print-object="no"><rest/><duration>1</duration><voice>1</voice><type>16th</type></note>
        """
    else:
        notes = "".join(
            "<note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration>"
            "<voice>1</voice><type>quarter</type></note>"
            for _ in range(4)
        )
    swing = (
        '<direction><direction-type><words>con swing</words></direction-type></direction>'
        if part_id == "P1" and not hidden_rest_case
        else ""
    )
    clef = (
        "<clef><sign>G</sign><line>2</line><clef-octave-change>-1</clef-octave-change></clef>"
        if name.casefold() == "tenor"
        else "<clef><sign>G</sign><line>2</line></clef>"
    )
    return f"""
      <part id="{part_id}"><measure number="1">
        <attributes><divisions>4</divisions><time><beats>{'2' if hidden_rest_case else '4'}</beats><beat-type>4</beat-type></time>{clef}</attributes>
        {swing}{notes}
      </measure></part>
    """


def score(parts: list[tuple[str, str, str]]) -> ET.Element:
    part_list = "".join(
        f'<score-part id="{part_id}"><part-name>{name}</part-name>'
        f'<score-instrument id="{part_id}-I1"><instrument-name>Voice</instrument-name>'
        f'<instrument-sound>keyboard.piano</instrument-sound></score-instrument></score-part>'
        for part_id, name, _ in parts
    )
    bodies = "".join(part(part_id, name, case) for part_id, name, case in parts)
    return ET.fromstring(f"<score-partwise><part-list>{part_list}</part-list>{bodies}</score-partwise>")


class PreparationRulesTest(unittest.TestCase):
    def test_swing_mark_in_one_part_converts_all_parts_and_sets_compound_tempo(self):
        root = score([("P1", "soprano", ""), ("P2", "tenor", "")])
        self.assertEqual(MODULE.convert_marked_swing(root), 1)
        self.assertEqual(
            [(time.findtext("beats"), time.findtext("beat-type")) for time in root.findall(".//time")],
            [("12", "8"), ("12", "8")],
        )
        self.assertTrue(MODULE.ensure_initial_tempo(root, 100))
        metronome = root.find(".//metronome")
        self.assertIsNotNone(metronome.find("beat-unit-dot"))
        self.assertEqual(metronome.findtext("per-minute"), "100")
        self.assertEqual(root.find(".//sound").get("tempo"), "150.0")
        for score_part in root.findall("part"):
            self.assertEqual(sum(map(int, [x.text for x in score_part.findall(".//duration")])), 24)

    def test_swing_can_be_confirmed_when_exporter_omits_the_words(self):
        root = score([("P2", "tenor", "")])
        self.assertFalse(root.findall(".//words"))
        self.assertEqual(MODULE.convert_marked_swing(root, force=True), 1)
        self.assertEqual(root.findtext(".//time/beats"), "12")
        self.assertEqual(root.findtext(".//time/beat-type"), "8")

    def test_hidden_rest_is_removed_only_when_it_is_provably_extra(self):
        root = score([("P1", "soprano", "extra"), ("P2", "alto", "required")])
        self.assertEqual(MODULE.repair_hidden_rests(root), (1, 1))
        self.assertEqual(len(root.findall("part[1]//rest")), 0)
        required = root.find("part[2]//note[rest]")
        self.assertIsNotNone(required)
        self.assertIsNone(required.get("print-object"))

    def test_vocal_identity_fix_preserves_display_name_and_pitch(self):
        root = score([("P1", "tenor", ""), ("P2", "bajo", "")])
        before = [node.text for node in root.findall(".//pitch/octave")]
        self.assertGreaterEqual(MODULE.normalize_vocal_parts(root), 1)
        self.assertEqual([node.text for node in root.findall(".//pitch/octave")], before)
        names = root.findall("part-list/score-part/part-name")
        self.assertEqual([name.text for name in names], ["tenor", "Bass"])
        self.assertIsNone(names[0].get("print-object"))
        self.assertIsNone(names[1].get("print-object"))
        self.assertEqual(
            [node.text for node in root.findall(".//instrument-sound")],
            ["keyboard.piano", "voice.bass"],
        )

    def test_tenor_pitch_is_raised_when_g8_would_put_it_below_bass(self):
        root = score([("P1", "tenor", ""), ("P2", "bajo", "")])
        before = [int(node.text) for node in root.findall("part[1]//pitch/octave")]
        repaired = MODULE.repair_tenor_octave(root)
        after = [int(node.text) for node in root.findall("part[1]//pitch/octave")]
        self.assertEqual(repaired, len(before))
        self.assertEqual(after, [value + 1 for value in before])


if __name__ == "__main__":
    unittest.main()
