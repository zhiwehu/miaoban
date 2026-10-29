import copy
import tempfile
import unittest
from voicehub.skills import Skills
from voicehub.workflows.manager import WorkflowManager
from voicehub.config import DEFAULT
class SkillTests(unittest.TestCase):
 def test_templates_bind_typed_inputs_and_freeze_run_snapshot(self):
  with tempfile.TemporaryDirectory() as root:
   manager=WorkflowManager(root,lambda:copy.deepcopy(DEFAULT));skills=Skills(root,manager)
   note=next(s for s in skills.list() if s['id']=='quick_note')
   task=skills.instantiate(note['id'],{'title':'测试','content':'金额$100'},'one')
   self.assertEqual(task['steps'][0]['arguments']['content'],'金额$100')
   with self.assertRaises(ValueError):skills.instantiate(note['id'],{'title':'测试','content':{'from':'other.content'}},'one')
   note['name']='改名';skills.save(note)
   self.assertEqual(manager.get(task['id'])['skill']['name'],'保存笔记')
 def test_invalid_templates_disabled_skills_and_unknown_tools_rejected(self):
  with tempfile.TemporaryDirectory() as root:
   manager=WorkflowManager(root,lambda:copy.deepcopy(DEFAULT));skills=Skills(root,manager);item=skills.list()[0]
   item['steps'][0]['tool']='shell.execute'
   with self.assertRaises(ValueError):skills.save(item)
   item=next(s for s in skills.list() if s['id']=='quick_note');item['enabled']=False;skills.save(item)
   with self.assertRaises(ValueError):skills.instantiate(item['id'],{'title':'a','content':'b'},'one')
