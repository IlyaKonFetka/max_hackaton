"""Модели данных и сессия SQLAlchemy.

Пользовательские данные — в БД. Справочник требований — в YAML (см. engine.rules):
он читается и ревьюится глазами, версия фиксируется вместе с кодом.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # user_id из MAX
    first_name: Mapped[str] = mapped_column(String(200), default="")
    last_name: Mapped[str] = mapped_column(String(200), default="")
    username: Mapped[str | None] = mapped_column(String(200), nullable=True)
    chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # диалог с ботом
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BotState(Base):
    """Состояние диалога (конечный автомат онбординга) — в БД, чтобы переживать перезапуск."""

    __tablename__ = "bot_state"
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[str] = mapped_column(String(64), default="idle")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Venue(Base):
    """Объект (точка общепита) с профилем."""

    __tablename__ = "venues"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    region: Mapped[str] = mapped_column(String(200), default="")
    lat: Mapped[float | None] = mapped_column(nullable=True)
    lon: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    sessions: Mapped[list["CheckSession"]] = relationship(back_populates="venue")


class CheckSession(Base):
    """Одна самопроверка по применимым требованиям."""

    __tablename__ = "check_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"), index=True)
    rules_version: Mapped[str] = mapped_column(String(32), default="")
    # Снимок применимости на момент старта: [{rule_id, applicable, reasons[]}]
    verdicts: Mapped[list] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    act_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    venue: Mapped[Venue] = relationship(back_populates="sessions")
    answers: Mapped[list["Answer"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    tasks: Mapped[list["Task"]] = relationship(back_populates="session")

    @property
    def applicable_ids(self) -> list[str]:
        return [v["rule_id"] for v in self.verdicts if v.get("applicable")]


class Answer(Base):
    __tablename__ = "answers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("check_sessions.id"), index=True)
    rule_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16))  # ok | violation | unknown
    comment: Mapped[str] = mapped_column(Text, default="")
    photo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    session: Mapped[CheckSession] = relationship(back_populates="answers")


class Task(Base):
    """Нарушение, превращённое в задачу со сроком и ответственным."""

    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("check_sessions.id"), index=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    rule_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(500))
    due_date: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | done
    assignee_name: Mapped[str] = mapped_column(String(200), default="")
    assignee_phone: Mapped[str] = mapped_column(String(64), default="")
    assignee_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    photo_before: Mapped[str | None] = mapped_column(String(500), nullable=True)
    photo_after: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_reminded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    overdue_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    session: Mapped[CheckSession] = relationship(back_populates="tasks")


class ShiftCheck(Base):
    """Короткий чек-лист смены по требованиям с периодичностью shift — живёт в боте.

    Пункты хранятся отдельной таблицей (ShiftItem), а не одним JSON-полем: тогда отметка
    одного пункта — это запись в одну строку и не может затереть отметку другого пункта,
    сделанную почти одновременно (при read-modify-write общего блоба второй commit
    переписывал весь снимок, включая ещё не увиденное им чужое изменение).
    """

    __tablename__ = "shift_checks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"), index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD локальная дата
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    items: Mapped[list["ShiftItem"]] = relationship(back_populates="check", cascade="all, delete-orphan")
    photos: Mapped[list["ShiftPhoto"]] = relationship(back_populates="check", cascade="all, delete-orphan")


class ShiftItem(Base):
    """Один пункт чек-листа смены. Уникален по (check, rule) — отметка обновляет ровно эту строку."""

    __tablename__ = "shift_items"
    __table_args__ = (UniqueConstraint("shift_check_id", "rule_id", name="uq_shift_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shift_check_id: Mapped[int] = mapped_column(ForeignKey("shift_checks.id"), index=True)
    rule_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # None | "ok"
    photo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    check: Mapped[ShiftCheck] = relationship(back_populates="items")


class ShiftPhoto(Base):
    """Фото, присланное к смене вне конкретного пункта (например, снимок журнала одним кадром)."""

    __tablename__ = "shift_photos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shift_check_id: Mapped[int] = mapped_column(ForeignKey("shift_checks.id"), index=True)
    photo_path: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    check: Mapped[ShiftCheck] = relationship(back_populates="photos")


engine = create_engine(settings.db_url, future=True, connect_args={"check_same_thread": False} if settings.db_url.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def init_db() -> None:
    Base.metadata.create_all(engine)


@contextmanager
def db_session() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
