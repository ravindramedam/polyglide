"""
orders.py
----------
FastAPI router module implementing order management endpoints,
including order creation, status transitions, and order-item handling.
Backed by an in-memory data store.

Features:
    - Pydantic models with validation
    - Order lifecycle state machine (pending -> paid -> shipped -> delivered / cancelled)
    - Line items with computed totals
    - Filtering and pagination
    - Order cancellation and refund simulation
"""

import logging
import uuid
from datetime import datetime
from enum import Enum
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, validator

logger = logging.getLogger("orders")
logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/orders", tags=["Orders"])


# --------------------------------------------------------------------------
# Enums & Models
# --------------------------------------------------------------------------
class OrderStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


# Valid forward transitions for the order state machine
_VALID_TRANSITIONS = {
    OrderStatus.PENDING: {OrderStatus.PAID, OrderStatus.CANCELLED},
    OrderStatus.PAID: {OrderStatus.SHIPPED, OrderStatus.CANCELLED, OrderStatus.REFUNDED},
    OrderStatus.SHIPPED: {OrderStatus.DELIVERED, OrderStatus.REFUNDED},
    OrderStatus.DELIVERED: {OrderStatus.REFUNDED},
    OrderStatus.CANCELLED: set(),
    OrderStatus.REFUNDED: set(),
}


class OrderItemIn(BaseModel):
    product_id: str
    product_name: str = Field(..., min_length=1, max_length=120)
    unit_price: float = Field(..., gt=0)
    quantity: int = Field(..., gt=0)


class OrderItemOut(OrderItemIn):
    line_total: float


class OrderCreate(BaseModel):
    customer_id: str
    customer_email: str
    items: List[OrderItemIn] = Field(..., min_items=1)
    shipping_address: str = Field(..., max_length=300)
    notes: Optional[str] = Field(None, max_length=500)

    @validator("items")
    def items_not_empty(cls, value: List[OrderItemIn]) -> List[OrderItemIn]:
        if not value:
            raise ValueError("order must contain at least one item")
        return value


class StatusChangeRequest(BaseModel):
    new_status: OrderStatus
    note: Optional[str] = Field(None, max_length=300)


class OrderOut(BaseModel):
    id: str
    customer_id: str
    customer_email: str
    items: List[OrderItemOut]
    shipping_address: str
    notes: Optional[str]
    status: OrderStatus
    subtotal: float
    tax: float
    total: float
    created_at: datetime
    updated_at: datetime


class OrderListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[OrderOut]


class StatusHistoryEntry(BaseModel):
    timestamp: datetime
    from_status: Optional[OrderStatus]
    to_status: OrderStatus
    note: Optional[str]


TAX_RATE = 0.08


# --------------------------------------------------------------------------
# In-memory "database"
# --------------------------------------------------------------------------
class OrderRecord:
    def __init__(self, customer_id: str, customer_email: str, items: List[OrderItemIn],
                 shipping_address: str, notes: Optional[str]):
        self.id = str(uuid.uuid4())
        self.customer_id = customer_id
        self.customer_email = customer_email
        self.items = [
            OrderItemOut(
                product_id=item.product_id,
                product_name=item.product_name,
                unit_price=item.unit_price,
                quantity=item.quantity,
                line_total=round(item.unit_price * item.quantity, 2),
            )
            for item in items
        ]
        self.shipping_address = shipping_address
        self.notes = notes
        self.status = OrderStatus.PENDING
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        self.status_history: List[StatusHistoryEntry] = [
            StatusHistoryEntry(
                timestamp=self.created_at,
                from_status=None,
                to_status=OrderStatus.PENDING,
                note="Order created",
            )
        ]

    @property
    def subtotal(self) -> float:
        return round(sum(item.line_total for item in self.items), 2)

    @property
    def tax(self) -> float:
        return round(self.subtotal * TAX_RATE, 2)

    @property
    def total(self) -> float:
        return round(self.subtotal + self.tax, 2)

    def to_out(self) -> OrderOut:
        return OrderOut(
            id=self.id,
            customer_id=self.customer_id,
            customer_email=self.customer_email,
            items=self.items,
            shipping_address=self.shipping_address,
            notes=self.notes,
            status=self.status,
            subtotal=self.subtotal,
            tax=self.tax,
            total=self.total,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


_ORDERS_DB: dict[str, OrderRecord] = {}


def _get_or_404(order_id: str) -> OrderRecord:
    record = _ORDERS_DB.get(order_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return record


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@router.post("/", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderCreate) -> OrderOut:
    """Create a new order from a list of line items."""
    record = OrderRecord(
        customer_id=payload.customer_id,
        customer_email=payload.customer_email,
        items=payload.items,
        shipping_address=payload.shipping_address,
        notes=payload.notes,
    )
    _ORDERS_DB[record.id] = record
    logger.info("Created order %s for customer %s (total=%.2f)", record.id, record.customer_id, record.total)
    return record.to_out()


@router.get("/", response_model=OrderListResponse)
def list_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    customer_id: Optional[str] = Query(None),
    status_filter: Optional[OrderStatus] = Query(None, alias="status"),
    min_total: Optional[float] = Query(None, ge=0),
) -> OrderListResponse:
    """List orders with filtering and pagination."""
    records = list(_ORDERS_DB.values())

    if customer_id is not None:
        records = [r for r in records if r.customer_id == customer_id]
    if status_filter is not None:
        records = [r for r in records if r.status == status_filter]
    if min_total is not None:
        records = [r for r in records if r.total >= min_total]

    records.sort(key=lambda r: r.created_at, reverse=True)

    total = len(records)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = records[start:end]

    return OrderListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[r.to_out() for r in page_items],
    )


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: str) -> OrderOut:
    """Retrieve a single order by ID."""
    record = _get_or_404(order_id)
    return record.to_out()


