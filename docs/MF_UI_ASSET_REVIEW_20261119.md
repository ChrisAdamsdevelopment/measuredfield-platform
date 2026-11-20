# MeasuredField UI Art Review — 2026-11-19

Owner-supplied image source directory:

`D:\PrescottsBestConstruction\Assets\GENERATED_CONCEPTS_INBOX`

**Do not relocate or delete originals.** The owner placed the MeasuredField
sheets in the existing PBC concepts inbox; all are borrowed solely for
MeasuredField's UI. Exact source hashes and crops are stored in:

`D:\GrowthEngineeringVenture\16_MEASUREDFIELD_LAUNCH_FOUNDATION_20261112\assets\ui_kit_review_20261119\ASSET_REVIEW_MANIFEST.json`

## Per-file disposition

| Source filename | Decision | Explanation |
|---|---|---|
| `mf_icons.png` | **USE selected glyphs** | Extracted 21 clean transparent 128px PNG icons, stripped tile backgrounds and captions. Existing production logo master remains authoritative. |
| `primary_secondary.png` | **DESIGN REFERENCE** | All labeled action buttons, toggles, search input, tabs and filters must be rendered as responsive, keyboard-friendly HTML/CSS using dynamic state. |
| `thesehavehardcodednumbers.png` | **DESIGN REFERENCE** | Fixed numbers, percentages, statuses, and progress would misrepresent real account evidence. |
| `approve_publish_buthashardcodedwords.png` | **DESIGN REFERENCE** | Fixed approval and publish claims, impact predictions, and example customer/site data cannot indicate real changes. |
| `full_of_hardcoded_words.png` | **DESIGN REFERENCE** | Example customer messages, automation run statuses, attachments, and result metrics cannot be actual workspace records. |
| `big_floating_picture_icons.png` | **SELECTED DECORATIVE ONLY** | Converted the neutral connected-account illustration to 21 KB WebP for the ChatGPT connection page. Retained other illustrative crops in QA folder, not deployed, because some show static counters or context-specific statuses. |

## Implemented components

- `app/static/media/ui_kit/*.png`: 21 reusable line-art icons; no captions,
  fixed metrics, or embedded button labels. Files are 128x128 transparent PNG.
- `app/static/media/ui_kit/connected_accounts.webp`: decorative 438x372
  pixel connected-account concept; **not** proof any provider is connected.
- `app/templates/app/base.html`: image icon per navigation item with
  unchanged human-readable text and keyboard/ARIA semantics.
- `app/templates/app/mcp_connections.html`: branded illustration and status
  text rendered separately based on real identity/link state.
- `app/static/workspace_refresh.css` and `mcp_connections.css`: responsive,
  accessible icon sizes and consistent palette; no rasterized controls.

## Production UI rules

1. **Never** render generated example percentages, review counts, leads,
   workflow metrics, messages, approvals or 'published' badges as data.
2. Status text derives from actual server state, not raster artwork.
3. Buttons remain real HTML buttons/links with labels, focus states, cursor,
   keyboard, touch and error handling, not images.
4. All decorative images use empty `alt` and don't duplicate text.
5. Retain the existing verified MeasuredField brand logo assets; the raster
   variations in `mf_icons.png` are design references.
6. Clean alpha/size and page tests must pass before asset distribution.

## Next images worth creating (optional)

Real owner-safe empty-state **illustrations without words or numbers**:
no connected computers, no website baseline, no accounts linked, and no work
receipts. Each should be a separate transparent illustration at approx
600–1000 px, with no provider-specific connected-state promise. A second
optional group is neutral local-map/geographic shapes without labels,
numbers, or map claims. Do not generate additional button sheets.
