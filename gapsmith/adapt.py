"""gapsmith — a gap class is a RULE, not a new tool.

Porting one codebase onto another raises the same few shapes of missing-symbol
gap over and over: a constant that can be copied verbatim, a data table that
travels with its function, a value that must be allocated rather than copied.
Instead of a bespoke script per shape, this module makes a shape data:

    a rule  =  which symbols it covers        (match: a regex over the gap)
            +  where the donor defines them   (find: file + line/block pattern)
            +  the PROOF that applying is safe (verify_identical byte checks,
                                                or an explicit "verify":"none")
            +  where the result lands         (target file + insert position)
            +  the judgment that can't be derived (map: donor->target renames)

A caller asks `resolve` whether a missing symbol is adaptable; what comes back
carries the donor definition AND the evidence that earned it. `apply` places
the result; `targets` names every file a rule may touch, so the caller's
snapshot/rollback can cover the engine's writes. Nothing here is domain-specific
— the rules file is.

Verification is not optional: a rule with no `verify_identical` clause must say
`"verify": "none"` out loud, or it never resolves — this is the inner gate that
keeps an unproven change from ever being proposed, with a downstream build as
the outer ground truth.
"""
from __future__ import annotations

import glob as globmod
import json
import re
from dataclasses import dataclass
from pathlib import Path

#: Marks every block this engine writes, so a human (or a rollback) can see
#: exactly what was machine-placed and under which authority.
MARK = "// --- gapsmith: rule-resolved definitions (each verified; see the rules file) ---"


@dataclass
class Adaptation:
    """One resolved gap: the symbol, the donor lines that define it, where they
    go, and the evidence that made the copy safe."""

    symbol: str
    rule: str
    target: Path
    lines: list[str]
    verified: str


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _fill(template: str, sym: str, groups: tuple, mapping: dict) -> str:
    """Template language, deliberately tiny: {sym}, {N}, {N.snake}, {N.target}.

    `.snake` is CamelCase->snake_case; `.target` applies the rule's judgment
    map first (Building -> building_hoenn) and falls back to snake. Judgment
    stays in the rule's data, never in code."""

    def repl(m: re.Match) -> str:
        key, filt = m.group(1), m.group(2)
        val = sym if key == "sym" else (groups[int(key) - 1] or "")
        if filt == "snake":
            val = _snake(val)
        elif filt == "target":
            val = mapping.get(val, _snake(val))
        return val

    return re.sub(r"\{(sym|\d+)(?:\.(\w+))?\}", repl, template)


def _first_glob(root: Path, pattern: str) -> Path | None:
    hits = sorted(globmod.glob(str(root / pattern)))
    return Path(hits[0]) if hits else None


