"""桌面界面的会话、报价状态与按身份隔离的只读查询。"""

import logging
from uuid import uuid4

from hotel_app.auth import AuthService
from hotel_app.booking import BookingError
from hotel_app.booking_service import BookingService
from hotel_app.database import operation
from hotel_app.order_service import OrderService, require_staff
from hotel_app.stay_service import StayService

LOGGER = logging.getLogger(__name__)


class DesktopModel:
    def __init__(self, connection) -> None:
        self.connection = connection
        self.auth = AuthService(connection)
        self.booking = BookingService(connection)
        self.orders = OrderService(connection)
        self.stays = StayService(connection)
        self.invalidate_quote()

    def invalidate_quote(self) -> None:
        self.quote = None
        self.request_key = None

    def login(self, username, password):
        self.invalidate_quote()
        return self.auth.login(username, password)

    def logout(self) -> None:
        self.auth.logout()
        self.invalidate_quote()

    def _account(self):
        if self.auth.current is None:
            raise BookingError("FORBIDDEN", "请先登录")
        return self.auth.current

    def _staff(self):
        account = self._account()
        with operation(self.connection, LOGGER, write=False):
            require_staff(self.orders.repository, account.id, account.hotel_id)
        return account

    def _read(self, sql, parameters=()):
        self._account()
        with operation(self.connection, LOGGER, write=False):
            return self.connection.execute(sql, parameters).fetchall()

    def get_quote(self, request):
        self.invalidate_quote()
        self.quote = self.booking.get_quote(self._account().id, request)
        self.request_key = uuid4().hex
        return self.quote

    def submit_quote(self, allow_duplicate=False):
        account = self._account()
        if self.quote is None:
            raise BookingError("INVALID_INPUT", "请先获取并确认报价")
        try:
            return self.booking.submit(
                account.id, self.quote.request, self.quote, self.request_key,
                allow_duplicate=allow_duplicate,
            )
        except BookingError as error:
            if error.code in ("PRICE_CHANGED", "UNAVAILABLE"):
                self.invalidate_quote()
            raise

    def hotels(self):
        return self._read("SELECT id, name FROM hotels WHERE status = 'OPEN' "
                          "ORDER BY name, id")

    def room_types(self, hotel_id):
        return self._read("SELECT id, name, capacity FROM room_types "
                          "WHERE hotel_id = ? ORDER BY name, id", (hotel_id,))

    def order_rows(self):
        account = self._account()
        scopes = {"CUSTOMER": ("o.customer_id = ?", (account.id,)),
                  "STAFF": ("o.hotel_id = ?", (account.hotel_id,)),
                  "ADMIN": ("1=1", ())}
        if account.role not in scopes:
            raise BookingError("FORBIDDEN", "角色无权查看订单")
        where, parameters = scopes[account.role]
        return self._read("SELECT o.*, h.name AS hotel_name FROM orders o "
                          "JOIN hotels h ON o.hotel_id = h.id WHERE " + where
                          + " ORDER BY o.created_at DESC, o.id", parameters)

    def room_rows(self):
        account = self._staff()
        return self._read("SELECT r.*, t.name AS type_name FROM rooms r "
                          "JOIN room_types t ON r.room_type_id = t.id "
                          "WHERE r.hotel_id = ? ORDER BY r.number", (account.hotel_id,))

    def item_rows(self, order_id):
        account = self._staff()
        if not self._read("SELECT id FROM orders WHERE id = ? AND hotel_id = ?",
                          (order_id, account.hotel_id)):
            raise BookingError("NOT_FOUND", "本酒店没有该订单")
        return self._read("SELECT i.*, t.name AS type_name FROM order_items i "
                          "JOIN room_types t ON i.room_type_id = t.id "
                          "WHERE i.order_id = ? ORDER BY i.id", (order_id,))
