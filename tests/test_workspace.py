from copy import deepcopy
import json
from pathlib import Path
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from thermal_pid.config import DEFAULTS
from thermal_pid.desktop_state import DesktopState
from thermal_pid.desktop_jobs import JobManager
from thermal_pid.desktop_server import DesktopServer
from thermal_pid.workspace import prepare_workspace, selected_workspace


def test_copy_history_key_and_internal_paths_without_deleting_source(tmp_path,monkeypatch):
    monkeypatch.delenv('LLM_API_KEY',raising=False)
    source=DesktopState(tmp_path/'old')
    cfg=deepcopy(DEFAULTS)
    history=source.root/'uploads'/'history.csv'
    history.write_text('time,u,pv\n0,0,30\n',encoding='utf-8')
    cfg['history']['file']=str(history)
    cfg['model']['custom_factory']=str(source.root/'uploads'/'model.py')+':create_model'
    cfg['llm']['credentials_file']=str(source.root/'private-key.json')
    source.save(dict(project=cfg,api_key='local-test-placeholder'))
    (source.root/'uploads'/'model.py').write_text('# fixture',encoding='utf-8')
    (source.root/'external.json').write_text('not json',encoding='utf-8')
    manager=JobManager(source)
    view=manager.start(dict(project=cfg))
    manager.jobs[view['id']].worker.join(10)
    assert manager.jobs[view['id']].status=='completed'
    before=(source.root/'jobs'/view['id']/'job.json').read_bytes()
    destination=tmp_path/'new'
    pointer=tmp_path/'location.json'
    new=prepare_workspace(source,str(destination),True,pointer)
    assert new.api_key()=='local-test-placeholder'
    assert new.project()['history']['file']==str(destination/'uploads'/'history.csv')
    assert new.project()['model']['custom_factory']==str(destination/'uploads'/'model.py')+':create_model'
    assert (destination/'external.json').read_text()=='not json'
    restored=JobManager(new)
    assert restored.history()[0]['id']==view['id']
    assert restored.get(view['id']).output.is_relative_to(destination)
    assert restored.get(view['id']).view()['final_pid']==manager.get(view['id']).view()['final_pid']
    assert selected_workspace(source.root,pointer)==destination
    assert (source.root/'jobs'/view['id']/'job.json').read_bytes()==before
    assert history.exists() and source.api_key()=='local-test-placeholder'


def test_empty_switch_and_reopen_existing_workspace(tmp_path):
    source=DesktopState(tmp_path/'old')
    source.save(dict(project=deepcopy(DEFAULTS),api_key='local-test-placeholder'))
    new=prepare_workspace(source,str(tmp_path/'empty'),False)
    assert not JobManager(new).history() and not new.api_key()
    restored=prepare_workspace(new,str(source.root),False)
    assert restored.api_key()=='local-test-placeholder'


@pytest.mark.parametrize('destination',['relative/path','',None])
def test_invalid_path_rejected(tmp_path,destination):
    with pytest.raises(ValueError):prepare_workspace(DesktopState(tmp_path/'old'),destination,True)


def test_copy_refuses_nested_root_and_occupied_destinations(tmp_path):
    source=DesktopState(tmp_path/'old')
    occupied=tmp_path/'occupied';occupied.mkdir()
    original=occupied/'keep.txt';original.write_text('keep')
    for destination in [source.root/'nested',tmp_path,Path(tmp_path.anchor),occupied]:
        with pytest.raises(ValueError):prepare_workspace(source,str(destination),True)
    assert original.read_text()=='keep'
    assert prepare_workspace(source,str(source.root),True) is source


def test_copy_error_keeps_old_location_and_records(tmp_path,monkeypatch):
    source=DesktopState(tmp_path/'old')
    pointer=tmp_path/'location.json'
    DesktopState._write(pointer,dict(workspace=str(source.root)))
    def fail(*args,**kwargs):raise OSError('fixture copy error')
    monkeypatch.setattr('thermal_pid.workspace.shutil.copytree',fail)
    with pytest.raises(ValueError,match='旧目录'):
        prepare_workspace(source,str(tmp_path/'new'),True,pointer)
    assert selected_workspace(tmp_path/'default',pointer)==source.root
    assert source.project_file.exists()


def test_live_storage_switch_and_running_task_rejection(tmp_path):
    pointer=tmp_path/'location.json'
    server=DesktopServer(tmp_path/'old',workspace_preferences=pointer)
    server.start()
    def api(path,body=None):
        req=Request(server.url+path,data=json.dumps(body).encode() if body is not None else None,
            headers={'X-Session-Token':server.token,'Content-Type':'application/json'})
        with urlopen(req,timeout=5) as response:return json.load(response)
    try:
        source=server.state.root
        cfg=server.state.project()
        job=server.manager.start(dict(project=cfg))
        worker=server.manager.jobs[job['id']]
        worker.worker.join(10)
        # A stopping device job must also block storage changes.
        worker.status='stopping'
        with pytest.raises(HTTPError) as error:
            api('/api/workspace',dict(directory=str(tmp_path/'new'),copy_existing=True))
        assert error.value.code==400 and server.state.root==source
        worker.status='completed';worker.persist()
        changed=api('/api/workspace',dict(directory=str(tmp_path/'new'),copy_existing=True))
        assert changed['state']['workspace']==str(tmp_path/'new')
        assert changed['old_data_deleted'] is False
        assert api('/api/history')[0]['id']==job['id']
        second=api('/api/start',dict(project=cfg))
        server.manager.jobs[second['id']].worker.join(10)
        assert server.manager.get(second['id']).output.is_relative_to(tmp_path/'new')
        assert source.exists()
    finally:server.close()


@pytest.mark.parametrize('explicit',[False,True])
def test_startup_remembers_location_and_cli_override_wins(tmp_path,monkeypatch,explicit):
    from types import SimpleNamespace
    from thermal_pid import desktop
    old=tmp_path/'default'
    new=tmp_path/'selected'
    pointer=old.parent/'ThermalPIDWorkbench.settings.json'
    DesktopState._write(pointer,dict(workspace=str(new)))
    observed=[]
    class Server:
        def __init__(self,path,*args,**kwargs):
            observed.append(Path(path))
            self.url='http://127.0.0.1:12345'
            self.thread=SimpleNamespace(is_alive=lambda:False)
        def start(self):pass
        def close(self):pass
    monkeypatch.setattr(desktop,'default_workspace',lambda:old)
    monkeypatch.setattr('thermal_pid.desktop_server.DesktopServer',Server)
    args=['--serve']+(['--workspace',str(old)] if explicit else [])
    assert desktop.main(args)==0
    assert observed==[old if explicit else new]
