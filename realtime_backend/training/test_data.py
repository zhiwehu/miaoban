import importlib.util
import json
import unittest
from pathlib import Path


class SplitTests(unittest.TestCase):
    def test_generated_sets_are_disjoint_and_regression_not_in_training(self):
        root=Path(__file__).resolve().parents[1]
        data=root/'data/training/router-v1'
        spec=importlib.util.spec_from_file_location('build_router_training_data',root/'scripts/build_router_training_data.py')
        import sys
        sys.path.insert(0,str(root/'scripts'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        sets={split:[json.loads(line) for line in (data/(split+'.jsonl')).read_text().splitlines()]
              for split in ('train','validation','test')}
        texts={name:{module.normalize(row['text']) for row in rows} for name,rows in sets.items()}
        for a,b in [('train','validation'),('train','test'),('validation','test')]:
            self.assertFalse(texts[a]&texts[b])
            self.assertFalse({r['family'] for r in sets[a]} & {r['family'] for r in sets[b]})
        self.assertFalse(texts['train'] & {module.normalize(text) for text,_ in module.CASES})
        templates=[module.normalize(template) for split in module.FAMILIES.values()
                   for rows in split.values() for template in rows]
        self.assertEqual(len(templates),len(set(templates)))
