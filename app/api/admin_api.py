"""Admin endpoints: users, settings, AI/search config, branding, backup, audit."""
import io
import re
from zoneinfo import available_timezones

from flask import Response, current_app, jsonify, request
from flask_login import current_user
from PIL import Image

from ..ai.providers import AIError, get_provider
from ..extensions import db
from ..models import ROLES, AIUsage, AuditLog, Invite, User, utcnow
from ..search import embed, index
from ..search.query import search as run_search
from ..services import backup, maintenance, settings
from ..services.audit import audit
from . import admin_required, body, bp

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


# ---------------------------------------------------------------- users

@bp.get("/admin/users")
@admin_required
def list_users():
    return jsonify([u.to_dict() for u in db.session.query(User).order_by(User.username)])


def _validate_user(d, user=None):
    if "role" in d and d["role"] not in ROLES:
        raise ValueError("Invalid role")
    if "username" in d:
        name = (d["username"] or "").strip()
        if not name:
            raise ValueError("Username is required")
        clash = db.session.query(User).filter(User.username == name).first()
        if clash and clash is not user:
            raise ValueError("That username is taken")
    if d.get("password") and len(d["password"]) < 8:
        raise ValueError("Password must have at least 8 characters")


@bp.post("/admin/users")
@admin_required
def create_user():
    d = body()
    try:
        _validate_user(d)
        if not d.get("password"):
            raise ValueError("Password is required")
    except ValueError as e:
        return jsonify(error=str(e)), 400
    u = User(username=d["username"].strip(), email=(d.get("email") or "").strip() or None,
             display_name=(d.get("display_name") or "").strip() or None, role=d.get("role") or "editor")
    u.set_password(d["password"])
    db.session.add(u)
    db.session.commit()
    audit("create", "user", u.id, u.username)
    return jsonify(u.to_dict()), 201


@bp.patch("/admin/users/<int:uid>")
@admin_required
def update_user(uid):
    u = db.session.get(User, uid)
    if u is None:
        return jsonify(error="User not found"), 404
    d = body()
    try:
        _validate_user(d, u)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    demoting = (d.get("role") not in (None, "admin")) or d.get("active") is False
    if u.is_admin and demoting:
        admins = db.session.query(User).filter(User.role == "admin", User.active.is_(True)).count()
        if admins <= 1:
            return jsonify(error="There must be at least one active admin"), 400
    for f in ("username", "role"):
        if f in d:
            setattr(u, f, d[f].strip() if isinstance(d[f], str) else d[f])
    for f in ("email", "display_name"):
        if f in d:
            setattr(u, f, (d[f] or "").strip() or None)
    if "active" in d:
        u.active = bool(d["active"])
    if d.get("password"):
        u.set_password(d["password"])
    db.session.commit()
    audit("update", "user", u.id, ",".join(k for k in d if k != "password") + (",password" if d.get("password") else ""))
    return jsonify(u.to_dict())


@bp.delete("/admin/users/<int:uid>")
@admin_required
def delete_user(uid):
    u = db.session.get(User, uid)
    if u is None:
        return jsonify(error="User not found"), 404
    if u.id == current_user.id:
        return jsonify(error="You can't delete yourself"), 400
    if u.is_admin and db.session.query(User).filter(User.role == "admin").count() <= 1:
        return jsonify(error="There must be at least one admin"), 400
    db.session.delete(u)
    db.session.commit()
    audit("delete", "user", uid, u.username)
    return jsonify(ok=True)


# ---------------------------------------------------------------- invite links

def _invite_url(inv: Invite) -> str:
    base = (settings.get("server.base_url") or request.host_url).rstrip("/")
    return f"{base}/join/{inv.token}"


@bp.get("/admin/invites")
@admin_required
def list_invites():
    rows = db.session.query(Invite).order_by(Invite.id.desc()).limit(200)
    return jsonify([{**i.to_dict(), "url": _invite_url(i)} for i in rows])


@bp.post("/admin/invites")
@admin_required
def create_invite():
    import secrets
    from datetime import timedelta
    d = body()
    role = d.get("role") or "editor"
    if role not in ROLES:
        return jsonify(error="Invalid role"), 400
    try:
        max_uses = max(1, min(1000, int(d.get("max_uses") or 1)))
        days = int(d["expires_days"]) if d.get("expires_days") not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        return jsonify(error="Uses and expiry must be numbers"), 400
    inv = Invite(token=secrets.token_urlsafe(24), role=role, max_uses=max_uses,
                 note=(d.get("note") or "").strip()[:200] or None,
                 expires_at=utcnow() + timedelta(days=days) if days else None,
                 created_by=current_user.id)
    db.session.add(inv)
    db.session.commit()
    audit("create", "invite", inv.id, f"role={role} uses={max_uses}")
    return jsonify({**inv.to_dict(), "url": _invite_url(inv)}), 201


