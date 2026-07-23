"""
Query-layer tenant isolation for Smart Pay.

Every request that has an authenticated account sets `current_account_id`
for the duration of that request (see `app.services.auth.AccountScopeMiddleware`
— this has to be ASGI middleware rather than a FastAPI `yield` dependency:
FastAPI runs a sync dependency's pre- and post-yield halves as two
separate threadpool calls, each working from its own copy of the
context, so a value set in one is invisible in the other. Middleware
runs as a single coroutine wrapping the whole request, so a value set
before `call_next()` is visible to everything downstream, including the
threadpool-dispatched route handler).
A session-level event then transparently appends
`account_id == current_account_id` to every SELECT issued against a
tenant-scoped model, so code that forgets to filter by account still
cannot read another account's rows. `before_flush` mirrors this on the
write path: a new row gets its account_id auto-filled from the current
context, and a write that names a *different* account's id is a hard
error rather than a silent cross-tenant write.

`Session.get()` is a documented exception to `with_loader_criteria` (it's
a pure identity-map/primary-key lookup by design), so `TenantScopedSession`
below adds an explicit check for that one path.

When no account is set in context (e.g. the startup migration, a
management script) queries run unscoped. Request-handling code must
always go through `require_account` so that never happens for a real
request.

Scope note: this only auto-filters SELECTs (via with_loader_criteria)
and single-object writes (via the before_flush guard, which also covers
`session.delete(obj)`). It does NOT rewrite bulk
`Query.update()`/`Query.delete()` statements, which bypass loader
criteria by design. Every write path in this app re-fetches the target
row through a scoped SELECT and checks it's not None before mutating or
bulk-deleting related rows, which is what actually keeps those safe today
— any new bulk update()/delete() on a tenant model must add its own
`.filter(Model.account_id == account_id)` explicitly.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional, Type

from sqlalchemy import bindparam, event
from sqlalchemy.orm import Session, with_loader_criteria

_current_account_id: ContextVar[Optional[int]] = ContextVar("current_account_id", default=None)

# Populated by app.models.database once the tenant-scoped models exist.
TENANT_MODELS: list[Type] = []


def register_tenant_model(model: Type) -> Type:
    TENANT_MODELS.append(model)
    return model


def get_current_account_id() -> Optional[int]:
    return _current_account_id.get()


# Built once, outside any lambda: SQLAlchemy's lambda-SQL cache analyzer
# refuses to invoke a callable *inside* a lambda body (it tries to
# statically extract bound values without running the lambda). Building
# the bindparam ahead of time and only referencing it inside the lambda
# as a closure variable satisfies that, while `callable_` still makes
# the bound *value* resolve fresh from the contextvar on every execute.
_tenant_account_param = bindparam("_tenant_account_id", callable_=_current_account_id.get)


@contextmanager
def scoped_to_account(account_id: Optional[int]):
    """Run the enclosed block with tenant filtering set to this account.

    Uses set(None) rather than a Token/reset() pair on purpose: FastAPI
    runs a sync `yield`-dependency's pre- and post-yield halves as two
    separate threadpool calls, each on its own copy of the context, so a
    Token minted in the first half is not valid for reset() in the
    second — it belongs to a different Context object.
    """
    _current_account_id.set(account_id)
    try:
        yield
    finally:
        _current_account_id.set(None)


class TenantScopedSession(Session):
    def get(self, entity, ident, **kw):
        obj = super().get(entity, ident, **kw)
        account_id = _current_account_id.get()
        if obj is not None and account_id is not None and entity in TENANT_MODELS:
            if getattr(obj, "account_id", None) != account_id:
                return None
        return obj


def install_isolation(session_class) -> None:
    @event.listens_for(session_class, "do_orm_execute")
    def _scope_selects(execute_state):
        if not execute_state.is_select:
            return
        if _current_account_id.get() is None:
            return
        for model in TENANT_MODELS:
            execute_state.statement = execute_state.statement.options(
                with_loader_criteria(
                    model,
                    # NOT `lambda cls, _acc=account_id: cls.account_id == _acc`:
                    # SQLAlchemy caches compiled statements by shape, and a
                    # plain Python closure's captured value isn't part of
                    # that cache key. Two calls with the same query shape
                    # but different account_id silently reused the first
                    # call's cached plan, leaking cross-tenant rows.
                    # _tenant_account_param's callable_ is re-resolved from
                    # the contextvar on every execute, cache hit or not.
                    lambda cls: cls.account_id == _tenant_account_param,
                    include_aliases=True,
                )
            )

    @event.listens_for(session_class, "before_flush")
    def _guard_writes(session, flush_context, instances):
        account_id = _current_account_id.get()
        if account_id is None:
            return
        for obj in list(session.new) + list(session.dirty):
            if type(obj) not in TENANT_MODELS:
                continue
            if getattr(obj, "account_id", None) is None:
                obj.account_id = account_id
            elif obj.account_id != account_id:
                raise PermissionError(
                    f"Refusing to write {type(obj).__name__} belonging to "
                    f"account {obj.account_id} while running as account {account_id}"
                )
