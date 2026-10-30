from pathlib import Path
import sys
import unittest

scripts=str(Path(__file__).resolve().parents[1]/'scripts')
sys.path.insert(0,scripts)
from router_stress_cases import cases
from evaluate_router_stress import error_kind
from voicehub.tools import validate_arguments


class StressEvaluationTests(unittest.TestCase):
    def test_cases_are_unique_and_ready_gold_parameters_are_valid(self):
        rows=cases()
        self.assertGreater(len(rows),800)
        self.assertEqual(len(rows),len({r['id'] for r in rows}))
        self.assertGreater(len({r['family'] for r in rows}),100)
        for row in rows:
            self.assertTrue(row['evaluation_only'])
            if row['expected']['status']=='ready':
                validate_arguments(row['expected']['tool'],row['expected']['arguments'])

    def test_dangerous_calls_and_annoying_clarifications_are_not_conflated(self):
        expected={'status':'chat','tool':None,'arguments':{}}
        self.assertEqual(error_kind(expected,{'status':'ready','tool':'weather','arguments':{'city':'上海'}}),'false_call')
        self.assertEqual(error_kind(expected,{'status':'clarify','tool':'weather','arguments':{}}),'false_route_or_clarification')
        self.assertIsNone(error_kind(expected,expected))
