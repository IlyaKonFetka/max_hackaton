"""PDF-акт самопроверки. reportlab + DejaVu (кириллица), без headless-браузера."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..config import BASE_DIR, settings
from ..db import CheckSession, Venue
from ..engine import PERIOD_LABELS, funnel, profile_summary_lines
from ..rulebook import get_rulebook
from .sessions import local_date, local_datetime, session_report

FONTS = BASE_DIR / "backend" / "assets" / "fonts"
_registered = False


def _fonts() -> None:
    global _registered
    if _registered:
        return
    pdfmetrics.registerFont(TTFont("DejaVu", str(FONTS / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(FONTS / "DejaVuSans-Bold.ttf")))
    _registered = True


STATUS_RU = {"ok": "Соблюдается", "violation": "Нарушение", "unknown": "Не знаю", None: "Не проверено"}
STATUS_COLOR = {
    "ok": colors.HexColor("#1a7f37"),
    "violation": colors.HexColor("#b42318"),
    "unknown": colors.HexColor("#8a6d00"),
    None: colors.HexColor("#666666"),
}


def build_act(session: CheckSession, venue: Venue, owner_name: str) -> Path:
    _fonts()
    tz = settings.timezone_offset_hours
    book = get_rulebook()
    report = session_report(session)
    out = settings.acts_dir / f"act_{session.id}.pdf"

    base = ParagraphStyle("base", fontName="DejaVu", fontSize=9.5, leading=13, alignment=TA_LEFT)
    small = ParagraphStyle("small", parent=base, fontSize=8, leading=10.5, textColor=colors.HexColor("#555555"))
    h1 = ParagraphStyle("h1", parent=base, fontName="DejaVu-Bold", fontSize=15, leading=19, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=base, fontName="DejaVu-Bold", fontSize=11.5, leading=15, spaceBefore=10, spaceAfter=4)
    bold = ParagraphStyle("bold", parent=base, fontName="DejaVu-Bold")

    doc = SimpleDocTemplate(
        str(out), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
        title=f"Акт самопроверки — {venue.name}", author="Движок применимости (MAX)",
    )
    story: list = []

    story.append(Paragraph("Акт самопроверки соблюдения обязательных требований", h1))
    story.append(Paragraph(
        f"Объект: <b>{_esc(venue.name)}</b>" + (f", {_esc(venue.region)}" if venue.region else "")
        + (f" · координаты {venue.lat:.5f}, {venue.lon:.5f}" if venue.lat is not None and venue.lon is not None else ""),
        base))
    story.append(Paragraph(
        f"Дата самопроверки: {local_date(session.finished_at or session.started_at, tz)} · "
        f"Проводил: {_esc(owner_name)} · Версия справочника: {session.rules_version}", small))
    story.append(Spacer(1, 4))

    # Профиль
    story.append(Paragraph("Профиль объекта", h2))
    prof_rows = [[Paragraph(_esc(line), base)] for line in profile_summary_lines(venue.profile, book)]
    story.append(_plain_table(prof_rows))

    # Какие проверочные листы относятся к объекту
    f = funnel(venue.profile, book)
    if f["checklists_total"]:
        story.append(Paragraph("Проверочные листы", h2))
        fl_rows = []
        for a in f["agencies"]:
            txt = f"{a['label']}: относится {a['yes']} из {a['total']}"
            if a["maybe"]:
                txt += f", ещё {a['maybe']} — при условиях, которых нет в профиле"
            fl_rows.append([Paragraph(_esc(txt), base)])
        for d in f["detailed"]:
            fl_rows.append([Paragraph(_esc(
                f"{d['doc']}: к объекту относятся {d['applicable']} вопросов из {d['questions']}, "
                f"{d['not_applicable']} не относятся по профилю, {d['other_domain']} — другие виды деятельности "
                f"({', '.join(s.lower() for s in d['other_scopes'])})"), base)])
        story.append(_plain_table(fl_rows))

    # Сводка
    c = report["counts"]
    applicable = sum(c.values())
    story.append(Paragraph("Итог", h2))
    summary_rows = [
        [Paragraph("Применимых требований", base), Paragraph(str(applicable), bold)],
        [Paragraph("Соблюдается", base), Paragraph(str(c["ok"]), bold)],
        [Paragraph("Нарушений", base), Paragraph(str(c["violation"]), bold)],
        [Paragraph("Требует уточнения («не знаю»)", base), Paragraph(str(c["unknown"]), bold)],
        [Paragraph("Не проверено", base), Paragraph(str(c["unanswered"]), bold)],
        [Paragraph("Не применимо к объекту", base),
         Paragraph(str(sum(1 for it in report["items"] if not it["applicable"] and not it["other_domain"])), bold)],
    ]
    story.append(_plain_table(summary_rows, col_widths=[120 * mm, 30 * mm]))

    # Применимые требования по ведомствам
    by_agency: dict[str, list] = {}
    for it in report["items"]:
        if it["applicable"]:
            by_agency.setdefault(it["rule"].agency_label, []).append(it)

    for agency, items in by_agency.items():
        story.append(Paragraph(_esc(agency), h2))
        rows = [[Paragraph("Требование и основание", bold), Paragraph("Статус", bold)]]
        for it in items:
            r = it["rule"]
            src = r.source
            cell = [Paragraph(_esc(r.title), base)]
            basis = f"{src.doc}" + (f", {src.clause}" if src.clause else "")
            cell.append(Paragraph(_esc(basis), small))
            if src.checklist:
                cell.append(Paragraph(_esc(src.checklist), small))
            meta = f"Периодичность: {PERIOD_LABELS.get(r.period, r.period)} · актуально на {src.as_of}"
            if not src.verified:
                meta += " · реквизиты требуют сверки"
            cell.append(Paragraph(_esc(meta), small))
            if it["comment"]:
                cell.append(Paragraph("Комментарий: " + _esc(it["comment"]), small))
            if it["photo_path"]:
                img = _thumb(it["photo_path"])
                if img is not None:
                    cell.append(img)
            st = it["status"]
            status_par = Paragraph(
                f'<font color="{STATUS_COLOR[st].hexval()}"><b>{STATUS_RU[st]}</b></font>', base)
            rows.append([cell, status_par])
        story.append(_grid_table(rows, col_widths=[135 * mm, 35 * mm]))

    # План устранения
    tasks = report["tasks"]
    if tasks:
        story.append(Paragraph("План устранения нарушений", h2))
        rows = [[Paragraph("Задача", bold), Paragraph("Срок", bold), Paragraph("Ответственный", bold), Paragraph("Статус", bold)]]
        for t in tasks:
            rows.append([
                Paragraph(_esc(t.title), base),
                Paragraph(local_date(t.due_date, tz), base),
                Paragraph(_esc(t.assignee_name or "—"), base),
                Paragraph("Выполнено" if t.status == "done" else "Открыта", base),
            ])
        story.append(_grid_table(rows, col_widths=[85 * mm, 25 * mm, 35 * mm, 25 * mm]))

    # Не применимо и почему
    na = [it for it in report["items"] if not it["applicable"] and not it["other_domain"]]
    other: dict[str, int] = {}
    for it in report["items"]:
        if not it["applicable"] and it["other_domain"]:
            other[it["other_domain"]] = other.get(it["other_domain"], 0) + 1
    if na:
        story.append(Paragraph("Требования, не применимые к объекту", h2))
        rows = [[Paragraph("Требование", bold), Paragraph("Почему не применимо", bold)]]
        for it in na:
            rows.append([
                Paragraph(_esc(it["rule"].title), base),
                Paragraph(_esc("; ".join(it["reasons"]) or "—"), small),
            ])
        story.append(_grid_table(rows, col_widths=[105 * mm, 65 * mm]))
    if other:
        story.append(Spacer(1, 4))
        story.append(Paragraph(_esc(
            "Требования справочника для других видов деятельности в акт не включены: "
            + ", ".join(f"{d.lower()} — {n}" for d, n in other.items()) + "."), small))

    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "Акт сформирован сервисом «Движок применимости» в мессенджере MAX на основании сведений, внесённых пользователем. "
        "Перечень требований подготовлен вручную по опубликованным нормативным актам. Проверочный лист Роспотребнадзора "
        "для общественного питания разобран полностью, листы Роструда и МЧС — в части ключевых требований; "
        "документ носит информационный характер и не является декларацией соблюдения обязательных требований.",
        small))
    story.append(Paragraph(f"Сформировано: {local_datetime(session.finished_at or session.started_at, tz)}", small))

    doc.build(story)
    return out


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _plain_table(rows: list, col_widths=None) -> Table:
    t = Table(rows, colWidths=col_widths, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def _grid_table(rows: list, col_widths=None) -> Table:
    t = Table(rows, colWidths=col_widths, hAlign="LEFT", repeatRows=1)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f2f5")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _thumb(path: str):
    p = Path(path)
    if not p.is_absolute():
        p = settings.data_dir / p
    if not p.exists():
        return None
    try:
        from reportlab.lib.utils import ImageReader

        iw, ih = ImageReader(str(p)).getSize()
        ratio = min(max(ih / float(iw or 1), 0.2), 2.0)
        w = 55 * mm
        img = Image(str(p), width=w, height=w * ratio)
        img.hAlign = "LEFT"
        return img
    except Exception:
        return None