@router.post("/{order_id}/status", response_model=OrderOut)
def change_order_status(order_id: str, payload: StatusChangeRequest) -> OrderOut:
    """Transition an order to a new status, enforcing the state machine."""
    record = _get_or_404(order_id)

    allowed = _VALID_TRANSITIONS.get(record.status, set())
    if payload.new_status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot transition order from {record.status.value} to {payload.new_status.value}",
        )

    previous_status = record.status
    record.status = payload.new_status
    record.updated_at = datetime.utcnow()
    record.status_history.append(
        StatusHistoryEntry(
            timestamp=record.updated_at,
            from_status=previous_status,
            to_status=payload.new_status,
            note=payload.note,
        )
    )
    logger.info("Order %s transitioned %s -> %s", order_id, previous_status.value, payload.new_status.value)
    return record.to_out()


@router.post("/{order_id}/cancel", response_model=OrderOut)
def cancel_order(order_id: str, note: Optional[str] = None) -> OrderOut:
    """Convenience endpoint to cancel a pending or paid order."""
    record = _get_or_404(order_id)
    if record.status not in (OrderStatus.PENDING, OrderStatus.PAID):
        raise HTTPException(status_code=400, detail="Order can no longer be cancelled")

    previous_status = record.status
    record.status = OrderStatus.CANCELLED
    record.updated_at = datetime.utcnow()
    record.status_history.append(
        StatusHistoryEntry(
            timestamp=record.updated_at,
            from_status=previous_status,
            to_status=OrderStatus.CANCELLED,
            note=note or "Cancelled by request",
        )
    )
    return record.to_out()


@router.get("/{order_id}/history", response_model=List[StatusHistoryEntry])
def get_order_history(order_id: str) -> List[StatusHistoryEntry]:
    """Return the full status history of an order."""
    record = _get_or_404(order_id)
    return record.status_history


@router.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_order(order_id: str) -> None:
    """Permanently delete an order record (admin use only)."""
    record = _get_or_404(order_id)
    del _ORDERS_DB[order_id]
    logger.info("Deleted order %s", order_id)
    return None


@router.get("/customer/{customer_id}/total-spent")
def total_spent_by_customer(customer_id: str) -> dict:
    """Compute total amount spent by a customer across non-cancelled orders."""
    orders = [
        r for r in _ORDERS_DB.values()
        if r.customer_id == customer_id and r.status not in (OrderStatus.CANCELLED, OrderStatus.REFUNDED)
    ]
    total_spent = round(sum(o.total for o in orders), 2)
    return {
        "customer_id": customer_id,
        "order_count": len(orders),
        "total_spent": total_spent,
    }


@router.get("/stats/revenue")
def revenue_report() -> dict:
    """Return aggregate revenue statistics across all orders."""
    completed = [
        r for r in _ORDERS_DB.values()
        if r.status in (OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.DELIVERED)
    ]
    total_revenue = round(sum(o.total for o in completed), 2)
    return {
        "completed_orders": len(completed),
        "total_revenue": total_revenue,
        "generated_at": datetime.utcnow().isoformat(),
    }


def seed_demo_orders(count: int = 3) -> None:
    """Populate the in-memory store with demo orders (used at startup)."""
    for i in range(count):
        items = [
            OrderItemIn(
                product_id=str(uuid.uuid4()),
                product_name=f"Demo Item {i}",
                unit_price=15.0 + i,
                quantity=2,
            )
        ]
        record = OrderRecord(
            customer_id=f"demo_customer_{i}",
            customer_email=f"customer{i}@example.com",
            items=items,
            shipping_address="123 Demo Street",
            notes=None,
        )
        _ORDERS_DB[record.id] = record
    logger.info("Seeded %d demo orders", count)
