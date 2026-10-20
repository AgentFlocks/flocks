"""Keeping ``{env:…}``, ``{secret:…}`` and ``{file:…}`` references out of view and on disk.

The loader expands these references anywhere in the config file text
(``Config.load_text``), so the in-memory config holds the real values. Two
rules keep them from leaking:

- A save starts from flocks.json as stored, references unexpanded, and writes
  into it only what the caller changed against the config it saw
  (``drop_unchanged``, then ``apply_changes``). Nothing else in the file is
  rewritten, so a stored reference stays a reference and nothing from another
  config layer is copied in. In a changed list, an item that is one of the
  stored items keeps its stored text. A change that would still carry the value
  of a reference (an edited copy, an item from another file, two references with
  the same value) is refused rather than written in plaintext.
- Non-admins never see a value that came from a reference: every place where a
  loaded file stores one is masked whole, and so is any other string holding
  what a reference expands to, since the config model copies and renames values
  (the deprecated ``mode`` into ``agent``, a second spelling kept as an extra).
  Credential-looking keys and MCP credentials are masked as well.
  ``restore_hidden`` maps an unchanged copy they send back to the real values.

Places are compared on the key spelling of a config dump: stored files are
brought to it by walking the config schema (``schema_normalize``) instead of
validating values, which references in typed fields would not pass.
"""

from __future__ import annotations

import copy
import functools
import inspect
import json
import os
import re
import typing
from typing import Any, Callable, Dict, Iterable, Iterator, List, NamedTuple, Optional, Set, Tuple, Union

# Same syntax the loader expands (replace_env_vars / replace_secret_refs / replace_file_refs).
CONFIG_REFERENCE = re.compile(r"\{(?:env|secret|file):[^}]+\}")

# Shown to non-admins instead of a value. Sending it back keeps the stored value.
REDACTED = "********"

# How the MCP page masks a credential inside a value (flocks.mcp.utils.MCP_MASKED_SECRET_VALUE).
_MCP_MASK = "***"

# Marks a reference written outside a JSON string (``"port": {env:PORT}``) while
# the file is read unexpanded, so a save writes it back the same way.
_BARE_MARK = "flocks-bare"

# A reference value this long counts wherever it appears in a string; a shorter
# one only when the string is exactly that value.
_REVEAL_MIN_LENGTH = 6

Path = Tuple[Union[str, int], ...]

_MISSING = object()
_WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
_SECRET_WORDS = {"token", "secret", "password", "passwd", "passphrase", "pwd", "authorization", "credential", "credentials", "cookie"}
_KEY_QUALIFIERS = {"api", "access", "private", "secret", "encrypt", "encryption", "sign", "signing", "license", "auth"}
_NOT_SECRET_LAST_WORDS = {"id", "ids", "type", "url", "uri", "endpoint", "name", "path", "file", "policy", "notice", "count", "limit", "ttl", "expiry", "expires", "header", "env", "ref", "source"}


# ---------------------------------------------------------------------------
# Walking values
# ---------------------------------------------------------------------------


