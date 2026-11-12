# Multi-host routing security — synthetic threat model (MF-003)

## Trust boundary

The platform and public customer sites share one ASGI listener. An incoming
HTTP request first crosses HostRouter. The raw Host authority must be unique,
ASCII, syntactically valid, free from control characters and ambiguous
credentials, and have an optional numeric TCP port (1–65535).

Only the normalized hostname is used to choose the private MeasuredField app
or resolve a customer's site. The raw authority remains available to origin
checks so an Origin cannot silently mismatch a caller-specified port.

Malformed and duplicated Host fields produce a 400 before either app handles
the request. The health endpoint is available on any valid hostname so Render
can probe the service before customer DNS is configured.

## Threats addressed

- Duplicate or mixed-case Host headers selecting ambiguous tenants
- Invalid/non-numeric/overflow ports (formerly silently dropped with split(':')[0])
- Credential syntax, Unicode, control characters, whitespace, URI delimiters
- Bracketed IPv6 authority parsing and IDN punycode canonicalization
- Customer-site routes consuming the same normalized hostname used for dispatch

All tests use synthetic hostnames and a stub ASGI application; no real DNS,
network registration, TLS, third-party credentials or customer data.

## Remaining work before public deployment

- Ensure the edge proxy rejects duplicate/malformed Host headers consistently.
- Require verified DNS/TLS ownership before activating any custom domain.
- Confirm session cookies are host-only on a separate registrable platform domain.
- Test CNAME takeover/reclamation and subdomain ownership changes against a
  disposable live staging environment.
- Configure trusted client-IP attribution at the Render/edge boundary.
- Run complete PostgreSQL-backed regression suite and independent review.

The routing checks do not prove safe public-domain provisioning or DNS/TLS.
