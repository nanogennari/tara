import io
import time

from PIL import Image


def _img_bytes(color="red"):
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, "JPEG")
    buf.seek(0)
    return buf


def _mk(client, path, **json):
    r = client.post(path, json=json)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def test_requires_login(app):
    c = app.test_client()
    assert c.get("/api/tree").status_code == 401
    assert c.get("/").status_code == 302


def test_folder_table_item_crud(client):
    f = _mk(client, "/api/folders", name="Rio")
    sub = _mk(client, "/api/folders", name="2026", parent_id=f["id"])
    t = _mk(client, "/api/tables", name="Box 01", folder_id=sub["id"], summary="Meds")
    it = _mk(client, f"/api/tables/{t['id']}/items", description="Aleve", quantity={"type": "pack", "value": 90, "unit": "tabs"})
    assert it["quantity_display"] == "90 tabs"

    r = client.patch(f"/api/items/{it['id']}", json={"observation": "Expired", "quantity": {"type": "count", "value": 75, "estimated": True}})
    assert r.get_json()["quantity_display"] == "~75"

    data = client.get(f"/api/tables/{t['id']}").get_json()
    assert data["path"] == ["Rio", "2026"]
    assert data["items"][0]["observation"] == "Expired"
    assert data["writable"] is True

    tree = client.get("/api/tree").get_json()
    assert {x["name"] for x in tree["folders"]} == {"Rio", "2026"}
    assert tree["tables"][0]["item_count"] == 1


def test_custom_columns(client):
    t = _mk(client, "/api/tables", name="T")
    col = _mk(client, f"/api/tables/{t['id']}/columns", label="Expiry", type="date")
    assert col["key"] == "expiry"
    it = _mk(client, f"/api/tables/{t['id']}/items", description="X", custom={"expiry": "2026-04-30", "bogus": 1})
    assert it["custom"] == {"expiry": "2026-04-30"}
    r = client.patch(f"/api/items/{it['id']}", json={"custom": {"expiry": "No date"}})
    assert r.get_json()["custom"]["expiry"] is None
    assert client.delete(f"/api/tables/{t['id']}/columns/description").status_code == 400
    assert client.delete(f"/api/tables/{t['id']}/columns/expiry").status_code == 200


def test_last_updated_bumps(client, app):
    t = _mk(client, "/api/tables", name="T")
    before = client.get(f"/api/tables/{t['id']}").get_json()["content_updated_at"]
    time.sleep(0.01)
    it = _mk(client, f"/api/tables/{t['id']}/items", description="A")
    d1 = client.get(f"/api/tables/{t['id']}").get_json()
    assert d1["content_updated_at"] > before
    assert d1["content_updated_by"] == "admin"

    time.sleep(0.01)
    r = client.post(f"/api/items/{it['id']}/photos", data={"files": (_img_bytes(), "a.jpg")},
                    content_type="multipart/form-data")
    assert r.status_code == 201
    d2 = client.get(f"/api/tables/{t['id']}").get_json()
    assert d2["content_updated_at"] > d1["content_updated_at"]

    time.sleep(0.01)
    client.post("/api/items/bulk", json={"ids": [it["id"]], "action": "delete"})
    d3 = client.get(f"/api/tables/{t['id']}").get_json()
    assert d3["content_updated_at"] > d2["content_updated_at"]

    # Folder shows the newest table's date
    f = _mk(client, "/api/folders", name="F")
    client.post("/api/nodes/bulk", json={"tables": [t["id"]], "action": "move", "target_folder_id": f["id"]})
    tree = client.get("/api/tree").get_json()
    folder = next(x for x in tree["folders"] if x["id"] == f["id"])
    table = next(x for x in tree["tables"] if x["id"] == t["id"])
    assert folder["content_updated_at"] == table["content_updated_at"]


