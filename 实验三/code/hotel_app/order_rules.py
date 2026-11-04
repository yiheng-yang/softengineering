"""订单生命周期规则：不读取数据库，不执行资金操作。"""

from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from hotel_app.booking import BookingError

BEIJING = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class Order:
    """订单当前快照；日期与时间字符串遵循数据库既有格式。"""

    id: str
    customer_id: str
    hotel_id: str
    check_in_date: str
    check_out_date: str
    created_at: str
    status: str
    total_cents: int
    fee_cents: int
    close_reason: Optional[str]


def cancellation_fee(total_cents: int) -> int:
    """顾客取消和未到店共用20%费用，四舍五入到分。"""
    return int((Decimal(total_cents) * Decimal("0.20"))
               .quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def transition(order: Order, action: str, now: datetime, reason: str) -> Order:
    """返回新快照；终止订单返回现状，不重放历史操作的响应。"""
    created = datetime.fromisoformat(order.created_at.replace("Z", "+00:00"))
    if order.status == "PENDING" and now >= created + timedelta(minutes=15):
        return replace(order, status="EXPIRED", fee_cents=0, close_reason="TIMEOUT")
    arrival = datetime.fromisoformat(order.check_in_date).replace(tzinfo=BEIJING)
    if order.status == "CONFIRMED" and now >= arrival + timedelta(days=1, hours=12):
        return replace(order, status="CANCELLED", close_reason="NO_SHOW",
                       fee_cents=cancellation_fee(order.total_cents))
    if order.status in ("COMPLETED", "CANCELLED", "REJECTED", "EXPIRED"):
        return order
    if action in ("EXPIRE", "NO_SHOW") or (
        action == "CONFIRM" and order.status == "CONFIRMED"
    ):
        return order
    if action in ("CONFIRM", "REJECT") and order.status == "PENDING":
        return replace(
            order, status="CONFIRMED" if action == "CONFIRM" else "REJECTED",
            close_reason=None if action == "CONFIRM" else "REJECTED: " + reason,
        )
    if action == "CANCEL" and order.status in ("PENDING", "CONFIRMED"):
        cutoff = datetime.combine(
            datetime.fromisoformat(order.check_in_date).date(), time(12), BEIJING
        )
        if now >= cutoff:
            raise BookingError("CUTOFF_PASSED", "已到入住日中午12点，不能普通取消")
        fee = 0 if order.status == "PENDING" else cancellation_fee(order.total_cents)
        return replace(
            order, status="CANCELLED", fee_cents=fee, close_reason="CUSTOMER"
        )
    if action == "HOTEL_CANCEL" and order.status == "CONFIRMED":
        return replace(
            order, status="CANCELLED", fee_cents=0, close_reason="HOTEL: " + reason
        )
    if action == "CHECK_IN" and order.status == "CONFIRMED":
        if now < arrival:
            raise BookingError("TOO_EARLY", "不能提前入住")
        return replace(order, status="CHECKED_IN")
    if action == "CHECK_OUT" and order.status == "CHECKED_IN":
        if now.astimezone(BEIJING).date().isoformat() < order.check_out_date:
            raise BookingError("TOO_EARLY", "本阶段不支持提前退房")
        return replace(order, status="COMPLETED")
    raise BookingError("INVALID_STATE", "当前订单状态不允许此操作")
