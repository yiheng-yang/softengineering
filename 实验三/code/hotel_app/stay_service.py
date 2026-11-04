"""整单入住、退房与清洁，不支持提前退房、续住或中途换房。"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, Optional

from hotel_app.booking import BookingError
from hotel_app.database import operation, utc_now
from hotel_app.order_repository import OrderRepository
from hotel_app.order_rules import Order, transition
from hotel_app.order_service import require_staff
from hotel_app.stay_repository import StayRepository

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class StayResult:
    """返回订单当前状态和预订项到实体房间的实际分配。"""

    order: Order
    assignments: Dict[str, str]


class StayService:
    def __init__(
        self, connection: sqlite3.Connection,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.connection = connection
        self.clock = clock
        self.orders = OrderRepository(connection)
        self.rooms = StayRepository(connection)

    def check_in(
        self, actor_id: str, order_id: str, mapping: Dict[str, str]
    ) -> StayResult:
        if not isinstance(mapping, dict) or not mapping or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in mapping.items()
        ):
            raise BookingError("INVALID_INPUT", "请提供预订项与房间编号的对应关系")
        with operation(self.connection, LOGGER):
            old = self.orders.get(order_id)
            require_staff(self.orders, actor_id, old.hotel_id)
            if old.status == "CHECKED_IN":
                existing = {
                    a["item_id"]: a["room_id"] for a in self.rooms.assignments(old.id)
                }
                if mapping != existing:
                    raise BookingError("ASSIGNMENT_CONFLICT", "已入住，不能覆盖原分配")
                return StayResult(old, existing)
            if old.status != "CONFIRMED":
                raise BookingError("INVALID_STATE", "只有已确认订单可以入住")
            now = utc_now(self.clock)
            new = transition(old, "CHECK_IN", now, "")
            if new.status == "CANCELLED":
                self.orders.save(old, new, None, now)
                return StayResult(new, {})  # 正常提交未到店关闭，不回滚库存释放。
            items = self.rooms.items(order_id)
            if set(mapping) != set(items) or len(set(mapping.values())) != len(mapping):
                raise BookingError("INVALID_INPUT", "需为每个预订项分配不同房间")
            for item_id, room_id in mapping.items():
                room = self.rooms.room(room_id)
                if room is None or room["hotel_id"] != old.hotel_id or (
                    room["room_type_id"] != items[item_id]
                ):
                    raise BookingError("INVALID_INPUT", "房间不属于正确酒店或房型")
                if room["status"] != "AVAILABLE":
                    raise BookingError("ROOM_UNAVAILABLE", "房间尚不可入住")
            self.rooms.allocate(mapping, now.isoformat().replace("+00:00", "Z"))
            self.orders.save(old, new, actor_id, now)
            return StayResult(new, dict(mapping))

    def check_out(self, actor_id: str, order_id: str) -> Order:
        with operation(self.connection, LOGGER):
            old = self.orders.get(order_id)
            require_staff(self.orders, actor_id, old.hotel_id)
            if old.status == "COMPLETED":
                return old  # 历史订单重试不能改动已被其他订单重新入住的房间。
            if old.status != "CHECKED_IN":
                raise BookingError("INVALID_STATE", "只有已入住订单可以退房")
            now = utc_now(self.clock)
            new = transition(old, "CHECK_OUT", now, "")
            assignments = self.rooms.assignments(order_id)
            if {a["item_id"] for a in assignments} != set(self.rooms.items(order_id)):
                raise BookingError("ASSIGNMENT_CONFLICT", "入住分配不完整")
            self.rooms.depart(assignments, now.isoformat().replace("+00:00", "Z"))
            self.orders.save(old, new, actor_id, now)
            return new

    def complete_cleaning(self, actor_id: str, room_id: str) -> str:
        with operation(self.connection, LOGGER):
            room = self.rooms.room(room_id)
            if room is None:
                raise BookingError("NOT_FOUND", "房间不存在")
            require_staff(self.orders, actor_id, room["hotel_id"])
            if room["status"] != "AVAILABLE":
                timestamp = utc_now(self.clock).isoformat().replace("+00:00", "Z")
                self.rooms.clean(room, actor_id, timestamp)
            return "AVAILABLE"
