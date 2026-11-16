# MF-026 — Lead Rescue navigation integration

Date: 2026-11-15 Arizona. Scope: only app/templates/app/base.html and this story's tests/docs.

MF-025 already delivered an authenticated local read-only inquiry prioritization page at `/app/lead-rescue`. This small integration adds an obvious sidebar link `Lead Rescue (local preview)` alongside the modules preview link. It uses the same `settings.is_production` gating as other development-only modules, with accessible normal anchor behavior and `aria-current="page"` on the active page.

Verified by template-rendered owner navigation tests for development vs production vs other active sections; no database or customer records are altered and no production deployment is authorized. MF-025 remains subject to independent security review, entitlements and browser/mobile QA before a publicly marketed release.
