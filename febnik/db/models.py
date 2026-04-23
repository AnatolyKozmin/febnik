import enum
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from febnik.db.base import Base


class UserRole(str, enum.Enum):
    participant = "participant"
    org = "org"


class ClaimStatus(str, enum.Enum):
    awaiting_handout = "awaiting_handout"
    handed_out = "handed_out"
    cancelled = "cancelled"


class TxKind(str, enum.Enum):
    interactive_reward = "interactive_reward"
    prize_purchase = "prize_purchase"
    admin_adjust = "admin_adjust"
    balance_request_grant = "balance_request_grant"


class BalanceRequestStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(512))
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.participant)
    balance_feb: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="user")
    claims: Mapped[list["Claim"]] = relationship(back_populates="user")
    balance_requests: Mapped[list["BalanceRequest"]] = relationship(back_populates="user")


class Activity(Base):
    """Интерактив (веб-админка; опционально синхронизация с Google)."""

    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sheet_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    time_text: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(512))
    reward_feb: Mapped[int] = mapped_column(Integer, default=0)
    responsible_username: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Prize(Base):
    __tablename__ = "prizes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sheet_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String(512))
    cost_feb: Mapped[int] = mapped_column(Integer)
    stock: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    claims: Mapped[list["Claim"]] = relationship(back_populates="prize")


class BalanceRequest(Base):
    """Заявка участника на начисление ФЭБарт (модерация в веб-админке)."""

    __tablename__ = "balance_requests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    amount_feb: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[BalanceRequestStatus] = mapped_column(
        SAEnum(BalanceRequestStatus), default=BalanceRequestStatus.pending
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="balance_requests")


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    prize_id: Mapped[int] = mapped_column(ForeignKey("prizes.id", ondelete="CASCADE"))
    status: Mapped[ClaimStatus] = mapped_column(SAEnum(ClaimStatus), default=ClaimStatus.awaiting_handout)
    cost_feb: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    handed_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    handed_out_by_tg: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    user: Mapped["User"] = relationship(back_populates="claims")
    prize: Mapped["Prize"] = relationship(back_populates="claims")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    delta: Mapped[int] = mapped_column(Integer)
    kind: Mapped[TxKind] = mapped_column(SAEnum(TxKind))
    balance_after: Mapped[int] = mapped_column(Integer)
    activity_id: Mapped[int | None] = mapped_column(ForeignKey("activities.id", ondelete="SET NULL"), nullable=True)
    prize_id: Mapped[int | None] = mapped_column(ForeignKey("prizes.id", ondelete="SET NULL"), nullable=True)
    claim_id: Mapped[int | None] = mapped_column(ForeignKey("claims.id", ondelete="SET NULL"), nullable=True)
    balance_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("balance_requests.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="transactions")
