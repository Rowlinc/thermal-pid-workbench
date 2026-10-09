import json
import pytest
from thermal_pid.config import DEFAULTS, ConfigError, load_project, write_defaults
from thermal_pid.config_comments import COMMENTS, parse_commented_json, render_commented_json


def test_all_fields_documented_and_round_trip():
    text = render_commented_json(DEFAULTS)
    assert parse_commented_json(text) == DEFAULTS
    def verify(value, path=''):
        for key, item in value.items():
            location = f'{path}.{key}' if path else key
            assert location in COMMENTS
            assert '// ' + COMMENTS[location] in text
            if isinstance(item, dict): verify(item, location)
    verify(DEFAULTS)


def test_comments_preserve_urls_quotes_and_literal_markers(tmp_path):
    data={'llm':{'base_url':'https://example.com/v1?x=//text', 'credentials_file':'dir/*literal*/config.json'},
          'name':'quoted "value" // literal \\ path'}
    text='/* 文件说明\n 不参与计算 */\n'+json.dumps(data)+' // 完成\n'
    path=tmp_path/'project.json'; path.write_text(text, encoding='utf-8')
    cfg,_=load_project(path)
    assert cfg['llm']['base_url']==data['llm']['base_url']
    assert cfg['llm']['credentials_file']==data['llm']['credentials_file']
    assert cfg['name']==data['name']


def test_commented_partial_config_keeps_defaults(tmp_path):
    path=tmp_path/'project.json'
    path.write_text('{\n// 从 30°C 到 95°C\n"task": {"target_temperature_c":95 /* 目标 */}\n}',encoding='utf-8')
    cfg,_=load_project(path)
    assert cfg['task']['target_temperature_c']==95
    assert cfg['task']['initial_temperature_c']==30


@pytest.mark.parametrize('content',['{} /* 未闭合', '{"mode":"test",}', '{// 说明\n"mod":"test"}'])
def test_bad_comments_or_fields_still_rejected(tmp_path, content):
    path=tmp_path/'project.json'; path.write_text(content,encoding='utf-8')
    with pytest.raises(ConfigError): load_project(path)


def test_init_creates_runnable_documented_config(tmp_path):
    path=tmp_path/'new.json'; write_defaults(path)
    assert '默认值' in path.read_text(encoding='utf-8')
    assert load_project(path)[0]==DEFAULTS


def test_legacy_help_metadata_does_not_enter_effective_config(tmp_path):
    path = tmp_path / 'project.json'
    path.write_text(json.dumps({'_help': {'task': 'old explanatory text'}}), encoding='utf-8')
    assert load_project(path)[0] == DEFAULTS
    assert '_help' not in DEFAULTS
