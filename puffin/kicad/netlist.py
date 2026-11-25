"""KiCad `kicadsexpr` netlist model. Pure: parsed text in, dataclasses out."""

from __future__ import annotations

from dataclasses import dataclass, field

from puffin.kicad.sexpr import SExpr, SExprError, child, children, parse, value

PUFFIN_FIELDS = ("Puffin_Part", "Puffin_Board", "Puffin_Variant")


class NetlistError(ValueError):
    """The netlist cannot be read."""


@dataclass(frozen=True)
class Node:
    ref: str
    pin: str  # pad number, compared as a string ("A6" is valid)
    pinfunction: str | None
    pintype: str | None

    @property
    def no_connect(self) -> bool:
        return bool(self.pintype and self.pintype.endswith("+no_connect"))


@dataclass(frozen=True)
class NetlistComponent:
    ref: str
    libpart: str  # "lib:part", e.g. "Device:R"; "part" alone when the lib is blank
    fields: dict[str, str] = field(default_factory=dict)

    def puffin(self, name: str) -> str | None:
        """A Puffin_* field's value, with empty or whitespace-only treated as absent."""
        text = self.fields.get(name, "").strip()
        return text or None


@dataclass(frozen=True)
class Net:
    name: str  # as KiCad wrote it, including any leading "/"
    nodes: tuple[Node, ...]

    @property
    def kind(self) -> str:
        """Net kind: unconnected, auto (KiCad-derived `Net-(...)`), or labelled."""
        if self.name.startswith("unconnected-("):
            return "unconnected"
        if self.name.startswith("Net-("):
            return "auto"
        return "labelled"


@dataclass(frozen=True)
class Netlist:
    components: dict[str, NetlistComponent]
    nets: tuple[Net, ...]


def read_netlist(text: str) -> Netlist:
    try:
        root = parse(text)
    except SExprError as exc:
        raise NetlistError(f"not a KiCad netlist: {exc}") from exc
    if not root or root[0] != "export":
        raise NetlistError("not a KiCad netlist: expected (export ...)")
    version = value(root, "version")
    if version != "E":
        raise NetlistError(f'unsupported netlist version {version!r}; expected "E" (KiCad 6+)')

    components: dict[str, NetlistComponent] = {}
    for comp in children(_section(root, "components"), "comp"):
        ref = value(comp, "ref")
        if ref is None:
            raise NetlistError("component without a (ref ...)")
        components[ref] = NetlistComponent(ref, _libpart(comp), _fields(comp))

    nets: list[Net] = []
    for net in children(_section(root, "nets"), "net"):
        name = value(net, "name")
        if name is None:
            raise NetlistError("net without a (name ...)")
        nodes = []
        for node in children(net, "node"):
            ref, pin = value(node, "ref"), value(node, "pin")
            if ref is None or pin is None:
                raise NetlistError(f"net {name!r} has a node without ref or pin")
            nodes.append(Node(ref, pin, value(node, "pinfunction"), value(node, "pintype")))
        nets.append(Net(name, tuple(nodes)))
    return Netlist(components, tuple(nets))


def _section(root: list[SExpr], key: str) -> list[SExpr]:
    found = child(root, key)
    return found if found is not None else []


def _libpart(comp: list[SExpr]) -> str:
    source = child(comp, "libsource")
    if source is None:
        return ""
    lib, part = value(source, "lib") or "", value(source, "part") or ""
    return f"{lib}:{part}" if lib else part


def _fields(comp: list[SExpr]) -> dict[str, str]:
    """User fields. KiCad writes an empty field as `(field (name "X"))` with no value."""
    result: dict[str, str] = {}
    block = child(comp, "fields")
    for item in children(block, "field") if block is not None else []:
        name = value(item, "name")
        text = next((v for v in item[2:] if isinstance(v, str)), "")
        if name is not None:
            result[name] = text
    return result
