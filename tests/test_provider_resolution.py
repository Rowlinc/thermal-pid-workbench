# Modified for Thermal PID Workbench; see CHANGELOG.md and NOTICE.
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parent.parent))

from llm.client import LLMTuner


class FakeOpenAI:
    def __init__(self, api_key, base_url, timeout=None, max_retries=None):
        self.api_key  = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries


class FakeAnthropic:
    def __init__(self, api_key, base_url, timeout=None, max_retries=None):
        self.api_key  = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb):
        return None

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload

    def iter_lines(self):
        import json

        if "content" in self.payload:
            text = self.payload["content"][0]["text"]
            data = {"type": "content_block_delta", "delta": {"text": text}}
            yield f"data: {json.dumps(data)}".encode("utf-8")
        elif "choices" in self.payload:
            content = self.payload["choices"][0]["message"]["content"]
            data    = {"choices": [{"delta": {"content": content}}]}
            yield f"data: {json.dumps(data)}".encode("utf-8")
        yield b"data: [DONE]"


class FakeRequests:
    def __init__(self, payload):
        self.payload = payload
        self.calls   = []

    def post(self, url, headers=None, json=None, timeout=None, **kwargs):
        self.calls.append(
            {
                "url"    : url,
                "headers": headers or {},
                "json"   : json or {},
                "timeout": timeout,
                "stream" : kwargs.get("stream", False),
            }
        )
        return FakeResponse(self.payload)


def build_fake_module(name, client_class_name, client_class):
    module = types.ModuleType(name)
    setattr(module, client_class_name, client_class)
    return module


class ProviderResolutionTests(unittest.TestCase):
    def test_http_fallback_preserves_json_and_thinking_options(self):
        from llm.providers import HTTPFallbackProvider
        fake = FakeRequests({'choices': [{'message': {'content': '{"p":1,"i":0.01,"d":0}'}}]})
        provider = HTTPFallbackProvider('test-key', 'https://api.deepseek.com/v1', 'deepseek-flash', 12, False, requests_module=fake)
        provider.request_options = {'max_tokens': 2048, 'response_format': {'type': 'json_object'}, 'extra_body': {'thinking': {'type': 'disabled'}}}
        provider.execute_request([], [], '', lambda chunk: None)
        payload = fake.calls[0]['json']
        self.assertEqual(payload['thinking'], {'type': 'disabled'})
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        self.assertEqual(payload['max_tokens'], 2048)
        self.assertNotIn('extra_body', payload)

    def test_sdk_receives_structured_request_options(self):
        from llm.providers import OpenAISDKProvider
        provider = OpenAISDKProvider('test-key', 'https://example.com/v1', 'model', 12)
        calls = []
        def create(**kwargs):
            calls.append(kwargs)
            return iter([])
        provider.client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
        provider.request_options = {'response_format': {'type': 'json_object'}, 'max_tokens': 512}
        provider.execute_request([], [], '', lambda chunk: None)
        self.assertEqual(calls[0]['max_tokens'], 512)
        self.assertEqual(calls[0]['response_format'], {'type': 'json_object'})

    def test_configured_timeout_reaches_sdk_and_nested_retries_are_disabled(self):
        tuner = LLMTuner('test-key', 'https://example.com/v1', 'model', timeout=12, max_attempts=2)
        self.assertEqual(tuner.llm_client.client.timeout, 12)
        self.assertEqual(tuner.llm_client.client.max_retries, 0)
        self.assertEqual(tuner.max_attempts, 2)

    def setUp(self):
        openai_module     = build_fake_module("openai", "OpenAI", FakeOpenAI)
        anthropic_module  = build_fake_module("anthropic", "Anthropic", FakeAnthropic)
        self.module_patch = patch.dict(
            sys.modules,
            {"openai": openai_module, "anthropic": anthropic_module}
        )
        self.module_patch.start()

    def tearDown(self):
        self.module_patch.stop()

    def test_claude_model_keeps_openai_transport_for_openai_provider(self):
        tuner = LLMTuner(
            "test-key",
            "https://relay.example.com/v1",
            "claude-3-5-sonnet",
            "openai"
        )

        self.assertEqual(tuner.provider, "openai")
        self.assertEqual(type(tuner.client).__name__, "FakeOpenAI")

    def test_openai_claude_alias_routes_to_openai_transport(self):
        tuner = LLMTuner(
            "test-key",
            "https://relay.example.com/v1",
            "claude-3-7-sonnet",
            "openai_claude",
        )

        self.assertEqual(tuner.provider, "openai")
        self.assertEqual(type(tuner.client).__name__, "FakeOpenAI")

    def test_native_anthropic_provider_routes_to_messages_api(self):
        from llm.providers import HTTPFallbackProvider
        tuner = LLMTuner(
            "test-key",
            "https://api.anthropic.com",
            "claude-3-5-sonnet",
            "anthropic"
        )
        fake_requests  = FakeRequests({"content": [{"text": "ok"}]})
        provider = HTTPFallbackProvider(
            "test-key", "https://api.anthropic.com", "claude-3-5-sonnet", 60.0, True, requests_module=fake_requests
        )
        chunks = []
        provider.execute_request(
            [{"role": "user", "content": "hello"}],
            [{"role": "user", "content": "hello"}],
            system_prompt="",
            on_chunk=chunks.append,
        )
        content = "".join(chunks)

        self.assertEqual(tuner.provider, "anthropic")
        self.assertEqual(content, "ok")
        self.assertEqual(
            fake_requests.calls[0]["url"], "https://api.anthropic.com/v1/messages"
        )
        self.assertIn("x-api-key", fake_requests.calls[0]["headers"])
        self.assertTrue(fake_requests.calls[0]["stream"], "HTTP 请求应使用 stream=True")

    def test_claude_openai_transport_uses_chat_completions_endpoint(self):
        from llm.providers import HTTPFallbackProvider
        tuner = LLMTuner(
            "test-key",
            "https://relay.example.com/v1",
            "claude-3-5-sonnet",
            "openai_claude",
        )
        fake_requests  = FakeRequests(
            {"choices": [{"message": {"content": '{"status":"DONE"}'}}]}
        )
        provider = HTTPFallbackProvider(
            "test-key", "https://relay.example.com/v1", "claude-3-5-sonnet", 60.0, False, requests_module=fake_requests
        )

        chunks = []
        provider.execute_request(
            [{"role": "user", "content": "hello"}],
            [{"role": "user", "content": "hello"}],
            system_prompt="",
            on_chunk=chunks.append,
        )
        content = "".join(chunks)

        self.assertEqual(content, '{"status":"DONE"}')
        self.assertEqual(
            fake_requests.calls[0]["url"],
            "https://relay.example.com/v1/chat/completions",
        )
        self.assertEqual(
            fake_requests.calls[0]["headers"].get("Authorization"),
            "Bearer test-key",
        )
        self.assertTrue(fake_requests.calls[0]["stream"], "HTTP 请求应使用 stream=True")


if __name__ == "__main__":
    unittest.main()
