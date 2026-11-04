-- 金额单位为分；日期为 YYYY-MM-DD；操作时间为 UTC ISO 8601。
-- 外键默认限制删除，避免删除仍被历史记录引用的数据。
CREATE TABLE hotels (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    address TEXT NOT NULL CHECK (length(trim(address)) > 0),
    status TEXT NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'CLOSED'))
);

CREATE TABLE users (
    id TEXT PRIMARY KEY NOT NULL,
    username TEXT NOT NULL UNIQUE CHECK (length(trim(username)) > 0),
    password_hash TEXT NOT NULL CHECK (length(password_hash) > 0),
    name TEXT NOT NULL,
    contact TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('CUSTOMER', 'STAFF', 'ADMIN')),
    hotel_id TEXT REFERENCES hotels(id),
    CHECK ((role = 'STAFF' AND hotel_id IS NOT NULL)
        OR (role != 'STAFF' AND hotel_id IS NULL))
);

CREATE TABLE room_types (
    id TEXT PRIMARY KEY NOT NULL,
    hotel_id TEXT NOT NULL REFERENCES hotels(id),
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    bed_type TEXT NOT NULL CHECK (length(trim(bed_type)) > 0),
    capacity INTEGER NOT NULL
        CHECK (typeof(capacity) = 'integer' AND capacity > 0),
    UNIQUE (id, hotel_id)
);

CREATE TABLE daily_inventory (
    id TEXT PRIMARY KEY NOT NULL,
    room_type_id TEXT NOT NULL REFERENCES room_types(id),
    stay_date TEXT NOT NULL CHECK (
        length(stay_date) = 10 AND date(stay_date, '+0 days') IS NOT NULL
        AND date(stay_date, '+0 days') = stay_date
    ),
    price_cents INTEGER NOT NULL
        CHECK (typeof(price_cents) = 'integer' AND price_cents >= 0),
    total_rooms INTEGER NOT NULL
        CHECK (typeof(total_rooms) = 'integer' AND total_rooms >= 0),
    occupied_rooms INTEGER NOT NULL DEFAULT 0 CHECK (
        typeof(occupied_rooms) = 'integer'
        AND occupied_rooms >= 0 AND occupied_rooms <= total_rooms
    ),
    sale_status TEXT NOT NULL DEFAULT 'OPEN'
        CHECK (sale_status IN ('OPEN', 'STOPPED')),
    UNIQUE (room_type_id, stay_date),
    UNIQUE (id, stay_date)
);

CREATE TABLE rooms (
    id TEXT PRIMARY KEY NOT NULL,
    hotel_id TEXT NOT NULL REFERENCES hotels(id),
    room_type_id TEXT NOT NULL,
    number TEXT NOT NULL CHECK (length(trim(number)) > 0),
    status TEXT NOT NULL DEFAULT 'AVAILABLE'
        CHECK (status IN ('AVAILABLE', 'OCCUPIED', 'DIRTY', 'MAINTENANCE')),
    FOREIGN KEY (room_type_id, hotel_id) REFERENCES room_types(id, hotel_id),
    UNIQUE (hotel_id, number)
);

CREATE TABLE orders (
    id TEXT PRIMARY KEY NOT NULL,
    customer_id TEXT NOT NULL REFERENCES users(id),
    hotel_id TEXT NOT NULL REFERENCES hotels(id),
    check_in_date TEXT NOT NULL CHECK (
        length(check_in_date) = 10 AND date(check_in_date, '+0 days') IS NOT NULL
        AND date(check_in_date, '+0 days') = check_in_date
    ),
    check_out_date TEXT NOT NULL CHECK (
        length(check_out_date) = 10 AND date(check_out_date, '+0 days') IS NOT NULL
        AND date(check_out_date, '+0 days') = check_out_date
    ),
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN (
        'PENDING', 'CONFIRMED', 'CHECKED_IN', 'COMPLETED',
        'CANCELLED', 'REJECTED', 'EXPIRED'
    )),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    total_cents INTEGER NOT NULL
        CHECK (typeof(total_cents) = 'integer' AND total_cents >= 0),
    fee_cents INTEGER NOT NULL DEFAULT 0
        CHECK (typeof(fee_cents) = 'integer' AND fee_cents >= 0),
    close_reason TEXT,
    CHECK (julianday(check_out_date) - julianday(check_in_date) BETWEEN 1 AND 14)
);

CREATE TABLE order_items (
    id TEXT PRIMARY KEY NOT NULL,
    order_id TEXT NOT NULL REFERENCES orders(id),
    room_type_id TEXT NOT NULL REFERENCES room_types(id),
    guests INTEGER NOT NULL CHECK (typeof(guests) = 'integer' AND guests > 0)
);

CREATE TABLE night_details (
    id TEXT PRIMARY KEY NOT NULL,
    item_id TEXT NOT NULL REFERENCES order_items(id),
    inventory_id TEXT NOT NULL,
    stay_date TEXT NOT NULL,
    price_cents INTEGER NOT NULL
        CHECK (typeof(price_cents) = 'integer' AND price_cents >= 0),
    occupancy_active INTEGER NOT NULL DEFAULT 1 CHECK (occupancy_active IN (0, 1)),
    FOREIGN KEY (inventory_id, stay_date) REFERENCES daily_inventory(id, stay_date),
    UNIQUE (item_id, stay_date)
);

CREATE TABLE room_assignments (
    id TEXT PRIMARY KEY NOT NULL,
    item_id TEXT NOT NULL UNIQUE REFERENCES order_items(id),
    room_id TEXT NOT NULL REFERENCES rooms(id),
    checked_in_at TEXT NOT NULL,
    checked_out_at TEXT,
    CHECK (julianday(checked_in_at) IS NOT NULL),
    CHECK (checked_out_at IS NULL OR (
        julianday(checked_out_at) IS NOT NULL
        AND julianday(checked_out_at) >= julianday(checked_in_at)
    ))
);

-- 已退房的记录不参与唯一性检查，历史分配可以一直保留。
CREATE UNIQUE INDEX one_active_assignment_per_room
    ON room_assignments(room_id) WHERE checked_out_at IS NULL;

-- 只记录成功请求，和订单在同一事务中保存；后续调整不覆盖此摘要。
CREATE TABLE booking_requests (
    customer_id TEXT NOT NULL REFERENCES users(id),
    request_key TEXT NOT NULL CHECK (length(trim(request_key)) > 0),
    payload_digest TEXT NOT NULL CHECK (length(payload_digest) > 0),
    order_id TEXT NOT NULL UNIQUE REFERENCES orders(id),
    PRIMARY KEY (customer_id, request_key)
);

CREATE TABLE audit_logs (
    id TEXT PRIMARY KEY NOT NULL,
    actor_id TEXT REFERENCES users(id),
    hotel_id TEXT REFERENCES hotels(id),
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE settlements (
    id TEXT PRIMARY KEY NOT NULL,
    order_id TEXT NOT NULL REFERENCES orders(id),
    actor_id TEXT NOT NULL REFERENCES users(id),
    request_key TEXT NOT NULL UNIQUE CHECK (length(trim(request_key)) > 0),
    kind TEXT NOT NULL CHECK (kind IN ('PAYMENT', 'REFUND')),
    amount_cents INTEGER NOT NULL
        CHECK (typeof(amount_cents) = 'integer' AND amount_cents > 0),
    note TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX orders_by_customer ON orders(customer_id);
CREATE INDEX orders_by_hotel_status ON orders(hotel_id, status);
CREATE INDEX items_by_order ON order_items(order_id);
CREATE INDEX nights_by_inventory ON night_details(inventory_id);
