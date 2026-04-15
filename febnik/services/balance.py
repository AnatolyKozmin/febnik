from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from febnik.db.models import (
    BalanceRequest,
    BalanceRequestStatus,
    Claim,
    ClaimStatus,
    Prize,
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
