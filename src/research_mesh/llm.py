from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol, Self
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class LLMConfigurationError(ValueError):
    """The LLM gateway or provider is not safely configured."""


class LLMProviderError(RuntimeError):
    """The configured upstream provider did not return a usable completion."""


class LLMMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=100_000)


class LLMCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[LLMMessage] = Field(min_length=1, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int = Field(default=1024, ge=1, le=32_768)
    response_format: Literal["text", "json_object"] = "text"

    @model_validator(mode="after")
    def prompt_size_is_bounded(self) -> Self:
        if sum(len(message.content) for message in self.messages) > 200_000:
            raise ValueError("combined message content exceeds 200000 characters")
        return self


class LLMUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class LLMCompletionResponse(BaseModel):
    request_id: str | None = None
    provider: str
    model: str
    content: str
    finish_reason: str | None = None
    usage: LLMUsage = Field(default_factory=LLMUsage)
    latency_ms: float


class LLMClient(Protocol):
    async def complete(self, request: LLMCompletionRequest) -> LLMCompletionResponse: ...


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, *, minimum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, value)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class LLMSettings:
    provider: str = "disabled"
    base_url: str | None = None
    api_key: SecretStr | None = None
    model: str | None = None
    timeout_seconds: float = 60.0
    retries: int = 2
    max_tokens_limit: int = 4096
    allow_model_override: bool = False
    gateway_token: SecretStr | None = None

    @classmethod
    def from_environment(cls) -> LLMSettings:
        api_key = os.getenv("RESEARCH_MESH_LLM_API_KEY") or None
        gateway_token = os.getenv("RESEARCH_MESH_LLM_GATEWAY_TOKEN") or None
        return cls(
            provider=os.getenv("RESEARCH_MESH_LLM_PROVIDER", "disabled")
            .strip()
            .lower(),
            base_url=os.getenv("RESEARCH_MESH_LLM_BASE_URL") or None,
            api_key=SecretStr(api_key) if api_key else None,
            model=os.getenv("RESEARCH_MESH_LLM_MODEL") or None,
            timeout_seconds=_env_float(
                "RESEARCH_MESH_LLM_TIMEOUT_SECONDS", 60.0, minimum=1.0
            ),
            retries=_env_int(
                "RESEARCH_MESH_LLM_RETRIES", 2, minimum=0, maximum=5
            ),
            max_tokens_limit=_env_int(
                "RESEARCH_MESH_LLM_MAX_TOKENS", 4096, minimum=1, maximum=32_768
            ),
            allow_model_override=_env_bool(
                "RESEARCH_MESH_LLM_ALLOW_MODEL_OVERRIDE", False
            ),
            gateway_token=SecretStr(gateway_token) if gateway_token else None,
        )

    def public_configuration(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "base_url_configured": self.base_url is not None,
            "model": self.model,
            "api_key_configured": self.api_key is not None,
            "gateway_auth_enabled": self.gateway_token is not None,
            "allow_model_override": self.allow_model_override,
            "max_tokens_limit": self.max_tokens_limit,
        }


