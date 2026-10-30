import unittest
from voicehub.workflows.plan import validate_plan,resolve_arguments

TOOLS=[{'name':'source','inputSchema':{'type':'object','properties':{},'additionalProperties':False},'outputs':{'text':'string','data':'object'}},
 {'name':'save','inputSchema':{'type':'object','properties':{'content':{'type':'string'}},'required':['content'],'additionalProperties':False},'outputs':{'artifact_id':'string'}}]
class PlanTests(unittest.TestCase):
 def test_orders_dependencies_and_resolves_only_data_references(self):
  steps=validate_plan({'steps':[{'id':'b','tool':'save','arguments':{'content':{'from':'a.text'}}},{'id':'a','tool':'source','arguments':{}}]},TOOLS)
  self.assertEqual([s['id'] for s in steps],['a','b'])
  self.assertEqual(resolve_arguments(steps[1]['arguments'],{'a':{'text':'actual result'}}),{'content':'actual result'})
 def test_unknown_tool_cycles_unknown_output_and_type_mismatch_rejected(self):
  plans=[{'steps':[{'id':'a','tool':'shell','arguments':{}}]},
   {'steps':[{'id':'a','tool':'save','arguments':{'content':{'from':'b.artifact_id'}}},{'id':'b','tool':'save','arguments':{'content':{'from':'a.artifact_id'}}}]},
   {'steps':[{'id':'a','tool':'source','arguments':{}},{'id':'b','tool':'save','arguments':{'content':{'from':'a.missing'}}}]},
   {'steps':[{'id':'a','tool':'source','arguments':{}},{'id':'b','tool':'save','arguments':{'content':{'from':'a.data'}}}]}]
  for plan in plans:
   with self.subTest(plan=plan),self.assertRaises(ValueError):validate_plan(plan,TOOLS)
 def test_model_confirmation_paths_and_extra_fields_are_not_authority(self):
  for args in ({'content':'x','confirmed':True},{'content':{'from':'a.__class__'}},{'content':{'from':'a.text','fallback':'pretend'}}):
   with self.assertRaises(ValueError):validate_plan({'steps':[{'id':'a','tool':'source','arguments':{}},{'id':'b','tool':'save','arguments':args}]},TOOLS)
 def test_unresolved_results_do_not_become_strings_or_guessed_values(self):
  with self.assertRaises(ValueError):resolve_arguments({'x':{'from':'a.text'}},{})
 def test_plan_size_limit_and_duplicate_ids(self):
  for steps in ([{'id':'a','tool':'source','arguments':{}}]*2,[{'id':f's{i}','tool':'source','arguments':{}} for i in range(9)]):
   with self.assertRaises(ValueError):validate_plan({'steps':steps},TOOLS)
