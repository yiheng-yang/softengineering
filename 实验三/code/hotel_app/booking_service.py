"""获取报价和提交预订；身份来自调用方可信会话，登录将在后续接入。"""

import logging
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Callable, List

from hotel_app.booking import (
    BookingError,
    BookingRequest,
    NightPrice,
    OrderReceipt,
    Quote,
)
from hotel_app.booking_repository import BookingRepository
from hotel_app.database import operation, utc_now

LOGGER = logging.getLogger(__name__)
BEIJING = timezone(timedelta(hours=8))


class BookingService:
    """协调规则和存储，不依赖 Tkinter。"""

    def __init__(
        self, connection: sqlite3.Connection, clock: Callable[[], datetime] = utc_now
    ) -> None:
        self.connection = connection
        self.repository = BookingRepository(connection)
        self.clock = clock

    def _authorize(self, actor_id: str) -> None:
        if (
            not isinstance(actor_id, str)
            or self.repository.user_role(actor_id) != "CUSTOMER"
        ):
            raise BookingError("FORBIDDEN", "只有已登录顾客可以预订")

    def _validate_catalog(self, request: BookingRequest, now: datetime) -> None:
        if request.check_in < now.astimezone(BEIJING).date():
            raise BookingError("INVALID_INPUT", "入住日期不能早于北京时间今天")
        status = self.repository.hotel_status(request.hotel_id)
        if status is None:
            raise BookingError("INVALID_INPUT", "酒店不存在")
        if status != "OPEN":
            raise BookingError("UNAVAILABLE", "酒店目前不营业")
        for room in request.rooms:
            room_type = self.repository.room_type(room.room_type_id)
            if room_type is None or room_type["hotel_id"] != request.hotel_id:
                raise BookingError("INVALID_INPUT", "房型不属于所选酒店")
            if room.guests > room_type["capacity"]:
                raise BookingError("INVALID_INPUT", "入住人数超过房型容量")

    def _available_quote(
        self, request: BookingRequest, rows: List[sqlite3.Row]
    ) -> Quote:
        counts = Counter(room.room_type_id for room in request.rooms)
        if len(rows) != len(counts) * len(request.nights):
            raise BookingError("UNAVAILABLE", "部分住宿日期尚未开放房源")
        prices = []
        for row in rows:
            available = row["total_rooms"] - row["occupied_rooms"]
            if (
                row["sale_status"] != "OPEN"
                or available < counts[row["room_type_id"]]
            ):
                raise BookingError("UNAVAILABLE", "部分住宿日期库存不足或停售")
            prices.append(NightPrice(
                row["room_type_id"], date.fromisoformat(row["stay_date"]),
                row["price_cents"]
            ))
        return Quote(request, tuple(sorted(prices)))

    @staticmethod
    def _request(request: BookingRequest) -> BookingRequest:
        if not isinstance(request, BookingRequest):
            raise BookingError("INVALID_INPUT", "预订内容格式不正确")
        return request.normalized()

    def get_quote(self, actor_id: str, request: BookingRequest) -> Quote:
        """一致的读快照，不占用库存；不保证稍后提交仍有房或同价。"""
        request = self._request(request)
        with operation(self.connection, LOGGER, write=False):
            self._authorize(actor_id)
            self._validate_catalog(request, utc_now(self.clock))
            return self._available_quote(request, self.repository.inventory(request))

    def submit(
        self, actor_id: str, request: BookingRequest, accepted_quote: Quote,
        request_key: str, *, allow_duplicate: bool = False,
    ) -> OrderReceipt:
        """提交成功或整体回滚；调用者应保留请求标识以处理响应丢失。"""
        request = self._request(request)
        if not isinstance(request_key, str) or not request_key.strip():
            raise BookingError("INVALID_INPUT", "请求标识不能为空")
        if type(allow_duplicate) is not bool or not isinstance(accepted_quote, Quote):
            raise BookingError("INVALID_INPUT", "提交参数不合法")
        accepted_quote = accepted_quote.normalized(request)
        with operation(self.connection, LOGGER):
            self._authorize(actor_id)
            previous = self.repository.handled_request(actor_id, request_key)
            if previous is not None:
                if previous["payload_digest"] != accepted_quote.digest():
                    raise BookingError("REQUEST_CONFLICT", "请求标识已用于不同内容")
                return self.repository.receipt(previous["order_id"])
            now = utc_now(self.clock)
            self._validate_catalog(request, now)
            matches = self.repository.matching_orders(actor_id, request, now)
            if matches and not allow_duplicate:
                raise BookingError("DUPLICATE_BOOKING", "已存在相同内容订单", matches)
            inventory = self.repository.inventory(request)
            current_quote = self._available_quote(request, inventory)
            if current_quote.prices != accepted_quote.prices:
                raise BookingError("PRICE_CHANGED", "逐晚价格已变化，请重新获取并确认报价")
            return self.repository.save_booking(
                actor_id, request_key, current_quote, inventory, utc_now(self.clock)
            )
