"""Comment-preserving YAML I/O for `puffin.yaml`."""

from __future__ import annotations

from io import StringIO
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.tokens import CommentToken


def _yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def parse(source: bytes) -> Any:
    return _yaml().load(source.decode("utf-8"))


def dump(document: Any) -> bytes:
    buffer = StringIO()
    _yaml().dump(document, buffer)
    return buffer.getvalue().encode("utf-8")


def to_plain(node: Any) -> Any:
    """Convert round-trip YAML nodes to plain Python values."""
    if isinstance(node, dict):
        return {str(key) if isinstance(key, str) else key: to_plain(v) for key, v in node.items()}
    if isinstance(node, list):
        return [to_plain(item) for item in node]
    if isinstance(node, bool) or node is None:
        return node
    if isinstance(node, str):
        return str(node)
    if isinstance(node, int):
        return int(node)
    if isinstance(node, float):
        return float(node)
    return node


def _slot(node: Any) -> int:
    """Index of the after-value comment in ruamel's per-item comment record."""
    return 2 if isinstance(node, CommentedMap) else 0


def _last_key(node: Any) -> Any:
    if isinstance(node, CommentedMap) and node:
        return next(reversed(node))
    if isinstance(node, CommentedSeq) and node:
        return len(node) - 1
    return None


def _take_trailing(node: Any) -> CommentToken | None:
    """Detach blank lines and section comments that follow the last item of `node`.

    A same-line comment stays with its item; only what comes after it is returned.
    """
    key = _last_key(node)
    if key is None:
        return None
    record = node.ca.items.get(key)
    token = record[_slot(node)] if record and len(record) > _slot(node) else None
    if token is None:
        return _take_trailing(node[key])
    if not token.value.startswith("#"):
        record[_slot(node)] = None
        return token  # type: ignore[no-any-return]
    head, _, rest = token.value.partition("\n")
    if not rest:
        return None
    token.value = head + "\n"
    return CommentToken("\n" + rest, token.start_mark, None)


def _put_trailing(node: Any, token: CommentToken) -> None:
    key = _last_key(node)
    child = node[key]
    is_block = (
        isinstance(child, CommentedMap | CommentedSeq) and child and not child.fa.flow_style()
    )
    if is_block:
        _put_trailing(child, token)
        return
    record = node.ca.items.setdefault(key, [None, None, None, None])
    record[_slot(node)] = token


def append_item(container: Any, key: Any, value: Any) -> None:
    """Add to the end of a block collection, keeping following comments in place."""
    trailing = _take_trailing(container)
    if isinstance(container, CommentedSeq):
        container.append(value)
    else:
        container[key] = value
    if trailing is not None:
        _put_trailing(container, trailing)


def remove_item(container: Any, key: Any) -> None:
    """Delete from a block collection, keeping following comments in place.

    If the collection becomes empty, the comments that followed it are lost.
    """
    trailing = _take_trailing(container) if key == _last_key(container) else None
    del container[key]
    if trailing is not None and container:
        _put_trailing(container, trailing)


def flow_seq(items: list[str]) -> CommentedSeq:
    seq = CommentedSeq(items)
    seq.fa.set_flow_style()
    return seq


def rename_key(mapping: CommentedMap, old: str, new: str) -> None:
    """Rename a key in place, keeping its position and its comments."""
    position = list(mapping).index(old)
    value = mapping[old]
    record = mapping.ca.items.pop(old, None)
    del mapping[old]
    mapping.insert(position, new, value)
    if record is not None:
        mapping.ca.items[new] = record
