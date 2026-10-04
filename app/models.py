import re
from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from flask_login import UserMixin
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import quantity
from .extensions import db

_ph = PasswordHasher()

ROLES = ("admin", "editor", "viewer")
BUILTIN_COLUMNS = ("description", "quantity", "observation", "photos")
CUSTOM_TYPES = ("text", "longtext", "number", "date", "checkbox", "select", "url")


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def default_columns():
    return [
        {"key": "description", "label": "Item", "type": "builtin", "width": 320},
        {"key": "quantity", "label": "Qty", "type": "builtin", "width": 120},
        {"key": "observation", "label": "Obs", "type": "builtin", "width": 300},
        {"key": "photos", "label": "Photo", "type": "builtin", "width": 110},
    ]


def slugify_key(label: str, existing: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40] or "field"
    if base in BUILTIN_COLUMNS:
        base = f"c_{base}"
    key, i = base, 2
    while key in existing:
        key = f"{base}_{i}"
        i += 1
    return key


class User(UserMixin, db.Model):
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(200))
    display_name: Mapped[str | None] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="editor", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login: Mapped[datetime | None] = mapped_column(DateTime)
    # Personal UI preferences, e.g. {"grid": {"rowH": 36, "font": 13}, "tables": {"12": {...}}}
    prefs: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSON), default=dict, server_default=text("'{}'"))

    def set_password(self, pw: str):
        self.password_hash = _ph.hash(pw)

    def check_password(self, pw: str) -> bool:
        try:
            ok = _ph.verify(self.password_hash, pw)
        except (VerifyMismatchError, InvalidHashError):
            return False
        if ok and _ph.check_needs_rehash(self.password_hash):
            self.set_password(pw)
        return ok

    @property
    def is_active(self):
        return self.active

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def can_edit(self):
        return self.role in ("admin", "editor")

    @property
    def name(self):
        return self.display_name or self.username

    def to_dict(self):
        return {
            "id": self.id, "username": self.username, "email": self.email,
            "display_name": self.display_name, "role": self.role, "active": self.active,
            "created_at": _iso(self.created_at), "last_login": _iso(self.last_login),
        }


class Folder(db.Model):
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("folder.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    delete_batch: Mapped[str | None] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    children = relationship("Folder", cascade="all, delete-orphan", passive_deletes=True,
                            order_by="Folder.position")
    tables = relationship("InvTable", back_populates="folder", cascade="all, delete-orphan",
                          passive_deletes=True, order_by="InvTable.position")

    def to_dict(self):
        return {"id": self.id, "parent_id": self.parent_id, "name": self.name,
                "position": self.position, "active": self.active}

    def path(self) -> list[str]:
        names, f = [], self
        while f is not None:
            names.append(f.name)
            f = db.session.get(Folder, f.parent_id) if f.parent_id else None
        return list(reversed(names))


class InvTable(db.Model):
    __tablename__ = "inv_table"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    folder_id: Mapped[int | None] = mapped_column(ForeignKey("folder.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    context: Mapped[str | None] = mapped_column(Text)
    columns: Mapped[list] = mapped_column(MutableList.as_mutable(JSON), default=default_columns)
    position: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    delete_batch: Mapped[str | None] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    # "Last updated" as shown to users: bumped on any content change (items, photos, columns...)
    content_updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    content_updated_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))

    folder = relationship("Folder", back_populates="tables")
    content_updater = relationship("User", foreign_keys=[content_updated_by])
    items = relationship("Item", back_populates="table", cascade="all, delete-orphan",
                         passive_deletes=True, order_by="Item.position")

    def custom_columns(self):
        return [c for c in self.columns if c.get("type") != "builtin"]

    def column_keys(self) -> set[str]:
        return {c["key"] for c in self.columns}

    def path(self) -> list[str]:
        return (self.folder.path() if self.folder else []) + [self.name]

    def to_dict(self, with_counts=False):
        d = {
            "id": self.id, "folder_id": self.folder_id, "name": self.name,
            "summary": self.summary or "", "context": self.context or "",
            "columns": list(self.columns or []), "position": self.position,
            "active": self.active,
            "content_updated_at": _iso(self.content_updated_at),
            "content_updated_by": self.content_updater.name if self.content_updater else None,
        }
        if with_counts:
            d["item_count"] = self.live_item_count()
        return d

    def live_item_count(self) -> int:
        return db.session.query(Item.id).filter(Item.table_id == self.id, Item.deleted_at.is_(None)).count()

    def live_items(self):
        return (db.session.query(Item).filter(Item.table_id == self.id, Item.deleted_at.is_(None))
                .order_by(Item.position, Item.id).all())


