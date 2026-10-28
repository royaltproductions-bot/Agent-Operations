import unittest

from tools.gambit_analytics import summarize_csv_sources


class GambitAnalyticsTest(unittest.TestCase):
    def test_monthly_summary(self):
        summaries = summarize_csv_sources([{"filename": "sales.csv", "text":
            "month,sales\nJanuary,10\nFebruary,20\nMarch,30\n"}])
        self.assertEqual(summaries[0]["data_rows"], 3)
        numbers = summaries[0]["columns"][0]
        self.assertEqual(numbers["sum"], "60")
        self.assertEqual(numbers["mean"], "20")
        self.assertEqual(numbers["maximum"], "30")
        self.assertEqual(numbers["highest_labels"], ["March"])

    def test_currency_and_missing_values(self):
        summaries = summarize_csv_sources([{"filename": "orders.csv", "text":
            'item,value\nAlpha,"$1,000.50"\nBeta,\nGamma,2\n'}])
        numbers = summaries[0]["columns"][0]
        self.assertEqual(numbers["sum"], "1002.50")
        self.assertEqual(numbers["numeric_rows"], 2)
        self.assertEqual(numbers["missing_rows"], 1)

    def test_mixed_column_never_silently_counts_as_zero(self):
        self.assertEqual(summarize_csv_sources([{"filename": "mix.csv", "text":
            "id,value\nA,10\nB,unknown\n"}]), [])

    def test_does_not_run_formulas(self):
        self.assertEqual(summarize_csv_sources([{"filename": "table.csv", "text":
            "name,count\nA,=2+2\n"}]), [])


if __name__ == "__main__":
    unittest.main()