def test_bulk_delete_undo_and_purge(client, app):
    f = _mk(client, "/api/folders", name="Old")
    sub = _mk(client, "/api/folders", name="Sub", parent_id=f["id"])
    t1 = _mk(client, "/api/tables", name="A", folder_id=f["id"])
    t2 = _mk(client, "/api/tables", name="B", folder_id=sub["id"])
    t3 = _mk(client, "/api/tables", name="Loose")
    ids = [_mk(client, f"/api/tables/{t['id']}/items", description=f"i{n}")["id"] for t in (t1, t2, t3) for n in range(3)]
    client.post(f"/api/items/{ids[0]}/photos", data={"files": (_img_bytes("blue"), "b.jpg")},
                content_type="multipart/form-data")

    imp = client.post("/api/nodes/impact", json={"folders": [f["id"]], "tables": [t3["id"]]}).get_json()
    assert imp == {"folders": 2, "tables": 3, "items": 9, "photos": 1}

    res = client.post("/api/nodes/bulk", json={"folders": [f["id"]], "tables": [t3["id"]], "action": "delete"}).get_json()
    assert res["tables"] == 3
    tree = client.get("/api/tree").get_json()
    assert tree["folders"] == [] and tree["tables"] == []

    trash = client.get("/api/trash").get_json()
    assert len(trash) == 1 and trash[0]["counts"]["tables"] == 3
    assert [x["name"] for x in trash[0]["folders"]] == ["Old"]  # only top-level shown

    client.post(f"/api/trash/{res['batch']}/restore")
    tree = client.get("/api/tree").get_json()
    assert len(tree["tables"]) == 3 and len(tree["folders"]) == 2

    # Row-level bulk delete + purge removes orphaned files
    r = client.post("/api/items/bulk", json={"ids": ids[:2], "action": "delete"}).get_json()
    assert r["deleted"] == 2
    assert client.get(f"/api/tables/{t1['id']}").get_json()["item_count"] == 1
    from app.services import photos as photo_svc
    with app.app_context():
        from app.models import Photo
        from app.extensions import db
        sha = db.session.query(Photo.sha256).first()[0]
        assert photo_svc.path_for(sha, "original").exists()
    assert client.delete(f"/api/trash/{r['batch']}").status_code == 200
    with app.app_context():
        assert not photo_svc.path_for(sha, "original").exists()


def test_editor_cannot_purge_viewer_cannot_edit(client, editor, viewer):
    t = _mk(client, "/api/tables", name="T")
    assert viewer.post(f"/api/tables/{t['id']}/items", json={"description": "x"}).status_code == 403
    assert viewer.get(f"/api/tables/{t['id']}").get_json()["writable"] is False
    it = _mk(editor, f"/api/tables/{t['id']}/items", description="x")
    r = editor.post("/api/items/bulk", json={"ids": [it["id"]], "action": "delete"}).get_json()
    assert editor.delete(f"/api/trash/{r['batch']}").status_code == 403
    assert editor.get("/api/admin/users").status_code == 403


def test_archived_is_read_only(client):
    f = _mk(client, "/api/folders", name="2025")
    t = _mk(client, "/api/tables", name="Box", folder_id=f["id"])
    it = _mk(client, f"/api/tables/{t['id']}/items", description="x")
    client.post("/api/nodes/bulk", json={"folders": [f["id"]], "action": "archive"})
    data = client.get(f"/api/tables/{t['id']}").get_json()
    assert data["effective_active"] is False and data["writable"] is False
    assert client.patch(f"/api/items/{it['id']}", json={"description": "y"}).status_code == 403
    assert client.post(f"/api/tables/{t['id']}/items", json={"description": "z"}).status_code == 403
    tree = client.get("/api/tree?include_archived=0").get_json()
    assert tree["tables"] == [] and tree["folders"] == []
    client.post("/api/nodes/bulk", json={"folders": [f["id"]], "action": "reactivate"})
    assert client.patch(f"/api/items/{it['id']}", json={"description": "y"}).status_code == 200


def test_move_folder_into_itself_rejected(client):
    a = _mk(client, "/api/folders", name="A")
    b = _mk(client, "/api/folders", name="B", parent_id=a["id"])
    r = client.patch(f"/api/folders/{a['id']}", json={"parent_id": b["id"]})
    assert r.status_code == 400


