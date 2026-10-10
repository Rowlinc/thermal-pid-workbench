#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Modified for Thermal PID Workbench; see CHANGELOG.md and NOTICE.
"""
llm/client.py - LLM client wrapper with streaming and prompt selection.
"""

from __future__ import annotations

import json
import time
import traceback
from typing import Any, Callable, Dict, List, Optional

from llm.prompts import SYSTEM_PROMPT, build_user_prompt, get_system_prompt
from llm.response_parser import parse_json_response
from llm.stream_formatter import JSONStreamFormatter
from llm.providers import BaseLLMProvider, OpenAISDKProvider, AnthropicSDKProvider, HTTPFallbackProvider
from llm.cancellation import RequestCancelled, interruptible_request


class LLMTuner:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        provider: str = "openai",
        stream_callback: Optional[Callable[[str, bool], None]] = None,
        log_callback: Optional[Callable[[str, str], None]] = None,
        emit_console: bool = True,
        abort_check: Optional[Callable[[], bool]] = None,
        timeout: float = 60.0,
        debug_output: bool = False,
        max_attempts: int = 5,
        request_options: Optional[Dict[str, Any]] = None,
        waiting_callback: Optional[Callable[[float], None]] = None,
    ):
        self.api_key = api_key
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.provider_choice = self._normalize_provider_choice(provider)
        self.provider = self._resolve_transport()
        self.timeout = timeout
        self.debug_output = debug_output
        self.max_attempts = max(1, int(max_attempts))
        self.request_options = dict(request_options or {})
        self.last_request_diagnostic: Dict[str, Any] = {}
        self.emit_console = emit_console
        self.stream_callback = stream_callback
        self.log_callback = log_callback
        self.abort_check = abort_check
        self.waiting_callback = waiting_callback

        self.llm_client: BaseLLMProvider = self._initialize_provider()
        self.llm_client.request_options = self.request_options
        # For backward compatibility in tests
        self.use_sdk = isinstance(self.llm_client, (OpenAISDKProvider, AnthropicSDKProvider))
        self.client = getattr(self.llm_client, "client", None)
        self.requests = getattr(self.llm_client, "requests", None)

    def fork(self, abort_check=None, log_callback=None, waiting_callback=None):
        """Each route owns its transport and response metadata."""
        return LLMTuner(self.api_key, self.base_url, self.model, self.provider_choice,
            emit_console=self.emit_console, debug_output=self.debug_output,
            timeout=self.timeout, max_attempts=self.max_attempts,
            request_options=dict(self.request_options),
            abort_check=abort_check or self.abort_check, log_callback=log_callback,
            waiting_callback=waiting_callback)

    def close(self):
        close = getattr(self.llm_client, 'close', None)
        if not close: return
        if self.abort_check and self.abort_check():
            import threading
            threading.Thread(target=close, name='pid-tuner-close', daemon=True).start()
        else:
            close()

    def _initialize_provider(self) -> BaseLLMProvider:
        try:
            if self.provider == "openai":
                return OpenAISDKProvider(self.api_key, self.base_url, self.model, self.timeout)
            elif self.provider == "anthropic":
                return AnthropicSDKProvider(self.api_key, self.base_url, self.model, self.timeout)
        except ImportError:
            pass
        except Exception:
            if self.debug_output:
                traceback.print_exc()
                
        return HTTPFallbackProvider(
            self.api_key, 
            self.base_url, 
            self.model, 
            self.timeout, 
            is_anthropic=(self.provider == "anthropic")
        )

    @staticmethod
    def _normalize_provider_choice(provider: Optional[str]) -> str:
        normalized = str(provider or "").strip().lower()
        normalized = normalized.replace("-", "_").replace(" ", "_")
        return normalized or "openai"

    def _resolve_transport(self) -> str:
        if self.provider_choice in (
            "openai",
            "openai_compat",
            "openai_compatible",
            "openai_claude",
            "claude_openai",
            "claude_relay",
        ):
            return "openai"
        if self.provider_choice in ("anthropic", "anthropic_native", "claude_native"):
            return "anthropic"

        base_url_lower = self.base_url.lower()
        if self.provider_choice == "auto" and "api.anthropic.com" in base_url_lower:
            return "anthropic"
        return "openai"

    def _interruptible_sleep(self, seconds: float) -> bool:
        """Poll `abort_check` every 0.1s while sleeping."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self.abort_check and self.abort_check():
                return False
            time.sleep(min(0.1, deadline - time.time()))
        return True

    def _emit_log(self, label: str, message: str) -> None:
        if self.log_callback is not None:
            self.log_callback(label, message)
        if self.emit_console:
            print(message)
            try:
                with open("logs/console_log.txt", "a", encoding="utf-8") as f:
                    f.write(message + "\n")
            except Exception:
                pass

    def _emit_stream_update(
        self,
        full_content: str,
        *,
        done: bool = False,
        formatter: Optional[JSONStreamFormatter] = None,
    ) -> None:
        if formatter is not None:
            formatter.process(full_content)
        if self.stream_callback is not None:
            self.stream_callback(full_content, done)

    def _call_with_retry(
        self, func: Callable[..., str], *args: Any, **kwargs: Any
    ) -> str:
        max_retries = getattr(self, 'max_attempts', 5)
        delays = [min(32, 2 ** (attempt + 1)) for attempt in range(max_retries)]
        last_exception: Optional[Exception] = None

        for attempt in range(max_retries):
            if self.abort_check and self.abort_check():
                return ""
            try:
                return func(*args, **kwargs)
            except (KeyboardInterrupt, SystemExit):
                raise
            except RequestCancelled:
                raise
            except Exception as exc:
                last_exception = exc
                if attempt < max_retries - 1:
                    self._emit_log(
                        "warn",
                        f"\n[WARN] LLM call failed: {exc}. Retrying in {delays[attempt]}s...",
                    )
                    if not self._interruptible_sleep(delays[attempt]):
                        return ""
                else:
                    self._emit_log(
                        "error",
                        f"\n[ERROR] LLM call failed after {max_retries} attempts: {exc}",
                    )
                    raise

        if last_exception is not None:
            raise last_exception
        return ""

    def _execute_request(
        self,
        openai_msgs: List[Dict[str, Any]],
        anthropic_msgs: List[Dict[str, Any]],
        system_prompt: str = SYSTEM_PROMPT,
    ) -> str:
        self._emit_log("llm", "  LLM is thinking...")
        full_content = []
        formatter = JSONStreamFormatter() if self.emit_console else None

        def on_chunk(chunk: str) -> None:
            if self.abort_check and self.abort_check():
                return
            full_content.append(chunk)
            self._emit_stream_update("".join(full_content), formatter=formatter)

        def execute(provider):
            return interruptible_request(lambda: provider.execute_request(
                openai_msgs=openai_msgs,
                anthropic_msgs=anthropic_msgs,
                system_prompt=system_prompt,
                on_chunk=on_chunk,
                abort_check=self.abort_check,
            ), self.abort_check, getattr(provider, 'close', lambda: None),
                getattr(self, 'waiting_callback', None))

        try:
            execute(self.llm_client)
        except RequestCancelled:
            raise
        except Exception as sdk_error:
            if self.abort_check and self.abort_check():
                raise RequestCancelled() from None
            if self.use_sdk:
                self._emit_log(
                    "warn",
                    f"\n[WARN] SDK request failed, falling back to HTTP: {sdk_error}",
                )
                previous = self.llm_client
                self.llm_client = HTTPFallbackProvider(
                    self.api_key,
                    self.base_url,
                    self.model,
                    self.timeout,
                    is_anthropic=(self.provider == "anthropic"),
                )
                self.use_sdk = False
                self.llm_client.request_options = getattr(self, 'request_options', {})
                self.requests = self.llm_client.requests
                full_content.clear()
                close = getattr(previous, 'close', None)
                if close: close()
                execute(self.llm_client)
            else:
                raise

        final_text = "".join(full_content)
        if final_text:
            self._emit_stream_update(final_text, done=True)
        if self.emit_console:
            print()
            try:
                with open("logs/console_log.txt", "a", encoding="utf-8") as f:
                    f.write(final_text + "\n\n")
            except Exception:
                pass
        return final_text

    def request_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> Optional[Dict[str, Any]]:
        self.last_request_diagnostic = {}
        openai_msgs: List[Any] = [{"role": "system", "content": system_prompt}]
        anthropic_msgs: List[Any] = [{"role": "user", "content": user_prompt}]
        openai_msgs.append({"role": "user", "content": user_prompt})

        try:
            content = self._call_with_retry(
                self._execute_request, openai_msgs, anthropic_msgs, system_prompt
            )
            if getattr(self, 'abort_check', None) and self.abort_check():
                raise RequestCancelled()

            if self.debug_output:
                self._emit_log(
                    "debug", f"\n[LLM raw response preview]\n{content[:500]}...\n"
                )

            metadata = dict(getattr(self.llm_client, 'response_metadata', {}) or {})
            self.last_request_diagnostic = metadata
            if metadata.get('finish_reason') == 'length':
                self.last_request_diagnostic.update(code='output_truncated', message=
                    'LLM 输出达到 token 上限而被截断；请提高最大输出 token 数，或关闭思考模式后重试。')
                self._emit_log('warn', self.last_request_diagnostic['message'])
                return None
            parsed = parse_json_response(content)
            if parsed:
                return parsed

            self.last_request_diagnostic.update(code='invalid_json' if content else 'empty_answer',
                message='LLM 未返回可解析的 JSON 参数。' if content else 'LLM 未返回正式答案；请检查思考模式和输出额度。')
            self._emit_log(
                "warn",
                "[WARN] LLM response could not be parsed as JSON; ignoring this round.",
            )
            return None
        except RequestCancelled:
            self.last_request_diagnostic = dict(code='cancelled', message='本地已停止等待大模型，不重试、不采用未完成建议。')
            return None
        except Exception as exc:
            self.last_request_diagnostic = dict(code='request_failed', message='LLM API 请求失败；请检查服务、网络和连接诊断。')
            self._emit_log("error", f"[ERROR] LLM request failed after retries: {exc}")
            return None

    def analyze(
        self,
        prompt_data: str,
        history_text: str,
        tuning_mode: str = "generic",
        prompt_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        system_prompt = get_system_prompt(tuning_mode, prompt_context=prompt_context)
        user_prompt = build_user_prompt(
            prompt_data,
            history_text,
            tuning_mode=tuning_mode,
            prompt_context=prompt_context,
        )
        return self.request_json(system_prompt=system_prompt, user_prompt=user_prompt)
