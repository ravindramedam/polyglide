"""
products.py
------------
FastAPI router module implementing product catalog and inventory
management endpoints backed by an in-memory data store.

Features:
    - Pydantic models with validation
    - Full CRUD operations
    - Inventory adjustment (stock in/out)
    - Category filtering, price range filtering, sorting, pagination
    - Soft-delete support
"""

import logging
import uuid
from datetime import datetime
from enum import Enum
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, validator

logger = logging.getLogger("products")
logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/products", tags=["Products"])


# --------------------------------------------------------------------------
# Enums & Models
# --------------------------------------------------------------------------
class ProductCategory(str, Enum):
    ELECTRONICS = "electronics"
    CLOTHING = "clothing"
    HOME = "home"
    BOOKS = "books"
    TOYS = "toys"
    OTHER = "other"


class ProductBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=500)
    category: ProductCategory = ProductCategory.OTHER
    price: float = Field(..., gt=0)
    sku: str = Field(..., min_length=3, max_length=40)

    @validator("price")
    def round_price(cls, value: float) -> float:
        return round(value, 2)


class ProductCreate(ProductBase):
    initial_stock: int = Field(0, ge=0)


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[ProductCategory] = None
    price: Optional[float] = Field(None, gt=0)


class StockAdjustment(BaseModel):
    quantity: int
    reason: str = Field(..., max_length=200)

    @validator("quantity")
    def quantity_nonzero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("quantity must not be zero")
        return value


class ProductOut(ProductBase):
    id: str
    stock: int
    is_deleted: bool
    created_at: datetime
    updated_at: datetime


class ProductListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[ProductOut]


class StockHistoryEntry(BaseModel):
    timestamp: datetime
    quantity_change: int
    reason: str
    resulting_stock: int


# --------------------------------------------------------------------------
# In-memory "database"
# --------------------------------------------------------------------------
class ProductRecord:
    def __init__(self, name: str, description: Optional[str], category: ProductCategory,
                 price: float, sku: str, initial_stock: int = 0):
        self.id = str(uuid.uuid4())
        self.name = name
        self.description = description
        self.category = category
        self.price = price
        self.sku = sku
        self.stock = initial_stock
        self.is_deleted = False
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        self.history: List[StockHistoryEntry] = []
        if initial_stock:
            self.history.append(
                StockHistoryEntry(
                    timestamp=self.created_at,
                    quantity_change=initial_stock,
                    reason="initial stock",
                    resulting_stock=initial_stock,
                )
            )

    def to_out(self) -> ProductOut:
        return ProductOut(
            id=self.id,
            name=self.name,
            description=self.description,
            category=self.category,
            price=self.price,
            sku=self.sku,
            stock=self.stock,
            is_deleted=self.is_deleted,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


_PRODUCTS_DB: dict[str, ProductRecord] = {}


def _find_by_sku(sku: str) -> Optional[ProductRecord]:
    for record in _PRODUCTS_DB.values():
        if record.sku.lower() == sku.lower() and not record.is_deleted:
            return record
    return None


def _get_active_or_404(product_id: str) -> ProductRecord:
    record = _PRODUCTS_DB.get(product_id)
    if record is None or record.is_deleted:
        raise HTTPException(status_code=404, detail="Product not found")
    return record


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@router.post("/", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreate) -> ProductOut:
    """Create a new product."""
    if _find_by_sku(payload.sku):
        raise HTTPException(status_code=400, detail="SKU already exists")

    record = ProductRecord(
        name=payload.name,
        description=payload.description,
        category=payload.category,
        price=payload.price,
        sku=payload.sku,
        initial_stock=payload.initial_stock,
    )
    _PRODUCTS_DB[record.id] = record
    logger.info("Created product %s (%s)", record.name, record.id)
    return record.to_out()


@router.get("/", response_model=ProductListResponse)
def list_products(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    category: Optional[ProductCategory] = Query(None),
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    in_stock_only: bool = Query(False),
    sort_by: str = Query("name", regex="^(name|price|stock|created_at)$"),
    descending: bool = Query(False),
) -> ProductListResponse:
    """List products with filtering, sorting, and pagination."""
    records = [r for r in _PRODUCTS_DB.values() if not r.is_deleted]

    if category is not None:
        records = [r for r in records if r.category == category]
    if min_price is not None:
        records = [r for r in records if r.price >= min_price]
    if max_price is not None:
        records = [r for r in records if r.price <= max_price]
    if in_stock_only:
        records = [r for r in records if r.stock > 0]

    records.sort(key=lambda r: getattr(r, sort_by), reverse=descending)

    total = len(records)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = records[start:end]

    return ProductListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[r.to_out() for r in page_items],
    )


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: str) -> ProductOut:
    """Retrieve a single product by ID."""
    record = _get_active_or_404(product_id)
    return record.to_out()


