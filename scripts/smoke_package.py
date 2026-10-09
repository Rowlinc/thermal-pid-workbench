"""Check the built executable in an isolated workspace, then its native renderer."""
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.request import Request, urlopen
import uuid

ROOT=Path(__file__).resolve().parents[1]

def main():
    exe=ROOT/'dist/desktop/PIDWorkbench.exe'
    folder=ROOT/'artifacts'/('packaged-test-'+uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    address=folder/'address.txt'
    proc=subprocess.Popen([str(exe),'--serve','--workspace',str(folder/'workspace'),'--port-file',str(address)],
                          cwd=folder,creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline=time.monotonic()+40
        while not address.is_file() and time.monotonic()<deadline:
            if proc.poll() is not None:raise RuntimeError('Packaged app exited before serving')
            time.sleep(.1)
        url=address.read_text(encoding='utf-8')
        with urlopen(url,timeout=10) as reply:doc=reply.read().decode()
        token=json.loads(re.search(r'window\.__SESSION__\s*=\s*("[^"]+")',doc).group(1))
        def request(path,body=None):
            raw=json.dumps(body).encode() if body is not None else None
            with urlopen(Request(url+path,data=raw,headers={'X-Session-Token':token}),timeout=10) as response:
                return json.loads(response.read())
        state=request('/api/state')
        assert len(state['fields'])>=170 and not state['key_configured']
        cfg=json.loads((ROOT/'examples/pressure.json').read_text(encoding='utf-8'))
        cfg['llm']['credentials_file']='config.json';cfg['output']['directory']='results'
        saved=request('/api/settings',dict(project=cfg,legacy=state['legacy']))
        job=request('/api/start',dict(project=saved['project'],legacy=saved['legacy']))
        deadline=time.monotonic()+30
        while job['status']=='running' and time.monotonic()<deadline:
            time.sleep(.1);job=request('/api/job/'+job['id'])
        assert job['status']=='completed',job['error']
        assert job['final_pid'] and job['process']['unit']=='MPa'
        assert request('/api/history')[0]['id']==job['id']
        with urlopen(Request(url+'/result/'+job['id']+'/pid.json',headers={'X-Session-Token':token}),timeout=5) as response:
            assert json.loads(response.read())['process']['unit']=='MPa'
        assert request('/api/diagnostics',{})
        print('Packaged server PASS: fields, pressure run, PID, persistent history, diagnostics')
    finally:
        # All jobs have finished; terminate only this isolated test server.
        if proc.poll() is None:
            # One-file PyInstaller uses a parent bootloader and a child app.
            # Terminate the whole owned test tree so no child holds the EXE open.
            subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True,check=True)
        proc.wait(timeout=10)
    diagnostic=folder/'native.json'
    subprocess.run([str(exe),'--workspace',str(folder/'native-workspace'),'--self-test',str(diagnostic)],
                   cwd=folder,check=True,timeout=40,creationflags=subprocess.CREATE_NO_WINDOW)
    value=json.loads(diagnostic.read_text(encoding='utf-8'))
    assert value['ready'] and value['fields']>=170,value
    print('Packaged native WebView2 PASS: '+json.dumps(value,ensure_ascii=False))
    (folder/'validation.json').write_text(json.dumps(dict(packaged_server='pass',native=value),ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
