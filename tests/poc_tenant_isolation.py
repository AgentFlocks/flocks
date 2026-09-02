"""C0 PoC-4: contracts/access tenant filtering end-to-end.

Validates the REAL policy chain:
  AuthUser(tenant_ids) -> PolicyContextResolver -> PolicyPlanCompiler
    -> Predicate(tenant_id IN tenant_ids, enforcement="driver-required")

i.e. a team_A principal cannot obtain team_B rows through WebUI contract
operations — the filter is compiled into the driver plan, not applied in
frontend params.

Run: PYTHONPATH=. .venv-poc/bin/python tests/poc_tenant_isolation.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("WORKSHOP_JWT_SECRET", "poc-secret-poc-secret-poc-secret")

import jwt as pyjwt

from flocks.auth.context import AuthUser
from flocks.contracts.access.models import Binding, ContractOperation
from flocks.contracts.access.plans import PolicyPlanCompiler
from flocks.contracts.access.runtime import NO_POLICY_SCOPE, PolicyContextResolver

PASS, FAIL = 0, 0


def check(name, ok, detail=""):
    global PASS, FAIL
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    PASS, FAIL = PASS + (1 if ok else 0), FAIL + (0 if ok else 0)


def team_user(teams):
    return AuthUser(id="u1", username="u1", role="member", tenant_ids=tuple(teams))


OP = ContractOperation(
    name="sessions.list",
    operation_type="query",
    adapter_required_fields=frozenset(),
    identity_fields=frozenset(),
    public_fields=frozenset({"session_id", "title"}),
    filter_fields=frozenset({"tenant_id"}),
    tenant_policy_field="tenant_id",
)
BINDING = Binding(
    binding_id="b1", binding_version=1, page_id="sessions", slot_id="list",
    contract_id="c1", contract_version="1", adapter_kind="test",
    source_page_id="sessions", source_root=Path("/tmp"),
    driver_available_fields=frozenset({"tenant_id", "session_id", "title"}),
    driver_allowlist_roots=(Path("/tmp"),),
)


async def main() -> int:
    resolver, compiler = PolicyContextResolver(), PolicyPlanCompiler()

    print("== PolicyContextResolver consumes AuthUser.tenant_ids ==")
    ctx_a = resolver.resolve(team_user(["team_A"]))
    check("team_A user → PolicyContext(tenant_ids=('team_A',))",
          ctx_a.tenant_ids == ("team_A",), str(ctx_a.tenant_ids))

    ctx_none = resolver.resolve(None)
    check("unauthenticated → NO_POLICY_SCOPE (deny-all)",
          ctx_none.tenant_ids == (NO_POLICY_SCOPE,), str(ctx_none.tenant_ids))

    ctx_admin = resolver.resolve(AuthUser(id="root", username="root", role="admin"))
    check("admin bypasses tenant policy (empty context)",
          ctx_admin.tenant_ids == (), str(ctx_admin.tenant_ids))

    print("== PolicyPlanCompiler enforces driver-native predicate ==")
    plan_a = compiler.compile(operation=OP, binding=BINDING, policy_context=ctx_a, params={})
    preds = plan_a.policy_predicates
    check("predicate generated", len(preds) == 1)
    p = preds[0]
    check("field = tenant_id", p.field == "tenant_id")
    check("operator IN values=('team_A',)", p.operator == "in" and p.values == ("team_A",))
    check("enforcement=driver-required (not frontend-optional)",
          p.enforcement == "driver-required" and p.filter_stage == "driver-native")

    ctx_b = resolver.resolve(team_user(["team_B"]))
    plan_b = compiler.compile(operation=OP, binding=BINDING, policy_context=ctx_b, params={})
    check("team_B principal gets team_B predicate — team_A rows are OUT of scope",
          plan_b.policy_predicates[0].values == ("team_B",))

    print("== Unenforceable binding is rejected, not silently skipped ==")
    from flocks.contracts.access.models import ContractRuntimeError
    bad_binding = Binding(binding_id="b2", binding_version=1, page_id="sessions", slot_id="list",
                          contract_id="c1", contract_version="1", adapter_kind="test",
                          source_page_id="sessions", source_root=Path("/tmp"),
                          driver_available_fields=frozenset({"title"}),
                          driver_allowlist_roots=(Path("/tmp"),))
    try:
        compiler.compile(operation=OP, binding=bad_binding, policy_context=ctx_a, params={})
        check("binding that cannot enforce tenant filter raises", False)
    except ContractRuntimeError as e:
        check("binding that cannot enforce tenant filter raises", True, str(e.admin_message)[:70])

    print("== End-to-end: cookie JWT → injected AuthUser drives the same chain ==")
    from flocks.workshop_auth import register_workshop_auth
    from flocks.auth.service import AuthService
    register_workshop_auth()
    now = int(time.time())
    token = pyjwt.encode({"sub": "u_team_a", "username": "u_team_a", "teams": ["team_A"],
                          "iss": "ai-agent-workshop", "aud": "flocks",
                          "iat": now, "exp": now + 900, "flocks_role": "member"},
                         os.environ["WORKSHOP_JWT_SECRET"], algorithm="HS256")
    local_user = await AuthService.get_user_by_session_id(token)
    e2e_ctx = resolver.resolve(local_user.to_auth_user())
    e2e_plan = compiler.compile(operation=OP, binding=BINDING, policy_context=e2e_ctx, params={})
    check("cookie-JWT user → driver-native tenant filter for team_A",
          e2e_plan.policy_predicates[0].values == ("team_A",),
          str(e2e_plan.policy_predicates[0].values))

    print(f"\nRESULT: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
