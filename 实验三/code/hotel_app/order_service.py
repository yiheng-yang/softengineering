"""确认、拒绝、取消与超时清理；界面依据返回状态显示实际结果。"""

import logging
import sqlite3
from datetime import datetime
from typing import Callable, Optional

from hotel_app.booking import BookingError
from hotel_app.database import operation, utc_now
from hotel_app.order_repository import OrderRepository
from hotel_app.order_rules import Order, transition

LOGGER = logging.getLogger(__name__)


def require_staff(repository: OrderRepository, actor_id: str, hotel_id: str) -> None:
    """订单与房态操作共用本酒店工作人员校验。"""
    actor = repository.actor(actor_id)
    if actor is None or actor["role"] != "STAFF" or actor["hotel_id"] != hotel_id:
        raise BookingError("FORBIDDEN", "仅本酒店工作人员可操作")


class OrderService:
    """按状态防止重复处理，不保存每次操作的独立历史响应。"""

    def __init__(
        self, connection: sqlite3.Connection,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.connection = connection
        self.repository = OrderRepository(connection)
        self.clock = clock

    def confirm(self, actor_id: str, order_id: str) -> Order:
        return self._apply(actor_id, order_id, "CONFIRM")

    def reject(self, actor_id: str, order_id: str, reason: str) -> Order:
        return self._apply(actor_id, order_id, "REJECT", reason)

    def cancel(self, actor_id: str, order_id: str) -> Order:
        return self._apply(actor_id, order_id, "CANCEL")

    def cancel_by_hotel(self, actor_id: str, order_id: str, reason: str) -> Order:
        return self._apply(actor_id, order_id, "HOTEL_CANCEL", reason)

    def _authorize(self, actor_id: str, order: Order, action: str) -> None:
        if action in ("CONFIRM", "REJECT"):
            require_staff(self.repository, actor_id, order.hotel_id)
            return
        actor = self.repository.actor(actor_id)
        allowed = actor is not None and (
            (action == "CANCEL" and actor["role"] == "CUSTOMER"
             and order.customer_id == actor_id)
            or (action == "HOTEL_CANCEL" and actor["role"] == "ADMIN")
        )
        if not allowed:
            raise BookingError("FORBIDDEN", "无权处理此订单")

    def _apply(
        self, actor_id: str, order_id: str, action: str, reason: str = ""
    ) -> Order:
        if not isinstance(actor_id, str) or not isinstance(order_id, str):
            raise BookingError("INVALID_INPUT", "用户及订单编号必须为字符串")
        if not isinstance(reason, str) or (
            action in ("REJECT", "HOTEL_CANCEL") and not reason.strip()
        ):
            raise BookingError("INVALID_INPUT", "请填写处理原因")
        with operation(self.connection, LOGGER):
            old = self.repository.get(order_id)
            self._authorize(actor_id, old, action)
            now = utc_now(self.clock)
            new = transition(old, action, now, reason.strip())
            if new != old:
                actor = None if new.close_reason in ("TIMEOUT", "NO_SHOW") else actor_id
                self.repository.save(old, new, actor, now)
        # 超时也正常提交，再返回 EXPIRED；不能抛异常回滚刚完成的释放。
        return new

    def expire_due(self) -> int:
        """供启动及定时调度调用；本方法不是用户操作入口。"""
        return self._close_due("PENDING", "EXPIRE")

    def close_no_shows(self) -> int:
        """关闭达到入住次日12点仍未入住的已确认订单。"""
        return self._close_due("CONFIRMED", "NO_SHOW")

    def _close_due(self, status: str, action: str) -> int:
        with operation(self.connection, LOGGER):
            now = utc_now(self.clock)
            count = 0
            for order_id in self.repository.waiting_ids(status):
                old = self.repository.get(order_id)
                new = transition(old, action, now, "")
                if new != old:
                    self.repository.save(old, new, None, now)
                    count += 1
        return count
