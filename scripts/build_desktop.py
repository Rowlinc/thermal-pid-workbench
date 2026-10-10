"""Build a Windows one-file app using an explicit public resource allowlist."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--name',default='PIDWorkbench')
    parser.add_argument('--output-dir',default='dist/desktop',help='Separate output directory, useful when an older app is still open')
    parser.add_argument('--clean',action='store_true',help='Refresh PyInstaller dependency caches')
    args=parser.parse_args()
    output=(ROOT/args.output_dir).resolve()
    work=ROOT/'artifacts'/'desktop-build'
    output.mkdir(parents=True,exist_ok=True);work.mkdir(parents=True,exist_ok=True)
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--onefile','--windowed',
             '--name',args.name,'--distpath',str(output),'--workpath',str(work),
             '--specpath',str(work),'--paths',str(ROOT),'--noupx']
    if args.clean:
        command.append('--clean')
    for package in ('thermal_pid','core','sim','hw','llm','reference','serial'):
        command+=['--collect-submodules',package]
    command+=['--collect-all','webview','--collect-all','pythonnet',
              '--collect-all','clr_loader','--collect-data','core','--collect-data','sim',
              '--hidden-import','openai','--hidden-import','anthropic','--hidden-import','requests',
              '--hidden-import','simulator','--hidden-import','tuner','--hidden-import','launcher',
              '--hidden-import','doctor','--hidden-import','pid_project']
    resources=[('thermal_pid/web','thermal_pid/web'),('reference/SOURCE.json','reference'),
               ('firmware.cpp','.'),('config.example.json','.'),('LICENSE','.'),('NOTICE','.'),
               ('docs/zh-CN/MATLAB_GUIDE.md','docs/zh-CN'),('docs/en-US/MATLAB_GUIDE.md','docs/en-US'),
               ('font/JetBrainsMonoNerdFont-Regular.ttf','font')]
    for source,destination in resources:
        command+=['--add-data',str(ROOT/source)+':'+destination]
    for package in ('PyQt5','PyQt6','PySide2','PySide6','tkinter','playwright','pytest','IPython','matplotlib'):
        command+=['--exclude-module',package]
    command+=[str(ROOT/'desktop_app.py')]
    subprocess.run(command,cwd=ROOT,check=True)
    executable=output/(args.name+'.exe' if sys.platform=='win32' else args.name)
    checksum=hashlib.sha256(executable.read_bytes()).hexdigest()
    manifest=dict(version='0.4.3',file=executable.name,sha256=checksum,size_bytes=executable.stat().st_size,
                  private_configuration_bundled=False,local_results_bundled=False)
    (output/'build-info.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    guide='双击 PIDWorkbench.exe。\n首次可选择温度、压力、流量或液位示例，然后修改模型与任务。\n结果直接保存在应用运行历史中。\n本机数据：%LOCALAPPDATA%\\ThermalPIDWorkbench\n需要 Windows Edge WebView2 Runtime；Simulink 另需 MATLAB 和兼容 Engine。\nAPI 密钥通过界面填写，安装包不含任何用户密钥。\n完整说明见 DESKTOP.md。\n'
    (output/'开始使用.txt').write_text(guide,encoding='utf-8-sig')
    archive=output/'PIDWorkbench-0.4.3-Windows-x64.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as handle:
        for path in (executable,output/'build-info.json',output/'开始使用.txt',ROOT/'LICENSE',ROOT/'NOTICE',ROOT/'docs'/'DESKTOP.md'):
            handle.write(path,path.name)
    print(f'Application: {executable}\nPortable package: {archive}\nSHA256: {checksum}')

if __name__=='__main__':main()
