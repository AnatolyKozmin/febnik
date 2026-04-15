"""Тексты справки в зависимости от роли."""

from febnik.config import can_handout, is_org
from febnik.web.deps import panel_base_url


def _participant_commands_only() -> str:
    return (
        "Команды:\n"
        "/start — регистрация и эта справка\n"
        "/score — сколько у вас ФЭБартов\n"
        "/activities — интерактивы на сегодня\n"
        "/prizes — призы и стоимость\n"
        "/claim — оформить приз (спишутся ФЭБарты; заберите на стойке)\n"
        "/request — заявка на начисление ФЭБ (решение принимает оргкомитет)\n"
        "/cancel — отменить текущий ввод"
    )


def build_help_text(telegram_id: int) -> str:
    base = panel_base_url()
    text = _participant_commands_only()

    if is_org(telegram_id):
        text += (
            "\n\n— Оргкомитет / стойка —\n"
            "/award — начислить ФЭБ за интерактив\n"
            "/handout номер_заявки — отметить выдачу приза\n"
            "/sync — подтянуть данные из Google Таблиц (если настроено)\n"
            "/export_balances — выгрузить балансы в Google (если настроено)\n\n"
            f"Панель управления (логин и пароль): {base}/admin/"
        )
    elif can_handout(telegram_id):
        text += (
            "\n\n— Стойка призов —\n"
            "/handout номер_заявки — отметить выдачу приза участнику"
        )

    return text
