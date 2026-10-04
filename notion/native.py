"""Bind verified original columns; never default to a stale mirror for charts."""
from urllib.parse import unquote
SOURCE_ROLES = {"sessions": "sessions", "sets": "sets", "membership": "sets", "progress": "sets"}
FIELDS = {
    "sessions": {"Date", "Week", "Split", "Done", "Recent 12 Weeks"},
    "sets": {"Date", "Week", "Exercise", "Primary Muscle", "Split", "Done", "Load", "Reps",
             "Set Type", "Condition", "e1RM", "e1RM Display", "Rep Band", "Comparable", "Pullup",
             "Issue", "Original Exercise", "Original Session", "Recent 12 Weeks"},
    "membership": {"Date", "Exercise", "Done", "Session Key", "Valid Exercise", "Original Session", "Recent 12 Weeks"},
    "progress": {"Date", "Exercise", "Done", "Metric", "Index Display", "Baseline Value",
                 "Baseline Version", "Baseline Set", "Recent 12 Weeks"},
}
RESULT_TYPES = {"string": "text", "boolean": "checkbox", "number": "number", "date": "date"}


def bind(client, config):
    cache, result = {}, {}
    for role, source_role in SOURCE_ROLES.items():
        source_id = config["data_sources"][source_role]
        if source_id not in cache:
            definition = client.request("GET", "/data_sources/" + source_id)
            # Result types are validated from actual rows, not guessed from expression text.
            sample = client.request("POST", f"/data_sources/{source_id}/query", {"page_size": 1})
            types = {}
            for row in sample["results"]:
                for name, prop in row["properties"].items():
                    if prop.get("type") == "formula":
                        types[name] = prop["formula"]["type"]
            props = {name: {**prop, "id": unquote(prop["id"])} for name, prop in definition["properties"].items()}
            cache[source_id] = props, types
        schema, types = cache[source_id]
        mapping = config.get("native_properties", {}).get(role, {})
        missing = FIELDS[role] - mapping.keys()
        if missing:
            raise ValueError(f"Verify live native columns for {role}: {', '.join(sorted(missing))}")
        canonical = {}
        for alias, name in mapping.items():
            if name not in schema:
                raise ValueError(f"Native column {role}.{alias} does not exist")
            prop = dict(schema[name])
            if prop["type"] == "formula":
                if name not in types or types[name] not in RESULT_TYPES:
                    raise ValueError("Verify a scalar formula result from a real source row")
                prop["result_type"] = RESULT_TYPES[types[name]]
            canonical[alias] = prop
        for name, prop in schema.items():
            if prop["type"] == "title":
                canonical["Name"] = prop
        for alias in ["Done", "Recent 12 Weeks"] + (["Comparable", "Pullup"] if role == "sets" else ["Valid Exercise"] if role == "membership" else []):
            prop = canonical[alias]
            if prop.get("result_type", prop["type"]) != "checkbox":
                raise ValueError(f"Native {alias} must be a verified boolean, not a manual status")
        if canonical["Recent 12 Weeks"]["type"] != "formula":
            raise ValueError("A rolling native period needs a live formula, not a frozen checkbox")
        for alias in ["Date"] + (["Week"] if role in {"sessions", "sets"} else []):
            prop = canonical[alias]
            if prop.get("result_type", prop["type"]) != "date":
                raise ValueError("Native chart dates must be scalar dates")
        for alias in ["e1RM", "e1RM Display", "Load", "Reps"] if role == "sets" else ["Index Display", "Baseline Value"] if role == "progress" else []:
            prop = canonical[alias]
            if prop.get("result_type", prop["type"]) != "number":
                raise ValueError("Native chart values must be numeric, not formatted strings")
        if role == "progress" and canonical["Index Display"]["type"] != "formula":
            raise ValueError("Progress must recalculate natively when original records change")
        result[role] = {"source_id": source_id, "schema": canonical, "all_properties": schema}
    return result


def adapt_filters(body, schema):
    def adapt(node):
        if "and" in node or "or" in node:
            for condition in node.get("and", node.get("or")):
                adapt(condition)
            return
        name = node["property"]
        prop = schema[name]
        node["property"] = prop["id"]
        if prop["type"] == "formula":
            kind = next(key for key in node if key != "property")
            value = node.pop(kind)
            kind = {"rich_text": "string", "select": "string", "checkbox": "checkbox"}.get(kind, kind)
            node["formula"] = {kind: value}
        elif prop["type"] == "rich_text" and "select" in node:
            node["rich_text"] = node.pop("select")
    if "filter" in body:
        adapt(body["filter"])
    return body
