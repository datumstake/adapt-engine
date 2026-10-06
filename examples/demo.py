#!/usr/bin/env python3
"""Runnable demo: resolve a metatile constant, proven by tileset byte-identity.

    python examples/demo.py

The target is missing `METATILE_Town_Sign`. The rule copies it from the donor —
but only because the tileset bytes are identical on both sides, which it checks
and reports. Edit `examples/target/tiles/sec/town/metatiles.bin` and run again:
the proof fails and the engine refuses, leaving the gap for a human.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # repo root, for `import adapt_engine`

from adapt_engine.adapt import apply, load_rules, resolve  # noqa: E402


def main() -> int:
    rules = load_rules(os.path.join(HERE, "rules.json"))
    target = os.path.join(HERE, "target", "labels.h")
    before = open(target, encoding="utf-8").read()

    sym = "METATILE_Town_Sign"
    a = resolve(sym, rules, HERE)
    if a is None:
        print(f"REFUSED: no rule proved {sym} safe to copy (did the tileset bytes "
              "diverge?). The gap stays a gap.")
        return 1

    print(f"RESOLVED {sym}")
    print(f"  proof:  {a.verified}")
    print(f"  copies: {a.lines[0]}")
    print(f"  into:   {os.path.relpath(a.target, HERE)} (before the include guard)")

    apply([a], rules)
    after = open(target, encoding="utf-8").read()
    print("\n--- target/labels.h now contains ---")
    for line in after.splitlines():
        if line not in before or "METATILE_Town_Sign" in line:
            print(f"  {line}")

    # Keep the demo repeatable: restore the target so the repo stays pristine.
    open(target, "w", encoding="utf-8", newline="\n").write(before)
    print("\n(demo restored target/labels.h to its original state)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