def contains_reference(value: Any) -> bool:
    """Whether any object key or string value in ``value`` holds a reference."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if CONFIG_REFERENCE.search(item):
                return True
        elif isinstance(item, dict):
            for key, child in item.items():
                if isinstance(key, str) and CONFIG_REFERENCE.search(key):
                    return True
                stack.append(child)
        elif isinstance(item, list):
            stack.extend(item)
    return False


def iter_leaves(value: Any) -> Iterator[Tuple[Path, Any]]:
    """Every scalar value (string, number, boolean) in ``value`` with its path."""
    stack: List[Tuple[Path, Any]] = [((), value)]
    while stack:
        path, item = stack.pop()
        if isinstance(item, dict):
            stack.extend((path + (key,), child) for key, child in item.items())
        elif isinstance(item, list):
            stack.extend((path + (index,), child) for index, child in enumerate(item))
        elif item is not None:
            yield path, item


def get_path(data: Any, path: Path) -> Any:
    """The value at ``path`` in ``data``, or ``_MISSING``."""
    current = data
    for step in path:
        if isinstance(step, int):
            if not isinstance(current, list) or not 0 <= step < len(current):
                return _MISSING
        elif not isinstance(current, dict) or step not in current:
            return _MISSING
        current = current[step]
    return current


def set_path(data: Any, path: Path, value: Any) -> None:
    """Replace the existing value at ``path`` (the path must exist)."""
    current = data
    for step in path[:-1]:
        current = current[step]
    current[path[-1]] = value


def is_missing(value: Any) -> bool:
    return value is _MISSING


def reference_leaves(raw: Any) -> Iterator[Tuple[Path, str]]:
    """String values of an unexpanded config that hold a reference."""
    for path, value in iter_leaves(raw):
        if isinstance(value, str) and CONFIG_REFERENCE.search(value):
            yield path, value


def _reveals(text: str, values: Iterable[str]) -> bool:
    """Whether ``text`` holds one of ``values`` (what references expand to)."""
    return any(
        value and (value in text if len(value) >= _REVEAL_MIN_LENGTH else value == text)
        for value in values
    )


def _values_in(node: Any, values: Dict[str, str]) -> Set[str]:
    """What the references written in ``node`` (unexpanded) expand to."""
    found: Set[str] = set()
    for _, leaf in iter_leaves(node):
        if isinstance(leaf, str):
            for token in CONFIG_REFERENCE.findall(leaf):
                value = values.get(token)
                if value:
                    found.add(value)
    return found


def _fingerprint(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _where(path: Path) -> str:
    text = ""
    for step in path:
        text += f"[{step}]" if isinstance(step, int) else (f".{step}" if text else str(step))
    return text or "the config"


# ---------------------------------------------------------------------------
# Reading stored files without expanding them
# ---------------------------------------------------------------------------


def quote_bare_references(text: str, *, mark: bool = False) -> str:
    """Put quotes around references written outside JSON strings (``"port": {env:PORT}``).

    The loader expands such templates into number or boolean literals, so the
    expanded text parses; quoting them lets the stored text parse too. With
    ``mark``, each quoted one starts with a marker that ``strip_bare_marks``
    turns into a list of places, so a save can write them unquoted again.
    """
    output: List[str] = []
    index = 0
    in_string = False
    length = len(text)
    prefix = _BARE_MARK if mark else ""
    while index < length:
        char = text[index]
        if in_string:
            output.append(char)
            if char == "\\" and index + 1 < length:
                output.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if text.startswith("//", index):
            end = text.find("\n", index)
            end = length if end == -1 else end
            output.append(text[index:end])
            index = end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = length if end == -1 else end + 2
            output.append(text[index:end])
            index = end
            continue
        match = CONFIG_REFERENCE.match(text, index) if char == "{" else None
        if match:
            output.append('"' + prefix + match.group(0).replace("\\", "\\\\").replace('"', '\\"') + '"')
            index = match.end()
            continue
        output.append(char)
        index += 1
    return "".join(output)


def strip_bare_marks(value: Any) -> Tuple[Any, Dict[Path, str]]:
    """``value`` read with ``quote_bare_references(mark=True)``, unmarked, and where marks were."""
    bare: Dict[Path, str] = {}

    def strip(node: Any, path: Path) -> Any:
        if isinstance(node, dict):
            return {key: strip(child, path + (key,)) for key, child in node.items()}
        if isinstance(node, list):
            return [strip(child, path + (index,)) for index, child in enumerate(node)]
        if isinstance(node, str) and node.startswith(_BARE_MARK):
            bare[path] = node[len(_BARE_MARK):]
            return bare[path]
        return node

    return strip(value, ()), bare


def write_unexpanded(data: Dict[str, Any], bare: Dict[Path, str]) -> str:
    """``data`` as config file text, references in ``bare`` written unquoted where they still stand."""
    data = copy.deepcopy(data)
    placeholders: Dict[str, str] = {}
    for number, (path, token) in enumerate(bare.items()):
        if get_path(data, path) == token:
            placeholder = f"flocks-bare-{number}"
            set_path(data, path, placeholder)
            placeholders[json.dumps(placeholder)] = token
    text = json.dumps(data, indent=2)
    for quoted, token in placeholders.items():
        text = text.replace(quoted, token)
    return text


def _config_model() -> type:
    from flocks.config.config import ConfigInfo

    return ConfigInfo


def _models_in(annotation: Any) -> List[type]:
    """The pydantic models an annotation can hold directly (through Optional / Union)."""
    from pydantic import BaseModel

    found: List[type] = []
    stack = [annotation]
    while stack:
        item = stack.pop()
        if inspect.isclass(item) and issubclass(item, BaseModel):
            found.append(item)
        elif typing.get_origin(item) is Union:
            stack.extend(typing.get_args(item))
    return found


def _container_args(annotation: Any, kinds: Tuple[type, ...]) -> Optional[Tuple[Any, ...]]:
    """The type arguments of a dict / list annotation (through Optional / Union)."""
    stack = [annotation]
    while stack:
        item = stack.pop()
        origin = typing.get_origin(item)
        if origin in kinds:
            return typing.get_args(item)
        if origin is Union:
            stack.extend(typing.get_args(item))
    return None


@functools.lru_cache(maxsize=None)
def _model_spellings(model: type) -> Tuple[Dict[str, Tuple[str, int, Any]], bool]:
    """Each key ``model`` reads a field from: (dump key, precedence, field annotation); whether it keeps extras.

    Precedence follows pydantic: the validation alias (choices in order), else
    the alias, then the field name when the model validates by name.
    """
    settings = model.model_config
    by_name = bool(settings.get("validate_by_name") or settings.get("populate_by_name"))
    table: Dict[str, Tuple[str, int, Any]] = {}
    for name, field in model.model_fields.items():
        key = field.serialization_alias or field.alias or name
        validation_alias = field.validation_alias
        choices = getattr(validation_alias, "choices", None)
        if isinstance(validation_alias, str):
            order = [validation_alias]
        elif choices:
            order = [choice for choice in choices if isinstance(choice, str)]
        elif field.alias:
            order = [field.alias]
        else:
            order = [name]
        if by_name and name not in order:
            order.append(name)
        for rank, spelling in enumerate(order):
            table.setdefault(spelling, (key, rank, field.annotation))
    return table, settings.get("extra") == "allow"


class _Fields(NamedTuple):
    """How a dict is read as the model(s) of an annotation."""

    spellings: Dict[str, Tuple[str, int, Any]]
    keeps_extra: bool

    def winners(self, value: Dict[str, Any]) -> Dict[str, Tuple[str, Any]]:
        """For each field ``value`` sets: dump key -> (the key it is read from, field annotation)."""
        best: Dict[str, Tuple[str, int, Any]] = {}
        for raw_key in value:
            entry = self.spellings.get(raw_key)
            if entry is None:
                continue
            key, rank, annotation = entry
            if key not in best or rank < best[key][1]:
                best[key] = (raw_key, rank, annotation)
        return {key: (raw_key, annotation) for key, (raw_key, _, annotation) in best.items()}

    def field(self, key: str) -> Tuple[List[str], Any]:
        """The spellings of the field dumped as ``key`` and its annotation (none if it is not a field)."""
        spellings = [spelling for spelling, entry in self.spellings.items() if entry[0] == key]
        spellings.sort(key=lambda spelling: self.spellings[spelling][1])
        annotation = self.spellings[spellings[0]][2] if spellings else None
        return spellings, annotation


def _fields_of(annotation: Any) -> Optional[_Fields]:
    models = _models_in(annotation)
    if not models:
        return None
    spellings: Dict[str, Tuple[str, int, Any]] = {}
    keeps_extra = False
    for model in models:
        table, extra = _model_spellings(model)
        keeps_extra = keeps_extra or extra
        for spelling, entry in table.items():
            spellings.setdefault(spelling, entry)
    return _Fields(spellings, keeps_extra)


def _dict_values_annotation(annotation: Any) -> Any:
    args = _container_args(annotation, (dict,))
    return args[1] if args and len(args) == 2 else _MISSING


def _list_items_annotation(annotation: Any) -> Any:
    args = _container_args(annotation, (list,))
    return args[0] if args else None


def schema_normalize(value: Any, annotation: Any = _MISSING) -> Any:
    """``value`` with config keys spelled as a config dump spells them; values untouched.

    Where a dict sets one field under two spellings, the one the model reads
    wins the dump key; the other stays under its own key when the model keeps
    extras (as pydantic does) and is dropped otherwise.
    """
    if annotation is _MISSING:
        annotation = _config_model()
    if isinstance(value, dict):
        fields = _fields_of(annotation)
        if fields is not None:
            winners = fields.winners(value)
            read_from = {raw_key: (key, child) for key, (raw_key, child) in winners.items()}
            normalized: Dict[str, Any] = {}
            for raw_key, child in value.items():
                if raw_key in read_from:
                    key, child_annotation = read_from[raw_key]
                    normalized[key] = schema_normalize(child, child_annotation)
                elif raw_key not in fields.spellings or fields.keeps_extra:
                    normalized.setdefault(raw_key, child)
            return normalized
        values_annotation = _dict_values_annotation(annotation)
        if values_annotation is not _MISSING:
            return {name: schema_normalize(child, values_annotation) for name, child in value.items()}
        return value
    if isinstance(value, list):
        items_annotation = _list_items_annotation(annotation)
        if items_annotation is not None:
            return [schema_normalize(child, items_annotation) for child in value]
    return value


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


class ChangeRefused(ValueError):
    """A save that would write the value of a reference instead of the reference."""

    def __init__(self, path: Path, ambiguous: bool = False) -> None:
        self.path = path
        if ambiguous:
            message = (
                f"{_where(path)} matches two stored {{env:…}}/{{secret:…}}/{{file:…}} references "
                "with the same value, so the save cannot tell which one it is; edit the config file directly"
            )
        else:
            message = (
                f"{_where(path)} would be saved with the value of a {{env:…}}/{{secret:…}}/{{file:…}} "
                "reference in plaintext; write the reference itself, or edit the config file that stores it"
            )
        super().__init__(message)


def drop_unchanged(incoming: Any, current: Any) -> Any:
    """``incoming`` without the parts equal to ``current`` at the same place.

    Dicts are compared key by key; anything else (lists included) as a whole.
    An object left empty changed nothing and is dropped too.
    """
    if not isinstance(incoming, dict) or not isinstance(current, dict):
        return incoming
    kept: Dict[str, Any] = {}
    for key, value in incoming.items():
        if key in current:
            existing = current[key]
            if value == existing:
                continue
            if isinstance(value, dict) and isinstance(existing, dict):
                rest = drop_unchanged(value, existing)
                if rest:
                    kept[key] = rest
                continue
        kept[key] = value
    return kept


def _loose(name: Any) -> Any:
    return name.replace("_", "").replace("-", "").lower() if isinstance(name, str) else name


def _stored_key(stored: Dict[str, Any], key: str, fields: Optional[_Fields], values_annotation: Any) -> Tuple[str, Any, List[str]]:
    """Where a change dumped under ``key`` goes in ``stored``: the key, its annotation, other spellings there."""
    if fields is not None:
        spellings, annotation = fields.field(key)
        present = [spelling for spelling in spellings if spelling in stored]
        if present:
            return present[0], annotation, present[1:]
        if not spellings and key in stored:
            return key, None, []
        return key, annotation, []
    if values_annotation is not _MISSING:
        return key, values_annotation, []
    if key not in stored:
        # Untyped settings: keep the stored spelling when it differs only in case or underscores.
        same = [name for name in stored if _loose(name) == _loose(key)]
        if len(same) == 1:
            return same[0], None, []
    return key, None, []


def _refuse_revealed(value: Any, values: Set[str], path: Path) -> None:
    if not values:
        return
    for leaf_path, leaf in iter_leaves(value):
        if isinstance(leaf, str) and _reveals(CONFIG_REFERENCE.sub("", leaf), values):
            raise ChangeRefused(path + leaf_path)


def apply_changes(
    stored: Any,
    changes: Any,
    seen: Any,
    loaded: Any,
    *,
    own_values: Dict[str, str],
    others: Callable[[Path], Set[str]],
    annotation: Any = _MISSING,
    path: Path = (),
) -> Any:
    """``stored`` with ``changes`` written in, and everything else as stored.

    ``stored`` is one config file read without expanding references, in its own
    key spellings; ``changes`` comes from ``drop_unchanged`` against ``seen``,
    the merged config the caller saw; ``loaded`` is the same file expanded
    (both are dumps). ``own_values`` maps the file's references to what they
    expand to, and ``others(path)`` gives what the references other loaded files
    store at a dump path expand to. Raises ``ChangeRefused`` rather than write
    such a value in plaintext.
    """
    if annotation is _MISSING:
        annotation = _config_model()
    if isinstance(changes, dict):
        if not isinstance(stored, dict):
            # A new object, or one replacing another kind of value: checked place by place.
            if not is_missing(stored):
                _refuse_revealed(changes, _values_in(stored, own_values), path)
            stored = {}
        return _apply_object(stored, changes, seen, loaded, own_values, others, annotation, path)
    if isinstance(changes, list):
        return _apply_list(stored, changes, seen, loaded, own_values, others, _list_items_annotation(annotation), path)
    _refuse_revealed(changes, _values_in(stored, own_values) | others(path), path)
    return copy.deepcopy(changes)


def _apply_object(stored, changes, seen, loaded, own_values, others, annotation, path) -> Dict[str, Any]:
    fields = _fields_of(annotation)
    values_annotation = _dict_values_annotation(annotation) if fields is None else _MISSING
    result = dict(stored)
    if not path and isinstance(changes.get("agent"), dict):
        result = _seed_agents_from_mode(result, changes["agent"], fields)
    for key, change in changes.items():
        stored_key, child_annotation, other_spellings = _stored_key(result, key, fields, values_annotation)
        current = result.get(stored_key, _MISSING)
        child = dict(
            seen=seen.get(key, _MISSING) if isinstance(seen, dict) else _MISSING,
            loaded=loaded.get(key, _MISSING) if isinstance(loaded, dict) else _MISSING,
            own_values=own_values,
            others=others,
            annotation=child_annotation,
            path=path + (key,),
        )
        result[stored_key] = apply_changes(current, change, **child)
        if not (isinstance(change, dict) and isinstance(current, dict)):
            # A replaced field keeps one spelling, so the file reads back unambiguously.
            for spelling in other_spellings:
                result.pop(spelling, None)
    return result


def _seed_agents_from_mode(stored: Dict[str, Any], agent_changes: Dict[str, Any], fields: Optional[_Fields]) -> Dict[str, Any]:
    """The model copies a deprecated ``mode`` entry into ``agent`` (as a primary agent).

    Changing such an agent starts from the stored mode entry, references and
    all, instead of a bare entry that would drop the rest of it.
    """
    mode_key = _stored_key(stored, "mode", fields, _MISSING)[0]
    agent_key = _stored_key(stored, "agent", fields, _MISSING)[0]
    modes = stored.get(mode_key)
    if not isinstance(modes, dict):
        return stored
    agents = stored.get(agent_key)
    agents = dict(agents) if isinstance(agents, dict) else {}
    seeded = False
    for name in agent_changes:
        if name not in agents and isinstance(modes.get(name), dict):
            agents[name] = {**modes[name], "mode": "primary"}
            seeded = True
    return {**stored, agent_key: agents} if seeded else stored


def _apply_list(stored, new, seen, loaded, own_values, others, items_annotation, path) -> List[Any]:
    """A changed list: stored items keep their stored text, new ones are written as given.

    An item counts as stored when it equals what one of the file's items
    expands to, wherever it now stands. Two stored references with that same
    value cannot be told apart, and an item still carrying a reference's value
    (edited, or from another file) cannot be written: both refuse the save.
    """
    stored_items = stored if isinstance(stored, list) else []
    loaded_items = loaded if isinstance(loaded, list) and len(loaded) == len(stored_items) else []
    if not loaded_items:
        stored_items = []
    seen_items = seen if isinstance(seen, list) else []
    in_place = bool(stored_items) and loaded_items == seen_items
    by_value: Dict[str, List[int]] = {}
    for index, item in enumerate(loaded_items):
        by_value.setdefault(_fingerprint(item), []).append(index)
    values = (_values_in(stored, own_values) if not is_missing(stored) else set()) | others(path)
    result: List[Any] = []
    for index, item in enumerate(new):
        item_path = path + (index,)
        if (
            in_place
            and index < len(seen_items)
            and item != seen_items[index]
            and isinstance(item, dict)
            and isinstance(seen_items[index], dict)
            and isinstance(stored_items[index], dict)
        ):
            # The object at this place of this file's list, partly changed.
            result.append(apply_changes(
                stored_items[index],
                drop_unchanged(item, seen_items[index]),
                seen_items[index],
                loaded_items[index],
                own_values=own_values,
                others=others,
                annotation=items_annotation,
                path=item_path,
            ))
            continue
        matches = by_value.get(_fingerprint(item), [])
        if len({_fingerprint(stored_items[match]) for match in matches}) > 1:
            raise ChangeRefused(item_path, ambiguous=True)
        if matches:
            result.append(copy.deepcopy(stored_items[matches[0]]))
            continue
        _refuse_revealed(item, values, item_path)
        result.append(copy.deepcopy(item))
    return result


# ---------------------------------------------------------------------------
# What non-admins may read
# ---------------------------------------------------------------------------


def is_secret_key(name: Any) -> bool:
    """Whether a config key name looks like it holds a credential."""
    if not isinstance(name, str) or not name:
        return False
    words = [word.lower() for word in _WORDS.findall(name)]
    if not words or words[-1] in _NOT_SECRET_LAST_WORDS:
        return False
    if any(word in _SECRET_WORDS for word in words):
        return True
    for index, word in enumerate(words):
        if word in {"key", "apikey"} and (word == "apikey" or len(words) == 1 or (index > 0 and words[index - 1] in _KEY_QUALIFIERS)):
            return True
    return False


class StoredLayer(NamedTuple):
    """One loaded config file as stored.

    ``data`` is the file read without expanding references, keys spelled as a
    config dump spells them (``None`` when it cannot be read that way);
    ``values`` maps each reference in the file to what it expands to.
    """

    file: Optional[Any]
    data: Optional[Dict[str, Any]]
    values: Dict[str, str]


def _same_file(first: Any, second: Any) -> bool:
    try:
        return os.path.realpath(first) == os.path.realpath(second)
    except (TypeError, ValueError):
        return False


class ReferencePlaces:
    """Where the loaded config files hold references (dump key spelling), and what those expand to."""

    def __init__(self, layers: Iterable[Union[StoredLayer, Dict[str, Any], None]] = ()) -> None:
        self.layers: List[StoredLayer] = [
            layer if isinstance(layer, StoredLayer) else StoredLayer(None, layer, {}) for layer in layers
        ]
        # A stored file that could not be read unexpanded: nothing can be told apart.
        self.unreadable = any(layer.data is None for layer in self.layers)
        self._prefixes: Set[Path] = set()
        for layer in self.layers:
            for path, _ in reference_leaves(layer.data or {}):
                list_at = next((index for index, step in enumerate(path) if isinstance(step, int)), None)
                # Lists are merged and reordered across layers: the whole list counts.
                self._prefixes.add(path if list_at is None else path[:list_at])
        self.values: Set[str] = {value for layer in self.layers for value in layer.values.values() if value}

    def covers(self, path: Path) -> bool:
        """Whether the value at ``path`` (or a container above it) came from a stored reference."""
        if self.unreadable:
            return True
        return any(path[:length] in self._prefixes for length in range(1, len(path) + 1))

    def reveals(self, text: Any) -> bool:
        """Whether ``text`` holds what one of the references expands to."""
        return isinstance(text, str) and _reveals(text, self.values)

    def values_at(self, path: Path, exclude: Any = None) -> Set[str]:
        """What the references that files other than ``exclude`` store at ``path`` expand to."""
        found: Set[str] = set()
        for layer in self.layers:
            if exclude is not None and layer.file is not None and _same_file(layer.file, exclude):
                continue
            if layer.data is None:
                found.update(value for value in layer.values.values() if value)
                continue
            node = get_path(layer.data, path)
            if not is_missing(node):
                found |= _values_in(node, layer.values)
        return found


def _drop_revealing_keys(node: Any, places: ReferencePlaces) -> None:
    """Remove entries whose key holds a reference's value (a reference written as a key)."""
    if isinstance(node, dict):
        for key in [key for key in node if places.reveals(key)]:
            del node[key]
        for child in node.values():
            _drop_revealing_keys(child, places)
    elif isinstance(node, list):
        for child in node:
            _drop_revealing_keys(child, places)


