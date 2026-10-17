#!/usr/bin/env python3
"""The source contract: its JSON Schemas, the entity registry, and a validator.

The schemas in contract/ are the contract. A source written in any language can
check itself against them with any JSON Schema library; this module is how the
hub does it without one. Python's standard library has no JSON Schema
validator and the hub installs nothing, so this implements only the keywords
contract/ uses (KEYWORDS). A schema that uses any other keyword is refused when
it is loaded instead of being silently not enforced -- an unenforced rule is
worse than no rule, because sources would be told they comply.

docs/contract.md explains the contract in prose.
"""
from __future__ import annotations

import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_DIR = os.path.join(ROOT, "contract")

CONTRACT_VERSION = 3           # the version of contract/ this hub implements
SUPPORTED = (1, 2, 3)          # versions it reads; each one's shapes are a subset of the next's
# Fields a version added, so a source that prints one says it needs that
# version: an older hub would refuse the output as an unknown field.
ADDED_IN = {("stat", "resets_at"): 2}
WIDGET_ADDED_IN = {"ranges": 3}    # manifest widget fields, the same way
DETECT_TIMEOUT = 5             # seconds; detect runs on every hub start
DEFAULT_TIMEOUT = 10           # seconds, for fetch and show; see manifest timeout_seconds
CALL_TIMEOUT = 600             # seconds, for call: a language server's first run can take minutes
MAX_CALL_ARGS = 64 * 1024      # bytes of JSON arguments the hub passes to one call
MAX_OUTPUT = 5 * 1024 * 1024   # bytes of stdout the hub will read from one command

SCHEMAS = ("widgets.schema.json", "manifest.schema.json", "detect.schema.json",
           "fetch.schema.json", "show.schema.json")

KEYWORDS = frozenset({
    "$schema", "$id", "$ref", "$defs", "title", "description", "default",
    "type", "const", "enum", "required", "properties", "additionalProperties",
    "items", "minItems", "maxItems", "uniqueItems", "minLength", "pattern",
    "minimum", "maximum", "allOf", "anyOf", "if", "then", "else",
})
_SUB_MAPS = ("properties", "$defs")                                 # name -> schema
_SUB_ONE = ("items", "additionalProperties", "if", "then", "else")  # schema
_SUB_LIST = ("allOf", "anyOf")                                      # [schema]

_TYPES = {
    "null": lambda v: v is None,
    "boolean": lambda v: isinstance(v, bool),
    # bool is a subclass of int in Python and is not a number in JSON.
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "string": lambda v: isinstance(v, str),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}

_cache: dict = {}


class ContractError(Exception):
    """contract/ itself is wrong: a $ref that does not resolve, or a keyword
    this validator does not enforce."""


def load(name: str) -> dict:
    """A schema from contract/, checked for keywords and $refs on first load."""
    if name not in _cache:
        try:
            with open(os.path.join(CONTRACT_DIR, name), encoding="utf-8") as f:
                _cache[name] = json.load(f)
        except (OSError, ValueError) as e:
            raise ContractError(f"{name}: {e}") from None
        audit(_cache[name], name)
    return _cache[name]


def entities() -> dict:
    """contract/entities.json: each entity type and the rule for its IDs."""
    if "entities.json" not in _cache:
        with open(os.path.join(CONTRACT_DIR, "entities.json"), encoding="utf-8") as f:
            _cache["entities.json"] = json.load(f)
    return _cache["entities.json"]


def audit(schema, name: str, where: str = "#") -> None:
    """Raise ContractError for a keyword outside KEYWORDS or a $ref that does
    not resolve, anywhere in schema."""
    if not isinstance(schema, dict):
        raise ContractError(f"{name}{where}: a schema must be an object")
    for key, value in schema.items():
        if key not in KEYWORDS:
            raise ContractError(f"{name}{where}: '{key}' is not a keyword this validator enforces")
        if key in _SUB_MAPS:
            for sub, s in value.items():
                audit(s, name, f"{where}/{key}/{sub}")
        elif key in _SUB_ONE and isinstance(value, dict):
            audit(value, name, f"{where}/{key}")
        elif key in _SUB_LIST:
            for i, s in enumerate(value):
                audit(s, name, f"{where}/{key}/{i}")
        elif key == "$ref":
            _resolve(value, name)


