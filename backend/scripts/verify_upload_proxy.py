"""Verify upload limits against the deployed Nginx image in a disposable container."""
from pathlib import Path
import subprocess,tempfile,http.client,time,uuid
root=Path(__file__).resolve().parents[2]
name=f'fund-engine-upload-proxy-audit-{uuid.uuid4().hex[:12]}'
image=next(line.split()[1] for line in (root/'deploy/api-proxy/Dockerfile').read_text().splitlines() if line.startswith('FROM nginx'))
def docker(*args):return subprocess.check_output(['docker',*args],text=True).strip()
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp)/'default.conf'
 config=(root/'deploy/api-proxy/nginx.conf.template').read_text().replace('${RESEARCH_BEARER_TOKEN}','test-only').replace('http://api:8000','http://127.0.0.1:8081')
 p.write_text(config+'\nserver { listen 8081; client_max_body_size 32m; access_log /tmp/upstream.log; location / { return 204; } }\n')
 docker('run','--detach','--rm','--name',name,'-p','127.0.0.1::8080','-v',f'{p}:/etc/nginx/conf.d/default.conf:ro',image)
 try:
  port=int(docker('port',name,'8080/tcp').split(':')[-1])
  for _ in range(50):
   try:
    c=http.client.HTTPConnection('127.0.0.1',port,timeout=5);c.request('GET','/health');r=c.getresponse();r.read();c.close()
    if r.status==200:break
   except OSError:time.sleep(.1)
  for path in ('/', '/index.html', '/research', '/assets/app.js'):
   c=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
   c.request('GET',path);r=c.getresponse();r.read();c.close()
   assert r.status==404, f'API proxy served a retired page: {path}'
  def request(size,header_only=False,chunked=False):
   c=http.client.HTTPConnection('127.0.0.1',port,timeout=10)
   try:
    if header_only:
     c.putrequest('POST','/api/upload');c.putheader('Content-Length',str(size));c.endheaders()
    else:
     chunks=(b'x'*min(65536,size-i) for i in range(0,size,65536))
     c.request('POST','/api/upload',body=chunks,headers={} if chunked else {'Content-Length':str(size)},encode_chunked=chunked)
    r=c.getresponse();r.read();return r.status
   finally:c.close()
  assert request(2*1024*1024)==204,'2MiB upload was rejected by proxy'
  assert request(20*1024*1024+65536)==204,'20MiB plus multipart overhead was rejected'
  assert request(21*1024*1024+1,header_only=True)==413,'oversized Content-Length was not rejected before body'
  assert request(21*1024*1024+1,chunked=True)==413,'oversized chunked body was not rejected'
  log=docker('exec',name,'cat','/tmp/upstream.log')
  assert log.count('POST /api/upload')==2,'oversized request reached upstream'
  print('PASS: 2MiB and 20MiB plus overhead forwarded; oversized declared and chunked bodies rejected; only valid requests reached upstream')
 finally:docker('stop',name)
