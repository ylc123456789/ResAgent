#!/usr/bin/env python3
"""M2-P0 contract check — reference implementation and golden-fixture gate.

Verifies, for every fixture under contracts/fixtures/:
- each example validates against its JSON Schema;
- each spec's spec_fingerprint recomputes to the golden value;
- the equivalence/distinction pairs in fingerprint_golden.json hold.

The fingerprint algorithm here IS the reference: three repos must produce
byte-identical results for the same fixtures (milestone-2 definition of
done, item 1). See contracts/README.md.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"
FIXTURES = CONTRACTS / "fixtures"


def _schema_registry() -> Registry:
    """Register every contract schema under both its filename and its $id,
    so relative $ref between schemas resolves offline."""
    registry = Registry()
    for schema_file in CONTRACTS.glob("*_v1.schema.json"):
        schema = json.loads(schema_file.read_text(encoding="utf-8"))
        resource = Resource.from_contents(schema, default_specification=DRAFT202012)
        registry = registry.with_resource(schema_file.name, resource)
        if schema.get("$id"):
            registry = registry.with_resource(schema["$id"], resource)
    return registry


# ── reference implementation = the canonical contract file ─────────────────

import importlib.util as _ilu


def _load_contract():
    path = CONTRACTS / "env_contract_v1.py"
    spec = _ilu.spec_from_file_location("env_contract_v1", path)
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_contract = _load_contract()
canonical_dumps = _contract.canonical_dumps
sha256_hex = _contract.sha256_hex
identity_subset = _contract.identity_subset
spec_fingerprint = _contract.spec_fingerprint
env_id = _contract.env_id


# ── fixture gate ─────────────────────────────────────────────────────────────

def _load(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def main() -> int:
    failures: list[str] = []

    # 1. every fixture validates against its schema
    schema_for = {
        "spec": "environment_spec_v1.schema.json",
        "manifest": "environment_manifest_v1.schema.json",
        "audit": "environment_audit_v1.schema.json",
        "lease": "resource_lease_v1.schema.json",
    }
    registry = _schema_registry()
    for kind, schema_file in schema_for.items():
        schema = _load(schema_file)
        validator = jsonschema.Draft202012Validator(schema, registry=registry)
        for path in sorted((FIXTURES / kind).glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            try:
                validator.validate(data)
                print(f"schema ok    {kind}/{path.name}")
            except jsonschema.ValidationError as exc:
                failures.append(f"{kind}/{path.name}: {exc.message}")

    # 2. fingerprint goldens
    golden = json.loads((FIXTURES / "fingerprint_golden.json").read_text(encoding="utf-8"))
    computed: dict[str, str] = {}
    for case in golden["cases"]:
        spec = json.loads((FIXTURES / case["spec"]).read_text(encoding="utf-8"))
        fp = spec_fingerprint(spec)
        computed[case["name"]] = fp
        if fp != case["spec_fingerprint"]:
            failures.append(
                f"{case['name']}: fingerprint {fp} != golden {case['spec_fingerprint']}"
            )
        else:
            print(f"fp ok        {case['name']}")

    # 3. equivalence / distinction relations
    for relation in golden.get("equal", []):
        a, b = relation
        if computed.get(a) != computed.get(b):
            failures.append(f"expected equal fingerprints: {a} vs {b}")
        else:
            print(f"equal ok     {a} == {b}")
    for relation in golden.get("distinct", []):
        a, b = relation
        if computed.get(a) == computed.get(b):
            failures.append(f"expected distinct fingerprints: {a} vs {b}")
        else:
            print(f"distinct ok  {a} != {b}")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(" -", failure)
        return 1
    print("\nM2-P0 contract check: all green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
