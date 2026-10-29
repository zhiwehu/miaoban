import json
from pathlib import Path
import unittest
from voicehub.tools import validate_arguments

class V3DataTests(unittest.TestCase):
    def test_all_splits_cover_all_tools_and_exclude_frozen_regression(self):
        root=Path(__file__).resolve().parents[1]
        import re
        norm=lambda text:re.sub(r'[\W_]+','',text).lower()
        forbidden={norm(json.loads(s)['text']) for s in (root.parent/'diagnostics/2026-10-29/router-stress-v1/cases.jsonl').read_text().splitlines()}
        seen=set();families=set()
        for split in ('train','validation','test'):
            rows=[json.loads(s) for s in (root/'data/training/router-slots-v3'/f'{split}.jsonl').read_text().splitlines()]
            texts={norm(r['text']) for r in rows};groups={r['family'] for r in rows}
            self.assertEqual({r['label'] for r in rows},{'null','weather','news','system_info'})
            self.assertEqual(len(rows),len(texts));self.assertFalse(texts&seen);self.assertFalse(texts&forbidden)
            self.assertFalse(groups&families);seen|=texts;families|=groups
            for row in rows:
                for span in row.get('slots',[]):
                    a,b=span['offset'];self.assertEqual(row['text'][a:b],span['span'])
                if 'expected' in row and row['expected']['status']=='ready':
                    validate_arguments(row['expected']['tool'],row['expected']['arguments'])
