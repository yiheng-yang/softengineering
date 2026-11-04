"""入住、退房、清洁及未到店关闭的完整业务测试。"""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from support import BookingFixture

from hotel_app.booking import BookingError
from hotel_app.database import connect
from hotel_app.order_service import OrderService
from hotel_app.stay_service import StayService

ARRIVAL = datetime(2026, 11, 9, 16, tzinfo=timezone.utc)
DEPARTURE = ARRIVAL + timedelta(days=2)


class StayTests(BookingFixture):
    def setUp(self) -> None:
        super().setUp()
        self.life = OrderService(self.connection, clock=lambda: self.now)
        self.stay = StayService(self.connection, clock=lambda: self.now)
        for room_id, room_type in (("double", self.double), ("twin", self.twin),
                                   ("other", self.foreign)):
            self.connection.execute(
                "INSERT INTO rooms (id, hotel_id, room_type_id, number) "
                "SELECT ?, hotel_id, id, ? FROM room_types WHERE id = ?",
                (room_id, room_id, room_type),
            )

    def create(self):
        key = str(self.scalar("SELECT count(*) FROM orders"))
        order_id = self.submit(key, allow_duplicate=True).order_id
        self.life.confirm("staff", order_id)
        mapping = {row[0]: "double" if row[1] == self.double else "twin"
                   for row in self.connection.execute(
                       "SELECT id, room_type_id FROM order_items WHERE order_id = ?",
                       (order_id,),
                   )}
        return order_id, mapping

    def error(self, code, method, *args):
        with self.assertRaises(BookingError) as caught:
            method(*args)
        self.assertEqual(caught.exception.code, code)

    def test_full_stay_retries_and_old_checkout_after_room_reuse(self) -> None:
        order_id, mapping = self.create()
        self.now = ARRIVAL - timedelta(microseconds=1)
        self.error("TOO_EARLY", self.stay.check_in, "staff", order_id, mapping)
        self.now = ARRIVAL
        result = self.stay.check_in("staff", order_id, mapping)
        self.assertEqual(result.order.status, "CHECKED_IN")
        self.assertEqual(self.stay.check_in("staff", order_id, mapping), result)
        self.assert_occupied(4)
        self.error("ASSIGNMENT_CONFLICT", self.stay.check_in, "staff", order_id,
                   {key: "other" for key in mapping})
        self.now = DEPARTURE - timedelta(microseconds=1)
        self.error("TOO_EARLY", self.stay.check_out, "staff", order_id)
        self.now = DEPARTURE
        self.assertEqual(self.stay.check_out("staff", order_id).status, "COMPLETED")
        for room_id in mapping.values():
            self.assertEqual(self.stay.rooms.room(room_id)["status"], "DIRTY")
            self.assertEqual(self.stay.complete_cleaning("staff", room_id), "AVAILABLE")
        self.connection.execute(
            "INSERT INTO daily_inventory (id, room_type_id, stay_date, price_cents, "
            "total_rooms) SELECT id || '-next', room_type_id, "
            "date(stay_date, '+2 days'), "
            "price_cents, total_rooms FROM daily_inventory"
        )
        self.request = replace(
            self.request, check_in=self.request.check_in + timedelta(days=2),
            check_out=self.request.check_out + timedelta(days=2),
        )
        next_id, next_mapping = self.create()
        self.stay.check_in("staff", next_id, next_mapping)
        self.assertEqual(self.stay.check_out("staff", order_id).status, "COMPLETED")
        for room_id in mapping.values():
            self.assertEqual(self.stay.rooms.room(room_id)["status"], "OCCUPIED")
        self.error("ROOM_UNAVAILABLE", self.stay.complete_cleaning, "staff", "double")

    def test_invalid_allocations_never_partially_check_in(self) -> None:
        order_id, mapping = self.create()
        self.now = ARRIVAL
        first, second = mapping
        cases = ({first: mapping[first]}, {**mapping, "extra": "other"},
                 {first: "double", second: "double"},
                 {key: "other" for key in mapping}, {first: "missing", second: "twin"},
                 dict(zip(mapping, reversed(list(mapping.values())))))
        for invalid in cases:
            with self.subTest(mapping=invalid):
                self.error(
                    "INVALID_INPUT", self.stay.check_in, "staff", order_id, invalid
                )
                self.assertEqual(
                    self.scalar("SELECT count(*) FROM room_assignments"), 0
                )
                self.assertEqual(self.life.repository.get(order_id).status, "CONFIRMED")
        self.error("INVALID_STATE", self.stay.check_out, "staff", order_id)

    def test_room_states_and_permissions(self) -> None:
        order_id, mapping = self.create()
        self.now = ARRIVAL
        pending = self.submit("pending", allow_duplicate=True).order_id
        self.error("INVALID_STATE", self.stay.check_in, "staff", pending, mapping)
        for status in ("DIRTY", "MAINTENANCE", "OCCUPIED"):
            self.connection.execute("UPDATE rooms SET status = ?", (status,))
            self.error(
                "ROOM_UNAVAILABLE", self.stay.check_in, "staff", order_id, mapping
            )
        self.error("ROOM_UNAVAILABLE", self.stay.complete_cleaning, "staff", "double")
        self.connection.execute("UPDATE rooms SET status = 'AVAILABLE'")
        self.error("FORBIDDEN", self.stay.check_in, "a", order_id, mapping)
        self.error("FORBIDDEN", self.stay.complete_cleaning, "staff", "other")
        self.connection.execute(
            "UPDATE users SET hotel_id = (SELECT hotel_id FROM rooms WHERE id='other') "
            "WHERE id='staff'"
        )
        self.error("FORBIDDEN", self.stay.check_in, "staff", order_id, mapping)
        self.error("FORBIDDEN", self.stay.check_out, "staff", order_id)

    def test_no_show_boundary_and_repeated_cleanup(self) -> None:
        first, first_mapping = self.create()
        second, second_mapping = self.create()
        third, _ = self.create()
        self.now = ARRIVAL + timedelta(hours=36) - timedelta(microseconds=1)
        self.assertEqual(self.stay.check_in("staff", first, first_mapping).order.status,
                         "CHECKED_IN")
        self.now += timedelta(microseconds=1)
        closed = self.stay.check_in("staff", second, second_mapping)
        self.assertEqual((closed.order.status, closed.order.close_reason,
                          closed.order.fee_cents, closed.assignments),
                         ("CANCELLED", "NO_SHOW", 24000, {}))
        self.assertEqual(self.life.close_no_shows(), 1)
        self.assertEqual(self.life.close_no_shows(), 0)
        self.assertEqual(self.life.repository.get(third).close_reason, "NO_SHOW")
        self.assert_occupied(8)  # 入住订单4晚占用，另两单各保留过去的2晚。

    def test_log_failure_rolls_back_check_in_checkout_and_cleaning(self) -> None:
        order_id, mapping = self.create()
        phases = ((self.stay.check_in, ("staff", order_id, mapping), ARRIVAL),
                  (self.stay.check_out, ("staff", order_id), DEPARTURE),
                  (self.stay.complete_cleaning, ("staff", "double"), DEPARTURE))
        for method, args, now in phases:
            self.now = now
            snapshot = list(self.connection.iterdump())
            self.connection.execute(
                "CREATE TEMP TRIGGER fail_log BEFORE INSERT ON audit_logs "
                "BEGIN SELECT RAISE(ABORT, 'simulated failure'); END"
            )
            with self.assertLogs("hotel_app.stay_service", level="ERROR"):
                self.error("STORAGE_ERROR", method, *args)
            self.assertEqual(list(self.connection.iterdump()), snapshot)
            self.connection.execute("DROP TRIGGER fail_log")
            method(*args)

    def test_concurrent_room_competition(self) -> None:
        self.race(overdue=False)

    def test_concurrent_check_in_and_no_show_cleanup(self) -> None:
        self.race(overdue=True)

    def race(self, overdue):
        first, first_mapping = self.create()
        second, second_mapping = self.create()
        self.now = ARRIVAL + timedelta(hours=36) if overdue else ARRIVAL
        barrier = threading.Barrier(2, timeout=5)

        def worker(index):
            with closing(connect(self.database_path)) as connection:
                service = StayService(connection, clock=lambda: self.now)
                barrier.wait()
                try:
                    if index and overdue:
                        life = OrderService(connection, clock=lambda: self.now)
                        return life.close_no_shows()
                    return service.check_in("staff", second if index else first,
                                            second_mapping if index else first_mapping)
                except BookingError as error:
                    return error

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker, index) for index in range(2)]
            results = [future.result(timeout=10) for future in futures]
        errors = [result.code for result in results if isinstance(result, BookingError)]
        if overdue:
            self.assertTrue(not errors or errors == ["INVALID_STATE"])
            self.assertEqual(self.life.repository.get(first).close_reason, "NO_SHOW")
            self.assertEqual(self.scalar("SELECT count(*) FROM room_assignments"), 0)
        else:
            self.assertEqual(errors, ["ROOM_UNAVAILABLE"])
            self.assertEqual(self.scalar("SELECT count(*) FROM room_assignments"), 2)
