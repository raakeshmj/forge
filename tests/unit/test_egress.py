import pytest

from forge.security.egress import EgressDeniedError, EgressPolicy, allowlist, check

pytestmark = pytest.mark.anyio
POLICY = EgressPolicy(allowed_hosts=allowlist(["api.example.com"]))


def resolving_to(*addresses: str):  # type: ignore[no-untyped-def]
    async def resolve(host: str, port: int) -> list[str]:
        return list(addresses)

    return resolve


async def test_allowed_public_host_is_pinned_to_its_vetted_address() -> None:
    target = await check("https://api.example.com/v1/x?a=1", POLICY, resolving_to("93.184.216.34"))

    assert target.pinned_url == "https://93.184.216.34:443/v1/x?a=1"
    assert target.host == "api.example.com"


@pytest.mark.parametrize(
    ("url", "addresses", "match"),
    [
        ("http://api.example.com/", ("93.184.216.34",), "scheme"),
        ("https://evil.example.net/", ("93.184.216.34",), "allowlist"),
        ("https://user:pw@api.example.com/", ("93.184.216.34",), "credentials"),
        ("https://api.example.com/", ("127.0.0.1",), "internal"),
        ("https://api.example.com/", ("10.1.2.3",), "internal"),
        ("https://api.example.com/", ("169.254.169.254",), "metadata"),
        ("https://api.example.com/", ("::ffff:127.0.0.1",), "internal"),
        ("https://api.example.com/", ("fe80::1",), "internal"),
        # DNS rebinding: one public and one internal answer is still refused.
        ("https://api.example.com/", ("93.184.216.34", "192.168.0.10"), "internal"),
        ("https://api.example.com/", (), "did not resolve"),
    ],
)
async def test_unsafe_destinations_are_refused(
    url: str, addresses: tuple[str, ...], match: str
) -> None:
    with pytest.raises(EgressDeniedError, match=match):
        await check(url, POLICY, resolving_to(*addresses))


async def test_private_and_http_need_explicit_opt_in() -> None:
    internal = EgressPolicy(allowlist(["svc.internal"]), allow_private=True, allow_http=True)

    target = await check("http://svc.internal:8080/ping", internal, resolving_to("10.0.0.7"))

    assert target.pinned_url == "http://10.0.0.7:8080/ping"
    with pytest.raises(EgressDeniedError, match="metadata"):  # metadata is never allowed
        await check("http://svc.internal/", internal, resolving_to("169.254.169.254"))