@bp.delete("/admin/invites/<int:iid>")
@admin_required
def revoke_invite(iid):
    inv = db.session.get(Invite, iid)
    if inv is None:
        return jsonify(error="Invite not found"), 404
    inv.revoked = True
    db.session.commit()
    audit("revoke", "invite", iid)
    return jsonify(ok=True)


# ---------------------------------------------------------------- settings

_EDITABLE = {
    "app.name": str, "app.tagline": str, "app.primary_color": str, "app.theme": str,
    "server.base_url": str, "server.image_max_px": int, "server.image_quality": int,
    "server.session_days": int, "server.allow_registration": bool, "server.trash_days": int,
    "server.stale_days": int, "server.timezone": str, "ai.provider": str, "ai.org_context": str, "ai.system_prompt": str,
    "ai.max_existing_items": int, "search.provider": str, "search.model": str,
    "search.min_similarity": float,
}
_BOUNDS = {"server.image_max_px": (512, 4096), "server.image_quality": (40, 95),
           "server.session_days": (1, 365), "server.trash_days": (1, 3650), "server.stale_days": (7, 3650),
           "ai.max_existing_items": (0, 2000), "search.min_similarity": (0.0, 0.95)}


@bp.get("/admin/settings")
@admin_required
def get_settings():
    from ..services.settings import PROVIDERS
    return jsonify({
        "values": {k: settings.get(k) for k in _EDITABLE},
        "ai": settings.ai_config(reveal=False),
        "providers": PROVIDERS,
        "embed_providers": embed.EMBED_PROVIDERS,
        "branding": {"logo": settings.get("app.logo"), "favicon": settings.get("app.favicon")},
        "timezones": sorted(available_timezones()),
    })


@bp.put("/admin/settings")
@admin_required
def put_settings():
    d = body()
    old_embed = embed.current_model_id()
    for k, v in (d.get("values") or {}).items():
        typ = _EDITABLE.get(k)
        if typ is None:
            continue
        try:
            v = typ(v) if typ is not bool else bool(v)
        except (TypeError, ValueError):
            return jsonify(error=f"Invalid value for {k}"), 400
        if k in _BOUNDS:
            lo, hi = _BOUNDS[k]
            v = max(lo, min(hi, v))
        if k == "app.primary_color" and not HEX.match(v):
            return jsonify(error="Color must be like #4f46e5"), 400
        if k == "server.timezone":
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
            try:
                ZoneInfo(v)
            except (ZoneInfoNotFoundError, ValueError):
                return jsonify(error="Unknown timezone"), 400
        if k == "app.theme" and v not in ("auto", "light", "dark"):
            return jsonify(error="Invalid theme"), 400
        if k == "ai.provider" and v not in settings.PROVIDERS:
            return jsonify(error="Unknown AI provider"), 400
        if k == "search.provider" and v not in embed.EMBED_PROVIDERS:
            return jsonify(error="Unknown embedding provider"), 400
        settings.set(k, v)
    for name, conf in (d.get("providers") or {}).items():
        if name in settings.PROVIDERS and isinstance(conf, dict):
            settings.save_provider(name, conf)
    audit("update", "settings", None, ",".join(list((d.get("values") or {}).keys()) + list((d.get("providers") or {}).keys())))
    if embed.current_model_id() != old_embed:
        index.enqueue({("embed", 0)})
    return get_settings()


# ---------------------------------------------------------------- AI test / models

@bp.post("/admin/ai/test")
@admin_required
def ai_test():
    d = body()
    name = d.get("provider") or settings.get("ai.provider")
    conf = {k: v for k, v in (d.get("conf") or {}).items() if v not in (None, "")}
    if conf.get("api_key") and ("…" in conf["api_key"] or "••••" in conf["api_key"]):
        conf.pop("api_key")
    try:
        msg = get_provider(name, conf).test()
    except AIError as e:
        return jsonify(ok=False, message=str(e))
    except Exception as e:  # noqa: BLE001 - surface anything to the admin
        return jsonify(ok=False, message=f"{type(e).__name__}: {e}")
    return jsonify(ok=True, message=msg)


@bp.post("/admin/ai/models")
@admin_required
def ai_models():
    d = body()
    conf = {k: v for k, v in (d.get("conf") or {}).items() if v not in (None, "")}
    if conf.get("api_key") and ("…" in conf["api_key"] or "••••" in conf["api_key"]):
        conf.pop("api_key")
    conf.setdefault("model", "-")
    try:
        return jsonify(models=get_provider(d.get("provider"), conf).list_models())
    except AIError as e:
        return jsonify(error=str(e)), 400


# ---------------------------------------------------------------- AI usage

