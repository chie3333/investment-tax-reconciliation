import json
import unittest
from decimal import Decimal
from pathlib import Path

from validation.fvoci_reference import calculate


class FvociAnswers(unittest.TestCase):
    def test_hand_calculated_answers_with_independent_decimal_oracle(self):
        examples = json.loads((Path(__file__).parents[1] / 'fixtures' / 'fvoci_cases.json').read_text())
        for case in examples['cases']:
            if len(case['state']['assets']) != 1:
                continue  # Mixed pool expectations are explicit hand calculations.
            for policy in ('TRANSFER', 'RETAIN'):
                for checkpoint in case['checks']:
                    with self.subTest(case=case['id'], policy=policy, as_of=checkpoint['asOf']):
                        actual = calculate(case['state'], checkpoint['asOf'], policy)
                        expected = checkpoint['positions'][0]
                        for key in ('qty', 'book', 'tax', 'reserve', 'activeOCI', 'oci'):
                            value = expected['retainedOCI'] if key == 'oci' and policy == 'RETAIN' else expected[key]
                            self.assertEqual(actual[key], Decimal(str(value)))
