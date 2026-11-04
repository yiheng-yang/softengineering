"""预订存储操作；所有写入共享服务开启的事务。"""

import sqlite3
from collections import Counter
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

from hotel_app.booking import BookingError, BookingRequest, OrderReceipt, Quote


class BookingRepository:
    """以明确的业务查询封装 SQL，不提交事务。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def user_role(self, user_id: str) -> Optional[str]:
        """读取当前用户角色；此方法本身不提供登录认证。"""
        row = self.connection.execute(
            "SELECT role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return row["role"] if row else None

    def hotel_status(self, hotel_id: str) -> Optional[str]:
        """返回酒店营业状态或不存在。"""
        row = self.connection.execute(
            "SELECT status FROM hotels WHERE id = ?", (hotel_id,)
        ).fetchone()
        return row["status"] if row else None

    def room_type(self, room_type_id: str) -> Optional[sqlite3.Row]:
        """返回房型所属酒店及容量。"""
        return self.connection.execute(
            "SELECT hotel_id, capacity FROM room_types WHERE id = ?", (room_type_id,)
        ).fetchone()

    def inventory(self, request: BookingRequest) -> List[sqlite3.Row]:
        """按固定顺序读取各房型全部住宿晚，缺失记录由服务判定不可售。"""
        rows = []
        for room_type_id in sorted({room.room_type_id for room in request.rooms}):
            rows.extend(self.connection.execute(
                "SELECT * FROM daily_inventory WHERE room_type_id = ? "
                "AND stay_date >= ? AND stay_date < ? ORDER BY stay_date",
                (room_type_id, request.check_in.isoformat(),
                 request.check_out.isoformat()),
            ).fetchall())
        return rows

    def handled_request(self, customer_id: str, key: str) -> Optional[sqlite3.Row]:
        """查询成功请求的原始摘要和对应订单。"""
        return self.connection.execute(
            "SELECT payload_digest, order_id FROM booking_requests "
            "WHERE customer_id = ? AND request_key = ?", (customer_id, key),
        ).fetchone()

    def receipt(self, order_id: str) -> OrderReceipt:
        """读取订单的当前结果，保留原始创建时间。"""
        row = self.connection.execute(
            "SELECT id, status, total_cents, created_at FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        return OrderReceipt(
            row["id"], row["status"], row["total_cents"],
            datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")),
        )

    def matching_orders(
        self, customer_id: str, request: BookingRequest, now: datetime
    ) -> Tuple[str, ...]:
        """重复预订按房型、每间人数和日期匹配，不按价格匹配。"""
        candidates = self.connection.execute(
            "SELECT id FROM orders WHERE customer_id = ? AND hotel_id = ? "
            "AND check_in_date = ? AND check_out_date = ? "
            "AND status IN ('PENDING', 'CONFIRMED') ORDER BY id",
            (customer_id, request.hotel_id, request.check_in.isoformat(),
             request.check_out.isoformat()),
        ).fetchall()
        expected = sorted((room.room_type_id, room.guests) for room in request.rooms)
        matches = []
        for candidate in candidates:
            receipt = self.receipt(candidate["id"])
            if receipt.status == "PENDING" and now >= receipt.confirmation_deadline:
                continue
            rows = self.connection.execute(
                "SELECT room_type_id, guests FROM order_items WHERE order_id = ?",
                (receipt.order_id,),
            ).fetchall()
            if sorted((row["room_type_id"], row["guests"]) for row in rows) == expected:
                matches.append(receipt.order_id)
        return tuple(matches)

    def save_booking(
        self, customer_id: str, key: str, quote: Quote,
        inventory: List[sqlite3.Row], now: datetime,
    ) -> OrderReceipt:
        """占库、订单、明细、请求结果和成功日志在同一事务内保存。"""
        if not self.connection.in_transaction:
            raise RuntimeError("保存预订必须在事务内进行")
        request = quote.request
        counts = Counter(room.room_type_id for room in request.rooms)
        inventory_ids: Dict[Tuple[str, str], str] = {}
        for row in inventory:
            quantity = counts[row["room_type_id"]]
            cursor = self.connection.execute(
                "UPDATE daily_inventory SET occupied_rooms = occupied_rooms + ? "
                "WHERE id = ? AND sale_status = 'OPEN' "
                "AND total_rooms - occupied_rooms >= ?",
                (quantity, row["id"], quantity),
            )
            if cursor.rowcount != 1:
                raise BookingError("UNAVAILABLE", "房源已变化，请重新查询")
            inventory_ids[(row["room_type_id"], row["stay_date"])] = row["id"]
        order_id = uuid4().hex
        timestamp = now.isoformat().replace("+00:00", "Z")
        self.connection.execute(
            "INSERT INTO orders (id, customer_id, hotel_id, check_in_date, "
            "check_out_date, created_at, total_cents) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (order_id, customer_id, request.hotel_id, request.check_in.isoformat(),
             request.check_out.isoformat(), timestamp, quote.total_cents),
        )
        prices = {(p.room_type_id, p.stay_date): p.price_cents for p in quote.prices}
        for room in request.rooms:
            item_id = uuid4().hex
            self.connection.execute(
                "INSERT INTO order_items (id, order_id, room_type_id, guests) "
                "VALUES (?, ?, ?, ?)",
                (item_id, order_id, room.room_type_id, room.guests),
            )
            for night in request.nights:
                self.connection.execute(
                    "INSERT INTO night_details (id, item_id, inventory_id, stay_date, "
                    "price_cents) VALUES (?, ?, ?, ?, ?)",
                    (uuid4().hex, item_id,
                     inventory_ids[(room.room_type_id, night.isoformat())],
                     night.isoformat(), prices[(room.room_type_id, night)]),
                )
        self.connection.execute(
            "INSERT INTO booking_requests "
            "(customer_id, request_key, payload_digest, order_id) VALUES (?, ?, ?, ?)",
            (customer_id, key, quote.digest(), order_id),
        )
        self.connection.execute(
            "INSERT INTO audit_logs "
            "(id, actor_id, hotel_id, action, entity_type, entity_id, "
            "detail, created_at) "
            "VALUES (?, ?, ?, 'BOOKING_CREATED', 'order', ?, ?, ?)",
            (uuid4().hex, customer_id, request.hotel_id, order_id,
             "创建待确认订单", timestamp),
        )
        return OrderReceipt(order_id, "PENDING", quote.total_cents, now)
