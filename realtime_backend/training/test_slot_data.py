import json
import re
import unittest
from pathlib import Path
from voicehub.slot_normalizer import FIELDS
from voicehub.tools import validate_arguments


class JointDataTests(unittest.TestCase):
    def test_joint_spans_plans_and_splits_are_consistent(self):
        root=Path(__file__).resolve().parents[1]/'data/training/router-slots-v2'
        seen=set();families=set()
        for split in ('train','validation','test'):
            rows=[json.loads(line) for line in (root/(split+'.jsonl')).read_text().splitlines()]
            texts={re.sub(r'[\W_]+','',row['text']).lower() for row in rows}
            current={row['family'] for row in rows}
            self.assertFalse(seen&texts);self.assertFalse(families&current)
            self.assertEqual(len(texts),len(rows))
            seen|=texts;families|=current
            for row in rows:
                if row.get('classification_only'):continue
                for span in row['slots']:
                    a,b=span['offset']
                    self.assertEqual(row['text'][a:b],span['span'])
                    self.assertIn(span['type'],FIELDS[row['label']])
                if row['expected']['status']=='ready':
                    validate_arguments(row['expected']['tool'],row['expected']['arguments'])
