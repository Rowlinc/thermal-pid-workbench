"""A valid transport can end before any usable PID answer is produced."""
from types import SimpleNamespace

from llm.client import LLMTuner
from llm.providers import OpenAISDKProvider, HTTPFallbackProvider


def client_with_response(content, metadata):
    client = LLMTuner.__new__(LLMTuner)
    client.llm_client = SimpleNamespace(response_metadata=metadata)
    client.debug_output = False
    client.last_request_diagnostic = {}
    client._emit_log = lambda *args: None
    client._call_with_retry = lambda *args: content
    client._execute_request = lambda *args: None
    return client


def test_reasoning_only_length_is_not_json_failure_or_pid():
    provider = OpenAISDKProvider.__new__(OpenAISDKProvider)
    provider.response_metadata = {}
    chunk = SimpleNamespace(choices=[SimpleNamespace(
        delta=SimpleNamespace(content=None, reasoning_content='private reasoning'),
        finish_reason='length')])
    assert provider._extract_chunk(chunk, '') == ''
    client = client_with_response('', provider.response_metadata)
    assert client.request_json(system_prompt='JSON', user_prompt='trial') is None
    assert client.last_request_diagnostic['code'] == 'output_truncated'
    assert client.last_request_diagnostic['reasoning_content_chars'] == 17
    assert 'private reasoning' not in str(client.last_request_diagnostic)


def test_truncated_valid_json_must_still_be_rejected():
    client = client_with_response('{"p":1,"i":0.1,"d":0}', {'finish_reason':'length'})
    assert client.request_json(system_prompt='JSON',user_prompt='trial') is None


def test_complete_json_is_accepted_and_diagnostics_reset():
    client = client_with_response('{"p":1,"i":0.1,"d":0}', {'finish_reason':'stop'})
    client.last_request_diagnostic={'code':'output_truncated'}
    assert client.request_json(system_prompt='JSON',user_prompt='trial')['p']==1
    assert 'code' not in client.last_request_diagnostic


def test_http_stream_has_same_termination_diagnostics():
    provider=HTTPFallbackProvider.__new__(HTTPFallbackProvider)
    provider.response_metadata={}
    assert provider._extract_openai({'choices':[{'delta':{'reasoning_content':'hidden'},'finish_reason':'length'}]})==''
    assert provider.response_metadata=={'finish_reason':'length','reasoning_content_chars':6}
