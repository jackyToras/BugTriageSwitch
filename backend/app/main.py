from contextlib import asynccontextmanager
import re
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from app.database import Base, SessionLocal, engine, get_db
from app.models import Report, ReportEvent, ReportStatus, ReportType, SwitchMessageLink
from app.schemas import ReportCreate, ReportRead, SwitchEventIn
from app.tasks import process_report


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if db.query(Report).count() == 0:
            rows = [
                Report(title="Checkout sends receipt to old email", description="After I change the account email, the profile page shows the new address but checkout still sends the receipt to the old one. Refreshing does not help.", environment="Chrome 131 · Windows 11", reporter="Maya Chen", status=ReportStatus.resolved, category=ReportType.bug, priority="high", summary="Checkout receipt destination does not update after account email change.", reproduction="1. Sign in with a test account.\n2. Change the account email and save.\n3. Start checkout and inspect the receipt destination.", result="Demo plan ready; execute against the application to confirm."),
                Report(title="Invite link expires too quickly", description="A teammate says the workspace invite link expired before they could use it. The invite was sent this morning.", environment="Chrome · macOS", reporter="Jordan Lee", status=ReportStatus.triaging, category=ReportType.bug, priority="medium", summary="Workspace invitation may expire earlier than expected."),
                Report(title="Can I export the activity feed?", description="How can I download the activity feed for our monthly review?", environment="Web app", reporter="Alex Rivera", status=ReportStatus.resolved, category=ReportType.question, priority="low", summary="User asks how to export activity history.", result="A teammate should confirm current export options."),
            ]
            db.add_all(rows)
            db.flush()
            for report, actor, kind, message in [
                (rows[0], "Triage agent", "classified", "Classified as a high-priority bug and routed to Repro agent."),
                (rows[0], "Repro agent", "plan_ready", "Verification plan created for the changed-email checkout flow."),
                (rows[1], "Triage agent", "started", "Checking the report details and environment."),
                (rows[2], "Triage agent", "answered", "Classified as a question for teammate response."),
            ]:
                db.add(ReportEvent(report=report, actor=actor, kind=kind, message=message))
            db.commit()
    yield


