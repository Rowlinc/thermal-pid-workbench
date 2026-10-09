"""Windows desktop shell and browser fallback, with per-user persistent workspace."""
import argparse
import os
from pathlib import Path
import sys
import threading
import webbrowser
import json
import time

def default_workspace():
    return Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.local/share')))/'ThermalPIDWorkbench'

class NativeDialogs:
    def __init__(self):self._window=None
    def pick_model(self):
        import webview
        value=self._window.create_file_dialog(webview.FileDialog.OPEN,allow_multiple=False,
                                            file_types=('Simulink (*.slx;*.mdl)','All files (*.*)'))
        return value[0] if value else None
    def pick_folder(self):
        import webview
        value=self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return value[0] if value else None
    def save_file(self, name, text):
        import webview
        value=self._window.create_file_dialog(webview.FileDialog.SAVE,save_filename=Path(name).name,
                                             file_types=('All files (*.*)',))
        if not value:return None
        path=Path(value[0] if isinstance(value,(tuple,list)) else value)
        path.write_text(text,encoding='utf-8-sig' if path.suffix.lower()=='.csv' else 'utf-8')
        return str(path)

def main(argv=None):
    parser=argparse.ArgumentParser(description='PID Workbench visual desktop app')
    parser.add_argument('--workspace',type=Path,default=default_workspace())
    parser.add_argument('--browser',action='store_true',help='open in system browser')
    parser.add_argument('--serve',action='store_true',help='serve without opening a window')
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--port-file',type=Path,help='write local address for integration tests')
    parser.add_argument('--self-test',type=Path,help='test native renderer, write diagnostics and close')
    args=parser.parse_args(argv)
    args.workspace.mkdir(parents=True,exist_ok=True)
    # Windowed PyInstaller executables have no stdout; upstream logging still needs it.
    if sys.stdout is None or sys.stderr is None:
        handle=(args.workspace/'application.log').open('a',encoding='utf-8',buffering=1)
        if sys.stdout is None:sys.stdout=handle
        if sys.stderr is None:sys.stderr=handle
    from .desktop_server import DesktopServer
    server=DesktopServer(args.workspace,args.port,native_bridge=not (args.browser or args.serve))
    server.start()
    if args.port_file:args.port_file.write_text(server.url,encoding='utf-8')
    try:
        if args.serve or args.browser:
            if args.browser:webbrowser.open(server.url)
            print('PID Workbench: '+server.url,flush=True)
            while server.thread.is_alive():server.thread.join(timeout=1)
        else:
            import webview
            dialogs=NativeDialogs()
            window=webview.create_window('PID Workbench · 通用 PID 工作台',server.url,
                    js_api=dialogs,width=1440,height=960,min_size=(1000,700),confirm_close=False,
                    hidden=False)
            dialogs._window=window
            def closing():
                active=[j for j in server.manager.jobs.values() if j.status=='running' or getattr(j,'worker',None) and j.worker.is_alive()]
                if active:
                    for job in active:server.manager.control(job.id,'stop')
                    window.evaluate_js("window.appNotice && window.appNotice('已请求停止。请等待运行结束后再关闭应用，以便完成审计或恢复。', true)")
                    return False
                return True
            window.events.closing+=closing
            if args.self_test:
                def native_test():
                    deadline=time.monotonic()+20
                    diagnostic={'ready':False}
                    while time.monotonic()<deadline:
                        try:
                            diagnostic=window.evaluate_js("({ready:document.querySelectorAll('[data-field]').length>0,fields:document.querySelectorAll('[data-field]').length,badge:document.querySelector('#state-badge').textContent})")
                            if diagnostic['ready']:break
                        except Exception:pass
                        time.sleep(.1)
                    args.self_test.parent.mkdir(parents=True,exist_ok=True)
                    args.self_test.write_text(json.dumps(diagnostic,ensure_ascii=False),encoding='utf-8')
                    window.destroy()
                window.events.loaded+=native_test
            webview.start(gui='edgechromium' if os.name=='nt' else None,debug=False)
    except KeyboardInterrupt:
        for job in server.manager.jobs.values():
            if job.status=='running':server.manager.control(job.id,'stop')
        # Finish outstanding deployment recovery before exiting a browser server.
        while any(j.status=='running' for j in server.manager.jobs.values()):
            threading.Event().wait(0.2)
    finally:server.close()
    return 0

if __name__=='__main__':raise SystemExit(main())
