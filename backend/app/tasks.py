import time
from app.celery_app import celery
from app.config import settings
from app.database import SessionLocal
from app.models import Report, ReportEvent, ReportStatus, ReportType


def event(db, report_id, actor, kind, message):
    db.add(ReportEvent(report_id=report_id, actor=actor, kind=kind, message=message))
    db.commit()


@celery.task(name="switchboard.process_report", bind=True, max_retries=3, default_retry_delay=4)
def process_report(self, report_id: str):
    db = SessionLocal()
    try:
        report = db.get(Report, report_id)
        if report is None:
            return {"ok": False, "reason": "report_not_found"}
        report.status = ReportStatus.triaging
        db.commit()
        event(db, report.id, "Triage agent", "started", "Reviewing the report and deciding the next step.")
        time.sleep(settings.demo_processing_delay_seconds)
        text = f"{report.title} {report.description}".lower()
        if any(w in text for w in ("error", "crash", "broken", "fails", "failed", "bug", "not working", "doesn't", "does not")):
            report.category = ReportType.bug
            report.priority = "high" if any(w in text for w in ("checkout", "payment", "security", "data loss")) else "medium"
            report.summary = f"{report.title.strip()}. Reported by {report.reporter}; environment: {report.environment}."
            report.status = ReportStatus.reproducing
            db.commit()
            event(db, report.id, "Triage agent", "classified", f"Classified as a bug with {report.priority} priority. Handing it to Repro agent.")
            event(db, report.id, "Repro agent", "started", "Preparing a minimal verification plan from the report.")
            time.sleep(settings.demo_processing_delay_seconds)
            report.reproduction = ("1. Sign in with a test account.\n2. Change the account email and save.\n3. Start checkout and inspect the receipt destination.\n4. Compare it with the updated profile email." if "email" in text or "receipt" in text else "1. Start from a clean session.\n2. Follow the reported steps.\n3. Capture the first unexpected response and relevant logs.")
            report.result = "Demo run: verification plan created. Connect a coding agent or test environment to execute it against the real application."
            report.status = ReportStatus.needs_info if len(report.description) < 45 else ReportStatus.resolved
            db.commit()
            event(db, report.id, "Repro agent", "plan_ready", "Verification plan is ready. The demo processor did not execute against an external application.")
        elif any(w in text for w in ("how ", "why ", "where ", "can i", "what ")):
            report.category = ReportType.question
            report.priority = "low"
            report.summary = f"Question: {report.title.strip()}"
            report.result = "A teammate should answer in the linked Switch room with relevant product context."
            report.status = ReportStatus.resolved
            db.commit()
            event(db, report.id, "Triage agent", "answered", "Classified as a question and prepared it for teammate response.")
        else:
            report.category = ReportType.request
            report.priority = "low"
            report.summary = f"Request: {report.title.strip()}"
            report.result = "Clarify the desired outcome and acceptance criteria before implementation."
            report.status = ReportStatus.needs_info
            db.commit()
            event(db, report.id, "Triage agent", "needs_info", "Classified as a request. Add the expected outcome and acceptance criteria.")
        return {"ok": True, "report_id": report_id}
    except Exception as exc:
        db.rollback()
        raise self.retry(exc=exc)
    finally:
        db.close()
