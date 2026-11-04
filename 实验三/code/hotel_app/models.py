"""第一批基础资料对象；其余业务对象随对应流程逐步实现。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Hotel:
    """酒店资料的只读快照。"""

    id: str
    name: str
    address: str
    status: str


@dataclass(frozen=True)
class RoomType:
    """房型资料的只读快照，不含按日变化的房价。"""

    id: str
    hotel_id: str
    name: str
    bed_type: str
    capacity: int