app = FastAPI(title="Switchboard API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health():
    return {"status": "ok", "service": "switchboard-api"}


@app.get("/api/v1/reports", response_model=list[ReportRead])
def list_reports(db: Session = Depends(get_db)):
    return db.scalars(select(Report).options(selectinload(Report.events)).order_by(Report.updated_at.desc())).all()


@app.post("/api/v1/reports", response_model=ReportRead, status_code=201)
def create_report(payload: ReportCreate, db: Session = Depends(get_db)):
    report = Report(**payload.model_dump())
    db.add(report)
    db.flush()
    db.add(ReportEvent(report_id=report.id, actor=payload.reporter, kind="reported", message="Submitted a new report."))
    db.commit()
    db.refresh(report)
    process_report.delay(report.id)
    return report


@app.get("/api/v1/reports/{report_id}", response_model=ReportRead)
def get_report(report_id: str, db: Session = Depends(get_db)):
    report = db.scalar(select(Report).options(selectinload(Report.events)).where(Report.id == report_id))
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@app.post("/api/v1/reports/{report_id}/retry", response_model=ReportRead)
def retry_report(report_id: str, db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    report.status = ReportStatus.queued
    db.add(ReportEvent(report_id=report.id, actor="You", kind="requeued", message="Requested another triage pass."))
    db.commit()
    db.refresh(report)
    process_report.delay(report.id)
    return report


@app.post("/api/v1/switch/events")
def ingest_switch_event(payload: SwitchEventIn, db: Session = Depends(get_db)):
    """Mirror a Switch room message into the dashboard's report timeline."""
    item = payload.event
    if item.get("type") != "message":
        return {"ok": True, "ignored": "not_a_message"}
    room_id = str(item.get("room_id") or "")
    message = item.get("payload") or {}
    message_id = str(message.get("message_id") or "")
    body = str(message.get("body") or "").strip()
    sender_id = str(message.get("sender") or "")
    sender = str(message.get("sender_name") or sender_id or "Switch teammate")
    thread_id = str(message.get("thread_id") or "")
    if not room_id or not message_id or not body or sender_id == payload.adapter_agent_id:
        return {"ok": True, "ignored": "missing_or_self_message"}

    existing = db.get(SwitchMessageLink, message_id)
    if existing:
        return {"ok": True, "duplicate": True, "report_id": existing.report_id}

    root_id = thread_id or message_id
    link = db.get(SwitchMessageLink, root_id)
    report = db.get(Report, link.report_id) if link else None
    if report is None and thread_id:
        return {"ok": True, "ignored": "thread_root_not_tracked"}

    # Console-managed agents cannot use the normal live event route. The local
    # relay exposes a compact history view instead, without thread IDs. Attach
    # recognizable workflow-agent replies to the newest Switch report as a
    # useful fallback for that transport.
    if report is None:
        agent_reply = "triag" in sender.lower() or "repro" in sender.lower()
        if agent_reply:
            report = db.scalar(
                select(Report)
                .join(SwitchMessageLink, SwitchMessageLink.report_id == Report.id)
                .where(SwitchMessageLink.room_id == room_id)
                .order_by(Report.updated_at.desc())
                .limit(1)
            )
            if report is None:
                return {"ok": True, "ignored": "agent_reply_without_tracked_report"}

    if report is None:
        first_line = body.splitlines()[0].strip()
        title = first_line[:177] + "…" if len(first_line) > 180 else first_line
        report = Report(title=title or "Report from Switch room", description=body, environment="Reported in Switch room", reporter=sender, status=ReportStatus.queued)
        db.add(report)
        db.flush()
        db.add(ReportEvent(report_id=report.id, actor=sender, kind="switch_report", message="Report received from the connected Switch room."))
        db.add(SwitchMessageLink(message_id=message_id, room_id=room_id, report_id=report.id))
        db.commit()
        return {"ok": True, "created": True, "report_id": report.id}

    kind = "switch_reply"
    low = body.lower()
    actor = sender.lower()
    if "triag" in actor:
        report.summary = body[:6000]
        report.category = ReportType.bug if any(word in low for word in ("bug", "defect", "reproduc")) else ReportType.question if "question" in low else ReportType.request if "request" in low else report.category
        report.priority = "high" if "high" in low else "medium" if "medium" in low else "low" if "low" in low else report.priority
        report.status = ReportStatus.reproducing if report.category == ReportType.bug else ReportStatus.needs_info
        kind = "triage_update"
    elif "repro" in actor:
        report.reproduction = body[:6000]
        # Avoid treating ordinary prose like "the form shows a fixed error
        # message" as proof that the reported defect itself was fixed.
        verified_fix = any(
            marker in low
            for marker in (
                "verified fix",
                "fix confirmed",
                "resolution verified",
                "fix has been applied",
                "fix was applied",
                "fix is deployed",
                "fix was deployed",
                "fix has been merged",
                "fix was merged",
                "fixed and verified",
            )
        ) or re.search(r"\b(?:issue|bug|problem)\s+(?:is|was|has been)\s+fixed\b", low)
        if verified_fix:
            report.status = ReportStatus.resolved
        elif any(marker in low for marker in ("could not reproduce", "cannot reproduce", "unable to reproduce", "not reproducible")):
            report.status = ReportStatus.needs_info
        else:
            # Reproducing a defect confirms it; it does not mean it has been
            # fixed. Partial and full reproductions remain in progress until
            # an agent explicitly reports that a fix was verified.
            report.status = ReportStatus.reproducing
        kind = "reproduction_update"
    db.add(ReportEvent(report_id=report.id, actor=sender, kind=kind, message=body[:6000]))
    db.add(SwitchMessageLink(message_id=message_id, room_id=room_id, report_id=report.id))
    db.commit()
    return {"ok": True, "report_id": report.id, "kind": kind}
