"""Remote API tools: allowlisted, egress-checked, credential-isolated HTTP GET (ADR 0005)."""

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx2

from forge.domain.common import JSONValue
from forge.domain.risk import RiskLevel
from forge.domain.tool import Idempotency, ToolDefinition, ToolResult, ToolSource
from forge.security import egress
from forge.security.secrets import (
    SecretNotGrantedError,
    SecretResolver,
    SecretUnavailableError,
    redact,
)
from forge.tools.base import ToolContext, ToolExecutionError

MAX_RESPONSE_BYTES = 32 * 1024


@dataclass(frozen=True, slots=True)
class HttpToolConfig:
    name: str
    description: str
    url: str  # fixed endpoint; arguments only fill the query string
    query_parameters: tuple[str, ...]
    policy: egress.EgressPolicy
    credential: str | None = None  # secret reference, resolved per call
    credential_header: str = "Authorization"
    credential_prefix: str = "Bearer "
    timeout_seconds: float = 10.0
    max_attempts: int = 2
    risk: RiskLevel = RiskLevel.LOW


class HttpApiTool:
    def __init__(
        self,
        config: HttpToolConfig,
        secrets: SecretResolver,
        *,
        resolver: egress.Resolver = egress.system_resolver,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._secrets = secrets
        self._resolver = resolver
        self._transport = transport
        self._definition = ToolDefinition(
            name=config.name,
            version="1",
            description=config.description,
            input_schema={
                "type": "object",
                "properties": {
                    p: {"type": "string", "maxLength": 200} for p in config.query_parameters
                },
                "additionalProperties": False,
            },
            risk=config.risk,
            idempotency=Idempotency.READ_ONLY,
            timeout_seconds=config.timeout_seconds,
            max_attempts=config.max_attempts,
            source=ToolSource.REMOTE,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def invoke(self, arguments: Mapping[str, JSONValue], context: ToolContext) -> ToolResult:
        query = {
            k: str(v) for k, v in sorted(arguments.items()) if k in self._config.query_parameters
        }
        url = self._config.url + (
            ("&" if "?" in self._config.url else "?") + urlencode(query) if query else ""
        )
        try:
            target = await egress.check(url, self._config.policy, self._resolver)
        except egress.EgressDeniedError as exc:
            return ToolResult(f"Request blocked by egress policy: {exc}", is_error=True)

        secrets: tuple[str, ...] = ()
        headers = {"Host": target.host, "Accept": "application/json, text/plain"}
        if self._config.credential is not None:
            try:
                secret = self._secrets.resolve(self._config.credential, agent=context.agent_name)
            except (SecretNotGrantedError, SecretUnavailableError) as exc:
                raise ToolExecutionError(str(exc), retryable=False) from exc
            secrets = (secret,)
            headers[self._config.credential_header] = self._config.credential_prefix + secret

        extensions = {"sni_hostname": target.host} if target.scheme == "https" else {}
        async with httpx2.AsyncClient(
            transport=self._transport, follow_redirects=False, timeout=self._config.timeout_seconds
        ) as client:
            try:
                response = await client.get(
                    target.pinned_url, headers=headers, extensions=extensions
                )
            except httpx2.TransportError as exc:
                raise ToolExecutionError(
                    f"request failed: {type(exc).__name__}", retryable=True
                ) from exc
        if response.is_redirect:
            return ToolResult("The service redirected; redirects are not followed.", is_error=True)
        if response.status_code == 429 or response.status_code >= 500:
            raise ToolExecutionError(f"HTTP {response.status_code}", retryable=True)
        body = response.content[: MAX_RESPONSE_BYTES + 1]
        if len(body) > MAX_RESPONSE_BYTES:
            return ToolResult(
                f"Response exceeded {MAX_RESPONSE_BYTES} bytes and was discarded.", is_error=True
            )
        text = redact(body.decode("utf-8", errors="replace"), secrets)
        return ToolResult(text, is_error=response.status_code >= 400)
