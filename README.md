# adapt-engine

[![ci](https://github.com/datumstake/adapt-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/datumstake/adapt-engine/actions/workflows/ci.yml)
[![tests](https://img.shields.io/badge/tests-13%20passing-success)](tests)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![dependencies](https://img.shields.io/badge/runtime%20deps-0-success)](pyproject.toml)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

A tiny engine for resolving a whole **class** of missing-symbol/porting gaps from
declarative rules — where each rule carries its own **machine-checkable proof** that
applying it is safe. No per-gap code; a new gap class is data, not a new tool.

```python
from adapt_engine.adapt import load_rules, resolve, apply

rules = load_rules("rules.json")
a = resolve("METATILE_MauvilleGym_Switch", rules, project_root)
if a:                      # None = no rule proved it safe; the gap stays a gap
    print(a.verified)      # e.g. "metatiles.bin byte-identical"
    apply([a], rules)      # places the donor definition where the build expects it
```

## The idea

Porting one codebase onto another throws up thousands of undefined symbols. Most
fall into a few **shapes**: a constant that can be copied verbatim, a data table that
travels with its function, a value that must be *allocated* rather than copied. The
usual fix is a bespoke script per shape. This engine makes a shape a rule instead:

```jsonc
{
  "name": "metatile-labels",
  "match": "^METATILE_([A-Za-z0-9]+)_\\w+$",     // which symbols it covers
  "find":  { "file": "donor/labels.h",            // where the donor defines them
             "line": "^#define {sym}\\b.*" },
  "verify_identical": [                            // the PROOF copying is safe
    ["donor/tiles/*/{1.snake}/metatiles.bin",
     "target/tiles/*/{1.target}/metatiles.bin"]],
  "map":    { "Building": "building_hoenn" },      // judgment that can't be derived
  "target": { "file": "target/labels.h", "insert": "before-endif" }
}
```

`resolve()` returns a result **only** when a rule matches, the donor actually defines
the symbol, and the proof holds. The proof is the point:

- **`verify_identical`** — byte-compare donor and target assets; copy the constant only
  when the thing it indexes is provably the same on both sides.
- **`"verify": "none"`** — an explicit opt-out for data that has no static proof, where
  a downstream build is the real check. Silence is never consent: a rule with no proof
  clause and no opt-out simply never resolves.
- **`allocate`** — for values that must be *assigned* from a free pool rather than
  copied (the target file is the ledger, so a rollback releases the slot), with an
  optional `reclaim_file` of provably-dead slots to reuse.

Supported find kinds: single-`line`, multi-line `block` (balanced-brace extraction),
and `allocate`. The template language (`{sym}`, `{1}`, `{1.snake}`, `{1.target}`) is
deliberately tiny.

## Why it exists (case study)

Extracted from a larger system where a small local model was driving a real porting
effort — merging one codebase's data and features onto another's engine, ~1100
undefined link symbols deep. The model can't hold the dependency graph or judge a
conflict, so the hard reasoning lives here, in rules with proofs, and the model just
asks "is this gap resolvable?" Each resolved batch was then ground-truthed by a real
compile+link. Representative rules that fell out: index constants proved safe by
byte-identity of the asset they index, donor static-data tables, donor constant
headers, and a fixed-pool resource allocator with dead-slot reclamation.

## Try it

```
pip install -e ".[dev]"
python examples/demo.py   # resolves a constant proven by tileset byte-identity,
                          # shows the proof, applies it, then restores the fixture
pytest                    # the suite pins proof-or-refusal, the allocator, guard-
                          # respecting inserts, idempotent apply, reclamation collision
```

Edit `examples/target/tiles/sec/town/metatiles.bin` and re-run the demo — the proof
fails and the engine refuses, which is the whole point.

No third-party runtime dependencies — standard library only.

## License

MIT. See [LICENSE](LICENSE).

---

Built by **[datumstake](https://github.com/datumstake)**. The rest of the set:

[self-verifying-ratchet](https://github.com/datumstake/self-verifying-ratchet) — the measure-or-roll-back loop ·
[browser-pilot](https://github.com/datumstake/browser-pilot) — the CDP browser driver ·
[focus-three](https://github.com/datumstake/focus-three) — a one-file offline focus tool
