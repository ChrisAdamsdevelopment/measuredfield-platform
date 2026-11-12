"""The two-question assessment engine.

Question 1: "What are you doing in your business right now that you're most excited about?"
Question 2: "What's keeping you from taking that to the next level?"

The engine is deliberately deterministic and inspectable. It does four things:

1. names what the owner values (from answer 1),
2. organizes the stated obstacles (from answer 2),
3. states, for every obstacle, what evidence would confirm it and that we do not have
   that evidence yet, and
4. maps obstacles to MeasuredField capabilities, concrete next steps and one offer.

It never fetches the owner's website, never invents numbers, and says so when an answer
does not match anything it can help with. No language model is called here; the optional
model provider (see services/ai.py) is not part of this path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..vendor.hub_privacy_filter import detect_prompt_injection

ENGINE_VERSION = "rules-2026.11"


@dataclass(frozen=True)
class Obstacle:
    code: str
    label: str
    patterns: tuple[str, ...]
    capability: str
    what_we_do: str
    first_step: str
    evidence_needed: str
    in_scope: bool = True
    improvement_metric: str = ""


OBSTACLES: tuple[Obstacle, ...] = (
    Obstacle(
        "follow_up", "Inquiries and follow-up slip through the cracks",
        (r"follow.?up", r"los(e|ing) track", r"get back to", r"call(ing)? (people )?back",
         r"miss(ed|ing)? (calls?|leads?|messages?|inquir)", r"leads? (fall|slip|go cold)",
         r"forget", r"respond", r"voicemail", r"too many (messages|texts|emails)",
         r"slip(s|ping)? through", r"keep(ing)? up with (leads|customers|requests|inquir)"),
        "inquiry_desk",
        "Put every inquiry in one private list with a status, an owner and a follow-up date, so nothing depends on memory.",
        "Route your website's contact form into the inquiry desk and set a follow-up date on every new request.",
        "How many inquiries arrive each week and how long each waits for a first reply. We can only measure this once inquiries flow through the workspace.",
        improvement_metric="Share of inquiries with a recorded first response, and days to first response",
    ),
    Obstacle(
        "web_presence", "The website is missing, outdated or not bringing in inquiries",
        (r"web\s?site", r"web ?page", r"online presence", r"no site", r"landing page",
         r"looks? (old|dated|unprofessional)", r"facebook page", r"domain"),
        "website",
        "Build a clear, mobile-friendly site that says what you do and where, with an inquiry form that feeds your workspace. You approve it before it is published.",
        "Draft your site from your services and service area, preview it, and publish when you approve it.",
        "Whether visitors reach the site and whether they start or finish the contact form. Measured only after the site is live.",
        improvement_metric="Page views, contact-form starts and completed inquiries on the published site",
    ),
    Obstacle(
        "visibility", "Not enough of the right people are finding the business",
        (r"\bseo\b", r"google", r"market(ing)?", r"advertis", r"\bads?\b", r"visib", r"get(ting)? (found|noticed)",
         r"more (customers|clients|leads|business)", r"word of mouth", r"referr", r"reviews?", r"social media",
         r"find(ing)? (new )?(customers|clients)", r"awareness", r"reach"),
        "visibility",
        "Make your services and service area unmistakable on your site, then measure real search and inquiry activity before spending on promotion.",
        "Publish service pages that match what you actually sell, then connect Google Search Console (read-only) so we work from real search data.",
        "Your actual search impressions, clicks and where current customers come from. We have none of this until you authorize access.",
        improvement_metric="Search Console impressions and clicks for authorized properties (requires your authorization)",
    ),
    Obstacle(
        "quoting", "Estimates, quotes or proposals take too long",
        (r"quot(e|es|ing)", r"estimat", r"proposal", r"bid(s|ding)?", r"pricing (jobs|work)", r"invoice", r"paperwork for jobs"),
        "proposals",
        "Reusable, itemized proposals your customer can review and approve from a link, with the approval recorded.",
        "Turn your most common job into a proposal template and send the next one through the workspace.",
        "How long quotes take today and how many are approved. Recorded from the first proposal sent through the workspace.",
        improvement_metric="Proposals sent, approved and declined, with dates",
    ),
    Obstacle(
        "time_admin", "Admin work is eating the owner's time",
        (r"no time", r"not enough time", r"too busy", r"hours in (the|a) day", r"admin", r"paperwork", r"overwhelm",
         r"wear(ing)? (too )?many hats", r"do(ing)? everything myself", r"stretched", r"burn(ed|t)? out", r"manual(ly)?",
         r"spreadsheet", r"repetitive", r"bookkeeping"),
        "operations",
        "Find the two or three repeated tasks that cost the most time and replace them with a simple shared process.",
        "List where a typical inquiry goes from first contact to finished job; we mark the steps that repeat.",
        "Where your hours actually go. This starts as your own estimate and is labelled that way until the workspace records real activity.",
        improvement_metric="Count of inquiries, follow-ups and proposals handled in the workspace per week",
    ),
    Obstacle(
        "organization", "Information is scattered across tools, texts and paper",
        (r"scatter", r"all over the place", r"disorgani[sz]", r"organi[sz]ed", r"sticky notes?", r"notebook",
         r"too many (apps|tools|systems)", r"one place", r"keep(ing)? track", r"files?", r"documents?", r"system(s)?\b",
         r"process(es)?\b", r"software"),
        "records",
        "One private workspace for customers, jobs, documents and notes, with access levels for your team.",
        "Move active customers and their documents into the workspace and give each team member the right access.",
        "Which tools you use today and what lives where. Based on your description until records are in the workspace.",
        improvement_metric="Customers, projects and documents recorded in the workspace",
    ),
    Obstacle(
        "team", "Hiring, training or relying on staff is the constraint",
        (r"hir(e|ing)", r"staff", r"employee", r"\bteam\b", r"\bcrew\b", r"train(ing)?", r"good help", r"delegat", r"labor"),
        "team_access",
        "We do not recruit. We can make handoffs easier: shared job information, clear follow-up ownership and role-based access.",
        "Add your team to the workspace with staff access and assign follow-ups by name.",
        "How work is handed off today. Owner-described; the workspace records assignments once they are made there.",
        improvement_metric="Follow-ups assigned to and completed by team members",
    ),
    Obstacle(
        "money", "Cash flow, capital or pricing is the constraint",
        (r"cash ?flow", r"capital", r"\bmoney\b", r"financ", r"\bloan", r"fund(s|ing)?", r"afford", r"profit", r"margin",
         r"budget", r"expens", r"cost(s)? (too|so) much"),
        "none",
        "This is outside what our software fixes. We do not give financial advice. We can make sure inquiries and proposals are not being lost while you work on it.",
        "Talk to an accountant or lender about the financing question; ask us only about the inquiry and proposal side.",
        "Your financial records. We do not ask for them and do not estimate revenue or profit.",
        in_scope=False,
    ),
)

PRIORITIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("growth", "Growing demand for the work",
     (r"grow", r"more (customers|clients|jobs|bookings|orders)", r"expand", r"busy", r"demand", r"booked", r"scal", r"second location", r"new (location|market|area)")),
    ("new_offering", "A new service or product",
     (r"new (service|product|line|offering|menu|class|program)", r"launch", r"start(ed|ing)? (to )?offer", r"added?", r"introduc")),
    ("craft", "The quality of the work itself",
     (r"quality", r"craft", r"proud", r"\bwork we do\b", r"custom", r"detail", r"design", r"build(ing)?", r"result")),
    ("customers", "Relationships with customers",
     (r"customers?", r"clients?", r"relationship", r"repeat", r"referr", r"review", r"communit", r"word of mouth", r"loyal")),
    ("team", "The people on the team",
     (r"\bteam\b", r"\bcrew\b", r"staff", r"employee", r"hir(ed|ing)", r"train")),
    ("independence", "Building something of their own",
     (r"my own", r"own boss", r"independen", r"started (my|our|the) (business|company)", r"just start", r"full[- ]time", r"side (business|hustle)")),
)

CAPABILITY_NAMES = {
    "inquiry_desk": "Inquiry desk and follow-up",
    "website": "Business website",
    "visibility": "Search visibility, measured",
    "proposals": "Estimates and proposals",
    "operations": "Workflow simplification",
    "records": "Customer, project and document records",
    "team_access": "Team access and assignments",
    "none": "Outside our scope",
}


def _clean(text: str, limit: int) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text or "")
    return re.sub(r"[ \t]+", " ", text).strip()[:limit]


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+|;\s+", text)
    return [p.strip() for p in parts if len(p.strip()) >= 4]


def _matches(patterns: tuple[str, ...], text: str) -> int:
    return sum(1 for p in patterns if re.search(p, text, re.IGNORECASE))


def _quote_for(patterns: tuple[str, ...], sentences: list[str]) -> str:
    for s in sentences:
        if any(re.search(p, s, re.IGNORECASE) for p in patterns):
            return s[:220]
    return ""


@dataclass
class AssessmentInput:
    business_name: str
    excited: str
    blocker: str
    website: str = ""
    industry: str = "other"
    extra: dict = field(default_factory=dict)


def assess(data: AssessmentInput) -> dict:
    excited = _clean(data.excited, 1500)
    blocker = _clean(data.blocker, 1500)
    flagged = bool(detect_prompt_injection(excited + "\n" + blocker))

    excited_sentences = _sentences(excited) or [excited]
    blocker_sentences = _sentences(blocker) or [blocker]

    priorities = []
    for code, label, patterns in PRIORITIES:
        score = _matches(patterns, excited)
        if score:
            priorities.append({"code": code, "label": label, "score": score,
                               "in_their_words": _quote_for(patterns, excited_sentences)})
    priorities.sort(key=lambda p: -p["score"])
    priorities = priorities[:3]

    found = []
    for ob in OBSTACLES:
        score = _matches(ob.patterns, blocker)
        if score:
            found.append((score, ob))
    # The website answer is only relevant context if the owner did not give one.
    if not data.website and not any(ob.code == "web_presence" for _, ob in found):
        if re.search(r"online|internet|found", blocker, re.IGNORECASE):
            found.append((1, next(o for o in OBSTACLES if o.code == "web_presence")))
    found.sort(key=lambda pair: (-pair[0], not pair[1].in_scope))
    found = found[:4]

    obstacles = []
    for score, ob in found:
        obstacles.append({
            "code": ob.code,
            "label": ob.label,
            "in_their_words": _quote_for(ob.patterns, blocker_sentences),
            "evidence_status": "owner_stated",
            "evidence_label": "You told us this. We have not verified it.",
            "evidence_needed": ob.evidence_needed,
            "capability": ob.capability,
            "capability_name": CAPABILITY_NAMES[ob.capability],
            "what_we_do": ob.what_we_do,
            "first_step": ob.first_step,
            "in_scope": ob.in_scope,
            "metric": ob.improvement_metric,
            "match_strength": score,
        })

    in_scope = [o for o in obstacles if o["in_scope"]]
    understood = bool(in_scope)

    next_steps = [o["first_step"] for o in in_scope[:3]]
    if not understood:
        next_steps = [
            "Book a working session so we can ask a few follow-up questions.",
            "Bring one recent example of the problem: a missed call, a late quote, a lost job.",
        ]

    codes = {o["code"] for o in in_scope}
    if not understood:
        recommended = "consult"
        reason = ("We could not match what you described to something we can confidently help with, "
                  "so the honest next step is a conversation, not a package.")
    elif codes & {"web_presence", "follow_up", "visibility"}:
        recommended = "launch"
        reason = ("What you described centers on being found and handling the inquiries that result. "
                  "The website and inquiry desk setup addresses that directly.")
    else:
        recommended = "consult"
        reason = ("What you described is mostly about how the work is organized. "
                  "A working session is the right way to decide what, if anything, to build.")

    unknowns = [
        "How many inquiries you receive and how quickly they get a reply",
        "Where your current customers come from",
        "Website traffic and search performance (no analytics or Search Console access has been granted)",
        "Revenue, profit and return on any change. We do not estimate these",
    ]
    if data.website:
        unknowns.insert(0, "Anything about your current website. We recorded the address but did not visit or scan it")

    summary_value = (priorities[0]["label"].lower() if priorities
                     else "the part of the business you described")
    if understood:
        headline = f"You're energized by {summary_value}. Here is what seems to be in the way."
    else:
        headline = "Thank you. We want to understand this properly before suggesting anything."

    return {
        "engine": ENGINE_VERSION,
        "method": "Rule-based reading of your two answers. No AI model, website scan or outside data was used.",
        "headline": headline,
        "understood": understood,
        "needs_human_review": flagged or not understood,
        "priorities": priorities,
        "obstacles": obstacles,
        "next_steps": next_steps,
        "recommended_offer": recommended,
        "recommendation_reason": reason,
        "unknowns": unknowns,
        "out_of_scope": [o["label"] for o in obstacles if not o["in_scope"]],
        "disclaimer": ("This assessment reflects only what you wrote. Nothing here is a measured result, "
                       "a forecast or a guarantee."),
    }


def improvements_from(result: dict) -> list[dict]:
    """Turn in-scope obstacles into proposed improvements for a new workspace."""
    items = []
    for o in result.get("obstacles", []):
        if not o.get("in_scope"):
            continue
        items.append({
            "title": o["capability_name"] + ": " + o["label"],
            "detail": o["what_we_do"],
            "capability": o["capability"],
            "metric": o.get("metric", ""),
            "evidence_status": "owner_stated",
            "evidence_note": o["evidence_needed"],
        })
    return items
