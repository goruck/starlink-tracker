import io
import json
import unittest
import zipfile
from docx import Document
import tracker as t
import reporting
from unittest.mock import patch
import assessment

class ReportTests(unittest.TestCase):
 def data(self):return assessment.assess({'mission':{'name':'Starship Flight 14'},'generated':'2026-09-29T12:00:00+00:00','satellites':[],'sources':[],'collection':None})
 def test_word_file_contains_assessment_and_limits(self):
  data=self.data();body=reporting.build_report(data)
  self.assertTrue(zipfile.is_zipfile(io.BytesIO(body)))
  doc=Document(io.BytesIO(body));text='\n'.join(p.text for p in doc.paragraphs)
  self.assertIn(data['assessment_summary']['conclusion'],text)
  self.assertIn('Service activation remains unconfirmed',text)
 def test_download_headers_and_word_payload(self):
  class Socket:
   def __init__(self):self.input=io.BytesIO(b'GET /api/report.docx HTTP/1.1\r\nHost: localhost\r\n\r\n');self.output=b''
   def makefile(self,*args,**kwargs):return self.input
   def sendall(self,data):self.output+=data
  sock=Socket()
  with patch.object(t,'snapshot',return_value=self.data()):t.Handler(sock,('127.0.0.1',12),None)
  headers,body=sock.output.split(b'\r\n\r\n',1)
  self.assertIn(b'200 OK',headers);self.assertIn(b'Content-Disposition: attachment;',headers)
  self.assertIn(b'application/vnd.openxmlformats-officedocument.wordprocessingml.document',headers)
  self.assertTrue(zipfile.is_zipfile(io.BytesIO(body)))