def member_view(expanded: Dict[str, Any], places: ReferencePlaces) -> Tuple[Dict[str, Any], Dict[Path, Any]]:
    """What a non-admin may read of ``expanded``, and the values it hides.

    Every value at a place where a loaded file stores a reference, and every
    string holding what a reference expands to, is replaced by ``REDACTED`` as
    a whole (it may be the secret, or the secret mixed with other text); so are
    non-empty values under credential-looking keys; MCP servers are masked the
    way the MCP page masks them. The second result maps each hidden path to its
    real value.
    """
    from flocks.mcp.utils import mask_sensitive_mcp_config_for_frontend

    view = copy.deepcopy(expanded)
    _drop_revealing_keys(view, places)
    hidden: Dict[Path, Any] = {}
    for path, value in list(iter_leaves(view)):
        if not path:
            continue
        name = next((step for step in reversed(path) if isinstance(step, str)), "")
        from_reference = places.covers(path) or places.reveals(value)
        looks_secret = isinstance(value, str) and value != "" and isinstance(path[-1], str) and is_secret_key(name)
        if from_reference or looks_secret:
            hidden[path] = value
            set_path(view, path, REDACTED)
    servers = view.get("mcp")
    if isinstance(servers, dict):
        for name, server in servers.items():
            if not isinstance(server, dict):
                continue
            masked = mask_sensitive_mcp_config_for_frontend(server)
            for leaf, shown in list(iter_leaves(masked)):
                full = ("mcp", name) + leaf
                before = get_path(server, leaf)
                if full in hidden or not isinstance(before, str) or shown == before:
                    continue
                hidden[full] = get_path(expanded, full)
                set_path(view, full, shown)
    return view, hidden


