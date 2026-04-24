from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from febnik.db.models import (
    BalanceRequest,
    BalanceRequestStatus,
    Claim,
    ClaimStatus,
    Prize,
    ScanAwardIdempotency,
    Transaction,
    TxKind,
    User,
)


async def get_user_by_telegram(session: AsyncSession, telegram_id: int) -> User | None:
    r = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return r.scalar_one_or_none()


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    from sqlalchemy import func

    u = username.strip().lstrip("@").lower()
    if not u:
        return None
    r = await session.execute(select(User).where(func.lower(User.username) == u))
    return r.scalar_one_or_none()


async def apply_interactive_reward(
    session: AsyncSession,
    user: User,
    amount: int,
    activity_id: int,
    note: str | None = None,
) -> Transaction:
    user.balance_feb += amount
    tx = Transaction(
        user_id=user.id,
        delta=amount,
        kind=TxKind.interactive_reward,
        balance_after=user.balance_feb,
        activity_id=activity_id,
        note=note,
    )
    session.add(tx)
    await session.flush()
    return tx


async def apply_participant_scan_reward(
    session: AsyncSession,
    user: User,
    amount: int,
    note: str | None = None,
    *,
    _flush: bool = True,
) -> Transaction:
    """Начисление с экрана скана QR без привязки к интерактиву (activity_id пустой)."""
    user.balance_feb += amount
    tx = Transaction(
        user_id=user.id,
        delta=amount,
        kind=TxKind.interactive_reward,
        balance_after=user.balance_feb,
        activity_id=None,
        note=note or "Начисление по QR",
    )
    session.add(tx)
    if _flush:
        await session.flush()
    return tx


def _normalize_idempotency_key(raw: str | None) -> str | None:
    if not raw:
        return None
    key = raw.strip()
    if not key or len(key) > 64:
        return None
    if not all(c.isalnum() or c in "-_" for c in key):
        return None
    return key


async def apply_participant_scan_reward_idempotent(
    session: AsyncSession,
    user: User,
    amount: int,
    note: str | None = None,
    *,
    idempotency_key: str | None = None,
) -> tuple[Transaction, bool]:
    """Начисление по QR с защитой от повторной отправки. Возвращает (транзакция, is_replay)."""
    key = _normalize_idempotency_key(idempotency_key)
    if not key:
        tx = await apply_participant_scan_reward(session, user, amount, note=note)
        return tx, False

    existing = await session.scalar(
        select(ScanAwardIdempotency).where(ScanAwardIdempotency.idempotency_key == key)
    )
    if existing is not None:
        if existing.user_id != user.id or existing.amount_feb != amount:
            raise ValueError(
                "Ключ запроса уже использован для другого начисления. Сгенерируйте новый QR или обновите страницу."
            )
        tx = await session.get(Transaction, existing.transaction_id)
        if tx is None:
            raise ValueError("Запись о начислении не найдена. Обратитесь к администратору.")
        await session.refresh(user)
        return tx, True

    try:
        async with session.begin_nested():
            tx = await apply_participant_scan_reward(session, user, amount, note=note, _flush=False)
            await session.flush()
            session.add(
                ScanAwardIdempotency(
                    idempotency_key=key,
                    user_id=user.id,
                    amount_feb=amount,
                    transaction_id=tx.id,
                )
            )
            await session.flush()
    except IntegrityError:
        existing2 = await session.scalar(
            select(ScanAwardIdempotency).where(ScanAwardIdempotency.idempotency_key == key)
        )
        if existing2 and existing2.user_id == user.id and existing2.amount_feb == amount:
            tx2 = await session.get(Transaction, existing2.transaction_id)
            if tx2 is None:
                raise ValueError("Коллизия при сохранении. Попробуйте ещё раз.") from None
            await session.refresh(user)
            return tx2, True
        raise ValueError("Не удалось сохранить начисление. Попробуйте ещё раз.") from None

    return tx, False


