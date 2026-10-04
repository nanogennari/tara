from flask import jsonify, request

from ..search.query import Scope, search
from . import bp, int_list


@bp.get("/search")
def global_search():
    a = request.args
    scope = Scope(
        folders=set(int_list(a.get("folders"))),
        exclude_folders=set(int_list(a.get("exclude_folders"))),
        tables=set(int_list(a.get("tables"))),
        include_archived=a.get("include_archived") in ("1", "true", "yes"),
    )
    limit = max(1, min(200, int(a.get("limit", 30) or 30)))
    return jsonify(search(a.get("q", ""), scope, limit))