def validate(instance, schema_name: str) -> list:
    """Every way instance breaks the named schema, as 'path: problem' strings.
    An empty list means it complies."""
    errors: list = []
    _check(instance, load(schema_name), schema_name, "$", errors)
    return errors


def data_problems(kind: str, data, path: str) -> tuple:
    """What the schemas cannot say, for data that already meets them: table
    rows keyed by real columns, and refs whose entity type is known and whose
    ID fits its rule. Returns (errors, warnings)."""
    errors, warnings = [], []
    refs = []
    if kind == "table":
        cols = [c["id"] for c in data["columns"]]
        for dup in sorted({c for c in cols if cols.count(c) > 1}):
            errors.append(f"{path}.columns: id '{dup}' is used more than once")
        for i, row in enumerate(data["rows"]):
            for key, cell in row.items():
                if key not in cols:
                    errors.append(f"{path}.rows[{i}]: '{key}' is not one of the columns")
                if isinstance(cell, dict):
                    refs.append((f"{path}.rows[{i}].{key}", cell))
    elif kind in ("list", "search"):
        field = "items" if kind == "list" else "results"
        for i, item in enumerate(data[field]):
            if "ref" in item:
                refs.append((f"{path}.{field}[{i}].ref", item["ref"]))

    types = entities()["types"]
    for where, ref in refs:
        t = types.get(ref["ref"])
        if t is None:
            warnings.append(f"{where}: entity type '{ref['ref']}' is not in contract/entities.json, "
                            f"so the hub shows it as plain text")
        elif not re.fullmatch(t["id_pattern"], ref["id"]):
            errors.append(f"{where}: {ref['id']!r} is not a valid {ref['ref']} ID")
    return errors, warnings


def version_problems(kind: str, data, path: str, version: int) -> list:
    """A field printed by a source that declares an older contract than the
    one that added it."""
    return [f"{path}.{field}: arrived in contract {v}, and this source declares contract {version}; "
            f"set \"contract\": {v} in source.json"
            for (k, field), v in sorted(ADDED_IN.items())
            if k == kind and v > version and isinstance(data, dict) and field in data]


def fetch_problems(value, kind: str, version: int = CONTRACT_VERSION) -> tuple:
    """Everything wrong with one `run fetch` output for a widget of this kind,
    from a source declaring contract `version`. Returns (errors, warnings)."""
    if isinstance(value, dict) and value.get("kind", kind) != kind:
        # Reported alone: the data was checked against the printed kind, so
        # its errors would describe the wrong shape.
        return [f"$.kind: the manifest says '{kind}', fetch printed {value['kind']!r}"], []
    errors = validate(value, "fetch.schema.json")
    if errors:
        return errors, []
    errors = version_problems(value["kind"], value["data"], "$.data", version)
    e, warnings = data_problems(value["kind"], value["data"], "$.data")
    return errors + e, warnings


def show_problems(value, version: int = CONTRACT_VERSION) -> tuple:
    """Everything wrong with one `run show` output, from a source declaring
    contract `version`. Returns (errors, warnings)."""
    errors = validate(value, "show.schema.json")
    if errors:
        return errors, []
    warnings = []
    ids = [w["id"] for w in value["widgets"]]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        errors.append(f"$.widgets: id '{dup}' is used more than once")
    for i, w in enumerate(value["widgets"]):
        errors += version_problems(w["kind"], w["data"], f"$.widgets[{i}].data", version)
        e, wn = data_problems(w["kind"], w["data"], f"$.widgets[{i}].data")
        errors += e
        warnings += wn
    return errors, warnings


