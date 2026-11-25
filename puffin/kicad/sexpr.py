"""Minimal s-expression reader for KiCad netlists. Pure: text in, nested lists out."""

from __future__ import annotations

from dataclasses import dataclass

SExpr = str | list["SExpr"]


class SExprError(ValueError):
    """The text is not a well-formed s-expression."""


@dataclass(frozen=True)
class Atom:
    """Marks a quoted string so it is never mistaken for a keyword."""

    text: str


def parse(text: str) -> list[SExpr]:
    """Parse one top-level expression. Quoted and bare atoms both become `str`."""
    tokens = _tokenize(text)
    if not tokens or tokens[0] != "(":
        raise SExprError("expected '(' at start of netlist")
    expr, end = _read(tokens, 0)
    if end != len(tokens):
        raise SExprError("unexpected text after the closing ')'")
    assert isinstance(expr, list)
    return expr


def _tokenize(text: str) -> list[str | Atom]:
    tokens: list[str | Atom] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch in "()":
            tokens.append(ch)
            i += 1
        elif ch == '"':
            i += 1
            out: list[str] = []
            while True:
                if i >= n:
                    raise SExprError("unterminated string")
                ch = text[i]
                if ch == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    out.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
                    i += 2
                elif ch == '"':
                    i += 1
                    break
                else:
                    out.append(ch)
                    i += 1
            tokens.append(Atom("".join(out)))
        else:
            start = i
            while i < n and not text[i].isspace() and text[i] not in '()"':
                i += 1
            tokens.append(Atom(text[start:i]))
    return tokens


def _read(tokens: list[str | Atom], i: int) -> tuple[SExpr, int]:
    token = tokens[i]
    if isinstance(token, Atom):
        return token.text, i + 1
    if token == ")":
        raise SExprError("unexpected ')'")
    items: list[SExpr] = []
    i += 1
    while True:
        if i >= len(tokens):
            raise SExprError("missing ')'")
        if tokens[i] == ")":
            return items, i + 1
        item, i = _read(tokens, i)
        items.append(item)


def children(expr: list[SExpr], key: str) -> list[list[SExpr]]:
    """Sub-lists of `expr` whose first atom is `key`."""
    return [c for c in expr if isinstance(c, list) and c and c[0] == key]


def child(expr: list[SExpr], key: str) -> list[SExpr] | None:
    found = children(expr, key)
    return found[0] if found else None


def value(expr: list[SExpr], key: str) -> str | None:
    """The first atom after `key` in the first `(key ...)` child, or None."""
    found = child(expr, key)
    if found is None or len(found) < 2 or not isinstance(found[1], str):
        return None
    return found[1]
