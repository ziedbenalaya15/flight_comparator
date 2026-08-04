from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Route(Base):
    __tablename__ = "routes"
    __table_args__ = (UniqueConstraint("origin", "destination", name="uq_routes_origin_destination"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    origin: Mapped[str] = mapped_column(String(3))
    destination: Mapped[str] = mapped_column(String(3))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    snapshots: Mapped[list["PriceSnapshot"]] = relationship(back_populates="route")


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"
    __table_args__ = (
        Index("ix_price_snapshots_route_currency_fetched", "route_id", "currency", "fetched_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id", ondelete="CASCADE"))
    currency: Mapped[str] = mapped_column(String(3))
    price: Mapped[float] = mapped_column(Numeric(10, 2))
    source: Mapped[str] = mapped_column(String(20), default="travelpayouts")
    cabin: Mapped[str] = mapped_column(String(20), default="economy")
    transfers: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    return_transfers: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    airline: Mapped[str | None] = mapped_column(String(8), nullable=True)
    depart_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    return_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    route: Mapped[Route] = relationship(back_populates="snapshots")


class Baseline(Base):
    __tablename__ = "baselines"
    __table_args__ = (UniqueConstraint("route_id", "currency", name="uq_baselines_route_currency"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id", ondelete="CASCADE"))
    currency: Mapped[str] = mapped_column(String(3))
    median_30d: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    p10: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    p25: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    stddev: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(20))  # record | seuil | cross_devise | chute
    currency: Mapped[str] = mapped_column(String(3))
    price: Mapped[float] = mapped_column(Numeric(10, 2))
    confidence: Mapped[str] = mapped_column(String(20))  # CONFIRME_LIVE | CACHE_SEULEMENT
    dedup_key: Mapped[str] = mapped_column(String(120), unique=True)
    email_status: Mapped[str] = mapped_column(String(20), default="pending")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AppConfig(Base):
    __tablename__ = "config"

    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
