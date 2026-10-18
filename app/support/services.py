import secrets

from sqlalchemy import func, select

from ..core.timeutil import now_utc
from ..extensions import db
from ..media import storage
from ..media.processing import ImageRejected, process_upload
from .models import MODULE_LABELS, BugReport, BugReportImage

MAX_SCREENSHOTS = 3
MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024  # tre skærmbilleder skal kunne være i én forespørgsel (højst 16 MB)
MAX_DESCRIPTION = 4000


class ReportRefused(ValueError):
    """Rapporten kan ikke modtages. Beskeden kan vises direkte for brugeren. `field` er det felt, fejlen gælder."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field


def create_report(user, module, description, screenshots, user_agent=None):
    """Opretter en fejlrapport. `screenshots` er en liste af (filnavn, bytes). Billederne omkodes og renses for
    metadata, før de gemmes. Kalderen committer."""
    description = (description or "").strip()
    if module not in MODULE_LABELS:
        raise ReportRefused("Vælg, hvor fejlen er.")
    if not description:
        raise ReportRefused("Beskriv fejlen.")
    if len(description) > MAX_DESCRIPTION:
        raise ReportRefused(f"Beskrivelsen må højst være {MAX_DESCRIPTION} tegn.")
    if len(screenshots) > MAX_SCREENSHOTS:
        raise ReportRefused(f"Du kan højst vedhæfte {MAX_SCREENSHOTS} skærmbilleder.", "screenshots")
    processed = []
    for name, data in screenshots:
        if len(data) > MAX_SCREENSHOT_BYTES:
            raise ReportRefused(f"{name}: Et skærmbillede må højst fylde 5 MB.", "screenshots")
        try:
            processed.append(process_upload(data, max_still_bytes=MAX_SCREENSHOT_BYTES, max_animated_bytes=0,
                                            animated_refusal="Skærmbilleder kan ikke være animerede."))
        except ImageRejected as rejection:
            raise ReportRefused(f"{name}: {rejection}", "screenshots")

    report = BugReport(user_id=user.id, module=module, description=description,
                       user_agent=(user_agent or "")[:255] or None)
    db.session.add(report)
    db.session.flush()
    for position, image in enumerate(processed):
        row = BugReportImage(report_id=report.id, public_id=secrets.token_urlsafe(16), position=position,
                             width=image.width, height=image.height, size_bytes=len(image.full))
        db.session.add(row)
        db.session.flush()
        storage.save_files(row, image)
    return report


def images_for(report):
    return db.session.scalars(
        select(BugReportImage).where(BugReportImage.report_id == report.id).order_by(BugReportImage.position)
    ).all()


def set_status(report, admin, status, note=None):
    report.status = status
    report.admin_note = (note or "").strip() or report.admin_note
    report.closed_at, report.closed_by_id = (now_utc(), admin.id) if status == "closed" else (None, None)


def open_count():
    return db.session.scalar(select(func.count(BugReport.id)).where(BugReport.status == "open"))


def purge_closed(before):
    """Afsluttede fejlrapporter slettes efter opbevaringsperioden. Filerne returneres til sletning efter commit."""
    reports = db.session.scalars(select(BugReport).where(BugReport.status == "closed", BugReport.closed_at < before)).all()
    files = []
    for report in reports:
        files += [(image.collection, image.public_id) for image in images_for(report)]
        db.session.delete(report)
    db.session.flush()
    return len(reports), files