def test_lexical_search_and_scope(client):
    rio = _mk(client, "/api/folders", name="Rio")
    sp = _mk(client, "/api/folders", name="SP")
    sub = _mk(client, "/api/folders", name="Shelf", parent_id=rio["id"])
    t1 = _mk(client, "/api/tables", name="Box 06 Zometool", folder_id=sub["id"])
    t2 = _mk(client, "/api/tables", name="Laptops", folder_id=sp["id"])
    _mk(client, f"/api/tables/{t1['id']}/items", description="Zometool blue struts", quantity="~370")
    _mk(client, f"/api/tables/{t2['id']}/items", description="Lenovo ThinkPad X13", observation="SIGMABR02 to SIGMABR09")
    _mk(client, f"/api/tables/{t2['id']}/items", description="Toolbox")

    def q(s, **params):
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        return client.get(f"/api/search?q={s}&{qs}").get_json()["results"]

    hits = [h for h in q("zometol") if h["type"] == "item"]  # typo
    assert hits and hits[0]["title"] == "Zometool blue struts"
    assert hits[0]["path"] == ["Rio", "Shelf", "Box 06 Zometool"]
    assert any(h["type"] == "table" for h in q("zometol"))
    assert "Toolbox" not in [h["title"] for h in q("zometol") if h["type"] == "item"]
    assert q("sigmabr05")[0]["title"] == "Lenovo ThinkPad X13"

    # Scope: folder includes its subfolders; exclusions; table scope
    assert all(h["path"][0] == "SP" for h in q("o", folders=sp["id"]))
    assert [h["title"] for h in q("zometool", folders=rio["id"]) if h["type"] == "item"] == ["Zometool blue struts"]
    assert [h for h in q("zometool", folders=rio["id"], exclude_folders=sub["id"]) if h["type"] == "item"] == []
    assert q("zometool", tables=t2["id"]) == []

    # Archived drop out unless asked for
    client.post("/api/nodes/bulk", json={"folders": [rio["id"]], "action": "archive"})
    assert [h for h in q("zometool") if h["type"] == "item"] == []
    assert all(h["active"] is False for h in q("zometool", include_archived=1))
    client.post("/api/nodes/bulk", json={"folders": [rio["id"]], "action": "reactivate"})
    assert q("zometool")

    # Deleted never appear; edits re-index
    t1_items = client.get(f"/api/tables/{t1['id']}").get_json()["items"]
    client.patch(f"/api/items/{t1_items[0]['id']}", json={"description": "Magnetic tiles"})
    assert [h for h in q("struts") if h["type"] == "item"] == []
    assert [h["title"] for h in q("magnetic") if h["type"] == "item"] == ["Magnetic tiles"]
    client.post("/api/items/bulk", json={"ids": [t1_items[0]["id"]], "action": "delete"})
    assert [h for h in q("magnetic", include_archived=1) if h["type"] == "item"] == []


def test_created_by_and_timezone(client, editor):
    t = _mk(client, "/api/tables", name="T")
    it = _mk(editor, f"/api/tables/{t['id']}/items", description="from editor")
    assert it["created_by"] == "editor" and it["created_at"]
    client.patch(f"/api/items/{it['id']}", json={"observation": "admin touched it"})
    data = client.get(f"/api/tables/{t['id']}").get_json()["items"][0]
    assert data["created_by"] == "editor" and data["updated_by"] == "admin"
    assert client.put("/api/admin/settings", json={"values": {"server.timezone": "Mars/Base"}}).status_code == 400
    r = client.put("/api/admin/settings", json={"values": {"server.timezone": "America/Sao_Paulo"}})
    assert r.get_json()["values"]["server.timezone"] == "America/Sao_Paulo"
    csv = client.get(f"/api/tables/{t['id']}/export.csv").get_data(as_text=True)
    assert "Added by" in csv and "editor" in csv


def test_invite_links(client, app):
    inv = client.post("/api/admin/invites", json={"role": "viewer", "max_uses": 2, "note": "volunteers"}).get_json()
    assert inv["url"].endswith("/join/" + inv["token"]) and inv["usable"]
    anon = app.test_client()
    assert anon.get(f"/join/{inv['token']}").status_code == 200
    form = lambda u: {"username": u, "password": "password123", "password2": "password123"}
    assert anon.post(f"/join/{inv['token']}", data=form("vol1")).status_code == 302
    anon2 = app.test_client()
    assert anon2.post(f"/join/{inv['token']}", data=form("vol2")).status_code == 302
    assert app.test_client().post(f"/join/{inv['token']}", data=form("vol3")).status_code == 410
    users = {u["username"]: u for u in client.get("/api/admin/users").get_json()}
    assert users["vol1"]["role"] == "viewer" and "vol3" not in users
    one = client.post("/api/admin/invites", json={}).get_json()
    assert one["max_uses"] == 1 and one["role"] == "editor"
    client.delete(f"/api/admin/invites/{one['id']}")
    assert app.test_client().get(f"/join/{one['token']}").status_code == 410
    assert app.test_client().get("/join/nope").status_code == 410


def test_deep_links_and_login_return(app, client):
    f = _mk(client, "/api/folders", name="Rio")
    t = _mk(client, "/api/tables", name="Box", folder_id=f["id"])
    it = _mk(client, f"/api/tables/{t['id']}/items", description="Tape")
    # Logged in: the shell is served with the target to open
    for path, target in ((f"/t/{t['id']}", '"type": "table"'), (f"/i/{it['id']}", '"type": "item"'),
                         (f"/f/{f['id']}", '"type": "folder"')):
        r = client.get(path)
        assert r.status_code == 200 and target in r.get_data(as_text=True), path
    assert client.get(f"/api/items/{it['id']}").get_json()["table_id"] == t["id"]

    # Logged out: sent to sign in, then back to the same link
    anon = app.test_client()
    r = anon.get(f"/i/{it['id']}")
    assert r.status_code == 302 and "/login" in r.location and f"next=/i/{it['id']}" in r.location.replace("%2F", "/")
    r = anon.post(r.location, data={"username": "editor", "password": "password123"})
    assert r.status_code == 302 and r.location.startswith(f"/i/{it['id']}")

    # Open redirects are refused
    for bad in ("//evil.example", "/\\evil.example", "https://evil.example"):
        r = app.test_client().post(f"/login?next={bad}", data={"username": "viewer", "password": "password123"})
        assert r.location == "/", bad