def restore_hidden(payload: Any, view: Dict[str, Any], hidden: Dict[Path, Any]) -> Any:
    """Map a non-admin's copy of ``view`` back to the real values it stands for.

    Only values sent back exactly as shown, at the place they were shown, are
    replaced; anything the caller changed or moved is kept as sent.
    """
    if not isinstance(payload, dict):
        return payload
    restored = copy.deepcopy(payload)
    for path, real in hidden.items():
        sent = get_path(restored, path)
        if is_missing(sent) or is_missing(real):
            continue
        if sent == get_path(view, path):
            set_path(restored, path, real)
    return restored


def leftover_masks(changed: Any, view: Dict[str, Any], hidden: Dict[Path, Any]) -> List[Path]:
    """Places in ``changed`` still holding a mask the member was shown (moved, or edited around).

    Saving them would write the mask itself over the hidden value.
    """
    shown_at = {path: get_path(view, path) for path in hidden}
    masks = {shown for shown in shown_at.values() if isinstance(shown, str) and shown}
    found: List[Path] = []
    for path, leaf in iter_leaves(changed):
        if not isinstance(leaf, str):
            continue
        shown = shown_at.get(path)
        if leaf in masks or (isinstance(shown, str) and _MCP_MASK in shown and _MCP_MASK in leaf):
            found.append(path)
    return found
