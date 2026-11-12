"""Customer website generator.

Derived from MeasuredField Studio 0.1 (13_MEASUREDFIELD_STUDIO_MVP_20261109/app.py:
``validate``, ``STYLE``, ``WEBSITE``, ``write_site``). The Studio wrote files to disk and
embedded an inline script; here the same template is a pure function from a validated
profile to (html, css) so a rendered site can be stored as an immutable version and served
from shared infrastructure under a strict Content-Security-Policy. The structured intake
fields (service + timing) come from the Shared Learning Hub's synthetic intake skill.

Every owner-supplied value is HTML-escaped. Colors are chosen from a fixed palette, never
passed through, so no owner input reaches CSS.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone

COLORS = {"pine": "#286854", "ocean": "#187582", "copper": "#a55c36", "navy": "#344c76",
          "plum": "#6a4470", "slate": "#3f5560"}
INDUSTRIES = ("cleaning", "construction", "landscaping", "food", "wellness", "professional", "retail", "other")
RESPONSE_WINDOWS = {
    "no_promise": "We'll follow up to discuss next steps.",
    "one_business_day": "We aim to respond within one business day.",
    "two_business_days": "We aim to respond within two business days.",
    "three_business_days": "We aim to respond within three business days.",
}
TIMINGS = {"this_week": "This week", "this_month": "This month", "planning": "Just planning"}
DEFAULT_HEADLINES = {
    "cleaning": ("A cleaner home. More room for life.", "Thoughtful, dependable cleaning services in {city}."),
    "construction": ("Make more of the place you call home.", "Careful remodeling and construction around {city}."),
    "landscaping": ("Outdoor spaces worth coming home to.", "Landscaping and grounds care in {city}."),
    "food": ("Made with care, served with pride.", "Fresh food and friendly service in {city}."),
    "wellness": ("Feel better, with people who listen.", "Personal wellness services in {city}."),
    "professional": ("Clear advice. Dependable work.", "Professional services for people and businesses in {city}."),
    "retail": ("Good things, chosen with care.", "A local shop serving {city}."),
    "other": ("Local service. Personal attention.", "A professional local team serving {city}."),
}
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,48}[a-z0-9])$")
RESERVED_SLUGS = {"www", "app", "api", "admin", "mail", "static", "assets", "operator", "login", "sites", "status"}


class ProfileError(ValueError):
    pass


def e(value) -> str:
    return html.escape(str(value), quote=True)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")
    return slug if len(slug) >= 3 else "site-" + (slug or "new")


def validate_slug(slug: str) -> str:
    slug = (slug or "").strip().lower()
    if not SLUG_RE.fullmatch(slug) or slug in RESERVED_SLUGS or "--" in slug:
        raise ProfileError("Use 3 to 50 letters, numbers or hyphens for the site address.")
    return slug


def _text(raw: dict, key: str, limit: int) -> str:
    value = raw.get(key, "")
    if not isinstance(value, str):
        raise ProfileError("Unexpected value for " + key + ".")
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value).strip()
    if len(value) > limit:
        raise ProfileError(key.replace("_", " ").capitalize() + " is too long.")
    return value


def parse_services(raw) -> list[dict]:
    """Accepts ``Name | optional description`` lines, or a list of dicts."""
    items: list[dict] = []
    if isinstance(raw, str):
        for line in raw.splitlines():
            if not line.strip():
                continue
            name, _, description = line.partition("|")
            items.append({"name": name.strip(), "description": description.strip()})
    elif isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict):
                items.append({"name": str(entry.get("name", "")).strip(),
                              "description": str(entry.get("description", "")).strip()})
            else:
                items.append({"name": str(entry).strip(), "description": ""})
    else:
        raise ProfileError("List your services, one per line.")
    items = [i for i in items if i["name"]]
    if not 1 <= len(items) <= 8:
        raise ProfileError("Provide 1 to 8 services, one on each line.")
    for i in items:
        if len(i["name"]) > 80 or len(i["description"]) > 240:
            raise ProfileError("A service name or description is too long.")
    return items


def validate_profile(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ProfileError("Expected website details.")
    p = {
        "name": _text(raw, "name", 90),
        "city": _text(raw, "city", 100),
        "industry": _text(raw, "industry", 40) or "other",
        "color": _text(raw, "color", 20) or "ocean",
        "email": _text(raw, "email", 120),
        "phone": _text(raw, "phone", 40),
        "about": _text(raw, "about", 800),
        "headline": _text(raw, "headline", 90),
        "intro": _text(raw, "intro", 240),
        "response_window": _text(raw, "response_window", 30) or "no_promise",
    }
    if not p["name"] or not p["city"]:
        raise ProfileError("Business name and service area are required.")
    if p["industry"] not in INDUSTRIES:
        raise ProfileError("Choose a supported business type.")
    if p["color"] not in COLORS:
        raise ProfileError("Choose a supported color.")
    if p["response_window"] not in RESPONSE_WINDOWS:
        raise ProfileError("Choose a supported response window.")
    if not p["phone"] and not p["email"]:
        raise ProfileError("Provide a business email or phone number.")
    if p["email"] and not EMAIL_RE.fullmatch(p["email"]):
        raise ProfileError("Enter a valid business email.")
    if p["phone"] and not re.fullmatch(r"[0-9+()\-. x]{7,40}", p["phone"]):
        raise ProfileError("Enter a valid phone number.")
    p["services"] = parse_services(raw.get("services", ""))
    p["structured_intake"] = raw.get("structured_intake") in (True, "on", "true", "1", 1)
    return p


def services_as_text(profile: dict) -> str:
    return "\n".join(s["name"] + (" | " + s["description"] if s.get("description") else "")
                     for s in profile.get("services", []))


STYLE = """*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;font:16px/1.6 system-ui,-apple-system,"Segoe UI",Arial,sans-serif;color:#1b343a;background:#fff}
:root{--accent:ACCENT}a{color:var(--accent)}.wrap{max-width:1100px;margin:auto;padding:0 22px}
a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.skip{position:absolute;left:-9999px}.skip:focus{left:12px;top:12px;background:#fff;padding:10px;z-index:9}
header{background:#fff;border-bottom:1px solid #e0e8e6}.nav{display:flex;align-items:center;justify-content:space-between;gap:18px;min-height:80px}
.logo{display:flex;align-items:center;gap:10px;font-size:1.25rem;font-weight:850;letter-spacing:-.04em;text-decoration:none;color:#18353c}
.mark{border-radius:13px;background:var(--accent);color:white;width:43px;height:43px;display:grid;place-items:center;font-size:.91rem}
nav{display:flex;gap:17px;flex-wrap:wrap}nav a{font-weight:650;text-decoration:none;color:#3b5359}
.hero{padding:100px 0 92px;background:linear-gradient(120deg,#e4f0ed,#fcf9f2)}
.eyebrow{text-transform:uppercase;font-size:.8rem;letter-spacing:.16em;font-weight:850;color:var(--accent)}
h1{font-size:clamp(2.4rem,6vw,4.8rem);letter-spacing:-.05em;line-height:1.07;max-width:800px;margin:18px 0}
.hero p{font-size:1.2rem;color:#4d6669;max-width:720px}.btn,button{display:inline-block;background:var(--accent);color:white;border:0;border-radius:9px;font:750 1rem system-ui;padding:14px 20px;text-decoration:none;cursor:pointer}
section{padding:76px 0}h2{font-size:clamp(1.9rem,4vw,2.7rem);letter-spacing:-.04em;line-height:1.15}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:17px}.tile{border:1px solid #dce9e7;padding:25px;border-radius:14px}.tile h3{margin:0 0 8px}.tile p{margin:0;color:#536a6d}
.about{background:#edf5f2}.about p{max-width:720px;font-size:1.09rem}
.contact{display:grid;grid-template-columns:1fr 1fr;gap:38px}label{display:block;font-weight:700;margin:15px 0 6px}
input,textarea,select{font:inherit;width:100%;min-height:46px;padding:11px;border:1px solid #b7ceca;border-radius:9px;background:#fff}textarea{min-height:108px}
.hidden{position:absolute;left:-9999px}#status{min-height:25px;display:block;margin:10px 0;font-weight:650}.fine{font-size:.85rem;color:#5d7275}
footer{background:#18343a;color:#d7e7e4;padding:28px 0;font-size:.86rem}
@media(max-width:720px){.nav{flex-direction:column;align-items:flex-start;padding:18px 0}.hero{padding:70px 0}section{padding:57px 0}.grid,.contact{grid-template-columns:1fr}}
"""

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="{description}"><title>{name} | {city}</title><link rel="stylesheet" href="{asset_base}style.css"></head>
<body><a class="skip" href="#top">Skip to content</a><header><div class="wrap nav"><a class="logo" href="#top"><span class="mark" aria-hidden="true">{initials}</span>{name}</a><nav aria-label="Main menu"><a href="#services">Services</a><a href="#about">About</a><a href="#contact">Contact</a></nav></div></header>
<main id="top"><div class="hero"><div class="wrap"><div class="eyebrow">Serving {city}</div><h1>{headline}</h1><p>{intro}</p><a href="#contact" class="btn">Request a conversation</a></div></div>
<section id="services"><div class="wrap"><div class="eyebrow">Our services</div><h2>How we can help</h2><div class="grid">{cards}</div></div></section>
<section id="about" class="about"><div class="wrap"><div class="eyebrow">Why choose us</div><h2>Great work starts with listening.</h2><p>{about}</p></div></section>
<section id="contact"><div class="wrap contact"><div><div class="eyebrow">Get in touch</div><h2>Let's talk about what you need.</h2><p>Tell us a little about what you have in mind. {response_notice}</p><p><strong>Service area:</strong> {city}</p>{contact}</div>
<form id="lead" method="post" action="{asset_base}api/lead" novalidate><label for="name">Your name *</label><input id="name" name="name" required maxlength="100" autocomplete="name"><label for="email">Email *</label><input type="email" name="email" id="email" required maxlength="120" autocomplete="email"><label for="phone">Phone (optional)</label><input type="tel" name="phone" id="phone" maxlength="40" autocomplete="tel">{intake}<label for="message">How can we help? *</label><textarea id="message" name="message" required maxlength="2000"></textarea>
<label class="hidden" for="company_site">Leave this field empty</label><input class="hidden" id="company_site" name="company_site" tabindex="-1" autocomplete="off">
<button type="submit">Send inquiry</button><span id="status" role="status" aria-live="polite"></span><p class="fine">We use what you send only to reply to your request.</p></form></div></section>
</main><footer><div class="wrap">&copy; {year} {name}. All rights reserved.</div></footer>
<script src="{asset_base}site.js" defer></script></body></html>
"""

# Served as a static file for every customer site; contains no tenant data.
SITE_JS = """(function(){
var f=document.getElementById('lead');if(!f)return;var s=document.getElementById('status');
var base=f.getAttribute('action').replace(/api\\/lead$/,'');
function beacon(kind){try{fetch(base+'api/event',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:kind}),keepalive:true})}catch(e){}}
beacon('page_view');var started=false;
f.addEventListener('input',function(){if(!started){started=true;beacon('form_start')}});
f.addEventListener('submit',async function(ev){ev.preventDefault();s.textContent='Sending\\u2026';
try{var r=await fetch(f.getAttribute('action'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(f)))});
var d=await r.json().catch(function(){return{}});
if(!r.ok)throw Error(d.error||'Please try again or contact us directly.');
s.textContent=d.preview?'Preview only: this inquiry was not saved.':'Thanks! Your inquiry was received.';f.reset();started=false}
catch(ex){s.textContent=ex.message}})})();
"""


def render(profile: dict, *, asset_base: str = "/") -> tuple[str, str]:
    """Render a validated profile. ``asset_base`` lets the same page run in a preview path."""
    default_headline, default_intro = DEFAULT_HEADLINES[profile["industry"]]
    headline = profile.get("headline") or default_headline
    intro = profile.get("intro") or default_intro.format(city=profile["city"])
    cards = "".join(
        '<article class="tile"><h3>' + e(s["name"]) + "</h3><p>" +
        e(s.get("description") or "Get in touch to discuss options, availability and next steps.") +
        "</p></article>" for s in profile["services"])
    contact = ""
    if profile.get("phone"):
        contact += '<p>Call: <a href="tel:' + e(re.sub(r"[^\d+]", "", profile["phone"])) + '">' + e(profile["phone"]) + "</a></p>"
    if profile.get("email"):
        contact += '<p>Email: <a href="mailto:' + e(profile["email"]) + '">' + e(profile["email"]) + "</a></p>"
    intake = ""
    if profile.get("structured_intake"):
        options = "".join('<option value="' + e(s["name"]) + '">' + e(s["name"]) + "</option>" for s in profile["services"])
        timing = "".join('<option value="' + k + '">' + e(v) + "</option>" for k, v in TIMINGS.items())
        intake = ('<label for="service">Which service? (optional)</label><select id="service" name="service"><option value="">Not sure yet</option>' + options +
                  '</select><label for="timing">When? (optional)</label><select id="timing" name="timing"><option value="">No date in mind</option>' + timing + "</select>")
    page = PAGE.format(
        name=e(profile["name"]), city=e(profile["city"]),
        initials=e("".join(x[0].upper() for x in profile["name"].split()[:2])),
        headline=e(headline), intro=e(intro),
        description=e(profile["name"] + " — " + intro),
        cards=cards, intake=intake,
        about=e(profile.get("about") or profile["name"] + " brings care, clear communication and attention to detail to every job."),
        contact=contact, response_notice=e(RESPONSE_WINDOWS[profile["response_window"]]),
        year=str(datetime.now(timezone.utc).year), asset_base=e(asset_base),
    )
    return page, STYLE.replace("ACCENT", COLORS[profile["color"]])
