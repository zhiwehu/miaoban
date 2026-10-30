"""Real TLS SMTP plus the separate stdio MCP; all mail stays on loopback."""
import asyncio
from email import policy
from email.parser import BytesParser
import io
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from tests.test_workflow_manager import PLAN
from voicehub.config import DEFAULT
from voicehub.workflows.manager import WorkflowManager
from types import SimpleNamespace

class SmtpTests(unittest.IsolatedAsyncioTestCase):
 async def test_stdio_mcp_tls_confirmation_attachment_and_single_submission(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);cert=root/'cert.pem';key=root/'key.pem'
   subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',str(key),'-out',str(cert),'-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key);received=[]
   async def smtp(reader,writer):
    def reply(value):writer.write(value+b'\r\n')
    reply(b'220 localhost fixture')
    try:
     while line:=await reader.readline():
      command=line.upper()
      if command.startswith(b'EHLO'):reply(b'250-localhost');reply(b'250 SIZE 10000000')
      elif command.startswith((b'MAIL FROM:',b'RCPT TO:')):reply(b'250 OK')
      elif command.startswith(b'DATA'):
       reply(b'354 Send message');await writer.drain();body=b''
       while (line:=await reader.readline())!=b'.\r\n':
        if not line:return
        body+=line
       received.append(body);reply(b'250 Queued locally')
      elif command.startswith(b'QUIT'):reply(b'221 Bye');break
      else:reply(b'500 Unsupported')
      await writer.drain()
    finally:writer.close();await writer.wait_closed()
   server=await asyncio.start_server(smtp,'127.0.0.1',0,ssl=context)
   manager=WorkflowManager(root,lambda:DEFAULT);manager.settings.save({'smtp_enabled':True,'host':'localhost','port':server.sockets[0].getsockname()[1],'from_address':'sender@example.com'})
   plan={'steps':[dict(step) for step in PLAN['steps']]};plan['steps'][0]={**plan['steps'][0],'arguments':{'title':'本地测试','format':'docx','content':'仅本地测试内容'}}
   task=manager.create('本地测试','fixture');manager.set_plan(task['id'],plan);pipeline=SimpleNamespace(session_id='fixture')
   try:
    with patch.dict(os.environ,{'SSL_CERT_FILE':str(cert)}):
     [e async for e in manager.run(task['id'],pipeline)];self.assertFalse(received);self.assertEqual(manager.get(task['id'])['status'],'waiting_confirmation')
     manager.approve(task['id'],'fixture');[e async for e in manager.run(task['id'],pipeline)]
     self.assertEqual(manager.get(task['id'])['status'],'completed');self.assertEqual(len(received),1)
     [e async for e in manager.run(task['id'],pipeline)];self.assertEqual(len(received),1)
    message=BytesParser(policy=policy.default).parsebytes(received[0]);self.assertEqual(message['To'],'recipient@example.com')
    attachment=next(message.iter_attachments());self.assertEqual(attachment.get_filename(),'本地测试.docx')
    with zipfile.ZipFile(io.BytesIO(attachment.get_payload(decode=True))) as archive:self.assertIn('仅本地测试内容',archive.read('word/document.xml').decode())
   finally:await manager.close();server.close();await server.wait_closed()
