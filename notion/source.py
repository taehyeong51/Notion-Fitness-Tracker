"""Explicitly mapped Notion properties; refuse silent field-name inference."""
from __future__ import annotations

import uuid


def value(prop):
    kind = prop.get("type")
    data = prop.get(kind)
    if kind in {"title", "rich_text"}:
        return "".join(part.get("plain_text", part.get("text", {}).get("content", "")) for part in data)
    if kind in {"select", "status"}:
        return data["name"] if data else None
    if kind == "date":
        if data and data.get("end"):
            raise ValueError("Session date ranges must be resolved explicitly")
        return data["start"] if data else None
    if kind == "relation":
        if prop.get("has_more"):
            raise ValueError("Relation is not fully paginated")
        return [part["id"] for part in data]
    if kind in {"number", "checkbox", "url", "string", "boolean"}:
        return data
    if kind == "formula":
        return value(data)
    raise ValueError(f"Unsupported source property type {kind}; map a single value")


def title(record):
    if record["object"] == "data_source":
        return "".join(x.get("plain_text", x.get("text", {}).get("content", "")) for x in record.get("title", []))
    for prop in record.get("properties", {}).values():
        if prop.get("type") == "title":
            return value(prop)
    return ""


def check_config(client, config):
    for identifier in [config["parent_page_id"], *config["data_sources"].values()]:
        if not identifier:
            raise ValueError("Populate real IDs from inspect; MD URLs are not verified API IDs")
        uuid.UUID(identifier)
    parent = client.request("GET", "/pages/" + config["parent_page_id"])
    if parent.get("in_trash"):
        raise ValueError("Target parent is in trash")
    expected = {
        "sessions": {"date": {"date", "formula"}, "split": {"select", "status", "rich_text", "formula"}, "done": {"checkbox", "status", "select"}},
        "sets": {"session": {"relation"}, "exercise": {"relation"}, "load": {"number", "formula"}, "reps": {"number", "formula"}, "set_type": {"select", "status", "rich_text", "formula"}, "done": {"checkbox", "status", "select"}},
        "exercises": {"label": {"title", "rich_text", "formula"}, "muscle": {"select", "rich_text", "formula"}},
    }
    schemas = {}
    for role, required in expected.items():
        source = client.request("GET", "/data_sources/" + config["data_sources"][role])
        if source.get("in_trash"):
            raise ValueError("Source is in trash")
        props = source["properties"]
        for field, types in required.items():
            name = config["properties"][role].get(field)
            if not name or name not in props or props[name]["type"] not in types:
                raise ValueError(f"Map and verify {role}.{field} against the live schema")
        if role in {"sessions", "sets"}:
            done_property = props[config["properties"][role]["done"]]
            if done_property["type"] in {"select", "status"}:
                allowed = {item["name"] for item in done_property[done_property["type"]]["options"]}
                configured = config["completed_values"][role]
                if not set(configured).intersection(allowed):
                    raise ValueError("Completed status values do not match the live source")
        if role == "sets":
            condition = config["properties"][role].get("historical_condition")
            if condition and (condition not in props or props[condition]["type"] not in {"rich_text", "select", "formula"}):
                raise ValueError("Historical condition must be a single source value")
            if condition and config.get("historical_condition_verified") is not True:
                raise ValueError("Verify historical condition semantics before comparing performances")
            confirmed = config["properties"][role].get("condition_confirmed")
            if confirmed and (confirmed not in props or props[confirmed]["type"] != "checkbox"):
                raise ValueError("Condition confirmation must be an inspected checkbox")
            for field, target in [("session", "sessions"), ("exercise", "exercises")]:
                relation = props[config["properties"][role][field]]["relation"]
                linked = relation.get("data_source_id")
                if not linked or uuid.UUID(linked) != uuid.UUID(config["data_sources"][target]):
                    raise ValueError(f"The {field} relation does not point at the configured source")
        schemas[role] = source
    return schemas


def normalize(client, config):
    records = {role: client.data(identifier) for role, identifier in config["data_sources"].items()}
    def get(role, row, field):
        name = config["properties"][role].get(field)
        return value(row["properties"][name]) if name else None
    def completed(role, row):
        raw = get(role, row, "done")
        if isinstance(raw, bool):
            return raw
        return raw in config["completed_values"][role]
    snapshot = {
        "sessions": [{"id": row["id"], "date": get("sessions", row, "date"),
                      "split": get("sessions", row, "split"), "done": completed("sessions", row)}
                     for row in records["sessions"]],
        "exercises": [{"id": row["id"], "label": get("exercises", row, "label"),
                       "muscle": get("exercises", row, "muscle")}
                      for row in records["exercises"]],
        "sets": [{"id": row["id"], "session_ids": get("sets", row, "session"),
                  "exercise_ids": get("sets", row, "exercise"),
                  "load": get("sets", row, "load"), "reps": get("sets", row, "reps"),
                  "set_type": config["set_type_values"].get(get("sets", row, "set_type")),
                  "done": completed("sets", row),
                  "condition": get("sets", row, "historical_condition")
                      if not config["properties"]["sets"].get("condition_confirmed") or get("sets", row, "condition_confirmed") is True else None,
                  "pullup": len(get("sets", row, "exercise")) == 1
                      and get("sets", row, "exercise")[0] in config["pullup_exercise_ids"]}
                 for row in records["sets"]],
    }
    # Never archive derived rows after an incomplete or changing source scan.
    for role, first in records.items():
        second = list(client.pages(f"/data_sources/{config['data_sources'][role]}/query"))
        fingerprint = lambda rows: {(row["id"], row["last_edited_time"]) for row in rows}
        if fingerprint(first) != fingerprint(second):
            raise ValueError("Source changed during the scan; rerun before modifying projections")
    return snapshot
