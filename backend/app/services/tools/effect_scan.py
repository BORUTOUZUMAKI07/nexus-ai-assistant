"""Resolve what submitted code does, instead of pattern-matching its text.

Strix finding #10: the human-approval gate for ``execute_python`` decided whether
a call was high-impact by running regexes over the *source string*. A regex
cannot see through ``getattr(os, "sys" + "tem")``,
``importlib.import_module("subprocess")`` or ``exec("import os; os.system(...)")``
-- each runs the effect while spelling none of it -- so the gate was walkable by
anything that did not write the effect literally.

This module resolves the code instead. It parses with :mod:`ast`, follows imports
to the module and attribute a reference actually denotes, folds constant string
expressions (``"sys" + "tem"`` -> ``system``), recurses into ``exec`` / ``eval``
of a literal, and **fails closed** when a ``getattr`` / ``__import__`` /
``import_module`` target cannot be resolved: an effect that cannot be shown to be
safe does not run unattended.

Pure and dependency-free: ``scan_effects(code) -> EffectScan``. It reports
*conditions*, not a verdict -- the caller owns the policy.

What it deliberately does not do: follow a rebound builtin (``f = __import__``)
or otherwise model an interpreter. The E2B sandbox remains the execution
boundary; this layer exists to decide when a human is asked first.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

__all__ = ["EffectScan", "scan_effects"]


# Modules whose every use is a process/shell effect: importing one is already the
# intent to spawn, and there is no benign use to preserve.
_SYSTEM_MODULES = frozenset({"subprocess", "pty", "ptyprocess", "pexpect"})

# Members of an otherwise-multi-purpose module that are themselves the effect.
_SYSTEM_MEMBERS: dict[str, frozenset[str]] = {
    "os": frozenset(
        {
            "system",
            "popen",
            "spawnl",
            "spawnle",
            "spawnlp",
            "spawnlpe",
            "spawnv",
            "spawnve",
            "spawnvp",
            "spawnvpe",
            "posix_spawn",
            "posix_spawnp",
            "execl",
            "execle",
            "execlp",
            "execlpe",
            "execv",
            "execve",
            "execvp",
            "execvpe",
            "remove",
            "unlink",
            "rmdir",
            "removedirs",
            "rename",
            "renames",
            "replace",
            "chmod",
            "chown",
            "lchown",
            "kill",
            "killpg",
            "fork",
            "forkpty",
        }
    ),
    "shutil": frozenset(
        {"rmtree", "move", "copy", "copy2", "copyfile", "copytree", "unpack_archive"}
    ),
}

# Modules whose every use is a network effect, plus the effectful members of
# modules that are otherwise multi-purpose.
_NETWORK_MODULES = frozenset(
    {
        "socket",
        "urllib",
        "urllib2",
        "urllib3",
        "requests",
        "httpx",
        "aiohttp",
        "smtplib",
        "ftplib",
        "telnetlib",
        "paramiko",
        "webbrowser",
        "ssl",
        "http",
        "xmlrpc",
    }
)
_NETWORK_MEMBERS: dict[str, frozenset[str]] = {
    "asyncio": frozenset({"open_connection"}),
}

_IMPORT_FUNCS = frozenset({"__import__", "importlib.import_module"})
_EXEC_FUNCS = frozenset({"exec", "eval"})


@dataclass(frozen=True)
class EffectScan:
    """What a piece of code was *resolved* to do."""

    system_commands: bool = False
    network: bool = False
    unresolved: bool = False
    reasons: tuple[str, ...] = ()

    @property
    def needs_approval(self) -> bool:
        """Any effect -- or any effect we could not rule out -- asks a human."""
        return self.system_commands or self.network or self.unresolved


def _fold_string(node: ast.AST | None) -> str | None:
    """The constant string a node denotes, folding ``+`` and literal f-strings."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _fold_string(node.left)
        right = _fold_string(node.right)
        if left is not None and right is not None:
            return left + right
        return None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                return None
        return "".join(parts)
    return None