def _validate_openai_compatible_settings(settings: LLMSettings) -> None:
    if not settings.base_url:
        raise LLMConfigurationError("RESEARCH_MESH_LLM_BASE_URL is required")
    parsed = urlsplit(settings.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise LLMConfigurationError("LLM base URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise LLMConfigurationError("credentials must not be embedded in LLM base URL")
    if parsed.query or parsed.fragment:
        raise LLMConfigurationError("LLM base URL must not contain query or fragment")
    if not settings.model:
        raise LLMConfigurationError("RESEARCH_MESH_LLM_MODEL is required")


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        raw = response.headers.get("Retry-After")
        if raw:
            try:
                return min(max(float(raw), 0.0), 10.0)
            except ValueError:
                pass
    return min(0.5 * (2**attempt), 5.0)


def _message_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        if parts:
            return "\n".join(parts)
    raise LLMProviderError("upstream response has no text content")


class OpenAICompatibleClient:
    def __init__(
        self,
        settings: LLMSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        _validate_openai_compatible_settings(settings)
        self.settings = settings
        self.transport = transport

    def _model_for(self, request: LLMCompletionRequest) -> str:
        configured = self.settings.model or ""
        if request.model and request.model != configured:
            if not self.settings.allow_model_override:
                raise LLMConfigurationError("per-request model override is disabled")
            return request.model
        return request.model or configured

    async def complete(self, request: LLMCompletionRequest) -> LLMCompletionResponse:
        model = self._model_for(request)
        if request.max_tokens > self.settings.max_tokens_limit:
            raise LLMConfigurationError(
                f"max_tokens exceeds gateway limit {self.settings.max_tokens_limit}"
            )
        payload: dict[str, Any] = {
            "model": model,
            "messages": [message.model_dump() for message in request.messages],
            "max_tokens": request.max_tokens,
            "stream": False,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}

        headers = {"Accept": "application/json"}
        if self.settings.api_key is not None:
            headers["Authorization"] = (
                f"Bearer {self.settings.api_key.get_secret_value()}"
            )

        started = time.perf_counter()
        response: httpx.Response | None = None
        base_url = f"{self.settings.base_url.rstrip('/')}/"
        async with httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=self.settings.timeout_seconds,
            transport=self.transport,
        ) as client:
            for attempt in range(self.settings.retries + 1):
                try:
                    response = await client.post("chat/completions", json=payload)
                except httpx.TransportError as exc:
                    if attempt >= self.settings.retries:
                        raise LLMProviderError(
                            f"upstream transport failed: {type(exc).__name__}"
                        ) from exc
                    await asyncio.sleep(_retry_delay(None, attempt))
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < self.settings.retries:
                        await asyncio.sleep(_retry_delay(response, attempt))
                        continue
                if response.is_error:
                    raise LLMProviderError(
                        f"upstream returned HTTP {response.status_code}"
                    )
                break

        if response is None:
            raise LLMProviderError("upstream returned no response")
        try:
            data = response.json()
            choices = data.get("choices")
            if not isinstance(choices, list) or not choices:
                raise LLMProviderError("upstream response has no choices")
            choice = choices[0]
            message = choice.get("message", {})
            content = _message_content(message.get("content"))
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        except (AttributeError, TypeError, ValueError) as exc:
            raise LLMProviderError("upstream returned invalid JSON") from exc

        return LLMCompletionResponse(
            request_id=response.headers.get("x-request-id") or data.get("id"),
            provider="openai-compatible",
            model=str(data.get("model") or model),
            content=content,
            finish_reason=choice.get("finish_reason"),
            usage=LLMUsage(
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
            ),
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
        )


def create_provider_client(settings: LLMSettings) -> LLMClient | None:
    if settings.provider == "disabled":
        return None
    if settings.provider == "openai-compatible":
        return OpenAICompatibleClient(settings)
    raise LLMConfigurationError(f"unsupported LLM provider: {settings.provider}")


class LLMGatewayClient:
    """The provider-neutral client used by all research agents."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8020",
        *,
        token: SecretStr | None = None,
        timeout_seconds: float = 65.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    @classmethod
    def from_environment(cls) -> LLMGatewayClient:
        token = os.getenv("RESEARCH_MESH_LLM_GATEWAY_TOKEN") or None
        return cls(
            base_url=os.getenv(
                "RESEARCH_MESH_LLM_GATEWAY_URL", "http://127.0.0.1:8020"
            ),
            token=SecretStr(token) if token else None,
        )

    async def complete(self, request: LLMCompletionRequest) -> LLMCompletionResponse:
        headers: dict[str, str] = {}
        if self.token is not None:
            headers["Authorization"] = f"Bearer {self.token.get_secret_value()}"
        async with httpx.AsyncClient(
            base_url=f"{self.base_url}/",
            headers=headers,
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            try:
                response = await client.post(
                    "v1/complete", json=request.model_dump(mode="json")
                )
                response.raise_for_status()
                return LLMCompletionResponse.model_validate(response.json())
            except httpx.HTTPStatusError as exc:
                raise LLMProviderError(
                    f"LLM gateway returned HTTP {exc.response.status_code}"
                ) from exc
            except httpx.TransportError as exc:
                raise LLMProviderError(
                    f"LLM gateway transport failed: {type(exc).__name__}"
                ) from exc
