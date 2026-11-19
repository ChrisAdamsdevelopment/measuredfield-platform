# MF-089: Google Search Console canonical sitemap and beta-signup discovery

Date: November 18, 2026. User requested verified Search Console sitemaps and public-page indexing in PBC Google account.

## Verified account and properties
- Local authorized PBC Google connection and dedicated Chrome Profile 10 belong to chris@prescottsbestconstruction.com.
- Search Console owner properties sc-domain:spectracleanse.com and sc-domain:spectracleanse.app independently verified using the official Google Search Console API. The other Chrome profiles were logged into other accounts and lacked access.
- The primary public website is https://spectracleanse.com. Its robots.txt advertises https://spectracleanse.com/sitemap.xml and the sitemap is publicly readable and parseable.
- https://spectracleanse.app currently serves a mirror copy of the marketing site (HTTP 200), with the same sitemap XML containing canonical spectracleanse.com URLs and robots.txt pointing to the .com sitemap. Do not submit duplicate .app URLs for independent indexing or create competing sitemap entries: .com is canonical. Future .app 301 consolidation should be reviewed separately for authentication and domain implications.

## Search Console actions, verified
- Before: neither Search Console property had any submitted sitemaps (official sitemaps.list API).
- In actual signed-in PBC Chrome profile, submitted the full URL https://spectracleanse.com/sitemap.xml to sc-domain:spectracleanse.com. Search Console displayed "Sitemap submitted successfully" on November 18, 2026. The **initial** UI row showed "Couldn't fetch", but follow-up official Sitemaps API **confirmed successfully downloaded** at 2026-11-18T15:48:22.835Z, type "sitemap", isPending false, **0 warnings, 0 errors**, seven public URLs submitted. The aggregate sitemap Indexed count was initially 0 while separate URL Inspection independently proved 6 already indexed; do not equate that provisional aggregate with live indexing status.
- Independent Search Console URL Inspection API for .com: homepage PASS "Submitted and indexed", crawled Nov 17 UTC; /platform PASS indexed, crawled Nov 18 UTC; /assessment PASS indexed, crawled Nov 16 UTC. /signup was "URL is unknown to Google" before this code change, so should be submitted for discovery.

## Narrow source change MF-089
- Add /signup to existing canonical production sitemap (now 8 public URLs). Preserve /, /platform, /assessment, /visibility-preview, /watch/measuredfield-business-launch, /privacy, /terms.
- Add exact https://spectracleanse.com/signup canonical link to the public beta signup template (using the configured production public_base_url). An .app mirror will use the .com canonical.
- Keep /login, /app, /operator, /accept-invite, /signup/thanks and sensitive tenant/customer routes OUT of sitemap.
- Regression checks production XML sitemap contains signup and excludes private URLs; signup page rendered as HTTP 200 with correct canonical. Full disposable restricted PostgreSQL, code lint, tracked secret scan, independent review before deploy.
- After deployment, use Search Console URL Inspection -> /signup -> Test live URL and Request indexing if available. Google decides indexing and processing time. The standard URL Inspection API is read-only; there is no general API for requesting arbitrary page indexing, and Google Indexing API is not appropriate for regular marketing pages.

## Evidence on D
- D:/PrescottsBestConstruction/07_PROJECT_PLANS/MF_GSC_CONNECTED_PROPERTIES_20261118.json
- D:/PrescottsBestConstruction/07_PROJECT_PLANS/MF_GSC_PBC_PROFILE10_SITEMAPS_20261118.png
- D:/PrescottsBestConstruction/07_PROJECT_PLANS/MF_GSC_COM_SITEMAP_AFTER_SUBMIT_20261118.png and .txt
- D:/PrescottsBestConstruction/07_PROJECT_PLANS/MF_GSC_SITEMAP_PRECHECK_20261118.py
- D:/PrescottsBestConstruction/07_PROJECT_PLANS/MF_GSC_CONNECTED_PROPERTIES_20261118.mjs
