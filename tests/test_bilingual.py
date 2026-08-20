import csv
import importlib.util
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "cantamus_optimize.py"
SPEC = importlib.util.spec_from_file_location("cantamus_optimize", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def score() -> ET.Element:
    return ET.fromstring(
        """
        <score-partwise>
          <part-list><score-part id="P1"><part-name>soprano</part-name></score-part></part-list>
          <part id="P1">
            <measure number="2">
              <note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration>
                <voice>1</voice><lyric number="1"><syllabic>single</syllabic><text>we</text></lyric>
              </note>
              <note><pitch><step>D</step><octave>4</octave></pitch><duration>1</duration>
                <voice>1</voice><lyric number="1"><syllabic>single</syllabic><text>casa</text></lyric>
              </note>
            </measure>
          </part>
        </score-partwise>
        """
    )


class BilingualWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.config = {
            "primary_language": "es",
            "secondary_language": "en",
            "replacements": {"we": "ui"},
            "passages": [{"part": "soprano", "measure_start": 2, "measure_end": 2}],
        }

    def test_preview_is_unconfirmed_and_keeps_unresolved_primary_text(self):
        root = score()
        rows = MODULE.phonetic_candidates(root, self.config)
        self.assertEqual([row["original"] for row in rows], ["we", "casa"])
        self.assertEqual(rows[0]["phonetic"], "ui")
        self.assertEqual(rows[0]["confirmed"], "no")
        self.assertEqual(rows[1]["status"], "unresolved")
        self.assertEqual(root.findtext(".//lyric/text"), "we")

    def test_only_confirmed_exact_text_is_replaced(self):
        root = score()
        rows = MODULE.phonetic_candidates(root, self.config)
        rows[0]["confirmed"] = "yes"
        with tempfile.TemporaryDirectory() as directory:
            preview = Path(directory) / "preview.csv"
            MODULE.write_phonetic_preview(preview, rows)
            self.assertEqual(MODULE.apply_confirmed_phonetics(root, preview), 1)
        self.assertEqual(root.findtext(".//lyric/text"), "ui")
        self.assertEqual(root.findall(".//lyric/text")[1].text, "casa")

    def test_expected_text_mismatch_stops_without_replacement(self):
        root = score()
        rows = MODULE.phonetic_candidates(root, self.config)
        rows[0]["confirmed"] = "yes"
        rows[0]["original"] = "you"
        with tempfile.TemporaryDirectory() as directory:
            preview = Path(directory) / "preview.csv"
            MODULE.write_phonetic_preview(preview, rows)
            with self.assertRaisesRegex(ValueError, "expected 'you'"):
                MODULE.apply_confirmed_phonetics(root, preview)
        self.assertEqual(root.findtext(".//lyric/text"), "we")


if __name__ == "__main__":
    unittest.main()
