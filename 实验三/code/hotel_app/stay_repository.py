"""实体房间及入住分配的存储操作，与订单共享事务。"""

import sqlite3
from typing import Dict, List, Optional
from uuid import uuid4

from hotel_app.booking import BookingError


class StayRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def room(self, room_id: str) -> Optional[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM rooms WHERE id = ?", (room_id,)
        ).fetchone()

    def items(self, order_id: str) -> Dict[str, str]:
        return dict(self.connection.execute(
            "SELECT id, room_type_id FROM order_items WHERE order_id = ?", (order_id,)
        ).fetchall())

    def assignments(self, order_id: str) -> List[sqlite3.Row]:
        return self.connection.execute(
            "SELECT a.* FROM room_assignments a JOIN order_items i ON a.item_id = i.id "
            "WHERE i.order_id = ? ORDER BY a.item_id", (order_id,),
        ).fetchall()

    def _room_status(self, room_id: str, old: str, new: str) -> None:
        if not self.connection.in_transaction:
            raise RuntimeError("房态变更必须在事务内进行")
        cursor = self.connection.execute(
            "UPDATE rooms SET status = ? WHERE id = ? AND status = ?",
            (new, room_id, old),
        )
        if cursor.rowcount != 1:
            raise BookingError("ROOM_UNAVAILABLE", "房间状态已变化，不能完成操作")

    def allocate(self, mapping: Dict[str, str], timestamp: str) -> None:
        for item_id, room_id in sorted(mapping.items()):
            self._room_status(room_id, "AVAILABLE", "OCCUPIED")
            self.connection.execute(
                "INSERT INTO room_assignments (id, item_id, room_id, checked_in_at) "
                "VALUES (?, ?, ?, ?)", (uuid4().hex, item_id, room_id, timestamp),
            )

    def depart(self, assignments: List[sqlite3.Row], timestamp: str) -> None:
        for assignment in assignments:
            self._room_status(assignment["room_id"], "OCCUPIED", "DIRTY")
            cursor = self.connection.execute(
                "UPDATE room_assignments SET checked_out_at = ? "
                "WHERE id = ? AND checked_out_at IS NULL",
                (timestamp, assignment["id"]),
            )
            if cursor.rowcount != 1:
                raise BookingError("ASSIGNMENT_CONFLICT", "入住分配已发生变化")

    def clean(self, room: sqlite3.Row, actor_id: str, timestamp: str) -> None:
        self._room_status(room["id"], "DIRTY", "AVAILABLE")
        self.connection.execute(
            "INSERT INTO audit_logs (id, actor_id, hotel_id, action, entity_type, "
            "entity_id, detail, created_at) VALUES (?, ?, ?, 'ROOM_CLEANED', 'room', "
            "?, '清洁完成', ?)",
            (uuid4().hex, actor_id, room["hotel_id"], room["id"], timestamp),
        )