def load_rules(path: Path | str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rules = data.get("rules", [])
    for r in rules:
        missing = {"name", "match", "find", "target"} - set(r)
        if missing:
            raise ValueError(f"rule {r.get('name', '?')!r} missing: {sorted(missing)}")
        if not {"line", "block"} & set(r["find"]):
            raise ValueError(f"rule {r['name']!r} find needs 'line' or 'block'")
        if "allocate" in r and not {"range", "used_pattern"} <= set(r["allocate"]):
            raise ValueError(
                f"rule {r['name']!r} allocate needs 'range' and 'used_pattern'")
    return rules


def _extract(find: dict, text: str, sym: str) -> str | None:
    """Pull the donor's definition of `sym` out of `text`.

    'line' is a single-line regex (constants, #defines). 'block' matches the
    START of a multi-line definition — a static table, a struct initializer —
    and extraction runs to the ';' that closes the statement, tracking brace
    depth so nested initializers survive. Depth-tracking reads braces in
    comments and strings too; donor data that trips that fails the outer
    build and rolls back, which is the engine's standing answer to its own
    blind spots."""
    if "line" in find:
        hit = re.search(find["line"].replace("{sym}", re.escape(sym)), text, re.M)
        return hit.group(0) if hit else None
    hit = re.search(find["block"].replace("{sym}", re.escape(sym)), text, re.M)
    if not hit:
        return None
    depth = 0
    for j in range(hit.start(), len(text)):
        ch = text[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == ";" and depth == 0:
            return text[hit.start():j + 1]
    return None


def resolve(sym: str, rules: list[dict], root: Path | str,
            session: dict | None = None) -> Adaptation | None:
    """Is this gap adaptable? None unless a rule matches, the donor actually
    defines the symbol, AND the rule's proof holds. A matching rule whose
    verification fails is a refusal, not an error — the symbol simply stays a
    gap for a human to judge.

    `session` carries intra-run allocation reservations: two symbols resolved
    for the same move must not be handed the same free slot. Pass one dict per
    analysis pass; durable claims live in the target file itself, so a rolled
    back move releases its slots with the file."""
    root = Path(root)
    for rule in rules:
        m = re.match(rule["match"], sym)
        if not m:
            continue
        src = root / rule["find"]["file"]
        if not src.is_file():
            continue
        chunk = _extract(rule["find"], src.read_text(encoding="utf-8", errors="replace"), sym)
        if chunk is None:
            continue
        alloc = rule.get("allocate")
        if alloc:
            # ALLOCATION, not copying: the donor's value may collide with a
            # slot the target already uses, so the rule hands out the lowest
            # free slot in its stated range. The target file is the ledger —
            # used_pattern reads claims straight from it, which keeps the
            # pool consistent through snapshots and rollbacks. An exhausted
            # pool is a refusal: the symbol stays a gap for a human to judge.
            tgt_file = root / rule["target"]["file"]
            text = tgt_file.read_text(encoding="utf-8", errors="replace") \
                if tgt_file.is_file() else ""
            used = {int(v, 16) for v in re.findall(alloc["used_pattern"], text)}
            # Reclamation: slots whose ONLY definitions are proven-dead names
            # (a regenerable measurement from the reclaim_file) are free to
            # reuse. The "only dead definers" test is load-bearing: once a live
            # var is allocated into a reclaimed slot, that slot gains a
            # non-dead definer and stops being reclaimable, so two new vars can
            # never collide on it.
            reclaim = alloc.get("reclaim_file")
            if reclaim:
                rpath = root / reclaim
                if rpath.is_file():
                    dead = set(json.loads(rpath.read_text(encoding="utf-8"))
                               .get("names", {}).values())
                    at_slot: dict[int, list[str]] = {}
                    for dm in re.finditer(r'#define\s+(\w+)\s+0x([0-9A-Fa-f]+)', text):
                        at_slot.setdefault(int(dm.group(2), 16), []).append(dm.group(1))
                    for slot, names in at_slot.items():
                        if names and all(n in dead for n in names):
                            used.discard(slot)
            if session is not None:
                used |= session.get("reserved", {}).get(rule["name"], set())
            lo, hi = (int(x, 16) for x in alloc["range"])
            slot = next((v for v in range(lo, hi + 1) if v not in used), None)
            if slot is None:
                continue
            if session is not None:
                session.setdefault("reserved", {}).setdefault(
                    rule["name"], set()).add(slot)
            return Adaptation(
                symbol=sym,
                rule=rule["name"],
                target=tgt_file,
                lines=[f"#define {sym} 0x{slot:04X}"],
                verified=f"allocated free slot 0x{slot:04X} "
                         "(donor-confirmed; the target file is the ledger)",
            )
        mapping = rule.get("map", {})
        pairs = rule.get("verify_identical")
        if not pairs and rule.get("verify") != "none":
            continue  # an unproven rule never resolves — say "verify": "none" out loud
        proofs: list[str] = []
        ok = True
        for donor_t, target_t in pairs or []:
            da = _first_glob(root, _fill(donor_t, sym, m.groups(), mapping))
            ta = _first_glob(root, _fill(target_t, sym, m.groups(), mapping))
            if not (da and ta and da.read_bytes() == ta.read_bytes()):
                ok = False
                break
            proofs.append(f"{da.name} byte-identical")
        if not ok:
            continue
        return Adaptation(
            symbol=sym,
            rule=rule["name"],
            target=root / rule["target"]["file"],
            lines=[chunk],
            verified="; ".join(proofs) or "verify: none (rule opted out)",
        )
    return None


def targets(rules: list[dict], root: Path | str) -> list[Path]:
    """Every file a rule may write — the proposer snapshots these before any
    move, so the engine's placements roll back with everything else."""
    root = Path(root)
    out: list[Path] = []
    for rule in rules:
        p = root / rule["target"]["file"]
        if p not in out:
            out.append(p)
    return out


def apply(adaptations: list[Adaptation], rules: list[dict]) -> list[Path]:
    """Place the resolved lines. A symbol already present in its target is
    skipped (re-running is safe); insertion honours the rule's position —
    'before-endif' keeps header guards intact, anything else appends."""
    insert_mode = {r["name"]: r["target"].get("insert", "append") for r in rules}
    by_target: dict[Path, list[Adaptation]] = {}
    for a in adaptations:
        by_target.setdefault(a.target, []).append(a)
    touched: list[Path] = []
    for target, ads in by_target.items():
        text = target.read_text(encoding="utf-8", errors="replace")
        add: list[str] = []
        for a in ads:
            if re.search(r"\b%s\b" % re.escape(a.symbol), text):
                continue
            add.extend(line for line in a.lines if line not in add)
        if not add:
            continue
        block = MARK + "\n" + "\n".join(add) + "\n"
        if any(insert_mode.get(a.rule) == "before-endif" for a in ads):
            m = None
            for m in re.finditer(r"^#endif[^\n]*", text, re.M):
                pass  # keep the LAST #endif — the include guard's close
            if m:
                text = text[: m.start()] + block + "\n" + text[m.start() :]
            else:
                text = text + "\n" + block
        else:
            text = text + "\n" + block
        target.write_text(text, encoding="utf-8", newline="\n")
        touched.append(target)
    return touched


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="adapt")
    ap.add_argument("rules", help="path to the adaptation-rules JSON")
    ap.add_argument("symbols", nargs="+", help="gap symbols to resolve")
    ap.add_argument("--root", default=".", help="project root paths resolve against")
    ap.add_argument("--apply", action="store_true", help="place what resolves")
    args = ap.parse_args()

    rules = load_rules(args.rules)
    resolved: list[Adaptation] = []
    session: dict = {}
    for sym in args.symbols:
        a = resolve(sym, rules, args.root, session)
        if a is None:
            print(f"  gap      {sym} (no rule resolves it)")
        else:
            resolved.append(a)
            print(f"  ADAPT    {sym} via {a.rule} ({a.verified})")
    if args.apply and resolved:
        for p in apply(resolved, rules):
            print(f"wrote {p}")
    return 0 if resolved else 1


if __name__ == "__main__":
    raise SystemExit(main())
