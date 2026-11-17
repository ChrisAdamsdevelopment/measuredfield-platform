"""Pure module catalog: product intentions, NOT subscription grants or deployed integrations.

Never use this registry as authorization. Actual entitlements are a server-side,
verified billing + organization permission decision, implemented separately.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ModuleSpec:
    id: str
    name: str
    subtitle: str
    description: str
    outcome: str
    stage: str
    capabilities: tuple[str, ...]
    icon: str
    path: str | None = None
    live_provider_enabled: bool = False
    desktop_agent_enabled: bool = False

    def serialize(self) -> dict:
        data = asdict(self)
        data["capabilities"] = list(self.capabilities)
        return data


MODULES = (
    ModuleSpec(
        id="local-search", name="Local Search Map", subtitle="Geographic rank comparison",
        description="Compare owner-imported SERP positions by search phrase, coordinate and city; see competitors and next-step suggestions without invented results.",
        outcome="Spot where your local visibility differs and what to investigate first.",
        stage="local_preview", icon="map-pin", path="/app/modules/local-search",
        capabilities=("Coordinate samples", "City comparisons", "Competitor gaps",
                      "Evidence-aware advice", "CSV import")),
    ModuleSpec(
        id="visibility", name="AI Visibility Lab", subtitle="Research & discovery",
        description="Measure appearances in AI answers and compare them with organic search demand.",
        outcome="Know where customers discover your business and where they do not.",
        stage="local_preview", icon="radar", path="/app/modules/visibility",
        capabilities=("Question library", "Google Search Console CSV analysis",
                      "Full experiment plan", "Evidence provenance", "Tracked opportunities")),
    ModuleSpec(
        id="computer-connect", name="Computer Connect", subtitle="Permissioned companion",
        description="Owner-approved Windows companion pilot for selected folders and reviewed edits; general ChatGPT remote computer access is not yet released.",
        outcome="Work with your own approved project files without handing over unrestricted control.",
        stage="private_pilot", icon="monitor", path="/app/computers",
        capabilities=("Device pairing", "Scoped read-only grants", "Per-action approval",
                      "Auditable sessions", "Revocation and emergency stop")),
    ModuleSpec(
        id="growth", name="Next-Step Intelligence", subtitle="Evidence to action",
        description="Turn imported geographic rank comparisons into prioritized, explainable owner-reviewed next steps.",
        outcome="Know what to investigate first, and why the evidence supports that priority.",
        stage="local_preview", icon="trending-up", path="/app/modules/local-search",
        capabilities=("Evidence-backed priorities", "Competitor gap review",
                      "Data gaps", "Owner-reviewed next steps")),
    ModuleSpec(
        id="lead-rescue", name="Lead Rescue", subtitle="Opportunity recovery",
        description="Local owner preview for identifying neglected inquiries and follow-ups from the workspace records.",
        outcome="Focus attention on customers who are already waiting to hear back.",
        stage="local_preview", icon="phone-forwarded", path="/app/lead-rescue",
        capabilities=("Lead review", "Follow-up gaps", "Priority suggestions", "Human-approved action")),
    ModuleSpec(
        id="operations", name="Customer Operations", subtitle="Inquiries to projects",
        description="Extend the platform's existing inquiry, project and estimate workflows.",
        outcome="Keep customers, estimates and follow-ups organized.",
        stage="existing_platform", icon="users",
        capabilities=("Inquiry desk", "Customer records", "Project coordination",
                      "Human-approved follow-up")),
    ModuleSpec(
        id="website-studio", name="Website Studio", subtitle="Build & improve",
        description="Connect existing draft and preview tooling to controlled website changes.",
        outcome="Improve the business website without losing track of versions.",
        stage="existing_platform", icon="layout-dashboard",
        capabilities=("Brand concepts", "Private site preview", "Version review",
                      "Approval-gated publishing")),
)


def catalog() -> list[dict]:
    return [module.serialize() for module in MODULES]


def get_module(module_id: str) -> ModuleSpec | None:
    return next((item for item in MODULES if item.id == module_id), None)


def connection_policy() -> dict:
    """Non-executable design contract for a future user-installed companion."""
    return {
        "status": "not_connected",
        "enabled": False,
        "local_companion_installed": False,
        "default_access": "deny",
        "grant_scope": "per_device_per_app_per_action",
        "allowed_without_approval": [],
        "requirements": [
            "Owner-installed and explicitly paired client",
            "Separate tenant/device identity and short-lived scoped credentials",
            "No unsolicited inbound ports; mutually authenticated outbound channel",
            "Per-action confirmation with preview of files/destinations",
            "No unrestricted shell command or remote-desktop access by default",
            "Append-only action audit, revocation and immediate stop",
            "Per-module entitlement and device/user permission checks",
        ],
    }
