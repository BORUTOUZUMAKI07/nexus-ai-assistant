"""Resolve the effective client IP for security decisions (rate limits).

A reverse proxy that appends to ``X-Forwarded-For`` lets any client prepend an
arbitrary hop, so a limiter that reads that header directly is trivially
bypassed. The rule here is: honour a forwarded header only when the *direct
peer* is a configured trusted proxy, and then take the right-most hop that is
itself not a trusted proxy (the address the nearest trusted proxy observed).

Pure and dependency-free so both the auth limiter and any future limiter share
one definition instead of drifting.
"""
from __future__ import annotations

from ipaddress import ip_address, ip_network

from fastapi import Request


def _parse_trusted(trusted: list[str]) -> list:
    networks = []
    for entry in trusted:
        entry = (entry or "").strip()
        if not entry:
            continue
        try:
            networks.append(ip_network(entry, strict=False))
        except ValueError:
            # A malformed entry is ignored rather than raising: a typo in the
            # allow-list must not take authentication down. It simply trusts
            # nothing for that line.
            continue
    return networks


def _is_trusted(host: str, networks: list) -> bool:
    try:
        addr = ip_address(host)
    except ValueError:
        return False
    if addr.is_loopback and not networks:
        # Without an explicit allow-list, a loopback peer is still trusted:
        # that is the local dev proxy (Next.js) and the test client, and the
        # forwarded header there is written by us, not the client.
        return True
    return any(addr in net for net in networks)


def resolve_client_ip(request: Request, trusted_proxies: list[str] | None = None) -> str:
    """Return the caller address a limiter may safely key on.

    ``trusted_proxies`` is an explicit allow-list of proxy IPs/CIDRs. When it is
    empty, only a loopback peer has its ``X-Forwarded-For`` honoured (the local
    dev stack); every other peer is keyed by its own address.
    """
    peer = request.client.host if request.client else "unknown"
    networks = _parse_trusted(trusted_proxies or [])
    if not _is_trusted(peer, networks):
        return peer

    forwarded = request.headers.get("x-forwarded-for", "")
    if not forwarded:
        return peer
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    for hop in reversed(hops):
        if not _is_trusted(hop, networks):
            return hop
    return hops[0] if hops else peer
