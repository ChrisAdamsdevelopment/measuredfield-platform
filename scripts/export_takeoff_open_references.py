"""Export the complete existing openly licensed reference corpus; never retailer research."""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
db = Path(sys.argv[1]).resolve()
digest = hashlib.sha256(db.read_bytes()).hexdigest()
with sqlite3.connect(db.as_uri()+"?mode=ro", uri=True) as conn:
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("""
        SELECT m.id,m.name,m.category,m.source_unit,m.price_cents,m.low_cents,
        m.high_cents,m.status,m.observed_version_date,s.url,s.version,s.rights,s.attribution
        FROM material_reference m JOIN source s ON m.source_id=s.id
        WHERE s.id='hammerio' AND s.rights='CC BY 4.0'
        ORDER BY m.category,m.name,m.id
    """)]
assert rows and all(r["rights"]=="CC BY 4.0" for r in rows)
assert len({r["id"] for r in rows})==len(rows)
assert hashlib.sha256(db.read_bytes()).hexdigest()==digest
out=ROOT/"app/static/takeoff/src/open-cost-references.mjs"
out.write_text("// Existing open research; source units and ranges unchanged. Not local supplier quotes.\\n"
               .replace("\\n","\n")+"export const OPEN_COST_REFERENCES="+json.dumps(rows,ensure_ascii=True,separators=(",",":"))+";\n"
               +"export const REFERENCE_DB_SHA256="+json.dumps(digest)+";\n",encoding="utf-8")
print("OPEN_REFERENCE_EXPORT",len(rows),"priced",sum(r["price_cents"] is not None for r in rows),"sha256",digest)
