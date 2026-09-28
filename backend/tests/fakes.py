"""
Shared in-memory fakes for unit tests (no database, no network).

A single ``FakeSession`` understands the small subset of sqlmodel/sqlalchemy
queries the services under test issue — ``select(...).where(...)`` with
equality / ``in_`` / ``ilike`` / and/or composition, ``add``, ``delete``,
``get`` — plus explicit per-entity select hooks for the joined queries that
are not practical to decompile (e.g. ``select(Organization, Role).join(...)``).
This mirrors the existing repo style (``test_elicitation.py``'s FakeRepo).
"""
import operator as _pyop
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql import operators as _saop
from sqlalchemy.sql.dml import Delete
from sqlalchemy.sql.elements import BinaryExpression, BindParameter, BooleanClauseList


class FakeResult:
    def __init__(self, rows: list[Any] | None):
        self._rows = rows or []

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)

    def one(self):
        return self._rows[0] if self._rows else None


def _select_entity(statement) -> Any | None:
    """Best-effort primary entity detection for a sqlmodel Select."""
    try:
        desc = statement.column_descriptions[0]
        return desc.get("entity") or desc.get("type")
    except Exception:
        return None


def _left_key(left) -> str | None:
    """Column name behind either an InstrumentedAttribute or AnnotatedColumn."""
    return getattr(left, "key", None)


def _unwrap(value):
    """SQLAlchemy 2 binds literals into BindParameters — yield the raw value."""
    return value.value if isinstance(value, BindParameter) else value


def _clause_attrs_present(clause, row) -> bool:
    """True when every column referenced by a clause exists on the row."""
    if isinstance(clause, BinaryExpression):
        key = _left_key(clause.left)
        if key is not None:
            return hasattr(row, key)
        return True
    if isinstance(clause, BooleanClauseList):
        return all(_clause_attrs_present(c, row) for c in clause.get_children())
    return True


def _eval_clause(clause, row) -> bool:
    """Evaluate a where-clause subtree against a model row."""
    if clause is None or clause is True:
        return True
    if isinstance(clause, BinaryExpression):
        left, raw_right = clause.left, clause.right
        op = getattr(clause, "operator", None)
        key = _left_key(left)
        if key is not None:
            if not hasattr(row, key):
                # Joined-column filter (e.g. member.user_id on an Organization
                # row): cannot evaluate against this row — defer to the caller's
                # select hook. We answer True so no row is wrongly excluded;
                # tests that need real join semantics register a hook.
                return True
            val = getattr(row, key)
            right = _unwrap(raw_right)
            if op in (_pyop.eq, _pyop.is_):
                return val == right
            if op in (_pyop.ne, _pyop.is_not):
                return val != right
            if op in (_pyop.lt, _pyop.le, _pyop.gt, _pyop.ge):
                try:
                    return bool(op(val, right))
                except Exception:
                    return True
            if op is _saop.in_op:
                if isinstance(right, (list, tuple, set)):
                    return val in right
                return True  # in_(subquery): cannot evaluate — defer
            if op in (_pyop.contains, _saop.contains_op):
                if isinstance(right, (list, tuple, set)):
                    return val in right
                return True
            if isinstance(right, str):  # ilike / like patterns
                return right.strip("%").lower() in str(val).lower()
            if op in (_pyop.and_, _pyop.or_):  # pragma: no cover
                return bool(op(_eval_clause(left, row), _eval_clause(right, row)))
            return True  # operators we don't model: do not exclude
        return True
    if isinstance(clause, BooleanClauseList):
        op = getattr(clause, "operator", None)
        children = list(getattr(clause, "get_children", lambda: [])())
        results = [_eval_clause(c, row) for c in children]
        if op is _pyop.or_:
            return any(results)
        return all(results)
    return True


class FakeSession:
    """In-memory stand-in for sqlmodel AsyncSession used by the services."""

    def __init__(self) -> None:
        self.rows: dict[type, list[Any]] = {}
        self.gets: dict[tuple[type, Any], Any] = {}
        self.added: list[Any] = []
        self.deleted: list[Any] = []
        self.delete_statements: list[Any] = []
        self.select_hooks: dict[type, Any] = {}
        self.commits = 0

    # ── seeding ──────────────────────────────────────────────────────────────
    def seed(self, entity: type, rows: list[Any]) -> None:
        self.rows.setdefault(entity, []).extend(rows)
        for r in rows:
            if getattr(r, "id", None) is not None:
                self.gets[(entity, r.id)] = r

    def add_hook(self, entity: type, fn) -> None:
        """Register a filter applied after the declarative where pass."""
        self.select_hooks[entity] = fn

    # ── session API ──────────────────────────────────────────────────────────
    async def get(self, entity: type, ident: Any):
        return self.gets.get((entity, ident))

    async def exec(self, statement, *args, **kwargs):
        if isinstance(statement, Delete):
            self.delete_statements.append(statement)
            try:
                criteria = list(statement._where_criteria)
            except Exception:
                criteria = []
            if criteria:
                for entity, rows in list(self.rows.items()):
                    survived = []
                    for r in rows:
                        if not all(_clause_attrs_present(c, r) for c in criteria):
                            survived.append(r)
                            continue
                        if all(_eval_clause(c, r) for c in criteria):
                            continue  # matched -> dropped
                        survived.append(r)
                    self.rows[entity] = survived
            return FakeResult([])
        entity = _select_entity(statement)
        rows = list(self.rows.get(entity, []))
        # declarative where pass (equality / in_/ ilike / and-or)
        try:
            where = list(statement._where_criteria) if entity is not None else []
            if where:
                rows = [r for r in rows if all(_eval_clause(c, r) for c in where)]
        except Exception:
            pass
        hook = self.select_hooks.get(entity)
        if hook:
            rows = list(hook(rows))
        return FakeResult(rows)

    def add(self, obj) -> None:
        self.added.append(obj)
        entity = type(obj)
        rows = self.rows.setdefault(entity, [])
        if all(r is not obj for r in rows):  # _deliver re-adds after commit
            rows.append(obj)
        if getattr(obj, "id", None) is not None:
            self.gets[(entity, obj.id)] = obj

    async def delete(self, obj) -> None:
        self.deleted.append(obj)
        entity = type(obj)
        rows = self.rows.get(entity, [])
        self.rows[entity] = [r for r in rows if r is not obj]

    async def commit(self) -> None:
        self.commits += 1

    async def flush(self) -> None:
        pass

    async def refresh(self, obj) -> None:
        pass


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
