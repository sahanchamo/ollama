import asyncio
import re
from urllib.parse import urlparse

import dns.resolver

DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE,
)
RECORD_TYPES = ("A", "AAAA", "NS", "MX", "TXT")
DOMAIN_IN_TEXT = re.compile(r"(?<![a-z0-9-])(?:https?://)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(?![a-z0-9-])", re.IGNORECASE)
LOOKUP_INTENT = re.compile(
    r"\b(host(?:ing|ed)?|provider|dns|name\s*server|nameserver|mx\s*record|a\s*record|ip\s*address|who\s+hosts|domain\s+lookup)\b",
    re.IGNORECASE,
)


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


async def live_domain_context(question: str) -> str | None:
    """Return limited public DNS context only when a user explicitly asks for it."""
    if not LOOKUP_INTENT.search(question):
        return None
    match = DOMAIN_IN_TEXT.search(question)
    if match is None:
        return None
    try:
        domain, records, hint, scope = await lookup_domain(match.group(0))
    except ValueError:
        return None
    populated = [f"{record_type}: {', '.join(values)}" for record_type, values in records.items() if values]
    details = "\n".join(populated) or "No public DNS records returned."
    provider = hint or "No provider could be identified from the public DNS records."
    caveat = scope or "DNS records do not necessarily identify the origin hosting provider."
    return (
        "LIVE DOMAIN LOOKUP (use this as current factual context; do not claim more than it proves):\n"
        f"Domain: {domain}\n{details}\nProvider hint: {provider}\nImportant: {caveat}"
    )
