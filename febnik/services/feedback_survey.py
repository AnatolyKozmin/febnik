"""Анкеты обратной связи по дням 1–3: настройки и отправка ответов."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from febnik.config import get_settings
from febnik.db.models import FeedbackResponse, FeedbackSurveySlot, User
from febnik.services.balance import apply_feedback_survey_reward
from febnik.survey_content import get_survey_day, normalize_survey_answers, validate_survey_answers


async def ensure_feedback_slots(session: AsyncSession) -> None:
    for d in (1, 2, 3):
        if await session.get(FeedbackSurveySlot, d) is None:
            session.add(FeedbackSurveySlot(day=d, is_open=False, reward_feb=0, title=None))
    await session.flush()


async def load_all_slots(session: AsyncSession) -> dict[int, FeedbackSurveySlot]:
    await ensure_feedback_slots(session)
    r = await session.execute(select(FeedbackSurveySlot).order_by(FeedbackSurveySlot.day))
    m = {row.day: row for row in r.scalars().all()}
    if set(m.keys()) != {1, 2, 3}:
        await ensure_feedback_slots(session)
        r2 = await session.execute(select(FeedbackSurveySlot).order_by(FeedbackSurveySlot.day))
        m = {row.day: row for row in r2.scalars().all()}
    return m


async def user_has_response(session: AsyncSession, user_id: int, day: int) -> bool:
    rid = await session.scalar(
        select(FeedbackResponse.id).where(
            FeedbackResponse.user_id == user_id,
            FeedbackResponse.day == day,
        )
    )
    return rid is not None


async def submit_feedback(
    session: AsyncSession,
    user: User,
    day: int,
    answers: dict[str, object],
) -> tuple[FeedbackResponse, int]:
    """answers — распарсенный JSON с фронта. Возвращает запись и сколько ФЭБарт начислено."""
    if day not in (1, 2, 3):
        raise ValueError("Некорректный день анкеты.")
    if get_survey_day(day) is None:
        raise ValueError("Анкета для этого дня не настроена.")
    err = validate_survey_answers(day, answers)
    if err:
        raise ValueError(err)

    await ensure_feedback_slots(session)
    slot = await session.get(FeedbackSurveySlot, day)
    if not slot or not slot.is_open:
        raise ValueError("Приём ответов по этой анкете сейчас закрыт.")
    if await user_has_response(session, user.id, day):
        raise ValueError("Вы уже проходили анкету за этот день.")

    normalized = normalize_survey_answers(day, answers)
    blob = json.dumps(normalized, ensure_ascii=False)

    settings = get_settings()

    row = FeedbackResponse(
        user_id=user.id,
        day=day,
        answers_json=blob,
    )
    session.add(row)
    await session.flush()

    slot_live = await session.get(FeedbackSurveySlot, day)
    reward = 0
    if slot_live and slot_live.is_open:
        reward = max(0, min(int(slot_live.reward_feb), settings.max_qr_award_feb))

    granted = 0
    if reward > 0:
        await apply_feedback_survey_reward(
            session,
            user,
            reward,
            day,
            note=f"ОС день {day}, награда {reward} ФЭБарт",
        )
        granted = reward
    await session.flush()
    return row, granted


async def list_responses_for_day(
    session: AsyncSession,
    day: int,
) -> list[FeedbackResponse]:
    r = await session.execute(
        select(FeedbackResponse)
        .where(FeedbackResponse.day == day)
        .options(selectinload(FeedbackResponse.user))
        .order_by(FeedbackResponse.created_at.desc())
    )
    return list(r.scalars().all())