@bp.get("/admin/usage")
@admin_required
def ai_usage():
    from datetime import timedelta

    from sqlalchemy import case, func
    days = max(1, min(3650, request.args.get("days", 30, type=int)))
    since = utcnow() - timedelta(days=days)
    names = {u.id: u.name for u in db.session.query(User)}
    cols = (func.count(AIUsage.id), func.sum(AIUsage.input_tokens), func.sum(AIUsage.output_tokens),
            func.sum(case((AIUsage.ok.is_(False), 1), else_=0)), func.max(AIUsage.created_at))

    def rows(group, flt=True):
        q = db.session.query(*group, *cols)
        if flt:
            q = q.filter(AIUsage.created_at >= since)
        return q.group_by(*group).all()

    def pack(r, n):
        calls, inp, out, failed, last = r[n:]
        return {"calls": calls, "input": inp or 0, "output": out or 0, "failed": failed or 0,
                "last": last.isoformat() + "Z" if last else None}

    per_user = []
    all_time = {r[0]: pack(r, 1) for r in rows((AIUsage.user_id,), flt=False)}
    for r in rows((AIUsage.user_id,)):
        per_user.append({"user": names.get(r[0], "(deleted user)" if r[0] else "system"), "user_id": r[0],
                         **pack(r, 1), "all_time": all_time.get(r[0])})
    per_user.sort(key=lambda x: x["input"] + x["output"], reverse=True)
    per_model = [{"provider": r[0], "model": r[1], **pack(r, 2)} for r in rows((AIUsage.provider, AIUsage.model))]
    per_kind = [{"kind": r[0], **pack(r, 1)} for r in rows((AIUsage.kind,))]
    recent = [{"at": u.created_at.isoformat() + "Z", "user": names.get(u.user_id), "kind": u.kind,
               "model": f"{u.provider}:{u.model}", "input": u.input_tokens, "output": u.output_tokens,
               "seconds": round(u.duration_ms / 1000, 1), "ok": u.ok}
              for u in db.session.query(AIUsage).order_by(AIUsage.id.desc()).limit(100)]
    return jsonify(days=days, per_user=per_user, per_model=per_model, per_kind=per_kind, recent=recent)


# ---------------------------------------------------------------- search index

@bp.get("/admin/search/status")
@admin_required
def search_status():
    return jsonify(index.status())


@bp.post("/admin/search/rebuild")
@admin_required
def search_rebuild():
    index.rebuild()
    audit("rebuild", "search_index")
    return jsonify(index.status())


@bp.post("/admin/search/test")
@admin_required
def search_test():
    from ..search.query import Scope
    return jsonify(run_search(body().get("q", ""), Scope(include_archived=True), limit=15))


# ---------------------------------------------------------------- branding

@bp.post("/admin/branding/<kind>")
@admin_required
def upload_branding(kind):
    if kind not in ("logo", "favicon"):
        return jsonify(error="Unknown asset"), 404
    f = request.files.get("file")
    if not f:
        return jsonify(error="No file"), 400
    raw = f.read()
    out_dir = current_app.config["BRANDING_DIR"]
    if f.filename.lower().endswith(".svg") and kind == "logo":
        if b"<script" in raw.lower() or b"onload" in raw.lower():
            return jsonify(error="SVG with scripts is not allowed"), 400
        name = "logo.svg"
        (out_dir / name).write_bytes(raw)
    else:
        try:
            img = Image.open(io.BytesIO(raw))
            img.load()
        except Exception:  # noqa: BLE001
            return jsonify(error="Not a valid image"), 400
        img = img.convert("RGBA")
        size = 256 if kind == "favicon" else 512
        img.thumbnail((size, size), Image.LANCZOS)
        name = f"{kind}.png"
        img.save(out_dir / name, "PNG", optimize=True)
        if kind == "favicon":
            img.save(out_dir / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    settings.set(f"app.{kind}", name)
    audit("update", "branding", None, kind)
    return jsonify({"file": name})


@bp.delete("/admin/branding/<kind>")
@admin_required
def delete_branding(kind):
    if kind not in ("logo", "favicon"):
        return jsonify(error="Unknown asset"), 404
    settings.set(f"app.{kind}", None)
    return jsonify(ok=True)


# ---------------------------------------------------------------- backup / maintenance / audit

@bp.get("/admin/backup")
@admin_required
def download_backup():
    data, name = backup.create()
    audit("backup", "system")
    return Response(data, mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@bp.post("/admin/restore")
@admin_required
def restore_backup():
    f = request.files.get("file")
    if not f:
        return jsonify(error="No file"), 400
    try:
        res = backup.restore(f.stream)
    except backup.RestoreError as e:
        return jsonify(error=str(e)), 400
    return jsonify(res)


@bp.post("/admin/maintenance/run")
@admin_required
def run_maintenance():
    return jsonify(maintenance.run_once())


@bp.get("/admin/audit")
@admin_required
def audit_log():
    users = {u.id: u.name for u in db.session.query(User)}
    rows = db.session.query(AuditLog).order_by(AuditLog.id.desc()).limit(300)
    return jsonify([{"at": r.created_at.isoformat() + "Z", "user": users.get(r.user_id), "action": r.action,
                     "entity": r.entity, "entity_id": r.entity_id, "detail": r.detail} for r in rows])