class _Resolver:
    """One pass over one syntax tree, accumulating resolved effects."""

    def __init__(self, aliases: dict[str, str] | None = None) -> None:
        self.aliases: dict[str, str] = dict(aliases or {})
        self.system = False
        self.network = False
        self.unresolved = False
        self.reasons: list[str] = []

    # ── classification ────────────────────────────────────────────────────────
    @staticmethod
    def _classify(dotted: str) -> str | None:
        head, _, rest = dotted.partition(".")
        if head in _SYSTEM_MODULES:
            return "system"
        if head in _NETWORK_MODULES:
            return "network"
        if rest and head in _SYSTEM_MEMBERS and rest.split(".")[0] in _SYSTEM_MEMBERS[head]:
            return "system"
        if rest and head in _NETWORK_MEMBERS and rest.split(".")[0] in _NETWORK_MEMBERS[head]:
            return "network"
        return None

    def _flag_dotted(self, dotted: str) -> None:
        kind = self._classify(dotted)
        if kind is None:
            return
        if kind == "system":
            self.system = True
        else:
            self.network = True
        self.reasons.append(dotted)

    def _fail_closed(self, reason: str) -> None:
        self.unresolved = True
        self.reasons.append(reason)

    def _merge(self, other: _Resolver) -> None:
        self.system = self.system or other.system
        self.network = self.network or other.network
        self.unresolved = self.unresolved or other.unresolved
        self.reasons.extend(other.reasons)

    # ── name resolution ───────────────────────────────────────────────────────
    def _dotted(self, node: ast.AST) -> str | None:
        """The dotted name a node denotes, following the import aliases."""
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self._dotted(node.value)
            return f"{base}.{node.attr}" if base else None
        if isinstance(node, ast.Call):
            return self._imported_module(node)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute):
            if node.value.attr == "__dict__":
                base = self._dotted(node.value.value)
                key = _fold_string(node.slice)
                if base and key is not None:
                    return f"{base}.{key}"
        return None

    def _grounded_dotted(self, node: ast.AST) -> str | None:
        """Like :meth:`_dotted`, but only through an actual import binding.

        ``getattr`` needs this stricter form: ``_dotted`` happily returns a bare
        parameter name, so ``getattr(x, "system")`` would look resolved to
        ``x.system`` -- a benign-looking target. An object with no import behind
        it is exactly the unresolvable target the gate must fail closed on.
        """
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id)
        if isinstance(node, ast.Attribute):
            base = self._grounded_dotted(node.value)
            return f"{base}.{node.attr}" if base else None
        if isinstance(node, ast.Call):
            return self._imported_module(node)
        return None

    def _imported_module(self, node: ast.Call) -> str | None:
        """``__import__("x")`` / ``importlib.import_module("x")`` -> ``x``."""
        func = self._dotted(node.func)
        if func not in _IMPORT_FUNCS or not node.args:
            return None
        return _fold_string(node.args[0])

    # ── traversal ─────────────────────────────────────────────────────────────
    def _collect_imports(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        self.aliases[alias.asname] = alias.name
                    else:
                        top = alias.name.split(".")[0]
                        self.aliases[top] = top
                    # A dangerous module named by an import is itself the effect
                    # (`import subprocess` has no benign reading).
                    self._flag_dotted(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    if alias.name == "*":
                        if module:
                            self._flag_dotted(f"{module}.*")
                        continue
                    target = f"{module}.{alias.name}" if module else alias.name
                    self.aliases[alias.asname or alias.name] = target

    def _walk(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                dotted = self._dotted(node)
                if dotted:
                    self._flag_dotted(dotted)
            elif isinstance(node, ast.Name):
                dotted = self.aliases.get(node.id)
                if dotted:
                    self._flag_dotted(dotted)
            elif isinstance(node, ast.Subscript):
                dotted = self._dotted(node)
                if dotted:
                    self._flag_dotted(dotted)
            elif isinstance(node, ast.Call):
                self._handle_call(node)

    def _handle_call(self, node: ast.Call) -> None:
        func = self._dotted(node.func)
        self._handle_shell_kw(node)
        if func in _EXEC_FUNCS:
            self._handle_exec(node, func)
        elif func == "getattr":
            self._handle_getattr(node)
        elif func in _IMPORT_FUNCS:
            self._handle_import(node, func)
        if func:
            self._flag_dotted(func)

    def _handle_exec(self, node: ast.Call, func: str) -> None:
        payload = _fold_string(node.args[0]) if node.args else None
        if payload is None:
            # exec/eval of anything but a literal could run any effect at all.
            self._fail_closed(f"{func}(<non-constant>)")
            return
        try:
            tree = ast.parse(payload)
        except (SyntaxError, ValueError):
            self._fail_closed(f"{func}(<unparseable>)")
            return
        child = _Resolver(self.aliases)
        child.run(tree)
        self._merge(child)

    def _handle_getattr(self, node: ast.Call) -> None:
        if len(node.args) < 2:
            self._fail_closed("getattr(<missing-name>)")
            return
        name = _fold_string(node.args[1])
        if name is None:
            self._fail_closed("getattr(<dynamic-name>)")
            return
        obj = self._grounded_dotted(node.args[0])
        if obj is None:
            self._fail_closed(f"getattr(<unresolved>, {name!r})")
            return
        self._flag_dotted(f"{obj}.{name}")

    def _handle_import(self, node: ast.Call, func: str) -> None:
        name = _fold_string(node.args[0]) if node.args else None
        if name is None:
            self._fail_closed(f"{func}(<dynamic>)")
            return
        self._flag_dotted(name)

    def _handle_shell_kw(self, node: ast.Call) -> None:
        for keyword in node.keywords:
            if keyword.arg != "shell":
                continue
            if isinstance(keyword.value, ast.Constant) and bool(keyword.value.value):
                self.system = True
                self.reasons.append("shell=True")

    def run(self, tree: ast.AST) -> None:
        self._collect_imports(tree)
        self._walk(tree)

    def result(self) -> EffectScan:
        return EffectScan(
            system_commands=self.system,
            network=self.network,
            unresolved=self.unresolved,
            reasons=tuple(dict.fromkeys(self.reasons)),
        )


def scan_effects(code: str) -> EffectScan:
    """Resolve ``code`` to the effects it denotes, folding and failing closed."""
    if not code or not code.strip():
        return EffectScan()
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        # Unparseable code cannot execute, so there is no effect to gate. An
        # evasion has to parse in order to run; a caller passing unparseable
        # code is a caller bug, not an attack.
        return EffectScan()
    resolver = _Resolver()
    resolver.run(tree)
    return resolver.result()
