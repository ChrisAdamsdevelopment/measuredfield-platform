"""PostgreSQL-backed, tenant-isolated AI Visibility research storage.

All mutation runs inside an authenticated tenant session. Never infer authority
from submitted org IDs, provider flags or imported model/source metadata.
No Gemini calls or remote desktop interactions are performed by this service.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import func, select

from ..models import (VisibilityExperiment, VisibilityGscSnapshot,
                      VisibilityObservation, VisibilityQuestion)
from .visibility_lab import (BENCHMARK_22, LabInputError, import_gsc_csv,
                             plan_experiment, propose_questions)

MAX_SNAPSHOTS = 100
MAX_QUESTIONS_STORED = 1000
MAX_EXPERIMENTS = 500
MAX_OBSERVATIONS = 10000
MAX_EVIDENCE_BATCH = 100
VALID_DECISIONS = {"draft", "approved", "rejected"}
VALID_ORIGINS = {"historical_import", "canvas_runtime", "reconstructed", "synthetic"}


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _count(db, model):
    return db.scalar(select(func.count()).select_from(model)) or 0


def _iso(value):
    return value.isoformat() if value else None


def snapshot_view(s, *, include_rows=False):
    result = {
        "id": str(s.id), "filename": s.filename, "source_type": s.source_type,
        "digest": s.digest, "row_count": s.row_count,
        "invalid_count": s.invalid_count, "impressions": s.impressions,
        "clicks": s.clicks, "date_start": _iso(s.date_start),
        "date_end": _iso(s.date_end), "created_at": _iso(s.created_at),
    }
    if include_rows:
        result["rows"] = s.rows
    return result


def question_view(q):
    return {
        "id": str(q.id), "text": q.question, "category": q.category,
        "market": q.market, "origin": q.origin, "status": q.status,
        "snapshot_id": str(q.source_snapshot_id) if q.source_snapshot_id else None,
        "source_queries": q.source_queries, "created_at": _iso(q.created_at),
    }


def experiment_view(ex, *, include_config=False):
    data = {
        "id": str(ex.id), "title": ex.title, "status": ex.status,
        "request_count": ex.request_count,
        "provider_requests_executed": ex.provider_requests_executed,
        "created_at": _iso(ex.created_at),
    }
    if include_config:
        data["configuration"] = ex.configuration
    return data


def evidence_view(run, *, with_text=False):
    data = {
        "id": str(run.id), "source_run_id": run.source_run_id,
        "experiment_id": str(run.experiment_id) if run.experiment_id else None,
        "provenance": run.provenance, "provider": run.provider,
        "model_id": run.model_id, "reported_mentioned": run.reported_mentioned,
        "observed_at": _iso(run.observed_at), "created_at": _iso(run.created_at),
    }
    if with_text:
        data.update(question=run.question, actual_prompt=run.actual_prompt,
                    answer=run.answer, grounding=run.grounding,
                    source_metadata=run.source_metadata)
    return data


def _date(value):
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise LabInputError("Reporting dates must use YYYY-MM-DD.") from None


def save_gsc_snapshot(db, org_id, user_id, *, csv_text, filename, start=None, end=None):
    """A source-true, idempotent CSV import with no fabricated reporting window."""
    if not filename or len(filename) > 200 or any(c in filename for c in "\r\n\\/"):
        raise LabInputError("Use a safe filename, up to 200 characters.")
    period_start, period_end = _date(start), _date(end)
    if (period_start is None) != (period_end is None):
        raise LabInputError("Provide both GSC reporting dates or leave both unknown.")
    if period_start and period_end and period_start > period_end:
        raise LabInputError("GSC reporting start must precede end.")
    parsed = import_gsc_csv(csv_text)
    if not parsed["rows"]:
        raise LabInputError("No valid query rows in GSC import.")
    digest = _sha(csv_text + "|" + str(period_start) + "|" + str(period_end))
    existing = db.scalar(select(VisibilityGscSnapshot).where(
        VisibilityGscSnapshot.digest == digest))
    if existing:
        return existing, False
    if _count(db, VisibilityGscSnapshot) >= MAX_SNAPSHOTS:
        raise LabInputError("GSC snapshot limit reached for this workspace.")
    snap = VisibilityGscSnapshot(
        org_id=org_id, digest=digest, filename=filename,
        date_start=period_start, date_end=period_end,
        row_count=parsed["row_count"], invalid_count=parsed["invalid_count"],
        impressions=parsed["impressions"], clicks=parsed["clicks"],
        rows=parsed["rows"], created_by=user_id, source_type="owner_csv",
    )
    db.add(snap)
    db.flush()
    return snap, True


def create_draft_questions(db, org_id, user_id, snapshot_id):
    snap = db.get(VisibilityGscSnapshot, snapshot_id)
    if snap is None or snap.org_id != org_id:
        raise LabInputError("GSC snapshot was not found in this workspace.")
    proposals = propose_questions(snap.rows)
    created, skipped = [], 0
    for item in proposals:
        question = item["question"]
        fingerprint = _sha(question.casefold().strip())
        existing = db.scalar(select(VisibilityQuestion).where(
            VisibilityQuestion.question_hash == fingerprint))
        if existing:
            skipped += 1
            continue
        if _count(db, VisibilityQuestion) >= MAX_QUESTIONS_STORED:
            raise LabInputError("Question storage limit reached.")
        q = VisibilityQuestion(
            org_id=org_id, source_snapshot_id=snap.id, question_hash=fingerprint,
            question=question, category=item["category"], market=item["geography"],
            origin="gsc_suggestion", status="draft",
            source_queries=item["source_queries"], created_by=user_id,
        )
        db.add(q)
        db.flush()
        created.append(q)
    return {"created": len(created), "already_existed": skipped,
            "questions": [question_view(x) for x in created]}


def set_question_review(db, org_id, question_id, status):
    if status not in VALID_DECISIONS:
        raise LabInputError("Question review status is not supported.")
    q = db.get(VisibilityQuestion, question_id)
    if q is None or q.org_id != org_id:
        raise LabInputError("Question was not found in this workspace.")
    q.status = status
    db.flush()
    return question_view(q)


def _validate_stored_questions(db, org_id, questions):
    for entry in questions:
        qid = str(entry.get("id", ""))
        text = str(entry.get("text", ""))
        if qid.startswith("q-seed-"):
            try:
                n = int(qid.removeprefix("q-seed-"))
            except ValueError:
                raise LabInputError("Invalid original benchmark identifier.") from None
            if not (1 <= n <= len(BENCHMARK_22)) or text != BENCHMARK_22[n - 1]:
                raise LabInputError("Original benchmark wording must remain unchanged.")
            continue
        try:
            uid = uuid.UUID(qid)
        except ValueError:
            raise LabInputError("Non-benchmark questions must be approved saved questions.") from None
        q = db.get(VisibilityQuestion, uid)
        if q is None or q.org_id != org_id or q.status != "approved" or q.question != text:
            raise LabInputError("One or more selected questions are not approved in this workspace.")


def save_plan(db, org_id, user_id, *, title, questions, markets, modes, repeats,
              chunk_size, max_total):
    if not 1 <= len(title.strip()) <= 160:
        raise LabInputError("Research plan title must be 1-160 characters.")
    _validate_stored_questions(db, org_id, questions)
    plan = plan_experiment(questions, markets, modes, repeats, chunk_size, max_total)
    if _count(db, VisibilityExperiment) >= MAX_EXPERIMENTS:
        raise LabInputError("Research plan storage limit reached.")
    ex = VisibilityExperiment(
        org_id=org_id, title=title.strip(),
        configuration={
            "questions": questions, "markets": markets, "modes": modes,
            "repetitions": repeats, "chunk_size": chunk_size,
            "max_total": max_total, "planner_version": "MF-019-v1",
            "last_preview": plan["preview"], "warnings": plan["warnings"],
            "note": "Planning only; 0 provider calls. Not a scheduled or billed job.",
        },
        request_count=plan["request_count"], status="planned",
        provider_requests_executed=0, created_by=user_id,
    )
    db.add(ex)
    db.flush()
    return experiment_view(ex, include_config=True)


def classify_imported_source(source_run_id, origin):
    """Server-side provenance: client metadata can NEVER mint verified_provider."""
    if origin not in VALID_ORIGINS:
        raise LabInputError("Unrecognized evidence origin.")
    if source_run_id.startswith("run-fresh78-") or origin == "synthetic":
        return "synthetic_fixture"
    if origin == "reconstructed" or "quarantined" in source_run_id:
        return "quarantined_reconstruction"
    if origin == "historical_import":
        return "historical_import"
    return "captured_unverified"


def _timestamp(value):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("Missing offset")
        return stamp.astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise LabInputError("Observation date requires an ISO timestamp with timezone.") from None


def import_evidence(db, org_id, user_id, runs, *, experiment_id=None):
    if not isinstance(runs, list) or not 1 <= len(runs) <= MAX_EVIDENCE_BATCH:
        raise LabInputError("Import 1 through 100 evidence rows per batch.")
    if experiment_id:
        ex = db.get(VisibilityExperiment, experiment_id)
        if ex is None or ex.org_id != org_id:
            raise LabInputError("Research plan does not belong to your workspace.")
    if _count(db, VisibilityObservation) + len(runs) > MAX_OBSERVATIONS:
        raise LabInputError("Evidence storage limit reached.")
    saved, skipped = [], 0
    seen = set()
    for raw in runs:
        ref = str(raw.get("source_run_id", "")).strip()
        if not 1 <= len(ref) <= 128 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", ref):
            raise LabInputError("Evidence source ID contains invalid characters.")
        if ref in seen:
            skipped += 1
            continue
        seen.add(ref)
        if db.scalar(select(VisibilityObservation.id).where(
                VisibilityObservation.source_run_id == ref)):
            skipped += 1
            continue
        provenance = classify_imported_source(ref, str(raw.get("origin", "")))
        question, prompt = str(raw.get("question", "")), str(raw.get("actual_prompt", ""))
        answer = str(raw.get("answer", ""))
        if len(question) > 500 or len(prompt) > 4000 or len(answer) > 30000:
            raise LabInputError("One evidence text field exceeds the size limit.")
        grounding = raw.get("grounding", {})
        metadata = raw.get("source_metadata", {})
        if not isinstance(grounding, dict) or not isinstance(metadata, dict):
            raise LabInputError("Grounding and source metadata must be JSON objects.")
        if len(json.dumps(grounding)) > 80000 or len(json.dumps(metadata)) > 80000:
            raise LabInputError("Grounding metadata is too large.")
        mention = raw.get("reported_mentioned")
        if mention is not None and type(mention) is not bool:
            raise LabInputError("Mention flag must be true, false or null.")
        # Preserve the original untrusted claims, but never elevate them to verified fields.
        source_meta = {
            "original_untrusted_metadata": metadata,
            "imported_origin": str(raw["origin"]),
            "verified_by_server": False,
            "classified_provenance": provenance,
        }
        record = VisibilityObservation(
            org_id=org_id, experiment_id=experiment_id, source_run_id=ref,
            provenance=provenance, question=question, actual_prompt=prompt,
            answer=answer, provider=str(raw.get("provider", ""))[:90],
            model_id=str(raw.get("model_id", ""))[:100], grounding=grounding,
            source_metadata=source_meta, reported_mentioned=mention,
            observed_at=_timestamp(raw.get("observed_at")), created_by=user_id,
        )
        db.add(record)
        db.flush()
        saved.append(record)
    return {"saved": len(saved), "duplicates": skipped,
            "records": [evidence_view(x) for x in saved]}


def evidence_summary(db):
    groups = dict(db.execute(select(
        VisibilityObservation.provenance,
        func.count(VisibilityObservation.id)).group_by(
        VisibilityObservation.provenance)).all())
    return {
        "provenance": groups,
        "verified_provider": 0 if not groups.get("verified_provider") else groups["verified_provider"],
        "note": "No imported evidence is verified by its own CSV/source labels.",
    }
