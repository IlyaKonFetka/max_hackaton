"""REST для мини-приложения. Авторизация — подписанный initData в заголовке X-Max-Init-Data."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ..config import settings
from ..db import CheckSession, Task, Venue, db_session
from ..engine import evaluate_all, profile_summary_lines, summary
from ..rulebook import get_rulebook
from ..services import sessions as svc
from ..services.media import save_upload
from .auth import MaxUser, current_user

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def _photo_url(rel: str | None) -> str | None:
    return f"{settings.public_api_url}/media/{rel}" if rel else None


def _venue_or_404(db, user: MaxUser) -> Venue:
    v = svc.current_venue(db, user.id)
    if v is None:
        _v, m = svc.role_of(db, user.id)
        if m is not None and m.role == "staff":
            raise HTTPException(status_code=403, detail="Самопроверка и акт — для владельца заведения. Ваши задачи и чек-лист смены — в чате с ботом.")
        raise HTTPException(status_code=404, detail="Профиль заведения не заполнен — пройдите онбординг в боте")
    return v


def _session_payload(session: CheckSession, venue: Venue) -> dict:
    rules = svc.rules_by_id()
    answers = {a.rule_id: a for a in session.answers}
    applicable, not_applicable = [], []
    for v in session.verdicts:
        r = rules.get(v["rule_id"])
        if r is None:
            continue
        d = r.to_dict()
        if v["applicable"]:
            a = answers.get(r.id)
            d.update({
                "status": a.status if a else None,
                "comment": a.comment if a else "",
                "photo_url": _photo_url(a.photo_path) if a else None,
            })
            applicable.append(d)
        else:
            d["reasons"] = v.get("reasons", [])
            not_applicable.append(d)
    return {
        "id": session.id,
        "started_at": session.started_at.isoformat(),
        "finished_at": session.finished_at.isoformat() if session.finished_at else None,
        "rules_version": session.rules_version,
        "venue": {"id": venue.id, "name": venue.name, "region": venue.region,
                  "profile_lines": profile_summary_lines(venue.profile, get_rulebook())},
        "progress": svc.progress(session),
        "applicable": applicable,
        "not_applicable": not_applicable,
        "agencies": [{"key": k, "label": lbl} for k, lbl in get_rulebook().agencies],
    }


@router.get("/me")
def me(user: MaxUser = Depends(current_user)):
    with db_session() as db:
        svc.get_or_create_user(db, user.id, user.first_name, user.last_name, user.username)
        venue = svc.current_venue(db, user.id)
        if venue is None:
            return {"user": user.__dict__, "venue": None, "session": None, "open_tasks": 0}
        s = svc.open_session(db, venue) or svc.latest_session(db, venue)
        return {
            "user": user.__dict__,
            "venue": {"id": venue.id, "name": venue.name, "region": venue.region,
                      "profile_lines": profile_summary_lines(venue.profile, get_rulebook())},
            "session": {"id": s.id, "finished": bool(s.finished_at), "progress": svc.progress(s)} if s else None,
            "open_tasks": len(svc.open_tasks(db, user.id, venue.id)),
            "start_param": user.start_param,
        }


@router.get("/session")
def get_session(id: int | None = None, user: MaxUser = Depends(current_user)):
    """Текущая самопроверка. Без id — открытая (или последняя); с id — конкретная (из start_param `s<id>`)."""
    with db_session() as db:
        venue = _venue_or_404(db, user)
        if id is not None:
            s = db.get(CheckSession, id)
            if s is None or s.venue_id != venue.id:
                raise HTTPException(status_code=404, detail="Сессия не найдена")
        else:
            s = svc.open_session(db, venue) or svc.latest_session(db, venue)
            if s is None:
                s = svc.start_session(db, venue)
        return _session_payload(s, venue)


@router.post("/session/new")
def new_session(user: MaxUser = Depends(current_user)):
    from ..db import utcnow

    with db_session() as db:
        venue = _venue_or_404(db, user)
        old = svc.open_session(db, venue)
        if old:
            old.finished_at = utcnow()
        s = svc.start_session(db, venue)
        return _session_payload(s, venue)


class AnswerIn(BaseModel):
    status: str = Field(pattern="^(ok|violation|unknown)$")
    comment: str | None = Field(default=None, max_length=1000)


@router.put("/session/{sessionId}/answers/{ruleId}")
def put_answer(sessionId: int, ruleId: str, body: AnswerIn, user: MaxUser = Depends(current_user)):
    with db_session() as db:
        venue = _venue_or_404(db, user)
        s = db.get(CheckSession, sessionId)
        if s is None or s.venue_id != venue.id:
            raise HTTPException(status_code=404, detail="Сессия не найдена")
        if s.finished_at:
            raise HTTPException(status_code=409, detail="Самопроверка уже завершена — начните новую")
        try:
            a = svc.set_answer(db, s, ruleId, body.status, comment=body.comment)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"rule_id": a.rule_id, "status": a.status, "comment": a.comment,
                "photo_url": _photo_url(a.photo_path), "progress": svc.progress(s)}


@router.post("/session/{sessionId}/answers/{ruleId}/photo")
async def put_photo(sessionId: int, ruleId: str, file: UploadFile = File(...), user: MaxUser = Depends(current_user)):
    content = await file.read()
    try:
        rel = save_upload(content, file.content_type, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    with db_session() as db:
        venue = _venue_or_404(db, user)
        s = db.get(CheckSession, sessionId)
        if s is None or s.venue_id != venue.id:
            raise HTTPException(status_code=404, detail="Сессия не найдена")
        if s.finished_at:
            raise HTTPException(status_code=409, detail="Самопроверка уже завершена")
        existing = {a.rule_id: a for a in s.answers}.get(ruleId)
        status = existing.status if existing else "violation"
        try:
            a = svc.set_answer(db, s, ruleId, status, photo_path=rel)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"rule_id": a.rule_id, "status": a.status, "photo_url": _photo_url(a.photo_path)}


@router.post("/session/{sessionId}/finish")
async def finish(sessionId: int, user: MaxUser = Depends(current_user)):
    with db_session() as db:
        venue = _venue_or_404(db, user)
        s = db.get(CheckSession, sessionId)
        if s is None or s.venue_id != venue.id:
            raise HTTPException(status_code=404, detail="Сессия не найдена")
        p = svc.progress(s)
        if p["answered"] == 0:
            raise HTTPException(status_code=400, detail="Отметьте хотя бы один пункт")
        if not s.finished_at:
            svc.finish_session(db, s, venue.owner_id)
        report = svc.session_report(s)
        counts = report["counts"]
        tasks = [_task_dict(t) for t in report["tasks"]]

    # Акт собирается и уходит в чат в фоне — мини-приложение не ждёт PDF.
    if settings.run_bot:
        from ..services.notify import send_act

        asyncio.create_task(send_act(sessionId))
    return {"id": sessionId, "counts": counts, "tasks": tasks, "act_sent_to_chat": settings.run_bot}


def _task_dict(t: Task) -> dict:
    return {
        "id": t.id, "rule_id": t.rule_id, "title": t.title, "status": t.status,
        "due_date": t.due_date.isoformat(), "assignee_name": t.assignee_name,
        "photo_before_url": _photo_url(t.photo_before), "photo_after_url": _photo_url(t.photo_after),
    }


@router.get("/tasks")
def tasks(user: MaxUser = Depends(current_user)):
    with db_session() as db:
        venue = _venue_or_404(db, user)
        own = svc.open_tasks(db, user.id, venue.id)
        assigned = svc.tasks_for_assignee(db, user.id)
        seen, out = set(), []
        for t in own + assigned:
            if t.id in seen:
                continue
            seen.add(t.id)
            out.append(_task_dict(t))
        return {"tasks": out}


@router.post("/tasks/{taskId}/done")
async def task_done(taskId: int, file: UploadFile | None = File(default=None), user: MaxUser = Depends(current_user)):
    rel = None
    if file is not None:
        try:
            rel = save_upload(await file.read(), file.content_type, file.filename)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    with db_session() as db:
        t = db.get(Task, taskId)
        if t is None or (t.owner_id != user.id and t.assignee_user_id != user.id):
            raise HTTPException(status_code=404, detail="Задача не найдена")
        if t.status == "open":
            svc.complete_task(db, t, rel)
        return _task_dict(t)


class ProfileIn(BaseModel):
    """Профиль объекта — те же поля, что задаёт онбординг в боте (rules/profile.yaml)."""

    activity: str = Field(description="cafe | coffee | canteen | bakery | streetfood | bar | shop", examples=["cafe"])
    has_kitchen: bool = Field(examples=[True])
    own_production: bool = Field(examples=[True])
    seats: int = Field(ge=0, description="Посадочных мест (0 — только навынос)", examples=[35])
    staff: int = Field(ge=0, description="Наёмных работников", examples=[10])
    alcohol: bool = Field(examples=[False])
    name: str | None = Field(default=None, max_length=200, examples=["Пекарня на Баумана"])
    region: str | None = Field(default=None, max_length=200, examples=["Республика Татарстан"])

    def engine_profile(self) -> dict:
        allowed = {o.value for o in get_rulebook().field("activity").options}
        if self.activity not in allowed:
            raise HTTPException(status_code=422, detail=f"activity: одно из {sorted(allowed)}")
        return self.model_dump(exclude={"name", "region"})


@router.post("/applicability")
def applicability(profile: ProfileIn):
    """Ядро продукта без авторизации: какие требования применимы к объекту с таким профилем и почему остальные — нет."""
    p = profile.engine_profile()
    book = get_rulebook()
    verdicts = evaluate_all(p, book)
    return {
        "rules_version": book.version,
        "summary": summary(p, book),
        "applicable": [v.rule.to_dict() for v in verdicts if v.applicable],
        "not_applicable": [{**v.rule.to_dict(), "reasons": list(v.reasons)} for v in verdicts if not v.applicable],
    }


@router.put("/venue")
def put_venue(profile: ProfileIn, user: MaxUser = Depends(current_user)):
    """Профиль заведения пользователя (то же, что онбординг в боте). Повторный вызов обновляет профиль."""
    p = profile.engine_profile()
    with db_session() as db:
        svc.get_or_create_user(db, user.id, user.first_name, user.last_name, user.username)
        _v, m = svc.role_of(db, user.id)
        if m is not None and m.role == "staff":
            raise HTTPException(status_code=403, detail="Сотрудник не может менять профиль заведения")
        venue = svc.save_venue(db, user.id, {**p, "name": profile.name or "Моё заведение", "region": profile.region or ""})
        summ = summary(venue.profile, get_rulebook())
        return {"id": venue.id, "name": venue.name, "region": venue.region,
                "profile_lines": profile_summary_lines(venue.profile, get_rulebook()), "summary": summ}


@router.get("/rules")
def rules():
    book = get_rulebook()
    return {
        "version": book.version,
        "fields": [
            {"key": f.key, "label": f.label, "type": f.type,
             "options": [{"value": o.value, "label": o.label} for o in f.options]}
            for f in book.fields
        ],
        "rules": [r.to_dict() for r in book.rules],
    }
