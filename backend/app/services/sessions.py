"""Самопроверка: создание сессии, ответы, завершение (нарушения → задачи)."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import Answer, CheckSession, Membership, Task, User, Venue, utcnow
from ..engine import Rule, evaluate_all
from ..rulebook import get_rulebook

STATUSES = ("ok", "violation", "unknown")


def get_or_create_user(db: Session, user_id: int, first_name: str = "", last_name: str = "",
                       username: str | None = None, chat_id: int | None = None) -> User:
    u = db.get(User, user_id)
    if u is None:
        u = User(id=user_id, first_name=first_name or "", last_name=last_name or "", username=username, chat_id=chat_id)
        db.add(u)
        db.flush()
    else:
        changed = False
        if first_name and u.first_name != first_name:
            u.first_name, changed = first_name, True
        if chat_id and u.chat_id != chat_id:
            u.chat_id, changed = chat_id, True
        if changed:
            db.flush()
    return u


def current_venue(db: Session, user_id: int) -> Venue | None:
    """Заведение, которым пользователь владеет."""
    return db.execute(
        select(Venue).where(Venue.owner_id == user_id).order_by(Venue.created_at.desc())
    ).scalars().first()


# ---------- роли ----------

def norm_phone(p: str | None) -> str:
    return "".join(ch for ch in (p or "") if ch.isdigit())[-10:]


def role_of(db: Session, user_id: int) -> tuple[Venue | None, Membership | None]:
    """(заведение, членство) пользователя: владелец приоритетнее; сотрудник — по присоединённому членству."""
    v = current_venue(db, user_id)
    if v is not None:
        m = ensure_owner_membership(db, v)
        return v, m
    m = db.execute(
        select(Membership).where(Membership.user_id == user_id, Membership.role == "staff", Membership.joined_at.is_not(None))
        .order_by(Membership.joined_at.desc())
    ).scalars().first()
    if m is None:
        return None, None
    return db.get(Venue, m.venue_id), m


def ensure_owner_membership(db: Session, venue: Venue) -> Membership:
    m = db.execute(
        select(Membership).where(Membership.venue_id == venue.id, Membership.user_id == venue.owner_id, Membership.role == "owner")
    ).scalars().first()
    if m is None:
        u = db.get(User, venue.owner_id)
        m = Membership(venue_id=venue.id, user_id=venue.owner_id, role="owner", joined_at=utcnow(),
                       name=f"{u.first_name} {u.last_name}".strip() if u else "")
        db.add(m)
        db.flush()
    return m


def find_staff(db: Session, venue_id: int, user_id: int | None = None, phone: str | None = None) -> Membership | None:
    q = select(Membership).where(Membership.venue_id == venue_id, Membership.role == "staff")
    if user_id is not None:
        m = db.execute(q.where(Membership.user_id == user_id)).scalars().first()
        if m:
            return m
    ph = norm_phone(phone)
    if ph:
        return db.execute(q.where(Membership.phone == ph)).scalars().first()
    return None


def invite_staff(db: Session, venue: Venue, invited_by: int, name: str, phone: str | None,
                 user_id: int | None) -> Membership:
    """Создаёт или обновляет запись сотрудника (ещё не присоединённого)."""
    m = find_staff(db, venue.id, user_id=user_id, phone=phone)
    if m is None:
        m = Membership(venue_id=venue.id, role="staff", invited_by=invited_by)
        db.add(m)
    if name and not m.name:
        m.name = name
    if phone:
        m.phone = norm_phone(phone)
    if user_id and not m.user_id:
        m.user_id = user_id
    db.flush()
    return m


def join_staff(db: Session, m: Membership, user_id: int, name: str = "") -> Membership:
    m.user_id = user_id
    if name and not m.name:
        m.name = name
    if m.joined_at is None:
        m.joined_at = utcnow()
    db.flush()
    return m


def staff_of(db: Session, venue_id: int) -> list[Membership]:
    return list(db.execute(
        select(Membership).where(Membership.venue_id == venue_id, Membership.role == "staff").order_by(Membership.created_at)
    ).scalars().all())


def save_venue(db: Session, user_id: int, profile: dict) -> Venue:
    """Профиль из онбординга → объект. Повторный онбординг обновляет последний объект."""
    v = current_venue(db, user_id)
    name = str(profile.get("name") or "Моё заведение")
    region = str(profile.get("region") or "")
    clean = {k: val for k, val in profile.items() if k not in ("name", "region", "lat", "lon")}
    if v is None:
        v = Venue(owner_id=user_id, name=name, profile=clean, region=region)
        db.add(v)
    else:
        v.name, v.profile, v.region = name, clean, region
    v.lat, v.lon = profile.get("lat"), profile.get("lon")
    db.flush()
    ensure_owner_membership(db, v)
    return v


def start_session(db: Session, venue: Venue) -> CheckSession:
    """Новая самопроверка со снимком применимости на текущей версии справочника."""
    book = get_rulebook()
    verdicts = evaluate_all(venue.profile, book)
    s = CheckSession(
        venue_id=venue.id,
        rules_version=book.version,
        verdicts=[
            {"rule_id": v.rule.id, "applicable": v.applicable, "reasons": list(v.reasons),
             "other_domain": v.other_domain}
            for v in verdicts
        ],
    )
    db.add(s)
    db.flush()
    return s


def open_session(db: Session, venue: Venue) -> CheckSession | None:
    return db.execute(
        select(CheckSession)
        .where(CheckSession.venue_id == venue.id, CheckSession.finished_at.is_(None))
        .order_by(CheckSession.started_at.desc())
    ).scalars().first()


def latest_session(db: Session, venue: Venue) -> CheckSession | None:
    return db.execute(
        select(CheckSession).where(CheckSession.venue_id == venue.id).order_by(CheckSession.started_at.desc())
    ).scalars().first()


def get_or_start_session(db: Session, venue: Venue) -> CheckSession:
    return open_session(db, venue) or start_session(db, venue)


def rules_by_id() -> dict[str, Rule]:
    return {r.id: r for r in get_rulebook().rules}


def set_answer(db: Session, session: CheckSession, rule_id: str, status: str,
               comment: str | None = None, photo_path: str | None = None) -> Answer:
    if status not in STATUSES:
        raise ValueError(f"недопустимый статус {status!r}")
    if rule_id not in session.applicable_ids:
        raise ValueError("требование не применимо к этой сессии")
    a = db.execute(
        select(Answer).where(Answer.session_id == session.id, Answer.rule_id == rule_id)
    ).scalars().first()
    if a is None:
        a = Answer(session_id=session.id, rule_id=rule_id, status=status)
        db.add(a)
    a.status = status
    if comment is not None:
        a.comment = comment
    if photo_path is not None:
        a.photo_path = photo_path
    a.updated_at = utcnow()
    db.flush()
    return a


def progress(session: CheckSession) -> dict:
    answered = {a.rule_id: a for a in session.answers}
    total = len(session.applicable_ids)
    counts = {s: 0 for s in STATUSES}
    for rid in session.applicable_ids:
        if rid in answered:
            counts[answered[rid].status] += 1
    done = sum(counts.values())
    return {"total": total, "answered": done, "remaining": total - done, **counts}


def finish_session(db: Session, session: CheckSession, owner_id: int) -> list[Task]:
    """Закрывает самопроверку и превращает каждое нарушение в задачу со сроком из справочника."""
    rules = rules_by_id()
    created: list[Task] = []
    existing = {t.rule_id for t in session.tasks}
    for a in session.answers:
        if a.status != "violation" or a.rule_id in existing:
            continue
        rule = rules.get(a.rule_id)
        if rule is None:
            continue
        due = utcnow() + timedelta(days=rule.fix_days)
        # 15:00 UTC = 18:00 по Москве: напоминание о сроке приходит в рабочее время, а не ночью.
        due = due.replace(hour=15, minute=0, second=0, microsecond=0)
        t = Task(
            session_id=session.id,
            venue_id=session.venue_id,
            owner_id=owner_id,
            rule_id=a.rule_id,
            title=rule.title,
            due_date=due,
            photo_before=a.photo_path,
        )
        db.add(t)
        session.tasks.append(t)
        created.append(t)
    session.finished_at = utcnow()
    db.flush()
    return created


def open_tasks(db: Session, owner_id: int, venue_id: int | None = None) -> list[Task]:
    q = select(Task).where(Task.owner_id == owner_id, Task.status == "open")
    if venue_id is not None:
        q = q.where(Task.venue_id == venue_id)
    return list(db.execute(q.order_by(Task.due_date.asc())).scalars().all())


def tasks_for_assignee(db: Session, user_id: int) -> list[Task]:
    return list(
        db.execute(
            select(Task).where(Task.assignee_user_id == user_id, Task.status == "open").order_by(Task.due_date.asc())
        ).scalars().all()
    )


def complete_task(db: Session, task: Task, photo_after: str | None = None) -> Task:
    task.status = "done"
    task.done_at = utcnow()
    if photo_after:
        task.photo_after = photo_after
    db.flush()
    return task


def session_report(session: CheckSession) -> dict:
    """Структура для акта и для экрана итога."""
    rules = rules_by_id()
    answers = {a.rule_id: a for a in session.answers}
    items = []
    for v in session.verdicts:
        r = rules.get(v["rule_id"])
        if r is None:
            continue
        a = answers.get(r.id)
        items.append(
            {
                "rule": r,
                "applicable": v["applicable"],
                "reasons": v.get("reasons", []),
                "other_domain": v.get("other_domain", ""),
                "status": a.status if a else None,
                "comment": a.comment if a else "",
                "photo_path": a.photo_path if a else None,
            }
        )
    counts = {"ok": 0, "violation": 0, "unknown": 0, "unanswered": 0}
    for it in items:
        if not it["applicable"]:
            continue
        counts[it["status"] or "unanswered"] += 1
    return {"items": items, "counts": counts, "tasks": list(session.tasks)}


def local_date(dt: datetime, offset_hours: int) -> str:
    return (dt + timedelta(hours=offset_hours)).strftime("%d.%m.%Y")


def local_datetime(dt: datetime, offset_hours: int) -> str:
    return (dt + timedelta(hours=offset_hours)).strftime("%d.%m.%Y %H:%M")
