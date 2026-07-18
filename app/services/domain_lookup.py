import asyncio
import re
from urllib.parse import urlparse

import dns.resolver

DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE,
)
RECORD_TYPES = ("A", "AAAA", "NS", "MX", "TXT")


def normalize_domain(value: str) -> str:
    candidate = value.strip().lower()
    if "://" in candidate:
        candidate = urlparse(candidate).hostname or ""
    candidate = candidate.rstrip(".")
    if not DOMAIN_PATTERN.fullmatch(candidate):
        raise ValueError("Provide a valid public domain name, such as example.com")
    return candidate


def _lookup(domain: str) -> dict[str, list[str]]:
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = ["1.1.1.1", "1.0.0.1"]
    resolver.timeout = 2
    resolver.lifetime = 4
    records: dict[str, list[str]] = {}
    for record_type in RECORD_TYPES:
        try:
            answers = resolver.resolve(domain, record_type, raise_on_no_answer=False)
            records[record_type] = [answer.to_text().strip('"') for answer in answers] if answers.rrset else []
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.exception.Timeout, dns.resolver.NoNameservers):
            records[record_type] = []
    return records


def provider_hint(records: dict[str, list[str]]) -> tuple[str | None, str | None]:
    nameservers = " ".join(records.get("NS", [])).lower()
    if "cloudflare.com" in nameservers:
        return "Cloudflare", "DNS and possibly CDN/proxy; the origin host may be hidden"
    if "awsdns" in nameservers:
        return "Amazon Route 53", "DNS provider; this does not prove the origin host"
    if "domaincontrol.com" in nameservers:
        return "GoDaddy", "DNS provider; this does not prove the origin host"
    if "digitalocean.com" in nameservers:
        return "DigitalOcean", "DNS provider; this does not prove the origin host"
    return None, None


async def lookup_domain(value: str) -> tuple[str, dict[str, list[str]], str | None, str | None]:
    domain = normalize_domain(value)
    records = await asyncio.to_thread(_lookup, domain)
    hint, scope = provider_hint(records)
    return domain, records, hint, scope
