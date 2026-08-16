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


# ── reference fingerprint implementation ────────────────────────────────────

def canonical_dumps(obj) -> str:
    """Canonical JSON: sorted keys, ASCII-safe, no insignificant whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def identity_subset(spec: dict) -> dict:
    """The identity-bearing subset of ENVIRONMENT_SPEC_V1 (see schema notes)."""
    return {
        "python": spec["python"],
        "os": spec["os"],
        "arch": spec["arch"],
        "accelerator": {
            "type": spec["accelerator"]["type"],
            "variant": spec["accelerator"].get("variant", ""),
        },
        "dependency_files": [
            {k: f[k] for k in ("path", "sha256", "revision") if k in f}
            for f in sorted(spec["dependency_files"], key=lambda f: f["path"])
        ],
        "channels": sorted(spec.get("channels", [])),
        "framework_constraints": sorted(spec.get("framework_constraints", [])),
    }


def spec_fingerprint(spec: dict) -> str:
    return sha256_hex(canonical_dumps(identity_subset(spec)))


def env_id(project: str, fingerprint: str) -> str:
    slug = re_sub_project(project)
    return f"resenv_{slug}_{fingerprint[:12]}"


def re_sub_project(project: str) -> str:
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", project.lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug) or "project"


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
