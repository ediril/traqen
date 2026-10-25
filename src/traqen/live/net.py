"""Fetching user-supplied URLs without letting them reach internal addresses.

Every hop (including redirects) is resolved once, every resolved address must be
public, and the request is sent to that exact address, so a DNS answer that
changes between the check and the connection cannot redirect it inward. TLS is
still verified against the original hostname via SNI.
"""

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

MAX_REDIRECTS = 5
MAX_BYTES = 2_000_000


class BlockedURL(Exception):
    pass


@dataclass(frozen=True)
class Fetched:
    url: str
    status: int
    content_type: str
    body: bytes


async def _public_address(host: str, port: int) -> str:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise httpx.ConnectError(f"Could not resolve {host}") from exc
    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise httpx.ConnectError(f"Could not resolve {host}")
    for address in addresses:
        if not ipaddress.ip_address(address.split("%")[0]).is_global:
            raise BlockedURL(f"{host} resolves to a non-public address")
    return sorted(addresses)[0]


async def safe_get(client: httpx.AsyncClient, url: str) -> Fetched:
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise BlockedURL("Only http and https links can be checked")
        host = parts.hostname
        port = parts.port or (443 if parts.scheme == "https" else 80)
        address = await _public_address(host, port)

        target = httpx.URL(url).copy_with(host=address)
        request = client.build_request(
            "GET",
            target,
            headers={"Host": parts.netloc.rsplit("@", 1)[-1]},
            extensions={"sni_hostname": host},
        )
        # Redirects are followed here, hop by hop, so each one is checked
        # above; never by the client, whatever it was set up to do.
        response = await client.send(request, stream=True, follow_redirects=False)
        try:
            if response.is_redirect and "location" in response.headers:
                url = urljoin(url, response.headers["location"])
                continue
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) >= MAX_BYTES:
                    break
            return Fetched(
                url=url,
                status=response.status_code,
                content_type=response.headers.get("content-type", ""),
                body=bytes(body[:MAX_BYTES]),
            )
        finally:
            await response.aclose()
    raise httpx.TooManyRedirects(f"More than {MAX_REDIRECTS} redirects")
