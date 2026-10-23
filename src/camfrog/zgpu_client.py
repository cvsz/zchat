"""zGPU RPC Client for LLM Inference.

Provides a fast, lightweight JSON-RPC / REST client using Python's standard library
to offload LLM inference workloads to a zGPU accelerated backend.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

log = logging.getLogger(__name__)

DEFAULT_ZGPU_RPC_URL = "http://127.0.0.1:8880/rpc"
DEFAULT_TIMEOUT_SECONDS = 15.0


@dataclass
class ZGPUResponse:
    """Structured response from zGPU LLM inference."""

    text: str
    tokens_used: int = 0
    latency_ms: float = 0.0
    model: str = ""
    finish_reason: str = "stop"
    raw: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.text)


class ZGPUClient:
    """Client for querying zGPU RPC acceleration services for LLM generation."""

    def __init__(
        self,
        endpoint_url: str = DEFAULT_ZGPU_RPC_URL,
        api_key: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        default_model: str = "default",
    ):
        self.endpoint_url = endpoint_url.strip() or DEFAULT_ZGPU_RPC_URL
        self.api_key = api_key
        self.timeout = max(1.0, float(timeout))
        self.default_model = default_model

    def _build_request(self, payload: Dict[str, Any]) -> urllib.request.Request:
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "ZChat-zGPU-Client/1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return urllib.request.Request(
            self.endpoint_url, data=data, headers=headers, method="POST"
        )

    def call_rpc(
        self, method: str, params: Dict[str, Any], req_id: int = 1
    ) -> Dict[str, Any]:
        """Execute a standard JSON-RPC 2.0 call against the zGPU endpoint."""
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        req = self._build_request(payload)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw_data = resp.read().decode("utf-8")
            return json.loads(raw_data)

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 256,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> ZGPUResponse:
        """Submit a generation request to the zGPU RPC backend."""
        start_t = time.perf_counter()
        target_model = model or self.default_model
        params: Dict[str, Any] = {
            "model": target_model,
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_prompt:
            params["system_prompt"] = system_prompt
        if extra_params:
            params.update(extra_params)

        try:
            res = self.call_rpc("llm.generate", params)
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0

            if "error" in res and res["error"]:
                err_msg = (
                    res["error"].get("message")
                    if isinstance(res["error"], dict)
                    else str(res["error"])
                )
                return ZGPUResponse(
                    text="",
                    latency_ms=elapsed_ms,
                    model=target_model,
                    error=f"zGPU RPC Error: {err_msg}",
                    raw=res,
                )

            result_data = res.get("result", {})
            # Handle string response or structured dict
            if isinstance(result_data, str):
                return ZGPUResponse(
                    text=result_data,
                    latency_ms=elapsed_ms,
                    model=target_model,
                    raw=res,
                )

            return ZGPUResponse(
                text=result_data.get("text", "") or result_data.get("reply", "") or "",
                tokens_used=result_data.get("tokens_used", 0)
                or result_data.get("usage", {}).get("total_tokens", 0),
                latency_ms=result_data.get("latency_ms", elapsed_ms),
                model=result_data.get("model", target_model),
                finish_reason=result_data.get("finish_reason", "stop"),
                raw=res,
            )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            log.warning("zGPU RPC request failed: %s", e)
            return ZGPUResponse(
                text="",
                latency_ms=elapsed_ms,
                model=target_model,
                error=str(e),
            )

    def health_check(self) -> Dict[str, Any]:
        """Check zGPU backend availability and status."""
        try:
            res = self.call_rpc("system.health", {}, req_id=0)
            return {"ok": True, "details": res.get("result", {})}
        except Exception as e:
            return {"ok": False, "error": str(e)}
