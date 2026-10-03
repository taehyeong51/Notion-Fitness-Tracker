"""Create managed native pages/views and synchronize derived data only.

Nothing in this module updates, archives, or reclassifies an original record.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from .charts import SPECS, payload
from .metrics import SEOUL
from .source import title, value

ROOT_TITLE = "Fitness Tracker · 분석"
ROOT_NOTE = "원본 기록과 기존 리뷰를 보존하는 네이티브 차트·연결 보기입니다."
PAGE_NAMES = ["운동 성과", "훈련 구성", "비교 분석", "관리·집계"]
MARKER = "NFT_MANAGED_PROJECTION_V1"
ROLE_TITLES = {"sessions": "NFT · Chart Sessions", "sets": "NFT · Chart Sets",
               "membership": "NFT · Exercise Session Membership", "progress": "NFT · Progress",
               "weekly": "NFT · Weekly Metrics"}
FIELD_NAMES = {
    "key": "Key", "date": "Date", "week": "Week", "exercise": "Exercise",
    "muscle": "Primary Muscle", "split": "Split", "done": "Done",
    "load": "Load", "reps": "Reps", "set_type": "Set Type",
    "condition": "Condition", "e1rm": "e1RM", "rep_band": "Rep Band",
    "e1rm_display": "e1RM Display", "index_display": "Index Display",
    "comparable": "Comparable", "pullup": "Pullup", "issue": "Issue",
    "top10": "Top 10",
    "metric": "Metric", "index": "Index", "baseline_value": "Baseline Value",
    "baseline_version": "Baseline Version", "observations": "Observations",
    "original_set": "Original Set", "original_session": "Original Session",
    "original_exercise": "Original Exercise", "baseline_set": "Baseline Set",
    "sessions_count": "Session Count", "sets_count": "Set Count", "working_count": "Confirmed Working",
    "warmup_count": "Warmup Count", "unknown_count": "Unclassified Count",
}
FIELD_TYPES = {
    "key": "rich_text", "date": "date", "week": "date", "exercise": "select",
    "muscle": "select", "split": "select", "done": "checkbox", "load": "number",
    "reps": "number", "set_type": "select", "condition": "rich_text", "e1rm": "number",
    "rep_band": "select", "comparable": "checkbox", "pullup": "checkbox",
    "e1rm_display": "number", "index_display": "number",
    "issue": "rich_text", "metric": "select", "index": "number", "baseline_value": "number",
    "top10": "checkbox",
    "baseline_version": "rich_text", "observations": "number", "original_set": "relation",
    "original_session": "relation", "original_exercise": "relation", "baseline_set": "relation",
    "sessions_count": "number", "sets_count": "number", "working_count": "number",
    "warmup_count": "number", "unknown_count": "number",
}
ROLE_FIELDS = {
    "sessions": ["key", "date", "week", "split", "done", "original_session"],
    "sets": ["key", "date", "week", "exercise", "muscle", "split", "done", "load", "reps",
             "set_type", "condition", "e1rm", "e1rm_display", "rep_band", "comparable", "pullup", "top10", "issue",
             "original_set", "original_session", "original_exercise"],
    "membership": ["key", "date", "exercise", "done", "original_session", "original_exercise", "original_set"],
    "progress": ["key", "date", "exercise", "condition", "done", "metric", "index", "index_display",
                 "baseline_value", "baseline_version", "observations", "original_set",
                 "baseline_set", "original_exercise"],
    "weekly": ["key", "date", "week", "done", "sessions_count", "sets_count", "working_count",
               "warmup_count", "unknown_count", "original_session"],
}


def text(content):
    return [{"type": "text", "text": {"content": content}}]


def paragraph(content):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": text(content)}}


def callout(content, color="yellow_background"):
    return {"object": "block", "type": "callout", "callout": {
        "rich_text": text(content), "icon": {"type": "emoji", "emoji": "ℹ️"}, "color": color}}


def save(path, state):
    # Never change permissions on a shared parent such as /tmp or /workspace.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    temporary.chmod(0o600)
    temporary.replace(path)


def load(path):
    return json.loads(path.read_text()) if path.exists() else {}


def schema(role, config):
    props = {"Name": {"title": {}}, "Managed": {"checkbox": {}}}
    relations = {"original_set": "sets", "baseline_set": "sets",
                 "original_session": "sessions", "original_exercise": "exercises"}
    for field in ROLE_FIELDS[role]:
        kind = FIELD_TYPES[field]
        definition = {}
        if kind == "number":
            definition = {"format": "number"}
        if kind == "select":
            definition = {"options": []}
        if kind == "relation":
            definition = {"data_source_id": config["data_sources"][relations[field]],
                          "type": "single_property", "single_property": {}}
        props[FIELD_NAMES[field]] = {kind: definition}
    return props


def properties(row):
    label = " · ".join(str(row[key]) for key in ["date", "exercise", "split"] if row.get(key))
    props = {"Name": {"title": text(label or row["key"])}, "Managed": {"checkbox": True}}
    for field, raw in row.items():
        kind = FIELD_TYPES[field]
        if kind == "rich_text":
            content = str(raw or "")
            if len(content) > 2000:
                raise ValueError("Text exceeds Notion's limit; do not truncate the source")
            encoded = text(content) if content else []
        elif kind == "relation":
            if len(raw) > 100:
                raise ValueError("Over 100 relations; do not truncate")
            encoded = [{"id": identifier} for identifier in raw]
        elif kind == "select":
            if raw and len(raw) > 100:
                raise ValueError("Category exceeds Notion's limit")
            encoded = {"name": raw} if raw else None
        elif kind == "date":
            encoded = {"start": raw} if raw else None
        else:
            encoded = raw
        props[FIELD_NAMES[field]] = {kind: encoded}
    return props


def equal_properties(current, expected):
    for name, encoded in expected.items():
        if name not in current:
            return False
        desired = value({"type": next(iter(encoded)), **encoded})
        existing = value(current[name])
        if isinstance(desired, list):
            if set(desired) != set(existing):
                return False
        elif desired != existing:
            return False
    return True


def contains(actual, expected):
    """The API supplies extra defaults and reference-line IDs on read-back."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and contains(actual[key], item)
                                               for key, item in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            contains(left, right) for left, right in zip(actual, expected))
    return actual == expected


