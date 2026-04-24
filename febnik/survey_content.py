"""Захардкоженные анкеты ОС по дням (1–3). Редактируйте тексты здесь."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SurveyFieldKind = Literal["rating", "text"]


@dataclass(frozen=True)
class SurveyField:
    id: str
    kind: SurveyFieldKind
    label: str
    required: bool = True
    placeholder: str = ""


@dataclass(frozen=True)
class SurveyDay:
    day: int
    fields: tuple[SurveyField, ...]
    closing_message: str


# ——— День 1 (как в ТЗ) ———
DAY1_FIELDS: tuple[SurveyField, ...] = (
    SurveyField(
        "org_living",
        "rating",
        "Как бы ты оценил(а) организацию проживания/питания/транспорта по шкале от 1 до 10?",
    ),
    SurveyField(
        "decor",
        "rating",
        "Как ты оцениваешь декор проекта от 1 до 10?",
    ),
    SurveyField(
        "icebreaker",
        "text",
        "Понравилось ли тебе проведение айсбрейкинга (игра на сплочение, атомы-молекулы)? Что было не так?",
        placeholder="Твой ответ",
    ),
    SurveyField(
        "masterclass",
        "rating",
        "Как ты оцениваешь проведение творческих мастер-классов? (Создание чего-то своими руками)",
    ),
    SurveyField(
        "interactives",
        "rating",
        "Как ты оцениваешь проведение интерактивов? (Активные треки)",
    ),
    SurveyField(
        "cinema",
        "text",
        "Понравилось ли тебе проведение элемента в тематике кино (игра в правду и своя игра)? Что было не так?",
        placeholder="Твой ответ",
    ),
    SurveyField(
        "disco",
        "rating",
        "Как ты оцениваешь организацию и проведение дискотеки от 1 до 10?",
    ),
    SurveyField(
        "night_quest",
        "text",
        "Понравилось ли тебе проведение ночного квеста? Что было не так?",
        placeholder="Твой ответ",
    ),
    SurveyField(
        "organizers",
        "rating",
        "Оцени работу организаторов (помощь с навигацией, оперативное реагирование на запросы, ответы на вопросы)?",
    ),
    SurveyField(
        "liked_today",
        "text",
        "Что особенно тебе сегодня понравилось/запомнилось?",
        placeholder="Твой ответ",
    ),
    SurveyField(
        "improve",
        "text",
        "Что тебе НЕ понравилось/что хотелось бы улучшить?",
        placeholder="Твой ответ",
    ),
    SurveyField(
        "wishes",
        "text",
        "Если у тебя осталось, что сказать или пожелать организаторам, то можешь сделать это здесь",
        required=False,
        placeholder="Необязательно",
    ),
)

DAY1_CLOSING = (
    "Надеемся, что этот день был для тебя насыщенным и очень интересным! "
    "Набирайся сил, а мы увидимся завтра :)"
)

# ——— Дни 2 и 3: те же вопросы; отличается только финальный текст. Пришлёшь отдельные формулировки — правим здесь. ———
DAY2_CLOSING = (
    "Спасибо за второй день проекта и за честные ответы! "
    "Отдохни и до встречи завтра."
)

DAY3_CLOSING = (
    "Это был финальный день — огромное спасибо, что провели это время с нами! "
    "Береги себя и до новых встреч."
)

SURVEY_BY_DAY: dict[int, SurveyDay] = {
    1: SurveyDay(1, DAY1_FIELDS, DAY1_CLOSING),
    2: SurveyDay(2, DAY1_FIELDS, DAY2_CLOSING),
    3: SurveyDay(3, DAY1_FIELDS, DAY3_CLOSING),
}


def get_survey_day(day: int) -> SurveyDay | None:
    return SURVEY_BY_DAY.get(day)


def validate_survey_answers(day: int, data: dict[str, object]) -> str | None:
    """Возвращает текст ошибки или None, если всё ок."""
    spec = get_survey_day(day)
    if not spec:
        return "Неизвестный день анкеты."
    for f in spec.fields:
        raw = data.get(f.id)
        if f.kind == "rating":
            if raw is None or raw == "":
                if f.required:
                    return f"Выберите оценку: {f.label[:50]}…"
                continue
            try:
                n = int(raw)
            except (TypeError, ValueError):
                return "Некорректная оценка по шкале."
            if n < 1 or n > 10:
                return "Оценка должна быть от 1 до 10."
        else:
            s = (str(raw) if raw is not None else "").strip()
            if f.required and not s:
                return "Заполните все обязательные поля."
            if len(s) > 8000:
                return "Слишком длинный ответ в одном из полей."
    return None


def normalize_survey_answers(day: int, data: dict[str, object]) -> dict[str, str | int]:
    spec = get_survey_day(day)
    if not spec:
        raise ValueError("Неизвестный день.")
    out: dict[str, str | int] = {}
    for f in spec.fields:
        raw = data.get(f.id)
        if f.kind == "rating":
            n = int(raw)
            out[f.id] = n
        else:
            s = (str(raw) if raw is not None else "").strip()
            if not s and not f.required:
                continue
            out[f.id] = s
    return out


def format_answers_for_admin(day: int, answers_json: str | None) -> list[tuple[str, str]]:
    """Пары подпись → значение для отображения в админке."""
    import json

    if not answers_json:
        return []
    try:
        data = json.loads(answers_json)
    except json.JSONDecodeError:
        return [("Сырые данные", answers_json[:2000])]
    if not isinstance(data, dict):
        return [("Сырые данные", str(data)[:2000])]
    leg = data.get("_legacy_v1")
    if isinstance(leg, dict):
        return [
            ("(старая форма) Ответ 1", str(leg.get("answer_liked", ""))),
            ("(старая форма) Ответ 2", str(leg.get("answer_improve", ""))),
            ("(старая форма) Ответ 3", str(leg.get("answer_extra", ""))),
        ]
    spec = get_survey_day(day)
    if not spec:
        return [(k, str(v)) for k, v in data.items()]
    rows: list[tuple[str, str]] = []
    for f in spec.fields:
        v = data.get(f.id)
        if v is None or v == "":
            if f.kind == "text" and not f.required:
                continue
            rows.append((f.label, "—"))
            continue
        if f.kind == "rating":
            rows.append((f.label, f"{v} / 10"))
        else:
            rows.append((f.label, str(v)))
    return rows