def _resolve(ref: str, base: str):
    file, _, pointer = ref.partition("#")
    file = file or base
    node = load(file)
    try:
        for part in (p for p in pointer.split("/") if p):
            node = node[part]
    except (KeyError, TypeError):
        raise ContractError(f"{base}: $ref {ref!r} does not resolve") from None
    return node, file


def _canon(v) -> str:
    return json.dumps(v, sort_keys=True)


def _json_type(v) -> str:
    for t in ("null", "boolean", "integer", "number", "string", "array", "object"):
        if _TYPES[t](v):
            return t
    return type(v).__name__


def _short(v) -> str:
    s = json.dumps(v)
    return s if len(s) <= 60 else s[:57] + "..."


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key) else f"{path}[{json.dumps(key)}]"


def _check(v, s: dict, base: str, path: str, errors: list) -> None:
    if "$ref" in s:
        target, file = _resolve(s["$ref"], base)
        _check(v, target, file, path, errors)

    if "type" in s:
        types = s["type"] if isinstance(s["type"], list) else [s["type"]]
        if not any(_TYPES[t](v) for t in types):
            errors.append(f"{path}: expected {' or '.join(types)}, got {_json_type(v)}")
            return
    if "const" in s and _canon(v) != _canon(s["const"]):
        errors.append(f"{path}: must be {_short(s['const'])}, got {_short(v)}")
    if "enum" in s and _canon(v) not in {_canon(e) for e in s["enum"]}:
        allowed = ", ".join(_short(e) for e in s["enum"])
        errors.append(f"{path}: must be one of {allowed}; got {_short(v)}")

    if isinstance(v, str):
        if len(v) < s.get("minLength", 0):
            errors.append(f"{path}: must not be empty" if s["minLength"] == 1
                          else f"{path}: shorter than {s['minLength']} characters")
        if "pattern" in s and not re.search(s["pattern"], v):
            # A pattern's title names what it accepts, in words.
            wanted = s.get("title", f"a match for {s['pattern']}")
            errors.append(f"{path}: {_short(v)} is not {wanted}")

    if _TYPES["number"](v):
        if "minimum" in s and v < s["minimum"]:
            errors.append(f"{path}: {v} is below the minimum of {s['minimum']}")
        if "maximum" in s and v > s["maximum"]:
            errors.append(f"{path}: {v} is above the maximum of {s['maximum']}")

    if isinstance(v, list):
        if len(v) < s.get("minItems", 0):
            errors.append(f"{path}: needs at least {s['minItems']} item(s)")
        if "maxItems" in s and len(v) > s["maxItems"]:
            errors.append(f"{path}: allows at most {s['maxItems']} item(s)")
        if s.get("uniqueItems"):
            seen = set()
            for item in v:
                c = _canon(item)
                if c in seen:
                    errors.append(f"{path}: {_short(item)} appears more than once")
                seen.add(c)
        if "items" in s:
            for i, item in enumerate(v):
                _check(item, s["items"], base, f"{path}[{i}]", errors)

    if isinstance(v, dict):
        for key in s.get("required", ()):
            if key not in v:
                errors.append(f"{path}: missing '{key}'")
        props = s.get("properties", {})
        extra = s.get("additionalProperties", True)
        for key, item in v.items():
            if key in props:
                _check(item, props[key], base, _join(path, key), errors)
            elif extra is False:
                errors.append(f"{path}: unexpected '{key}'")
            elif isinstance(extra, dict):
                _check(item, extra, base, _join(path, key), errors)

    for sub in s.get("allOf", ()):
        _check(v, sub, base, path, errors)

    if "anyOf" in s:
        firsts = []
        for sub in s["anyOf"]:
            attempt: list = []
            _check(v, sub, base, path, attempt)
            if not attempt:
                break
            firsts.append(attempt[0].split(": ", 1)[-1])
        else:
            errors.append(f"{path}: matches no allowed form ({'; or '.join(firsts)})")

    if "if" in s:
        probe: list = []
        _check(v, s["if"], base, path, probe)
        branch = s.get("else") if probe else s.get("then")
        if branch is not None:
            _check(v, branch, base, path, errors)
