"""Loopback-only desktop HTTP UI. Session protected; no general filesystem endpoint."""
import hmac
import json
import mimetypes
import os
from pathlib import Path
import secrets
import threading
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote, parse_qs

from .desktop_state import DesktopState
from .desktop_jobs import JobManager
from .config_comments import parse_commented_json
from .config import DEFAULTS, _merge, validate
from .process import public_config

WEB = Path(__file__).with_name('web')
RESOURCES = {'firmware.cpp','config.example.json','docs/zh-CN/MATLAB_GUIDE.md'}
RESULT_FILES = {'report.html', 'summary.json', 'pid.json', 'metrics.csv',
                'original_zn.csv', 'corrected_zn.csv', 'legacy_route.csv','corrected_legacy_route.csv', 'zn_pi_route.csv',
                'simc_route.csv', 'user_route.csv', 'selected.csv', 'temperature_c.svg',
                'output.svg', 'device_audit.json', 'legacy_result.json', 'events.json', 'samples.csv'}

class DesktopServer:
    def __init__(self, workspace, port=0, native_bridge=False):
        self.state = DesktopState(workspace)
        self.manager = JobManager(self.state)
        self.token = secrets.token_urlsafe(32)
        self.native_bridge = native_bridge
        app = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def trusted(self, mutation=False, require_session=False):
                host = self.headers.get('Host', '')
                if host != f'127.0.0.1:{app.http.server_port}':
                    raise PermissionError('invalid local host')
                origin = self.headers.get('Origin')
                if origin and origin != app.url:
                    raise PermissionError('foreign origin')
                if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                    raise PermissionError('cross-site request')
                if mutation:
                    value = self.headers.get('X-Session-Token','')
                else:
                    value = self.headers.get('X-Session-Token','')
                    if not value:
                        cookies = self.headers.get('Cookie','').split(';')
                        value = next((c.strip().partition('=')[2] for c in cookies if c.strip().startswith('pid_session=')), '')
                if (mutation or require_session) and not hmac.compare_digest(value,app.token):
                    raise PermissionError('invalid desktop session')

            def send(self, data, mime='application/json; charset=utf-8', status=200, cookie=False):
                if isinstance(data, (dict,list)):
                    data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
                elif isinstance(data,str): data=data.encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type',mime)
                self.send_header('Content-Length',str(len(data)))
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Referrer-Policy','same-origin')
                # pywebview's trusted native bridge uses eval/new Function.
                # Browser-only mode keeps that capability disabled.
                evaluation=" 'unsafe-eval'" if app.native_bridge else ''
                self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'"+evaluation+"; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'self'")
                if cookie:self.send_header('Set-Cookie',f'pid_session={app.token}; HttpOnly; SameSite=Strict; Path=/')
                self.end_headers();self.wfile.write(data)

            def error(self, exc):
                if isinstance(exc,(BrokenPipeError,ConnectionAbortedError,ConnectionResetError)):
                    return
                status=403 if isinstance(exc,PermissionError) else 404 if isinstance(exc,FileNotFoundError) else 400
                # Never return arbitrary transport exception bodies or imported credentials.
                message = str(exc) if isinstance(exc,(ValueError,PermissionError,FileNotFoundError)) else type(exc).__name__
                try:key=app.state.api_key()
                except (OSError,ValueError):key=''
                for key in (key,):
                    if key:message=message.replace(key,'[密钥已隐藏]')
                try:self.send({'error':message},status=status)
                except (BrokenPipeError,ConnectionAbortedError,ConnectionResetError):pass

            def do_GET(self):
                try:
                    route=unquote(urlparse(self.path).path)
                    self.trusted(require_session=route.startswith(('/api/','/result/','/resource/')))
                    if route=='/':
                        doc=(WEB/'index.html').read_text(encoding='utf-8').replace('__SESSION_JSON__',json.dumps(app.token))
                        return self.send(doc,'text/html; charset=utf-8',cookie=True)
                    if route in ('/app.js','/style.css'):
                        return self.send((WEB/route[1:]).read_bytes(), 'application/javascript; charset=utf-8' if route.endswith('.js') else 'text/css; charset=utf-8')
                    if route=='/api/state':return self.send(app.state.snapshot())
                    if route=='/api/history':return self.send(app.manager.history())
                    if route.startswith('/api/job/'):
                        return self.send(app.manager.get(route.rsplit('/',1)[1]).view())
                    if route.startswith('/api/job-config/'):
                        job=app.manager.get(route.rsplit('/',1)[1])
                        return self.send(dict(project=job.project,
                            legacy=json.loads((job.directory/'legacy.json').read_text(encoding='utf-8')),
                            workflow=job.workflow))
                    if route=='/api/export':
                        return self.send(public_config(app.state.project()))
                    if route=='/api/legacy-export':return self.send(app.state.legacy())
                    if route=='/api/ports':
                        from serial.tools.list_ports import comports
                        return self.send([dict(port=p.device,description=p.description) for p in comports()])
                    if route.startswith('/resource/'):
                        name=route[len('/resource/'):]
                        if name not in RESOURCES:raise PermissionError('resource is not public')
                        roots=[Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1])),Path(sys.prefix)/'share/thermal-pid-workbench']
                        target=next((root/name for root in roots if (root/name).is_file()),None)
                        if target is None:raise FileNotFoundError('资源不存在')
                        return self.send(target.read_bytes(),'text/plain; charset=utf-8')
                    if route.startswith('/result/'):
                        pieces=route.split('/')
                        if len(pieces)!=4 or pieces[3] not in RESULT_FILES:
                            raise PermissionError('file is not a public result')
                        job=app.manager.get(pieces[2])
                        if not job.output:raise FileNotFoundError('结果尚未生成')
                        target=job.output/pieces[3]
                        return self.send(target.read_bytes(),mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
                    raise FileNotFoundError('页面不存在')
                except Exception as exc:self.error(exc)

            def do_POST(self):
                try:
                    self.trusted(mutation=True)
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0 <= length <= 21*1024*1024:raise ValueError('请求过大')
                    raw=self.rfile.read(length)
                    parsed=urlparse(self.path);route=parsed.path
                    if route in ('/api/upload-csv','/api/upload-model'):
                        params=parse_qs(parsed.query)
                        name=params.get('name',[''])[0]
                        return self.send(app.state.upload_csv(name,raw) if route.endswith('csv') else
                                         app.state.upload_model(name,raw,params.get('trusted',['false'])[0]=='true'))
                    body=json.loads(raw or b'{}')
                    if route=='/api/settings':return self.send(app.state.save(body))
                    if route=='/api/diagnostics':
                        with app.manager.lock:
                            if any(j.status in ('running','stopping') for j in app.manager.jobs.values()):
                                raise ValueError('请在运行结束后执行诊断')
                            import importlib.util
                            checks=[dict(name='应用版本',status='PASS',detail='0.4.4 · Python '+sys.version.split()[0]),
                                    dict(name='项目配置',status='PASS',detail='模型、单位、任务与限制已通过配置检查'),
                                    dict(name='LLM密钥',status='PASS' if app.state.api_key() else 'WARN',detail='已配置（不显示）' if app.state.api_key() else '未配置；关闭LLM仍可仿真')]
                            for label,module in [('串口支持','serial'),('OpenAI SDK','openai'),('Anthropic SDK','anthropic')]:
                                checks.append(dict(name=label,status='PASS' if importlib.util.find_spec(module) else 'WARN',detail='已包含' if importlib.util.find_spec(module) else '未安装'))
                            from core.doctoring import _collect_matlab_checks
                            for item in _collect_matlab_checks(app.state.legacy(),tr_fn=lambda cn,en:cn,path_exists=os.path.exists):
                                checks.append(dict(name=item.name,status=item.status,detail=item.detail))
                            if body.get('probe_llm') is True:
                                from .workflow import make_tuner
                                try:
                                    client=make_tuner(app.state.project(),app.state.root).client
                                    reply=client.request_json(system_prompt='Return JSON only with p=0, i=0, d=0, status=TUNING. This is a connection check, not a control request.',user_prompt='Check the API connection.')
                                    checks.append(dict(name='LLM实际API请求',status='PASS' if isinstance(reply,dict) else 'FAIL',detail='服务返回可解析JSON' if isinstance(reply,dict) else '未获得有效回复；检查服务、模型和密钥'))
                                except Exception as exc:
                                    checks.append(dict(name='LLM实际API请求',status='FAIL',detail=type(exc).__name__))
                            return self.send(checks)
                    if route=='/api/start':return self.send(app.manager.start(body))
                    if route=='/api/control':return self.send(app.manager.control(body['id'],body['action']))
                    if route=='/api/profile-save':
                        app.state.save_profile(body['name'],body);return self.send(app.state.snapshot())
                    if route=='/api/profile-load':return self.send(app.state.load_profile(body['name']))
                    if route=='/api/import':
                        data=parse_commented_json(body['text'])
                        if not isinstance(data,dict):raise ValueError('配置必须是 JSON 对象')
                        if body.get('kind')=='legacy':
                            project=app.state.project()
                            for old,new in [('LLM_API_BASE_URL','base_url'),('LLM_MODEL_NAME','model'),('LLM_PROVIDER','provider'),('LLM_REQUEST_TIMEOUT','timeout_s')]:
                                if old in data:project['llm'][new]=data.pop(old)
                            key=data.pop('LLM_API_KEY',data.pop('API_KEY',''))
                            return self.send(app.state.save(dict(project=project,legacy=data,api_key=key)))
                        return self.send(app.state.save(dict(project=data)))
                    if route=='/api/open-folder':
                        folder=app.manager.get(body['id']).output if body.get('id') else app.state.root
                        if folder is None:raise ValueError('结果尚未生成')
                        if os.name=='nt':os.startfile(str(folder))
                        else:raise ValueError('此按钮仅在Windows应用中打开文件夹')
                        return self.send({'ok':True})
                    raise FileNotFoundError('接口不存在')
                except Exception as exc:self.error(exc)

        self.http=ThreadingHTTPServer(('127.0.0.1',port),Handler)
        self.http.daemon_threads=True
        self.http.timeout=1
        self.url=f'http://127.0.0.1:{self.http.server_port}'

    def start(self):
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True)
        self.thread.start()
        return self.url

    def close(self):
        self.http.shutdown();self.http.server_close()
