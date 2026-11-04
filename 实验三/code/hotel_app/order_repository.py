"""订单状态与有效库存占用的存储操作，不自行提交事务。"""

import sqlite3
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from hotel_app.booking import BookingError
from hotel_app.order_rules import BEIJING, Order


class OrderRepository:
    """复用已有订单、明细和日志表，不增加操作请求表。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self, order_id: str) -> Order:
        row = self.connection.execute(
            "SELECT id, customer_id, hotel_id, check_in_date, check_out_date, "
            "created_at, status, "
            "total_cents, fee_cents, close_reason FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        if row is None:
            raise BookingError("NOT_FOUND", "订单不存在")
        return Order(**dict(row))

    def actor(self, actor_id: str) -> Optional[sqlite3.Row]:
        return self.connection.execute(
            "SELECT role, hotel_id FROM users WHERE id = ?", (actor_id,)
        ).fetchone()

    def waiting_ids(self, status: str) -> List[str]:
        return [row[0] for row in self.connection.execute(
            "SELECT id FROM orders WHERE status = ? ORDER BY id", (status,)
        ).fetchall()]

    def save(
        self, old: Order, new: Order, actor_id: Optional[str], now: datetime
    ) -> None:
        if not self.connection.in_transaction:
            raise RuntimeError("订单变更必须在事务内进行")
        if new.status in ("CANCELLED", "REJECTED", "EXPIRED"):
            self._release(old.id, now.astimezone(BEIJING).date().isoformat())
        self.connection.execute(
            "UPDATE orders SET status = ?, fee_cents = ?, close_reason = ? "
            "WHERE id = ?",
            (new.status, new.fee_cents, new.close_reason, old.id),
        )
        self.connection.execute(
            "INSERT INTO audit_logs (id, actor_id, hotel_id, action, entity_type, "
            "entity_id, detail, created_at) VALUES (?, ?, ?, ?, 'order', ?, ?, ?)",
            (uuid4().hex, actor_id, old.hotel_id, "ORDER_" + new.status, old.id,
             f"{old.status}->{new.status}; fee={new.fee_cents}; "
             f"reason={new.close_reason}",
             now.isoformat().replace("+00:00", "Z")),
        )

    def _release(self, order_id: str, today: str) -> None:
        # 过去住宿晚保留历史占用；今天及以后才恢复可售余量。
        predicate = (
            "item_id IN (SELECT id FROM order_items WHERE order_id = ?) "
            "AND occupancy_active = 1 AND stay_date >= ?"
        )
        rows = self.connection.execute(
            "SELECT inventory_id, count(*) AS quantity FROM night_details WHERE "
            + predicate + " GROUP BY inventory_id ORDER BY inventory_id",
            (order_id, today),
        ).fetchall()
        for row in rows:
            updated = self.connection.execute(
                "UPDATE daily_inventory SET occupied_rooms = occupied_rooms - ? "
                "WHERE id = ? AND occupied_rooms >= ?",
                (row["quantity"], row["inventory_id"], row["quantity"]),
            )
            if updated.rowcount != 1:
                raise BookingError("INVENTORY_CONFLICT", "库存与订单占用不一致")
        self.connection.execute(
            "UPDATE night_details SET occupancy_active = 0 WHERE " + predicate,
            (order_id, today),
        )