@router.put("/{product_id}", response_model=ProductOut)
def update_product(product_id: str, payload: ProductUpdate) -> ProductOut:
    """Update mutable product fields."""
    record = _get_active_or_404(product_id)

    if payload.name is not None:
        record.name = payload.name
    if payload.description is not None:
        record.description = payload.description
    if payload.category is not None:
        record.category = payload.category
    if payload.price is not None:
        record.price = round(payload.price, 2)

    record.updated_at = datetime.utcnow()
    logger.info("Updated product %s", product_id)
    return record.to_out()


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: str) -> None:
    """Soft-delete a product."""
    record = _get_active_or_404(product_id)
    record.is_deleted = True
    record.updated_at = datetime.utcnow()
    logger.info("Soft-deleted product %s", product_id)
    return None


@router.post("/{product_id}/stock", response_model=ProductOut)
def adjust_stock(product_id: str, payload: StockAdjustment) -> ProductOut:
    """Increase or decrease stock for a product."""
    record = _get_active_or_404(product_id)

    new_stock = record.stock + payload.quantity
    if new_stock < 0:
        raise HTTPException(status_code=400, detail="Insufficient stock for this adjustment")

    record.stock = new_stock
    record.updated_at = datetime.utcnow()
    record.history.append(
        StockHistoryEntry(
            timestamp=record.updated_at,
            quantity_change=payload.quantity,
            reason=payload.reason,
            resulting_stock=new_stock,
        )
    )
    logger.info("Adjusted stock for %s by %d (reason: %s)", product_id, payload.quantity, payload.reason)
    return record.to_out()


@router.get("/{product_id}/history", response_model=List[StockHistoryEntry])
def get_stock_history(product_id: str) -> List[StockHistoryEntry]:
    """Return the stock adjustment history for a product."""
    record = _get_active_or_404(product_id)
    return record.history


@router.get("/category/{category}/count")
def count_by_category(category: ProductCategory) -> dict:
    """Return the number of active products in a given category."""
    count = sum(
        1 for r in _PRODUCTS_DB.values() if not r.is_deleted and r.category == category
    )
    return {"category": category.value, "count": count}


@router.get("/stats/low-stock")
def low_stock_report(threshold: int = Query(5, ge=0)) -> List[ProductOut]:
    """Return products whose stock is at or below the given threshold."""
    low_stock = [
        r.to_out() for r in _PRODUCTS_DB.values()
        if not r.is_deleted and r.stock <= threshold
    ]
    return low_stock


def seed_demo_products(count: int = 5) -> None:
    """Populate the in-memory store with demo products (used at startup)."""
    categories = list(ProductCategory)
    for i in range(count):
        sku = f"SKU-DEMO-{i:04d}"
        if _find_by_sku(sku):
            continue
        record = ProductRecord(
            name=f"Demo Product {i}",
            description="An automatically generated demo product.",
            category=categories[i % len(categories)],
            price=9.99 + i,
            sku=sku,
            initial_stock=10 + i,
        )
        _PRODUCTS_DB[record.id] = record
    logger.info("Seeded %d demo products", count)
