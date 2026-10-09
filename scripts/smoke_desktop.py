"""Exercise actual UI with installed Edge, without any API or physical device."""
import json
from pathlib import Path
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from thermal_pid.desktop_server import DesktopServer
from playwright.sync_api import sync_playwright, expect

def main():
    folder=ROOT/'artifacts'/'desktop-ui-test'
    folder.mkdir(parents=True,exist_ok=True)
    app=DesktopServer(folder/('workspace_'+uuid.uuid4().hex[:8]));app.start()
    errors=[];runs=[]
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(channel='msedge',headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1)
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(app.url)
            page.locator('#state-badge').filter(has_text='测试模式').wait_for()
            for kind in ('temperature','pressure','flow','level'):
                page.locator('button[data-page="setup"]').click()
                page.locator(f'button[data-preset="{kind}"]').click()
                page.locator('#run').click()
                page.locator('#state-badge').filter(has_text='已完成').wait_for(timeout=30000)
                assert page.locator('#metrics tbody tr').count()>0
                assert page.locator('#curve-svg').count()==1
                row=app.manager.history()[0]
                view=app.manager.get(row['id']).view()
                runs.append(dict(kind=kind,id=row['id'],selected=view['selected_method'],metrics=view['metrics']))
                if kind=='pressure':page.screenshot(path=str(folder/'pressure-results.png'),full_page=True)
            page.locator('button[data-page="history"]').click()
            page.locator('button[data-history]').first.wait_for()
            assert page.locator('button[data-history]').count()>=4
            page.locator('button[data-history="'+runs[0]['id']+'"]').click()
            page.locator('#job-info').filter(has_text=runs[0]['id']).wait_for()
            page.locator('#metrics').filter(has_text='℃').wait_for()
            assert '℃' in page.locator('#metrics').inner_text()
            page.locator('#restore-run').click()
            page.locator('#page-setup').wait_for()
            assert page.locator('#page-setup').is_visible()
            # Upload a real pressure step and choose column names visually.
            page.locator('button[data-preset="pressure"]').click()
            from thermal_pid.models import Plant
            cfg=app.state.project()
            # Use the current UI pressure config rather than the previous persisted one.
            cfg=page.evaluate('state.project')
            plant=Plant(cfg);lines=['seconds,valve_percent,pressure_mpa']
            for i in range(1201):
                u=20 if i<50 else 30
                lines.append(f'{plant.time},{u},{plant.temp}')
                plant.pwm=u;plant.update()
            path=folder/'pressure.csv';path.write_text('\n'.join(lines),encoding='utf-8')
            page.locator('button[data-page="model"]').click()
            page.locator('#csv-file').set_input_files(str(path))
            page.locator('#csv-preview table').wait_for()
            assert page.locator('[data-field="history.columns.output"]').input_value()=='valve_percent'
            assert page.locator('[data-field="history.columns.temperature"]').input_value()=='pressure_mpa'
            page.locator('#run').click()
            page.locator('#state-badge').filter(has_text='已完成').wait_for(timeout=30000)
            assert page.locator('#metrics tbody tr').count()==3
            page.locator('button[data-page="llm"]').click()
            page.locator('#api-key').fill('test-private-ui-key')
            page.locator('#save').click()
            page.locator('#key-status').filter(has_text='已配置').wait_for()
            expect(page.locator('#api-key')).to_have_value('')
            assert page.locator('#api-key').input_value()==''
            snapshot=page.evaluate("api('/api/state')")
            assert 'test-private-ui-key' not in json.dumps(snapshot)
            page.locator('button[data-page="advanced"]').click()
            page.locator('#profile-name').fill('压力测试方案')
            page.locator('#profile-save').click()
            page.locator('#profiles option').filter(has_text='压力测试方案').wait_for(state='attached')
            assert not errors, errors
            page.locator('button[data-page="setup"]').click()
            page.screenshot(path=str(folder/'workbench.png'),full_page=True)
            browser.close()
        (folder/'ui-results.json').write_text(json.dumps(dict(errors=errors,runs=runs),ensure_ascii=False,indent=2),encoding='utf-8')
        print('UI PASS: four quantities, stored history, replay, CSV mapping, masked key, profiles; '+str(folder))
    finally:app.close()

if __name__=='__main__':main()
