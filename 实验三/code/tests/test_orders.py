"""订单状态、期限、费用、权限及真实双连接竞争测试。"""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from support import BookingFixture

from hotel_app.booking import BookingError, RoomRequest
from hotel_app.database import connect
from hotel_app.order_service import OrderService


class OrderTests(BookingFixture):
    def setUp(self) -> None:
        super().setUp()
        self.life = OrderService(self.connection, clock=lambda: self.now)

    def create(self, confirmed=False):
        key = str(self.scalar("SELECT count(*) FROM orders"))
        order_id = self.submit(key, allow_duplicate=True).order_id
        if confirmed:
            self.life.confirm("staff", order_id)
        return order_id

    def assert_error(self, code, method, *args):
        with self.assertRaises(BookingError) as caught:
            method(*args)
        self.assertEqual(caught.exception.code, code)

    def test_confirmation_is_repeatable_without_extra_log(self) -> None:
        order_id = self.create()
        first = self.life.confirm("staff", order_id)
        self.assertEqual(first.status, "CONFIRMED")
        self.assertEqual(first, self.life.confirm("staff", order_id))
        self.assert_occupied(4)
        self.assertEqual(self.scalar("SELECT count(*) FROM audit_logs"), 2)

    def test_free_closures_preserve_history_and_do_not_release_twice(self) -> None:
        cases = ((self.life.reject, "staff", ("无接待条件",), False),
                 (self.life.cancel, "a", (), False),
                 (self.life.cancel_by_hotel, "admin", ("酒店停业",), True))
        for method, actor, reasons, confirmed in cases:
            with self.subTest(method=method.__name__):
                order_id = self.create(confirmed)
                result = method(actor, order_id, *reasons)
                self.assertIn(result.status, ("REJECTED", "CANCELLED"))
                self.assertEqual((result.fee_cents, result.total_cents), (0, 120000))
                self.assertEqual(method(actor, order_id, *reasons), result)
                self.assert_occupied(0)
        self.assertEqual(self.scalar("SELECT count(*) FROM night_details"), 12)
        self.assertEqual(
            self.scalar("SELECT sum(occupancy_active) FROM night_details"), 0
        )

    def test_fee_rounding_and_later_admin_retry_preserve_customer_fee(self) -> None:
        self.request = replace(
            self.request, check_out=self.request.check_in + timedelta(days=1),
            rooms=(RoomRequest(self.double, 1),),
        )
        self.connection.execute("UPDATE daily_inventory SET price_cents = 10003")
        order_id = self.create(confirmed=True)
        result = self.life.cancel("a", order_id)
        self.assertEqual((result.total_cents, result.fee_cents), (10003, 2001))
        self.assertEqual(self.life.cancel("a", order_id), result)
        self.assertEqual(self.life.cancel_by_hotel("admin", order_id, "新原因"), result)
        self.assert_occupied(0)
        self.assertEqual(self.scalar("SELECT count(*) FROM settlements"), 0)

    def test_permissions_are_checked_before_state_changes(self) -> None:
        order_id = self.create()
        self.connection.execute(
            "UPDATE users SET hotel_id = "
            "(SELECT hotel_id FROM room_types WHERE id = ?) "
            "WHERE id = 'staff'", (self.foreign,),
        )
        for method, args in (
            (self.life.confirm, ("staff", order_id)),
            (self.life.cancel, ("b", order_id)),
            (self.life.cancel_by_hotel, ("staff", order_id, "原因")),
            (self.life.confirm, ("missing", order_id)),
        ):
            self.assert_error("FORBIDDEN", method, *args)
        self.assertEqual(self.scalar("SELECT status FROM orders"), "PENDING")
        self.assert_occupied(4)
        self.assertEqual(self.scalar("SELECT count(*) FROM audit_logs"), 1)

    def test_invalid_reason_and_illegal_states_do_not_mutate(self) -> None:
        order_id = self.create()
        self.assert_error("INVALID_INPUT", self.life.reject, "staff", order_id, " ")
        self.assert_error(
            "INVALID_INPUT", self.life.cancel_by_hotel, "admin", order_id, ""
        )
        self.assert_error(
            "INVALID_STATE", self.life.cancel_by_hotel, "admin", order_id, "原因"
        )
        self.life.confirm("staff", order_id)
        self.assert_error("INVALID_STATE", self.life.reject, "staff", order_id, "原因")
        self.connection.execute("UPDATE orders SET status = 'CHECKED_IN'")
        self.assert_error("INVALID_STATE", self.life.cancel, "a", order_id)
        self.assert_occupied(4)

    def test_confirmation_just_before_deadline(self) -> None:
        order_id = self.create()
        self.now += timedelta(minutes=15) - timedelta(microseconds=1)
        self.assertEqual(self.life.confirm("staff", order_id).status, "CONFIRMED")

    def test_actions_at_deadline_commit_expiry_instead_of_rolling_back(self) -> None:
        cases = ((self.life.confirm, "staff", ()), (self.life.reject, "staff", ("原因",)),
                 (self.life.cancel, "a", ()))
        for method, actor, reasons in cases:
            order_id = self.create()
            self.now += timedelta(minutes=15)
            self.assertEqual(method(actor, order_id, *reasons).status, "EXPIRED")
            with closing(connect(self.database_path)) as connection:
                self.assertEqual(connection.execute(
                    "SELECT status FROM orders WHERE id = ?", (order_id,)
                ).fetchone()[0], "EXPIRED")
                self.assertEqual(connection.execute(
                    "SELECT sum(occupied_rooms) FROM daily_inventory"
                ).fetchone()[0], 0)

    def test_noon_cancellation_boundary(self) -> None:
        first, second = self.create(True), self.create(True)
        self.now = datetime(2026, 11, 10, 3, 59, 59, tzinfo=timezone.utc)
        self.assertEqual(self.life.cancel("a", first).fee_cents, 24000)
        self.now += timedelta(seconds=1)
        self.assert_error("CUTOFF_PASSED", self.life.cancel, "a", second)
        self.assertEqual(self.life.repository.get(second).status, "CONFIRMED")
        self.assert_occupied(4)

    def test_cleanup_only_releases_due_pending_orders_once(self) -> None:
        self.create()
        confirmed = self.create(True)
        self.now += timedelta(minutes=10)
        fresh = self.create()
        self.now += timedelta(minutes=5)
        self.assertEqual(self.life.expire_due(), 1)
        self.assertEqual(self.life.expire_due(), 0)
        self.assertEqual(self.life.repository.get(confirmed).status, "CONFIRMED")
        self.assertEqual(self.life.repository.get(fresh).status, "PENDING")
        self.assert_occupied(8)

    def test_delayed_cleanup_preserves_past_night_inventory(self) -> None:
        self.create()
        self.now = datetime(2026, 11, 11, tzinfo=timezone.utc)
        self.assertEqual(self.life.expire_due(), 1)
        self.assert_occupied(2)
        self.assertEqual(
            self.scalar("SELECT sum(occupancy_active) FROM night_details"), 2
        )

    def test_log_failure_rolls_back_status_stock_details_and_fee(self) -> None:
        order_id = self.create(True)
        before = self.life.repository.get(order_id)
        self.connection.execute(
            "CREATE TEMP TRIGGER fail_log BEFORE INSERT ON audit_logs "
            "BEGIN SELECT RAISE(ABORT, 'simulated failure'); END"
        )
        with self.assertLogs("hotel_app.order_service", level="ERROR"):
            self.assert_error("STORAGE_ERROR", self.life.cancel, "a", order_id)
        self.assertEqual(self.life.repository.get(order_id), before)
        self.assert_occupied(4)
        self.assertEqual(
            self.scalar("SELECT sum(occupancy_active) FROM night_details"), 4
        )
        self.connection.execute("DROP TRIGGER fail_log")
        self.assertEqual(self.life.cancel("a", order_id).fee_cents, 24000)

    def race(self, expiry):
        order_id = self.create()
        if expiry:
            self.now += timedelta(minutes=15)
        barrier = threading.Barrier(2, timeout=5)

        def worker(confirm):
            with closing(connect(self.database_path)) as connection:
                life = OrderService(connection, clock=lambda: self.now)
                barrier.wait()
                if confirm:
                    return life.confirm("staff", order_id)
                return life.expire_due() if expiry else life.cancel("a", order_id)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker, confirm) for confirm in (True, False)]
            for future in futures:
                future.result(timeout=10)
        self.assert_occupied(0)
        return self.life.repository.get(order_id)

    def test_concurrent_confirm_and_cancel_release_once(self) -> None:
        result = self.race(expiry=False)
        self.assertEqual(result.status, "CANCELLED")
        self.assertIn(result.fee_cents, (0, 24000))

    def test_concurrent_confirm_and_expire_at_deadline(self) -> None:
        self.assertEqual(self.race(expiry=True).status, "EXPIRED")
