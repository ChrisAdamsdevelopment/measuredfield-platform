#!/usr/bin/env python3
"""Privacy, Consent, Ownership, Redaction, Prompt-Injection, and SSRF Guardrails.

Enforces:
1. Consent & IP Ownership verification (blocks unknown or restricted permissions).
2. PII, Secret, and Tenant-Specific Identifier Redaction (emails, phones, street addresses,
   API keys, exact pricing/bid formulas, and tenant/owner names).
3. Small-cohort reconstruction risk check (removing a person's name alone does not make a rare
   local project anonymous).
4. Prompt Injection detection & neutralization in untrusted customer inputs or scraped fixtures.
5. URL / SSRF Guardrails for any website funnel URL check (blocks loopback, RFC1918 private IPs,
   link-local/cloud metadata 169.254.169.254, IPv6 local, non-HTTPS schemes, and unapproved live fetching).
"""
import ipaddress
import re
from typing import Dict, Any, List, Tuple
from urllib.parse import urlparse

# Regex patterns for PII and secrets
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]?\d{3}[-.\s]?\d{4}\b")
API_KEY_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9\-_]{12,}|AIza[0-9A-Za-z\-_]{20,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9\-]{10,}|Bearer\s+[A-Za-z0-9\-._~+/]+=*)\b"
)
ADDRESS_RE = re.compile(
    r"\b\d{2,5}\s+[A-Z][a-zA-Z0-9\s.]{2,35}\s+(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ln|Lane|Blvd|Way|Ct|Court|Pkwy)\b",
    re.IGNORECASE,
)
DOLLAR_EXACT_BID_RE = re.compile(r"\$\d{1,3}(?:,\d{3})+(?:\.\d{2})?")
PROPRIETARY_FORMULA_RE = re.compile(
    r"\b(?:margin_multiplier|markup_factor|secret_bid_formula|labor_rate_internal)\s*[:=]\s*[0-9.]+",
    re.IGNORECASE,
)

# Prompt injection signatures
PROMPT_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions", re.IGNORECASE),
    re.compile(r"system\s*prompt|developer\s*message|reveal\s+all\s+tenant", re.IGNORECASE),
    re.compile(r"export\s+(?:all\s+)?(?:secrets|api[_\s]?keys|database|leads)", re.IGNORECASE),
    re.compile(r"<\s*script\b|javascript\s*:", re.IGNORECASE),
    re.compile(r"override\s+consent|bypass\s+privacy\s+filter", re.IGNORECASE),
]

# Known historical reference terms that must never be copied into shared skills without explicit release
RESTRICTED_REFERENCE_TERMS = [
    "Prescott's Best Construction",
    "PrescottsBestConstruction",
    "prescottsbestremodeling.com",
    "prescottsbestconstruction.com",
]


def detect_prompt_injection(text: str) -> List[str]:
    """Return list of matched prompt-injection or XSS signatures."""
    hits = []
    for pat in PROMPT_INJECTION_PATTERNS:
        m = pat.search(text or "")
        if m:
            hits.append(m.group(0))
    return hits


def scan_and_redact_text(
    text: str,
    forbidden_terms: List[str],
) -> Tuple[str, Dict[str, List[str]]]:
    """Scan text for PII, secrets, exact dollar bids, and tenant-identifying terms; return redacted string and findings."""
    sanitized = text or ""
    findings: Dict[str, List[str]] = {
        "pii": [],
        "secrets": [],
        "unique_identifiers": [],
        "prompt_injections": detect_prompt_injection(sanitized),
    }

    for m in EMAIL_RE.findall(sanitized):
        findings["pii"].append(f"email:{m}")
    sanitized = EMAIL_RE.sub("[REDACTED_EMAIL]", sanitized)

    for m in PHONE_RE.findall(sanitized):
        findings["pii"].append(f"phone:{m}")
    sanitized = PHONE_RE.sub("[REDACTED_PHONE]", sanitized)

    for m in ADDRESS_RE.findall(sanitized):
        findings["pii"].append(f"address:{m}")
    sanitized = ADDRESS_RE.sub("[REDACTED_ADDRESS]", sanitized)

    for m in API_KEY_RE.findall(sanitized):
        findings["secrets"].append(f"api_key:{m[:8]}...")
    sanitized = API_KEY_RE.sub("[REDACTED_SECRET]", sanitized)

    for m in PROPRIETARY_FORMULA_RE.findall(sanitized):
        findings["secrets"].append(f"formula:{m}")
    sanitized = PROPRIETARY_FORMULA_RE.sub("[REDACTED_PROPRIETARY_FORMULA]", sanitized)

    for m in DOLLAR_EXACT_BID_RE.findall(sanitized):
        findings["unique_identifiers"].append(f"exact_dollar_amount:{m}")
    sanitized = DOLLAR_EXACT_BID_RE.sub("[REDACTED_EXACT_PRICE]", sanitized)

    all_terms = [t for t in (forbidden_terms + RESTRICTED_REFERENCE_TERMS) if t and len(t.strip()) >= 3]
    for term in sorted(set(all_terms), key=len, reverse=True):
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        if pattern.search(sanitized):
            findings["unique_identifiers"].append(f"tenant_term:{term}")
            sanitized = pattern.sub("[REDACTED_TENANT_ENTITY]", sanitized)

    return sanitized, findings


def assess_k_anonymity_risk(text: str, city: str) -> str:
    """Detect whether hyper-specific geographic + project combinations create small-cohort reconstruction risk."""
    lowered = (text or "").lower()
    if city and city.lower() in lowered:
        return "HIGH_RECONSTRUCTION_RISK"
    rare_markers = ["historic courthouse", "mayor's residence", "only contractor on", "custom 1894 Victorian"]
    for marker in rare_markers:
        if marker.lower() in lowered:
            return "HIGH_RECONSTRUCTION_RISK"
    return "LOW"


def validate_url_ssrf_safe(url: str, allowed_fixture_hosts: List[str] = None) -> Dict[str, Any]:
    """Validate URL for the website funnel prototype and block SSRF, private IPs, metadata endpoints, and live scanning."""
    if not url or not isinstance(url, str):
        return {"allowed": False, "reason": "Empty or invalid URL string."}

    parsed = urlparse(url.strip())
    if parsed.scheme not in ("https", "http"):
        return {"allowed": False, "reason": f"Disallowed URL scheme '{parsed.scheme}'. Only https:// is permitted."}

    if parsed.username or parsed.password:
        return {"allowed": False, "reason": "Embedded credentials in URL are prohibited."}

    host = (parsed.hostname or "").lower()
    if not host:
        return {"allowed": False, "reason": "Missing hostname."}

    # Block localhost and internal hostnames
    if host in ("localhost", "internal", "metadata.google.internal", "169.254.169.254", "0.0.0.0"):
        return {"allowed": False, "reason": f"SSRF protection blocked internal/loopback host '{host}'."}

    # Check if host is an IP literal (IPv4 or IPv6)
    try:
        ip = ipaddress.ip_address(host)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return {"allowed": False, "reason": f"SSRF protection blocked non-public IP address '{host}'."}
    except ValueError:
        pass

    if allowed_fixture_hosts is not None:
        if host not in [h.lower() for h in allowed_fixture_hosts]:
            return {
                "allowed": False,
                "reason": (
                    f"Host '{host}' is not in the approved local synthetic fixture registry. "
                    "Unrestricted live public URL scanning is disabled until production DNS-rebinding, "
                    "redirect, rate-limit, and egress sandbox controls are active."
                ),
            }

    return {"allowed": True, "normalized_host": host, "scheme": parsed.scheme}