async def apply_feedback_survey_reward(
    session: AsyncSession,
    user: User,
    amount: int,
    day: int,
    note: str | None = None,
) -> Transaction:
    if amount < 1:
        raise ValueError("Сумма награды за анкету должна быть не меньше 1 ФЭБарт.")
    user.balance_feb += amount
    tx = Transaction(
        user_id=user.id,
        delta=amount,
        kind=TxKind.feedback_reward,
        balance_after=user.balance_feb,
        activity_id=None,
        note=note or f"Анкета ОС, день {day}",
    )
    session.add(tx)
    await session.flush()
    return tx


async def apply_admin_balance_set(
    session: AsyncSession,
    user: User,
    new_balance: int,
    note: str | None = None,
) -> Transaction | None:
    """Выставить баланс участника вручную (запись admin_adjust)."""
    if new_balance < 0:
        raise ValueError("Баланс не может быть отрицательным.")
    delta = new_balance - user.balance_feb
    if delta == 0:
        return None
    user.balance_feb = new_balance
    tx = Transaction(
        user_id=user.id,
        delta=delta,
        kind=TxKind.admin_adjust,
        balance_after=user.balance_feb,
        note=(note or "").strip() or "Корректировка в админке",
    )
    session.add(tx)
    await session.flush()
    return tx


async def create_prize_claim(
    session: AsyncSession,
    user: User,
    prize: Prize,
) -> tuple[Claim, Transaction]:
    if prize.stock <= 0:
        raise ValueError("Приз закончился.")
    if user.balance_feb < prize.cost_feb:
        raise ValueError("Недостаточно ФЭБартов.")

    prize.stock -= 1
    user.balance_feb -= prize.cost_feb

    claim = Claim(
        user_id=user.id,
        prize_id=prize.id,
        status=ClaimStatus.awaiting_handout,
        cost_feb=prize.cost_feb,
    )
    session.add(claim)
    await session.flush()

    tx = Transaction(
        user_id=user.id,
        delta=-prize.cost_feb,
        kind=TxKind.prize_purchase,
        balance_after=user.balance_feb,
        prize_id=prize.id,
        claim_id=claim.id,
        note=f"Заявка на приз: {prize.name}",
    )
    session.add(tx)
    await session.flush()
    return claim, tx


async def mark_claim_handed_out(
    session: AsyncSession,
    claim: Claim,
    staff_telegram_id: int,
) -> None:
    from datetime import datetime, timezone

    claim.status = ClaimStatus.handed_out
    claim.handed_out_at = datetime.now(timezone.utc)
    claim.handed_out_by_tg = staff_telegram_id


async def has_pending_balance_request(session: AsyncSession, user_id: int) -> bool:
    r = await session.scalar(
        select(BalanceRequest.id).where(
            BalanceRequest.user_id == user_id,
            BalanceRequest.status == BalanceRequestStatus.pending,
        )
    )
    return r is not None


async def approve_balance_request(
    session: AsyncSession,
    req: BalanceRequest,
) -> Transaction:
    from datetime import datetime, timezone

    if req.status != BalanceRequestStatus.pending:
        raise ValueError("Заявка уже обработана.")
    user = await session.get(User, req.user_id)
    if not user:
        raise ValueError("Пользователь не найден.")

    user.balance_feb += req.amount_feb
    req.status = BalanceRequestStatus.approved
    req.resolved_at = datetime.now(timezone.utc)

    tx = Transaction(
        user_id=user.id,
        delta=req.amount_feb,
        kind=TxKind.balance_request_grant,
        balance_after=user.balance_feb,
        balance_request_id=req.id,
        note=req.comment or f"Заявка №{req.id}",
    )
    session.add(tx)
    await session.flush()
    return tx


async def reject_balance_request(
    session: AsyncSession,
    req: BalanceRequest,
    reason: str | None,
) -> None:
    from datetime import datetime, timezone

    if req.status != BalanceRequestStatus.pending:
        raise ValueError("Заявка уже обработана.")
    req.status = BalanceRequestStatus.rejected
    req.resolved_at = datetime.now(timezone.utc)
    req.reject_reason = (reason or "").strip() or None
