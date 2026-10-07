"""Outbound network policy for remote tools and HTTP MCP servers (SECURITY §9, ADR 0005).

The caller connects to the exact address that was checked, so DNS rebinding cannot
swap the target between the check and the connection.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from urllib.parse import urlsplit

type Resolver = Callable[[str, int], Awaitable[list[str]]]

# Cloud metadata endpoints are link-local or well-known private addresses; listed for clarity.
_METADATA = frozenset({"169.254.169.254", "fd00:ec2::254", "100.100.100.200"})


class EgressDeniedError(PermissionError):
    """A request was refused by the egress policy. Not retryable."""


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    allowed_hosts: frozenset[str]
    allow_private: bool = False  # only for explicitly trusted internal services
    allow_http: bool = False


@dataclass(frozen=True, slots=True)
class Target:
    scheme: str
    host: str
    port: int
    address: str  # the vetted IP to connect to
    path_and_query: str

    @property
    def pinned_url(self) -> str:
        host = f"[{self.address}]" if ":" in self.address else self.address
        return f"{self.scheme}://{host}:{self.port}{self.path_and_query}"


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


async def check(url: str, policy: EgressPolicy, resolver: Resolver = system_resolver) -> Target:
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme != "https" and not (scheme == "http" and policy.allow_http):
        raise EgressDeniedError(f"scheme {scheme!r} is not allowed")
    host = (parts.hostname or "").lower()
    if not host or host not in policy.allowed_hosts:
        raise EgressDeniedError(f"host {host!r} is not on the allowlist")
    if parts.username or parts.password:
        raise EgressDeniedError("credentials in URLs are not allowed")
    port = parts.port or (443 if scheme == "https" else 80)
    addresses = await resolver(host, port)
    if not addresses:
        raise EgressDeniedError(f"host {host!r} did not resolve")
    for address in addresses:  # every answer must pass: an attacker controls the order
        _check_address(address, policy.allow_private)
    path = parts.path or "/"
    return Target(
        scheme, host, port, addresses[0], path + (f"?{parts.query}" if parts.query else "")
    )


def _check_address(address: str, allow_private: bool) -> None:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if str(ip) in _METADATA:
        raise EgressDeniedError(f"address {ip} is a cloud metadata endpoint")
    internal = (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )
    if internal and not allow_private:
        raise EgressDeniedError(f"address {ip} is internal")


def allowlist(hosts: Collection[str]) -> frozenset[str]:
    return frozenset(host.lower() for host in hosts)
