import unittest
from unittest.mock import Mock,patch
from training.everyday_eval import evaluate
class EvalModeTests(unittest.TestCase):
 def test_validation_disables_dropout_before_prediction(self):
  model=Mock()
  def predict(*args):
   model.eval.assert_called_once();return 'null'
  with patch('training.everyday_eval.predict',side_effect=predict):
   result=evaluate(model,None,[{'text':'你好','label':'null'}])
  self.assertEqual(result['accuracy'],1)