def test_user_table_view_prefs(client, editor):
    t = _mk(client, "/api/tables", name="T")
    tid = str(t["id"])
    r = editor.patch(f"/api/me/prefs/tables/{tid}?size=large", json={
        "rowH": 80, "font": 99, "hidden": ["observation"], "widths": {"description": 400},
        "order": ["quantity", "description"], "showCreated": True, "bogus": 1})
    assert r.get_json() == {"rowH": 80, "font": 22, "hidden": ["observation"], "widths": {"description": 400},
                            "order": ["quantity", "description"], "showCreated": True}
    editor.patch(f"/api/me/prefs/tables/{tid}?size=small", json={"rowH": 28, "hidden": ["photos"]})
    p = editor.get("/api/me/prefs").get_json()
    assert p["tables"][tid]["small"] == {"rowH": 28, "hidden": ["photos"]}  # separate small-screen version
    assert p["tables"][tid]["large"]["rowH"] == 80
    # Per user: the admin's view and the table definition are untouched
    assert client.get("/api/me/prefs").get_json() == {}
    assert not any(c.get("hidden") for c in client.get(f"/api/tables/{tid}").get_json()["columns"])
    # Default for large screens + "apply everywhere": drops large density overrides only
    p = editor.put("/api/me/prefs/grid?size=large", json={"rowH": 48, "font": 15, "apply_everywhere": True}).get_json()
    assert p["grid"] == {"large": {"rowH": 48, "font": 15}}
    assert "rowH" not in p["tables"][tid]["large"] and p["tables"][tid]["large"]["hidden"] == ["observation"]
    assert p["tables"][tid]["small"]["rowH"] == 28
    # None removes settings; empty sizes/tables are dropped
    editor.patch(f"/api/me/prefs/tables/{tid}?size=large", json={"hidden": None, "widths": None, "order": None, "showCreated": None, "font": None})
    editor.patch(f"/api/me/prefs/tables/{tid}?size=small", json={"rowH": None, "hidden": None})
    assert tid not in editor.get("/api/me/prefs").get_json()["tables"]


def test_folder_all_items(client):
    rio = _mk(client, "/api/folders", name="Rio")
    sub = _mk(client, "/api/folders", name="Shelf", parent_id=rio["id"])
    other = _mk(client, "/api/folders", name="Other")
    t1 = _mk(client, "/api/tables", name="Box 1", folder_id=rio["id"])
    t2 = _mk(client, "/api/tables", name="Box 2", folder_id=sub["id"])
    t3 = _mk(client, "/api/tables", name="Elsewhere", folder_id=other["id"])
    _mk(client, f"/api/tables/{t2['id']}/columns", label="Expiry", type="date")
    _mk(client, f"/api/tables/{t1['id']}/items", description="Tape", quantity="3 rolls")
    _mk(client, f"/api/tables/{t2['id']}/items", description="Aspirin", custom={"expiry": "2027-01-31"})
    _mk(client, f"/api/tables/{t3['id']}/items", description="Not here")
    gone = _mk(client, f"/api/tables/{t1['id']}/items", description="Deleted")
    client.post("/api/items/bulk", json={"ids": [gone["id"]], "action": "delete"})

    d = client.get(f"/api/folders/{rio['id']}/items").get_json()
    by = {i["description"]: i for i in d["items"]}
    assert set(by) == {"Tape", "Aspirin"}
    assert by["Tape"]["path"] == "Box 1" and by["Tape"]["quantity_display"] == "3 rolls"
    assert by["Aspirin"]["path"] == "Shelf / Box 2" and by["Aspirin"]["fields"] == "Expiry: 2027-01-31"

    client.post("/api/nodes/bulk", json={"folders": [sub["id"]], "action": "archive"})
    d = client.get(f"/api/folders/{rio['id']}/items").get_json()
    assert [i["description"] for i in d["items"]] == ["Tape"] and d["archived_hidden"]
    d = client.get(f"/api/folders/{rio['id']}/items?include_archived=1").get_json()
    assert {i["description"]: i["archived"] for i in d["items"]} == {"Tape": False, "Aspirin": True}
    assert client.get(f"/f/{rio['id']}/all").status_code == 200
