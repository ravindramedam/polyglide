"""
users.py
---------
FastAPI router module that implements a complete set of user-management
endpoints backed by an in-memory data store. This module is meant to be
imported into a parent FastAPI application (see main.py).

Features:
    - Pydantic models for request/response validation
    - Full CRUD operations (Create, Read, Update, Delete)
    - Pagination and search/filtering
    - Simple password hashing simulation
    - Custom exception handling
    - Logging of every operation
"""

import logging
import re
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field, validator

logger = logging.getLogger("users")
logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/users", tags=["Users"])


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------
class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    email: EmailStr
    full_name: Optional[str] = Field(None, max_length=100)
    is_active: bool = True

    @validator("username")
    def username_alphanumeric(cls, value: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_]+$", value):
            raise ValueError("username must be alphanumeric or underscore only")
        return value


class UserCreate(UserBase):
    password: str = Field(..., min_length=8)

    @validator("password")
    def password_strength(cls, value: str) -> str:
        if not re.search(r"[A-Z]", value):
            raise ValueError("password must contain at least one uppercase letter")
        if not re.search(r"[0-9]", value):
            raise ValueError("password must contain at least one digit")
        return value


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    is_active: Optional[bool] = None


class UserOut(UserBase):
    id: str
    created_at: datetime
    updated_at: datetime


class UserListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[UserOut]


# --------------------------------------------------------------------------
# In-memory "database"
# --------------------------------------------------------------------------
class UserRecord:
    def __init__(self, username: str, email: str, full_name: Optional[str],
                 password_hash: str, is_active: bool = True):
        self.id = str(uuid.uuid4())
        self.username = username
        self.email = email
        self.full_name = full_name
        self.password_hash = password_hash
        self.is_active = is_active
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()

    def to_out(self) -> UserOut:
        return UserOut(
            id=self.id,
            username=self.username,
            email=self.email,
            full_name=self.full_name,
            is_active=self.is_active,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


_USERS_DB: dict[str, UserRecord] = {}


def _hash_password(raw_password: str) -> str:
    """Simulate password hashing (NOT secure, demo purposes only)."""
    return f"hashed::{raw_password[::-1]}"


def _find_by_username(username: str) -> Optional[UserRecord]:
    for record in _USERS_DB.values():
        if record.username.lower() == username.lower():
            return record
    return None


def _find_by_email(email: str) -> Optional[UserRecord]:
    for record in _USERS_DB.values():
        if record.email.lower() == email.lower():
            return record
    return None


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@router.post("/", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate) -> UserOut:
    """Create a new user."""
    if _find_by_username(payload.username):
        raise HTTPException(status_code=400, detail="Username already taken")
    if _find_by_email(payload.email):
        raise HTTPException(status_code=400, detail="Email already registered")

    record = UserRecord(
        username=payload.username,
        email=payload.email,
        full_name=payload.full_name,
        password_hash=_hash_password(payload.password),
        is_active=payload.is_active,
    )
    _USERS_DB[record.id] = record
    logger.info("Created user %s (%s)", record.username, record.id)
    return record.to_out()


@router.get("/", response_model=UserListResponse)
def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    search: Optional[str] = Query(None, description="Search by username or email"),
    is_active: Optional[bool] = Query(None),
) -> UserListResponse:
    """List users with optional search and pagination."""
    records = list(_USERS_DB.values())

    if search:
        search_lower = search.lower()
        records = [
            r for r in records
            if search_lower in r.username.lower() or search_lower in r.email.lower()
        ]

    if is_active is not None:
        records = [r for r in records if r.is_active == is_active]

    records.sort(key=lambda r: r.created_at)

    total = len(records)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = records[start:end]

    return UserListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[r.to_out() for r in page_items],
    )


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: str) -> UserOut:
    """Retrieve a single user by ID."""
    record = _USERS_DB.get(user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="User not found")
    return record.to_out()


@router.put("/{user_id}", response_model=UserOut)
def update_user(user_id: str, payload: UserUpdate) -> UserOut:
    """Update an existing user's mutable fields."""
    record = _USERS_DB.get(user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.email is not None:
        existing = _find_by_email(payload.email)
        if existing and existing.id != user_id:
            raise HTTPException(status_code=400, detail="Email already registered")
        record.email = payload.email

    if payload.full_name is not None:
        record.full_name = payload.full_name

    if payload.is_active is not None:
        record.is_active = payload.is_active

    record.updated_at = datetime.utcnow()
    logger.info("Updated user %s", user_id)
    return record.to_out()


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: str) -> None:
    """Delete a user permanently."""
    if user_id not in _USERS_DB:
        raise HTTPException(status_code=404, detail="User not found")
    del _USERS_DB[user_id]
    logger.info("Deleted user %s", user_id)
    return None


@router.post("/{user_id}/deactivate", response_model=UserOut)
def deactivate_user(user_id: str) -> UserOut:
    """Convenience endpoint to deactivate a user."""
    record = _USERS_DB.get(user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="User not found")
    record.is_active = False
    record.updated_at = datetime.utcnow()
    return record.to_out()


@router.post("/{user_id}/activate", response_model=UserOut)
def activate_user(user_id: str) -> UserOut:
    """Convenience endpoint to activate a user."""
    record = _USERS_DB.get(user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="User not found")
    record.is_active = True
    record.updated_at = datetime.utcnow()
    return record.to_out()


@router.get("/stats/summary")
def user_stats_summary() -> dict:
    """Return aggregate statistics about the user base."""
    total = len(_USERS_DB)
    active = sum(1 for r in _USERS_DB.values() if r.is_active)
    inactive = total - active
    return {
        "total_users": total,
        "active_users": active,
        "inactive_users": inactive,
        "generated_at": datetime.utcnow().isoformat(),
    }


def seed_demo_users(count: int = 5) -> None:
    """Populate the in-memory store with demo users (used at startup)."""
    for i in range(count):
        username = f"demo_user_{i}"
        if _find_by_username(username):
            continue
        record = UserRecord(
            username=username,
            email=f"{username}@example.com",
            full_name=f"Demo User {i}",
            password_hash=_hash_password("Password1"),
            is_active=True,
        )
        _USERS_DB[record.id] = record
    logger.info("Seeded %d demo users", count)
