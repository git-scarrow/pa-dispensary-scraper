import unittest

import dispo_watch


class IHeartJaneTerpParserTests(unittest.TestCase):
    def test_parses_colon_separated_three_decimal_values(self) -> None:
        text = (
            "Caryophyllene: 0.418% | Humulene: 0.193% | Limonene : 0.983% | "
            "Linalool : 0.148% | Myrcene : 0.148% | Terpinolene : 0.008% | "
            "Pinene : 0.152% | b-Pinene : 0.12%"
        )
        parsed = dispo_watch._extract_iheartjane_terps_from_description(text)

        self.assertEqual(parsed["terp_text_parsed"], 1)
        self.assertAlmostEqual(parsed["terp_caryophyllene"], 0.418, places=3)
        self.assertAlmostEqual(parsed["terp_humulene"], 0.193, places=3)
        self.assertAlmostEqual(parsed["terp_limonene"], 0.983, places=3)
        self.assertAlmostEqual(parsed["terp_linalool"], 0.148, places=3)
        self.assertAlmostEqual(parsed["terp_myrcene"], 0.148, places=3)
        self.assertAlmostEqual(parsed["terp_terpinolene"], 0.008, places=3)
        # Direct pinene field wins; beta-pinene is not double-counted by the parser.
        self.assertAlmostEqual(parsed["terp_pinene"], 0.152, places=3)
        self.assertAlmostEqual(parsed["terp_total"], 2.05, places=2)

    def test_parses_total_terpenes_when_explicit(self) -> None:
        text = "Top profile: Myrcene 0.48%, Limonene 0.31%, Total Terpenes 2.4%."
        parsed = dispo_watch._extract_iheartjane_terps_from_description(text)

        self.assertEqual(parsed["terp_text_parsed"], 1)
        self.assertAlmostEqual(parsed["terp_myrcene"], 0.48, places=2)
        self.assertAlmostEqual(parsed["terp_limonene"], 0.31, places=2)
        self.assertAlmostEqual(parsed["terp_total"], 2.4, places=2)

    def test_does_not_false_positive_on_name_only_marketing_text(self) -> None:
        text = (
            "Top terpenes Limonene & Linalool swirl to produce aromas of rosemary, "
            "tangerine, and pine with smooth relaxing effects."
        )
        parsed = dispo_watch._extract_iheartjane_terps_from_description(text)

        self.assertEqual(parsed["terp_names_present"], 1)
        self.assertEqual(parsed["terp_text_parsed"], 0)
        self.assertIsNone(parsed["terp_limonene"])
        self.assertIsNone(parsed["terp_linalool"])
        self.assertIsNone(parsed["terp_total"])


if __name__ == "__main__":
    unittest.main()
