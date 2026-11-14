# MeasuredField Track B Preview & Diagnostic Security Architecture (MF-014)

## 1. Purpose and Scope

Track B delivers the isolated public website diagnostic scanner, deterministic Scope & Readiness Explorer widget generator, and no-network concept preview renderer inside the canonical `mf_platform` repository:

- `app/schemas/__init__.py` & `app/schemas/preview.py` — Typed tri-state diagnostic observations (`DiagnosticFinding`, `DiagnosticObservationCard`), partial concept profiles (`PreviewConceptProfile`), validated widget specs (`WidgetSpec`), `adapt_to_sitegen_profile()`, `build_assessment_result_from_preview()`, and `improvements_from_preview_result()`.
- `app/services/scanner.py` — SSRF-hardened, IP-pinned static HTML scanner (`validate_ip_address()`, `validate_and_resolve_url()`, `PinnedPublicIPTransport`, `PinnedSyncBackend`, `inspect_public_url()`).
- `app/services/widgets.py` — Accessible, industry-adaptive Scope & Readiness Explorer (`default_widget_for_industry()`, `render_widgets_html()`, `WIDGET_CSS`).
- `app/services/preview_renderer.py` — CSP-compatible diagnostic card and interactive concept preview renderer (`render_diagnostic_card_html()`, `render_concept_preview()`, `DEMO_PREVIEW_JS`, `PREVIEW_CSP`).
- `tests/test_track_b_preview_suite.py` — Unit, security, accessibility, and regression test suite.

No modifications were made to shared database models (`app/models.py`), migrations (`migrations/`), routers (`app/routers/`), site lifecycle (`app/services/sites.py`, `app/services/sitegen.py`), or billing (`app/services/orders.py`, `app/services/payments.py`).

---

## 2. SSRF & DNS-Rebinding Defenses Implemented

All five failures and structural findings identified in `27_ARGON_TRACK_B_EXECUTABLE_AUDIT_20261114.md` have been remediated and verified with adversarial unit tests:

1. **Disjoint Exception Hierarchy (No `ValueError` Catch-All Swallowing)**:
   - `SSRFBlockedError` subclasses `ScannerError(Exception)` and is **not** a subclass of `ValueError`.
   - Direct IP literals (IPv4, bracketed IPv6, dotted octal/hex, and integer representations) are rejected before DNS resolution outside any `except ValueError` block. Even if a mocked resolver returns a public IP, any literal IP URL raises `SSRFBlockedError` prior to resolver invocation.
2. **Pre-Flight URL & Host Policy**:
   - Permits only `http` and `https` schemes on standard ports (`80` for `http`, `443` for `https`).
   - Rejects embedded userinfo (`user:pass@`), single-label hostnames, numeric TLDs, and internal/reserved suffixes (`.localhost`, `.local`, `.internal`, `.arpa`, `.corp`, `.lan`, `metadata.google.internal`, `instance-data`).
3. **Comprehensive IPv4/IPv6 Blocklist**:
   - `validate_ip_address()` rejects loopback (`127.0.0.0/8`, `::1/128`), RFC 1918 private networks, link-local and cloud metadata (`169.254.0.0/16`, `fe80::/10`, `fd00:ec2::254`), CGNAT and Alibaba metadata (`100.64.0.0/10`), IPv4-mapped IPv6 (`::ffff:0:0/96`), NAT64 (`64:ff9b::/96`, `64:ff9b:1::/48`), Teredo (`2001::/32`), 6to4 (`2002::/16`), documentation, benchmarking, multicast, unspecified, reserved, and any address where `not ip.is_global`.
   - If a DNS response contains multiple records and *any* record is non-global, `validate_and_resolve_url()` fails closed with `SSRFBlockedError`.
4. **Transport-Level IP Pinning & TLS SNI Preservation**:
   - `validate_and_resolve_url()` resolves DNS **once** per hop and returns a `PinnedTarget` with `pinned_ip`.
   - `build_pinned_request()` targets the numeric `pinned_ip` URL (`https://<pinned_ip>/...`), sets the `Host` header to the validated domain name, and attaches `extensions={"pinned_ip": ..., "sni_hostname": ...}`.
   - `PinnedPublicIPTransport` and `PinnedSyncBackend` verify that `connect_tcp` receives a numeric global IP literal (never a hostname that could trigger a second OS DNS lookup), verify the connected socket's `getpeername()` matches `pinned_ip` before TLS handshake or HTTP transmission, and pass `server_hostname=sni_hostname` to `start_tls()` so TLS SNI and X.509 certificate verification validate the original hostname.
   - `httpx.Client` runs with `trust_env=False` and `follow_redirects=False`.
5. **Independent Redirect Re-Validation**:
   - Up to `MAX_REDIRECTS = 3` HTTP `301/302/303/307/308` redirects are resolved relative to the canonical URL and passed through `validate_and_resolve_url()` and re-pinned before connecting.

---

## 3. Evidence Provenance & Tri-State Diagnostic Integrity

- **Tri-State Status (`observed` | `not_observed` | `unable_to_determine`)**:
  - When a site is unreachable, times out, returns an HTTP error status (e.g., `403`, `404`, `500`, `503`), or returns a non-HTML `Content-Type`, every diagnostic check is recorded as `unable_to_determine` with zero `not_observed` findings and zero fabricated obstacles.
- **Strict Contact Form Detection**:
  - `_StaticHTMLDiagnosticParser` ignores `<input>`, `<select>`, and `<textarea>` elements outside `<form>...</form>`, ignores `<form role="search">` and search-action forms, and ignores `type="search"` / `name="q"` inputs. A contact form is recorded as `observed` only when a `<form>` contains genuine contact/inquiry fields.
- **No Fabricated Obstacles or False `owner_stated` Labels**:
  - Completed static scans with all checks `observed` produce `obstacles = []` and `recommended_offer = "consult"`.
  - Scanner findings carry `evidence_status = "system_recorded"` (or `"unknown"` when `unable_to_determine`), never `"owner_stated"`.
  - `improvements_from_preview_result()` preserves `evidence_status` when constructing `Improvement` records.

---

## 4. Isolated No-Network Demo & CSP Compatibility

- `DEMO_PREVIEW_JS` performs local DOM updates only and contains zero network calls (`fetch`, `XMLHttpRequest`, `sendBeacon`, `/api/lead`, `/api/event`).
- `render_concept_preview()` rewrites the `#lead` form to `method="get" action="#contact" data-mf-demo-form="true"` and emits zero inline `style="..."` attributes or `<style>` tags, operating under `PREVIEW_CSP` (`default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self' data:; form-action 'none'`).
- Unconfirmed profile attributes (`city`, `contact_method`, `services`) are explicitly surfaced in the preview banner rather than fabricated.