class Item(db.Model):
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("inv_table.id", ondelete="CASCADE"), index=True, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(Text, default="")
    observation: Mapped[str] = mapped_column(Text, default="")
    qty_type: Mapped[str] = mapped_column(String(20), default="count")
    qty_value: Mapped[float | None] = mapped_column(Float)
    qty_unit: Mapped[str] = mapped_column(String(30), default="")
    qty_text: Mapped[str] = mapped_column(String(200), default="")
    qty_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    custom: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSON), default=dict)
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    delete_batch: Mapped[str | None] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)

    table = relationship("InvTable", back_populates="items")
    updater = relationship("User", foreign_keys=[updated_by])
    creator = relationship("User", foreign_keys=[created_by])
    photos = relationship("Photo", back_populates="item", cascade="all, delete-orphan",
                          passive_deletes=True, order_by="Photo.position")

    @property
    def quantity(self) -> dict:
        return {"type": self.qty_type, "value": self.qty_value, "unit": self.qty_unit or "",
                "text": self.qty_text or "", "estimated": bool(self.qty_estimated)}

    @quantity.setter
    def quantity(self, q):
        q = quantity.clean(q)
        self.qty_type, self.qty_value, self.qty_unit = q["type"], q["value"], q["unit"]
        self.qty_text, self.qty_estimated = q["text"], q["estimated"]

    def to_dict(self):
        q = self.quantity
        return {
            "id": self.id, "table_id": self.table_id, "position": self.position,
            "description": self.description or "", "observation": self.observation or "",
            "quantity": q, "quantity_display": quantity.format(q),
            "custom": dict(self.custom or {}), "ai_generated": self.ai_generated,
            "photos": [p.to_dict() for p in self.photos],
            "updated_at": _iso(self.updated_at),
            "updated_by": self.updater.name if self.updater else None,
            "created_at": _iso(self.created_at),
            "created_by": self.creator.name if self.creator else None,
        }


class Photo(db.Model):
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("item.id", ondelete="CASCADE"), index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    original_name: Mapped[str | None] = mapped_column(String(255))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))

    item = relationship("Item", back_populates="photos")

    def to_dict(self):
        return {
            "id": self.id, "item_id": self.item_id, "width": self.width, "height": self.height,
            "thumb": f"/media/{self.sha256}/thumb", "display": f"/media/{self.sha256}/display",
            "original": f"/media/{self.sha256}/original", "name": self.original_name,
        }


class Setting(db.Model):
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)


class AuditLog(db.Model):
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(40))
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Invite(db.Model):
    """Registration link that can be used a limited number of times."""
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="editor", nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    uses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    note: Mapped[str | None] = mapped_column(String(200))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    creator = relationship("User", foreign_keys=[created_by])

    @property
    def usable(self) -> bool:
        if self.revoked or self.uses >= self.max_uses:
            return False
        return self.expires_at is None or self.expires_at > utcnow()

    def to_dict(self):
        return {"id": self.id, "token": self.token, "role": self.role, "max_uses": self.max_uses,
                "uses": self.uses, "note": self.note, "expires_at": _iso(self.expires_at),
                "revoked": self.revoked, "usable": self.usable, "created_at": _iso(self.created_at),
                "created_by": self.creator.name if self.creator else None}


class AIUsage(db.Model):
    """One model call (proposal, refinement, chat step...) and its token counts."""
    __tablename__ = "ai_usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # propose | refine | chat | test
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(120))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class SearchDoc(db.Model):
    __tablename__ = "search_doc"
    __table_args__ = (db.UniqueConstraint("entity_type", "entity_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(10), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    table_id: Mapped[int | None] = mapped_column(Integer, index=True)
    folder_ids: Mapped[str] = mapped_column(Text, default="")  # ",1,4,9," ancestor chain for scoping
    active: Mapped[bool] = mapped_column(Boolean, default=True)  # effective (archived ancestors)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)  # effective (deleted ancestors)
    title: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    embedding: Mapped[bytes | None] = mapped_column(db.LargeBinary)
    embed_model: Mapped[str | None] = mapped_column(String(200))
    embed_hash: Mapped[str | None] = mapped_column(String(64))  # content_hash the vector was built from
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None
