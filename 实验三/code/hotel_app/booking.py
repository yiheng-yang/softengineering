"""预订数据契约与纯业务规则，不依赖数据库或界面。"""

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Tuple


class BookingError(Exception):
    """可供界面识别的失败类型；重复预订时附上已有订单编号。"""

    def __init__(self, code: str, message: str, order_ids: Tuple[str, ...] = ()):
        super().__init__(message)
        self.code = code
        self.order_ids = order_ids


@dataclass(frozen=True, order=True)
class RoomRequest:
    """一间房的预订要求，不指定实体房号。"""

    room_type_id: str
    guests: int


@dataclass(frozen=True)
class BookingRequest:
    """同一家酒店、相同住宿日期的一至五间房。"""

    hotel_id: str
    check_in: date
    check_out: date
    rooms: Tuple[RoomRequest, ...]

    def normalized(self) -> "BookingRequest":
        """验证不需要外部数据的约束，再按房型和人数稳定排序。"""
        if not isinstance(self.hotel_id, str) or not self.hotel_id.strip():
            raise BookingError("INVALID_INPUT", "请选择酒店")
        if type(self.check_in) is not date or type(self.check_out) is not date:
            raise BookingError("INVALID_INPUT", "住宿日期必须为日期值")
        if not 1 <= (self.check_out - self.check_in).days <= 14:
            raise BookingError("INVALID_INPUT", "住宿时间必须为1至14晚")
        if not isinstance(self.rooms, tuple) or not 1 <= len(self.rooms) <= 5:
            raise BookingError("INVALID_INPUT", "每单必须预订1至5间房")
        for room in self.rooms:
            if (
                not isinstance(room, RoomRequest)
                or not isinstance(room.room_type_id, str)
                or not room.room_type_id.strip()
                or type(room.guests) is not int
                or room.guests <= 0
            ):
                raise BookingError("INVALID_INPUT", "房型及入住人数不合法")
        return BookingRequest(
            self.hotel_id, self.check_in, self.check_out, tuple(sorted(self.rooms))
        )

    @property
    def nights(self) -> Tuple[date, ...]:
        """包含入住日，不包含退房日；在 normalized 校验后使用。"""
        return tuple(
            self.check_in + timedelta(days=offset)
            for offset in range((self.check_out - self.check_in).days)
        )


@dataclass(frozen=True, order=True)
class NightPrice:
    """某房型某晚每间房的成交单价，单位为分。"""

    room_type_id: str
    stay_date: date
    price_cents: int


@dataclass(frozen=True)
class Quote:
    """报价绑定预订内容；同一房型同一晚只保存一个单价。"""

    request: BookingRequest
    prices: Tuple[NightPrice, ...]

    def normalized(self, request: BookingRequest) -> "Quote":
        """拒绝缺漏、重复或属于另一份预订内容的认可报价。"""
        if not isinstance(self.request, BookingRequest):
            raise BookingError("INVALID_INPUT", "报价缺少预订内容")
        if self.request.normalized() != request:
            raise BookingError("INVALID_INPUT", "报价与预订内容不一致")
        if not isinstance(self.prices, tuple):
            raise BookingError("INVALID_INPUT", "逐晚报价格式不正确")
        actual = []
        for price in self.prices:
            if (
                not isinstance(price, NightPrice)
                or not isinstance(price.room_type_id, str)
                or type(price.stay_date) is not date
                or type(price.price_cents) is not int
                or price.price_cents < 0
            ):
                raise BookingError("INVALID_INPUT", "逐晚报价不合法")
            actual.append((price.room_type_id, price.stay_date))
        expected = {
            (room.room_type_id, night) for room in request.rooms
            for night in request.nights
        }
        if len(actual) != len(expected) or set(actual) != expected:
            raise BookingError("INVALID_INPUT", "逐晚报价有缺漏或重复")
        return Quote(request, tuple(sorted(self.prices)))

    @property
    def total_cents(self) -> int:
        """整数分相加，无浮点误差；同房型按预订间数计价。"""
        counts = Counter(room.room_type_id for room in self.request.rooms)
        return sum(
            price.price_cents * counts[price.room_type_id] for price in self.prices
        )

    def digest(self) -> str:
        """摘要包含认可报价；房间清单重排不会改变请求含义。"""
        payload = {
            "hotel": self.request.hotel_id,
            "check_in": self.request.check_in.isoformat(),
            "check_out": self.request.check_out.isoformat(),
            "rooms": [(room.room_type_id, room.guests) for room in self.request.rooms],
            "prices": [
                (price.room_type_id, price.stay_date.isoformat(), price.price_cents)
                for price in self.prices
            ],
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OrderReceipt:
    """提交结果；重复提交返回原订单的当前状态和原确认期限。"""

    order_id: str
    status: str
    total_cents: int
    created_at: datetime

    @property
    def confirmation_deadline(self) -> datetime:
        """确认截止时间固定为创建时间后15分钟，不因重试延长。"""
        return self.created_at + timedelta(minutes=15)