class Deploy:
    def __init__(self, client, config, state_path):
        self.client, self.config = client, config
        self._view_cache = {}
        self.path = Path(state_path)
        self.state = load(self.path)
        if self.state.get("parent") not in {None, config["parent_page_id"]}:
            raise ValueError("State belongs to another parent; do not reuse it")
        self.state["parent"] = config["parent_page_id"]
        for field in ["pages", "databases", "views", "rows", "status_blocks"]:
            self.state.setdefault(field, {})

    def persist(self):
        save(self.path, self.state)

    def create_page(self, name, parent, note):
        matches = [row for row in self.client.search(name)
                   if row["object"] == "page" and title(row) == name
                   and row.get("parent", {}).get("page_id") == parent
                   and not row.get("in_trash")]
        if len(matches) > 1:
            raise ValueError("Duplicate target page titles; inspect before retrying")
        if matches:
            row = matches[0]
            blocks = list(self.client.pages(f"/blocks/{row['id']}/children", method="GET"))
            if not any(block.get("type") == "paragraph"
                       and value({"type": "rich_text", "rich_text": block["paragraph"]["rich_text"]}) == note
                       for block in blocks):
                raise ValueError("An existing page is not managed; preserve it")
            status = [block for block in blocks if block.get("type") == "callout" and value({"type": "rich_text", "rich_text": block['callout']['rich_text']}).startswith(("집계", "원본 조회/집계"))]
            if len(status) != 1:
                raise ValueError("Managed page status block is ambiguous")
            return row, status[0]["id"]
        row = self.client.request("POST", "/pages", {
            "parent": {"type": "page_id", "page_id": parent},
            "properties": {"title": {"title": text(name)}},
            "children": [paragraph(note), callout("집계 미완료 · 검증 전")],
        })
        blocks = list(self.client.pages(f"/blocks/{row['id']}/children", method="GET"))
        status = [block for block in blocks if block.get("type") == "callout"]
        if len(status) != 1:
            raise ValueError("Created page did not return its status block")
        return row, status[0]["id"]

    def ensure_pages(self):
        root = self.state.get("root")
        if not root:
            row, status = self.create_page(ROOT_TITLE, self.config["parent_page_id"], ROOT_NOTE)
            root = self.state["root"] = row["id"]
            self.state["root_url"] = row["url"]
            self.state["status_blocks"][ROOT_TITLE] = status
            self.persist()
        else:
            page = self.client.request("GET", "/pages/" + root)
            if page.get("in_trash") or page["parent"].get("page_id") != self.config["parent_page_id"]:
                raise ValueError("Managed root was moved or trashed")
        notes = {
            "운동 성과": "같은 조건의 중량·반복·추정 근력. 비교 조건이 미확인인 관측은 제외합니다.",
            "훈련 구성": "세션 횟수와 기록 세트는 서로 다른 단위입니다. 미분류는 작업 세트로 추정하지 않습니다.",
            "비교 분석": "확정한 기준 수행을 100으로 비교합니다. 풀업 반복 지수는 중량 종목 지수와 합산하지 않습니다.",
            "관리·집계": "이 DB들은 원본의 파생 집계입니다. 원본 기록과 기존 리뷰는 변경하지 않습니다.",
        }
        for name in PAGE_NAMES:
            if name not in self.state["pages"]:
                row, status = self.create_page(name, root, notes[name])
                self.state["pages"][name] = row["id"]
                self.state["status_blocks"][name] = status
                self.persist()
            else:
                row = self.client.request("GET", "/pages/" + self.state["pages"][name])
                if row.get("in_trash") or row["parent"].get("page_id") != root:
                    raise ValueError("Managed child page was moved or trashed")

    def navigation(self):
        links = []
        for name in ["운동 성과", "훈련 구성", "비교 분석"]:
            if links:
                links.extend(text("  ·  "))
            links.append({"type": "text", "text": {"content": name, "link": {"url": "https://www.notion.so/" + self.state["pages"][name].replace("-", "")}}})
        for page, label in [(self.state["root"], "분석 바로가기  ·  "), (self.config["parent_page_id"], "운동 분석  ·  ")]:
            key = "nav_" + page
            body = {"callout": {"rich_text": text(label) + links, "icon": {"type": "emoji", "emoji": "📊"}, "color": "blue_background"}}
            if self.state.get(key):
                self.client.request("PATCH", "/blocks/" + self.state[key], body)
                continue
            # Recover a previously successful append before retrying a create.
            existing = list(self.client.pages(f"/blocks/{page}/children", method="GET"))
            matches = [b for b in existing if b["type"] == "callout" and value({"type": "rich_text", "rich_text": b["callout"]["rich_text"]}).startswith(label)]
            if len(matches) > 1:
                raise ValueError("Duplicate managed navigation")
            if matches:
                identifier = matches[0]["id"]
            else:
                response = self.client.request("PATCH", f"/blocks/{page}/children", {"position": {"type": "start"}, "children": [{"object": "block", "type": "callout", **body}]})
                identifier = response["results"][0]["id"]
            self.state[key] = identifier
            self.persist()
        home = list(self.client.pages(f"/blocks/{self.config['parent_page_id']}/children", method="GET"))
        performance = [b for b in home if b['type'] == 'paragraph' and value({'type':'rich_text','rich_text':b['paragraph']['rich_text']}).startswith('운동 성과 —')]
        if len(performance) > 1:
            raise ValueError('Existing performance navigation is ambiguous')
        if performance:
            block = performance[0]
            label = value({'type':'rich_text','rich_text':block['paragraph']['rich_text']})
            desired = [{'type':'text','text':{'content':label,'link':{'url':'https://www.notion.so/'+self.state['pages']['운동 성과'].replace('-','')}}}]
            if not contains(block['paragraph']['rich_text'], desired):
                self.state.setdefault('previous_performance_navigation', block)
                self.persist()
                self.client.request('PATCH','/blocks/'+block['id'],{'paragraph':{'rich_text':desired}})

    def status(self, message, success=False):
        color = "green_background" if success else "yellow_background"
        for identifier in self.state["status_blocks"].values():
            self.client.request("PATCH", "/blocks/" + identifier, {"callout": {
                "rich_text": text(message), "color": color}})

    def comparison_note(self, projection):
        candidates = set(self.config.get('strength_exercise_ids', []) + self.config.get('pullup_exercise_ids', []))
        core = [row for row in projection['sets'] if row['original_exercise'] and row['original_exercise'][0] in candidates]
        confirmed = [row for row in core if row['comparable']]
        exercises = {row['original_exercise'][0] for row in confirmed}
        message = (f"측정조건 확인 종목 {len(exercises)}/{len(candidates)} · 핵심 종목 세트 {len(confirmed)}/{len(core)}"
                   " · 발전 지수는 확인된 동일 조건과 유효 기준이 있는 수행만 표시합니다."
                   " 조건 미확인 추세는 운동 성과에서 관측치로 확인할 수 있습니다.")
        body = callout(message, 'yellow_background')
        identifier = self.state.get('comparison_note')
        if identifier:
            self.client.request('PATCH','/blocks/'+identifier,{'callout':body['callout']})
        else:
            parent = self.state['pages']['비교 분석']
            existing = list(self.client.pages(f'/blocks/{parent}/children',method='GET'))
            matches = [b for b in existing if b['type'] == 'callout' and value({'type':'rich_text','rich_text':b['callout']['rich_text']}).startswith('측정조건 확인 종목 ')]
            if len(matches) > 1:
                raise ValueError('Duplicate comparison note')
            if matches:
                identifier = matches[0]['id']
            else:
                response = self.client.request('PATCH',f'/blocks/{parent}/children',{'position':{'type':'start'},'children':[body]})
                identifier = response['results'][0]['id']
            self.state['comparison_note'] = identifier
            self.persist()

    def ensure_databases(self, roles=None):
        result = {}
        parent = self.state["pages"]["관리·집계"]
        for role, name in ROLE_TITLES.items():
            if roles is not None and role not in roles:
                continue
            record = self.state["databases"].get(role)
            if not record:
                children = list(self.client.pages(f"/blocks/{parent}/children", method="GET"))
                matches = [block for block in children if block.get("type") == "child_database"
                           and block["child_database"]["title"] == name]
                if len(matches) > 1:
                    raise ValueError("Duplicate managed databases")
                if matches:
                    db = self.client.request("GET", "/databases/" + matches[0]["id"])
                    description = value({"type": "rich_text", "rich_text": db.get("description", [])})
                    if description != MARKER:
                        raise ValueError("Matching database is not managed; preserve it")
                else:
                    db = self.client.request("POST", "/databases", {
                        "parent": {"type": "page_id", "page_id": parent},
                        "title": text(name), "description": text(MARKER), "is_inline": True,
                        "initial_data_source": {"properties": schema(role, self.config)},
                    })
                if len(db["data_sources"]) != 1:
                    raise ValueError("Managed database must have exactly one data source")
                record = {"database_id": db["id"], "data_source_id": db["data_sources"][0]["id"]}
                self.state["databases"][role] = record
                self.persist()
            db = self.client.request("GET", "/databases/" + record["database_id"])
            description = value({"type": "rich_text", "rich_text": db.get("description", [])})
            if db.get("in_trash") or db["parent"].get("page_id") != parent or description != MARKER:
                raise ValueError("Managed database ownership/parent changed")
            if record["data_source_id"] not in {item["id"] for item in db["data_sources"]}:
                raise ValueError("Stored data source is not owned by the managed database")
            if record["data_source_id"] in self.config["data_sources"].values():
                raise ValueError("Original data sources cannot be used as projection targets")
            source = self.client.request("GET", "/data_sources/" + record["data_source_id"])
            if source["parent"].get("database_id") != db["id"]:
                raise ValueError("Projection source parent changed")
            expected = schema(role, self.config)
            for prop, definition in expected.items():
                current = source["properties"].get(prop, {})
                kind = next(iter(definition))
                if current.get("type") != kind:
                    raise ValueError("Managed projection schema changed")
                if kind == "relation" and current["relation"].get("data_source_id") != definition[kind]["data_source_id"]:
                    raise ValueError("Managed relation target changed")
            result[role] = {name: {**prop, "id": unquote(prop["id"])} for name, prop in source["properties"].items()}
        return result

    def sync_rows(self, role, rows):
        source = self.state["databases"][role]["data_source_id"]
        remote = self.client.data(source)
        by_key = {}
        for row in remote:
            if value(row["properties"].get("Managed", {"type": "checkbox", "checkbox": False})) is not True:
                continue
            key = value(row["properties"]["Key"])
            if not key:
                continue  # Preserve manually added, unowned rows.
            if key in by_key:
                raise ValueError("Duplicate projection key; stop before modifying rows")
            by_key[key] = row
        keys = [row["key"] for row in rows]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate desired projection keys")
        remembered = self.state["rows"].setdefault(role, {})
        changes = {"created": 0, "updated": 0, "archived": 0}
        for row in rows:
            key = row["key"]
            expected = properties(row)
            found = by_key.get(key)
            if not found and key in remembered:
                found = self.client.request("GET", "/pages/" + remembered[key])
                if found["parent"].get("data_source_id") != source:
                    raise ValueError("Remembered row no longer belongs to the managed source")
                if value(found["properties"]["Key"]) != key:
                    raise ValueError("Remembered row key changed")
                if value(found["properties"].get("Managed", {"type": "checkbox", "checkbox": False})) is not True:
                    raise ValueError("Remembered row is no longer managed")
            if found:
                if found.get("in_trash") or not equal_properties(found["properties"], expected):
                    self.client.request("PATCH", "/pages/" + found["id"],
                                        {"properties": expected, "in_trash": False})
                    changes["updated"] += 1
            else:
                found = self.client.request("POST", "/pages", {
                    "parent": {"type": "data_source_id", "data_source_id": source},
                    "properties": expected})
                changes["created"] += 1
            remembered[key] = found["id"]
            self.persist()
        for key, row in by_key.items():
            if key not in keys:
                self.client.request("PATCH", "/pages/" + row["id"], {"in_trash": True})
                remembered[key] = row["id"]
                self.persist()
                changes["archived"] += 1
        verified = self.client.data(source)
        owned = [row for row in verified if value(row["properties"].get("Managed", {"type": "checkbox", "checkbox": False})) is True
                 and value(row["properties"]["Key"])]
        verified_by_key = {value(row["properties"]["Key"]): row for row in owned}
        if len(verified_by_key) != len(owned):
            raise ValueError("Duplicate remote projection keys after sync")
        if set(verified_by_key) != set(keys):
            raise ValueError("Projection key set did not reconcile")
        for row in rows:
            if not equal_properties(verified_by_key[row["key"]]["properties"], properties(row)):
                raise ValueError("Projection property verification failed")
        return changes

    def ensure_view(self, identity, page_name, role, body, source_override=None, container_identity=None):
        source = source_override or self.state["databases"][role]["data_source_id"]
        identifier = self.state["views"].get(identity)
        if not identifier:
            parent = self.state["root"] if page_name == ROOT_TITLE else self.state["pages"][page_name]
            if source not in self._view_cache:
                refs = self.client.pages("/views", query={"data_source_id": source}, method="GET")
                self._view_cache[source] = [self.client.request("GET", "/views/" + ref["id"]) for ref in refs]
            matches = []
            for view in self._view_cache[source]:
                if view["name"] != body["name"] or view.get("data_source_id") != source:
                    continue
                container = self.client.request("GET", "/databases/" + view["parent"]["database_id"])
                if container["parent"].get("page_id") == parent:
                    matches.append(view)
            if len(matches) > 1:
                raise ValueError("Duplicate view names; inspect before retrying")
            if matches:
                identifier = matches[0]["id"]
            else:
                placement = {"create_database": {"parent": {"type": "page_id", "page_id": parent}}}
                if container_identity:
                    anchor = self.client.request("GET", "/views/" + self.state["views"][container_identity])
                    if anchor["data_source_id"] != source:
                        raise ValueError("Tabs must reference the same original data source")
                    placement = {"database_id": anchor["parent"]["database_id"]}
                created = self.client.request("POST", "/views", {
                    "data_source_id": source,
                    **placement,
                    **body,
                })
                identifier = created["id"]
                self._view_cache[source].append(created)
            self.state["views"][identity] = identifier
            self.persist()
        view = self.client.request("GET", "/views/" + identifier)
        if view.get("data_source_id") != source:
            raise ValueError("Managed view source changed")
        db = self.client.request("GET", "/databases/" + view["parent"]["database_id"])
        target = self.state["root"] if page_name == ROOT_TITLE else self.state["pages"][page_name]
        if db["parent"].get("page_id") != target:
            raise ValueError("Managed view moved to another page")
        if not all(contains(view.get(field), body[field]) for field in ["name", "configuration", "filter", "sorts"] if field in body):
            self.client.request("PATCH", "/views/" + identifier, body)
        actual = self.client.request("GET", "/views/" + identifier)
        if actual["type"] != body["type"]:
            raise ValueError("View type did not apply")
        for field in ["name", "configuration", "filter", "sorts"]:
            if field in body and not contains(actual.get(field), body[field]):
                raise ValueError(f"View configuration did not apply: {field}")
        return identifier

    def views(self, schemas, native_bindings=None):
        from .native import adapt_filters
        for spec in SPECS:
            ident, page_name, role = spec[:3]
            direct = native_bindings
            bound = native_bindings[role] if direct else None
            chart_schema = bound["schema"] if direct else schemas[role]
            body = payload(spec, chart_schema, self.config, native=bool(direct))
            adapt_filters(body, chart_schema)
            self.ensure_view(ident, page_name, role, body, bound["source_id"] if direct else None)
        if native_bindings:
            from copy import deepcopy
            main = next(spec for spec in SPECS if spec[0] == "C01")
            for index, exercise_id in enumerate(self.config.get("strength_exercise_ids", [])):
                if exercise_id == self.config["strength_scope"]["exercise_id"]:
                    continue
                scoped = deepcopy(self.config)
                scoped["strength_scope"]["exercise_id"] = exercise_id
                label = self.config.get("exercise_labels", {}).get(exercise_id, exercise_id)
                spec = (f"C01_{index}", main[1], main[2], label + " · e1RM", *main[4:])
                body = adapt_filters(payload(spec, native_bindings["sets"]["schema"], scoped, native=True), native_bindings["sets"]["schema"])
                self.ensure_view(spec[0], main[1], "sets", body, native_bindings["sets"]["source_id"], "C01")
            scoped = deepcopy(self.config)
            scoped["pullup_scope"]["condition"] = "패러럴그립 · 부하모드 미확인"
            main = next(spec for spec in SPECS if spec[0] == "C08")
            spec = ("C08_Parallel", *main[1:3], "풀업 · 패러럴그립", *main[4:])
            body = adapt_filters(payload(spec, native_bindings["sets"]["schema"], scoped, native=True), native_bindings["sets"]["schema"])
            self.ensure_view("C08_Parallel", main[1], "sets", body, native_bindings["sets"]["source_id"], "C08")
            for identity, role, label in [("H01", "sessions", "최근 12주 · 완료 세션"), ("H02", "sets", "최근 12주 · 기록 세트")]:
                bound = native_bindings[role]
                number = {"name": "NFT " + identity + " · " + label, "type": "chart",
                          "filter": {"and": [{"property": "Done", "checkbox": {"equals": True}}, {"property": "Recent 12 Weeks", "checkbox": {"equals": True}}]},
                          "configuration": {"type": "chart", "chart_type": "number", "value": {"aggregator": "count"}, "color_theme": "teal", "height": "small", "caption": "원본 직접 갱신 · 세션과 세트는 다른 단위"}}
                adapt_filters(number, bound["schema"])
                self.ensure_view(identity, ROOT_TITLE, role, number, bound["source_id"])
            for identity in ["C04A", "C04B"]:
                main = next(spec for spec in SPECS if spec[0] == identity)
                spec = (identity + "_Home", ROOT_TITLE, *main[2:])
                bound = native_bindings[spec[2]]
                body = adapt_filters(payload(spec, bound["schema"], self.config, native=True), bound["schema"])
                self.ensure_view(spec[0], ROOT_TITLE, spec[2], body, bound["source_id"])
        tables = [
            ("T01", "운동 성과", "sets", "최근 수행과 근거", ["Date", "Exercise", "Load", "Reps", "e1RM Display", "Condition", "Original Set"]),
            ("T02", "훈련 구성", "weekly", "주간 훈련 요약 · 전체 기간", ["Week", "Session Count", "Set Count", "Confirmed Working", "Warmup Count", "Unclassified Count", "Original Session"]),
            ("T03", "훈련 구성", "sets", "입력 점검", ["Date", "Exercise", "Set Type", "Issue", "Original Set"]),
            ("T04", "비교 분석", "progress", "비교 기준과 근거", ["Date", "Exercise", "Index Display", "Metric", "Baseline Value", "Baseline Version", "Baseline Set", "Original Set"]),
            ("T05", "훈련 구성", "membership", "빈도 근거 · 세션별 세트", ["Date", "Exercise", "Original Session", "Original Set"]),
        ]
        if native_bindings:
            tables.append(("T06", "비교 분석", "progress", "비교 준비 · 조건과 기준 입력", ["Date", "Exercise", "NFT Measurement Condition", "NFT Condition Confirmed", "NFT Baseline", "NFT Baseline Value", "NFT Baseline Version", "NFT Comparable", "NFT Issue"]))
        for ident, page_name, role, label, names in tables:
            direct = native_bindings and role != "weekly"
            bound = native_bindings[role] if direct else None
            schema_props = bound["all_properties"] if direct else schemas[role]
            if direct:
                mapping = self.config["native_properties"][role]
                names = [mapping.get(name, name) for name in names]
                names += ["Set", "Session", "Exercise", "Set Notes"]
                date_id = bound["schema"]["Date"]["id"]
            else:
                date_id = schema_props["Date"]["id"]
            table = {
                "name": "NFT " + ident + " · " + label, "type": "table",
                "sorts": [{"property": date_id, "direction": "descending"}],
                "configuration": {"type": "table", "wrap_cells": True,
                    "properties": [{"property_id": prop["id"], "visible": name in names,
                                    "width": 150} for name, prop in schema_props.items()]},
            }
            if ident == "T03":
                table["filter"] = {"or": [
                    {"property": "Issue", "rich_text": {"is_not_empty": True}},
                    {"property": "Set Type", "select": {"equals": "미분류"}},
                ]}
                adapt_filters(table, bound["schema"] if direct else schema_props)
            if ident == "T04" and direct:
                table["filter"] = {"property": "Metric", "select": {"is_not_empty": True}}
                adapt_filters(table, bound["schema"])
            if ident == "T01" and direct:
                table["filter"] = {"and": [{"property": "Done", "checkbox": {"equals": True}}, {"property": "Recent 12 Weeks", "checkbox": {"equals": True}}]}
                adapt_filters(table, bound["schema"])
            if ident == "T06" and direct:
                table["filter"] = {"or": [{"property": schema_props["Exercise"]["id"], "relation": {"contains": identifier}} for identifier in self.config.get("strength_exercise_ids", []) + self.config.get("pullup_exercise_ids", [])]}
            self.ensure_view(ident, page_name, role, table, bound["source_id"] if direct else None)

    def apply(self, projection):
        from .native import bind
        mode = self.config.get("chart_source_mode", "native")
        if mode not in {"native", "projection"}:
            raise ValueError("Select native mode or explicitly opt into projection snapshots")
        native_bindings = bind(self.client, self.config) if mode == "native" else None
        self.ensure_pages()
        self.navigation()
        self.comparison_note(projection)
        previous = self.state.get("last_success", "없음")
        self.status("집계 실행 중 · 마지막 성공: " + previous)
        try:
            roles = {"weekly"} if native_bindings else set(ROLE_TITLES)
            schemas = self.ensure_databases(roles)
            changes = {role: self.sync_rows(role, projection[role]) for role in roles}
            self.views(schemas, native_bindings)
            completed = datetime.now(SEOUL).isoformat(timespec="seconds")
            diag = projection["diagnostics"]
            message = (f"집계 성공: {completed} · 원본 {diag['source_sessions']}세션 / {diag['source_sets']}세트"
                       f" · 조건 확인 {diag['comparable_sets']}세트 · 기준은 비교 보기에서 확인"
                       " · 차트는 원본 직접 갱신 · 상위 10 선정/주간 표는 이 시각의 집계")
            self.status(message, success=True)
            self.state["last_success"] = completed
            self.persist()
            return {"changes": changes, "last_success": completed,
                    "root_url": self.state["root_url"], "native_views": len(self.state["views"])}
        except Exception:
            try:
                self.status("집계 실패/미완료 · 마지막 성공: " + previous)
            except Exception:
                pass  # Preserve the original error; a status update must not mask it.
            raise
