import io
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from voicehub.workflows.artifacts import Artifacts
class ArtifactTests(unittest.TestCase):
 def test_formats_are_real_files_and_references_are_scoped(self):
  with tempfile.TemporaryDirectory() as root:
   store=Artifacts(root)
   for format in ('txt','md','csv','docx'):
    result=store.create('会议纪要',format,'名称,数量\n文档,2','task-one')
    meta,raw=store.read(result['artifact_id'],'task-one');self.assertTrue(raw)
    with self.assertRaises(ValueError):store.read(result['artifact_id'],'another-task')
    if format=='docx':
     with zipfile.ZipFile(io.BytesIO(raw)) as archive:
      ET.fromstring(archive.read('word/document.xml'));self.assertIn('_rels/.rels',archive.namelist())
   with self.assertRaises(ValueError):store.read('../outside')
 def test_attachment_changes_and_model_paths_are_refused(self):
  with tempfile.TemporaryDirectory() as root:
   store=Artifacts(root)
   with self.assertRaises(ValueError):store.create('../outside','txt','body','task')
   result=store.create('safe','txt','body','task');(store.root/(result['artifact_id']+'.txt')).write_text('changed')
   with self.assertRaises(ValueError):store.read(result['artifact_id'])
 def test_csv_formulas_are_escaped(self):
  with tempfile.TemporaryDirectory() as root:
   store=Artifacts(root);result=store.create('safe','csv','=1+1,2\n@cmd,3','task')
   _,raw=store.read(result['artifact_id']);self.assertIn("'=1+1",raw.decode('utf-8-sig'))
