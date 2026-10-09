"""The adaptation engine (gapsmith/adapt.py).

The promise under test: a gap symbol resolves ONLY when a rule matches it, the
donor really defines it, and the rule's stated proof holds — an unproven rule
never resolves, a failed byte-check is a refusal, and placement respects the
target's include guard. These drive the engine against tiny fixture trees, so
the contract is pinned by execution, not by reading the code.
"""
from __future__ import annotations

import json

import pytest

from gapsmith.adapt import apply, load_rules, resolve, targets


def make_tree(tmp_path, *, donor_bytes=b"TILESET", target_bytes=b"TILESET",
              verify="identical"):
    """A miniature donor/target pair shaped like the real workload."""
    (tmp_path / "donor").mkdir(exist_ok=True)
    (tmp_path / "donor" / "labels.h").write_text(
        "// gTileset_MauvilleGym\n"
        "#define METATILE_MauvilleGym_Switch  0x22A\n", encoding="utf-8")
    # "sec" stands in for the real tree's primary/secondary level, which the
    # rule templates cross with a glob.
    (tmp_path / "donor" / "tiles" / "sec" / "mauville_gym").mkdir(
        parents=True, exist_ok=True)
    (tmp_path / "donor" / "tiles" / "sec" / "mauville_gym" / "metatiles.bin").write_bytes(donor_bytes)
    (tmp_path / "tgt" / "tiles" / "sec" / "mauville_gym").mkdir(
        parents=True, exist_ok=True)
    (tmp_path / "tgt" / "tiles" / "sec" / "mauville_gym" / "metatiles.bin").write_bytes(target_bytes)
    (tmp_path / "tgt" / "labels.h").write_text(
        "#ifndef GUARD\n#define GUARD\n\n#endif // GUARD\n", encoding="utf-8")
    rule = {
        "name": "metatile",
        "match": r"^METATILE_([A-Za-z0-9]+)_\w+$",
        "find": {"file": "donor/labels.h",
                 "line": r"^[ \t]*#define[ \t]+{sym}\b[^\n]*"},
        "target": {"file": "tgt/labels.h", "insert": "before-endif"},
    }
    if verify == "identical":
        rule["verify_identical"] = [
            ["donor/tiles/*/{1.snake}/metatiles.bin",
             "tgt/tiles/*/{1.target}/metatiles.bin"]]
    elif verify == "none":
        rule["verify"] = "none"
    # verify == "missing": rule states no proof at all
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps({"rules": [rule]}), encoding="utf-8")
    return rules_path


def test_resolves_with_proof_when_bytes_match(tmp_path):
    rules = load_rules(make_tree(tmp_path))
    a = resolve("METATILE_MauvilleGym_Switch", rules, tmp_path)
    assert a is not None and a.rule == "metatile"
    assert "byte-identical" in a.verified
    assert a.lines == ["#define METATILE_MauvilleGym_Switch  0x22A"]


def test_failed_byte_check_is_a_refusal(tmp_path):
    rules = load_rules(make_tree(tmp_path, target_bytes=b"DIFFERENT"))
    assert resolve("METATILE_MauvilleGym_Switch", rules, tmp_path) is None


def test_unproven_rule_never_resolves(tmp_path):
    # A rule that states no proof must say "verify": "none" out loud; silence
    # is not consent — the symbol stays a gap.
    rules = load_rules(make_tree(tmp_path, verify="missing"))
    assert resolve("METATILE_MauvilleGym_Switch", rules, tmp_path) is None
    rules_optout = load_rules(make_tree(tmp_path, verify="none"))
    a = resolve("METATILE_MauvilleGym_Switch", rules_optout, tmp_path)
    assert a is not None and "opted out" in a.verified


def test_symbol_outside_every_rule_stays_a_gap(tmp_path):
    rules = load_rules(make_tree(tmp_path))
    assert resolve("VAR_CRUISE_STEP_COUNT", rules, tmp_path) is None
    # Matches the rule's shape but the donor never defines it:
    assert resolve("METATILE_MauvilleGym_Invented", rules, tmp_path) is None


def test_map_judgment_redirects_the_target_side(tmp_path):
    # Donor tileset "building", target renamed "building_hoenn" — the rule's
    # map carries that judgment, and the proof must hit the renamed dir.
    rules_path = make_tree(tmp_path)
    data = json.loads(rules_path.read_text(encoding="utf-8"))
    data["rules"][0]["map"] = {"MauvilleGym": "mauville_hoenn"}
    rules_path.write_text(json.dumps(data), encoding="utf-8")
    rules = load_rules(rules_path)
    assert resolve("METATILE_MauvilleGym_Switch", rules, tmp_path) is None
    (tmp_path / "tgt" / "tiles" / "sec" / "mauville_gym").rename(
        tmp_path / "tgt" / "tiles" / "sec" / "mauville_hoenn")
    assert resolve("METATILE_MauvilleGym_Switch", rules, tmp_path) is not None


def test_apply_inserts_inside_the_guard_and_is_idempotent(tmp_path):
    rules = load_rules(make_tree(tmp_path))
    a = resolve("METATILE_MauvilleGym_Switch", rules, tmp_path)
    touched = apply([a], rules)
    text = (tmp_path / "tgt" / "labels.h").read_text(encoding="utf-8")
    assert touched == [tmp_path / "tgt" / "labels.h"]
    # Inside the guard: the define sits BEFORE the closing #endif.
    assert text.index("METATILE_MauvilleGym_Switch") < text.index("#endif // GUARD")
    # Re-applying the same symbol writes nothing.
    assert apply([a], rules) == []
    assert text.count("METATILE_MauvilleGym_Switch") == \
        (tmp_path / "tgt" / "labels.h").read_text(encoding="utf-8").count(
            "METATILE_MauvilleGym_Switch")


def test_targets_names_every_file_a_rule_may_touch(tmp_path):
    rules = load_rules(make_tree(tmp_path))
    assert targets(rules, tmp_path) == [tmp_path / "tgt" / "labels.h"]


def test_block_find_extracts_balanced_braces_to_the_closing_semicolon(tmp_path):
    # The donor-static-data shape: a file-scope table with nested initializers.
    # 'block' must carry the WHOLE statement — stopping at the first '}' or
    # grabbing one line would ship a broken copy.
    donor = tmp_path / "donor.c"
    donor.write_text(
        "static const struct UCoords8 sSwitchCoords[] =\n"
        "{\n"
        "    { 0 + MAP_OFFSET, 15 + MAP_OFFSET},\n"
        "    { 4 + MAP_OFFSET, 12 + MAP_OFFSET},\n"
        "};\n"
        "static EWRAM_DATA u8 sDoorFrame = 0;\n"
        "void UsesIt(void)\n{\n    sDoorFrame = 1;\n}\n", encoding="utf-8")
    target = tmp_path / "tgt.c"
    target.write_text("// target\n", encoding="utf-8")
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps({"rules": [{
        "name": "static-data",
        "match": r"^s[A-Z]\w*$",
        "find": {"file": "donor.c",
                 "block": r"^static\s+[^;{()=]*\b{sym}\b[^;{(=]*(?:=|;)"},
        "verify": "none",
        "target": {"file": "tgt.c", "insert": "append"},
    }]}), encoding="utf-8")
    rules = load_rules(rules_path)
    a = resolve("sSwitchCoords", rules, tmp_path)
    assert a is not None
    assert a.lines[0].startswith("static const struct UCoords8")
    assert a.lines[0].endswith("};") and a.lines[0].count("{") == 3
    # The one-liner static matches at its DEFINITION, not its use in a body.
    b = resolve("sDoorFrame", rules, tmp_path)
    assert b.lines == ["static EWRAM_DATA u8 sDoorFrame = 0;"]
    apply([a, b], rules)
    text = target.read_text(encoding="utf-8")
    assert "MAP_OFFSET, 15" in text and "sDoorFrame = 0;" in text


def alloc_tree(tmp_path):
    """Donor defines two save vars; target uses two of the four pool slots."""
    (tmp_path / "donor_vars.h").write_text(
        "#define VAR_CRUISE_STEP_COUNT   0x404A\n"
        "#define VAR_RECORD_TIME_H       0x4028\n"
        "#define VAR_SPECIAL_THING       0x8014\n", encoding="utf-8")
    (tmp_path / "tgt_vars.h").write_text(
        "#ifndef G\n#define G\n"
        "#define VAR_A 0x4010\n"
        "#define VAR_B 0x4012\n"
        "#endif // G\n", encoding="utf-8")
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps({"rules": [{
        "name": "var-allocate",
        "match": r"^VAR_\w+$",
        "find": {"file": "donor_vars.h",
                 "line": r"^[ \t]*#define[ \t]+{sym}[ \t]+0x4[0-9A-Fa-f]{3}\b[^\n]*"},
        "allocate": {"range": ["0x4010", "0x4013"],
                     "used_pattern": r"0x40[0-9A-Fa-f]{2}"},
        "target": {"file": "tgt_vars.h", "insert": "before-endif"},
    }]}), encoding="utf-8")
    return load_rules(rules_path)


def test_allocator_hands_out_lowest_free_slots_without_collision(tmp_path):
    rules = alloc_tree(tmp_path)
    session = {}
    a = resolve("VAR_CRUISE_STEP_COUNT", rules, tmp_path, session)
    b = resolve("VAR_RECORD_TIME_H", rules, tmp_path, session)
    # 0x4010 and 0x4012 are claimed in the file; the two free slots go out
    # in order, never twice.
    assert a.lines == ["#define VAR_CRUISE_STEP_COUNT 0x4011"]
    assert b.lines == ["#define VAR_RECORD_TIME_H 0x4013"]
    # Pool now exhausted within this session: the next ask is a refusal.
    (tmp_path / "donor_vars.h").write_text(
        (tmp_path / "donor_vars.h").read_text(encoding="utf-8")
        + "#define VAR_THIRD 0x4030\n", encoding="utf-8")
    assert resolve("VAR_THIRD", rules, tmp_path, session) is None


def test_allocator_refuses_a_donor_special_var(tmp_path):
    # 0x8014 in the donor = a special var backed by a C global, not the save
    # array; the find guard must keep it out of save space.
    rules = alloc_tree(tmp_path)
    assert resolve("VAR_SPECIAL_THING", rules, tmp_path, {}) is None


def test_allocator_ledger_is_the_target_file(tmp_path):
    # Apply one allocation; a FRESH session must see that slot as claimed —
    # and a rollback (file restore) would release it the same way.
    rules = alloc_tree(tmp_path)
    a = resolve("VAR_CRUISE_STEP_COUNT", rules, tmp_path, {})
    apply([a], rules)
    b = resolve("VAR_RECORD_TIME_H", rules, tmp_path, {})
    assert b.lines == ["#define VAR_RECORD_TIME_H 0x4013"]


def test_allocator_reclaims_dead_slots_but_not_once_live(tmp_path):
    # A slot whose only definer is a proven-dead name is reclaimable; the
    # moment a live var is allocated there it must stop being reclaimable, or
    # two new vars collide on it.
    (tmp_path / "donor_vars.h").write_text(
        "#define VAR_NEW_ONE   0x404A\n"
        "#define VAR_NEW_TWO   0x404B\n", encoding="utf-8")
    (tmp_path / "tgt_vars.h").write_text(
        "#ifndef G\n#define G\n"
        "#define VAR_LIVE     0x4010\n"
        "#define VAR_0x4011   0x4011\n"   # dead placeholder, reclaimable
        "#define VAR_0x4012   0x4012\n"   # dead placeholder, reclaimable
        "#endif // G\n", encoding="utf-8")
    (tmp_path / "free-vars.json").write_text(json.dumps(
        {"names": {"0x4011": "VAR_0x4011", "0x4012": "VAR_0x4012"}}),
        encoding="utf-8")
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps({"rules": [{
        "name": "var-allocate",
        "match": r"^VAR_\w+$",
        "find": {"file": "donor_vars.h",
                 "line": r"^[ \t]*#define[ \t]+{sym}[ \t]+0x4[0-9A-Fa-f]{3}\b[^\n]*"},
        "allocate": {"range": ["0x4010", "0x4012"],
                     "used_pattern": r"0x40[0-9A-Fa-f]{2}",
                     "reclaim_file": "free-vars.json"},
        "target": {"file": "tgt_vars.h", "insert": "before-endif"},
    }]}), encoding="utf-8")
    rules = load_rules(rules_path)
    # 0x4010 is live; 0x4011/0x4012 reclaimed -> first alloc gets 0x4011.
    a = resolve("VAR_NEW_ONE", rules, tmp_path, {})
    assert a.lines == ["#define VAR_NEW_ONE 0x4011"]
    apply([a], rules)
    # Now 0x4011 has a live definer (VAR_NEW_ONE) -> no longer reclaimable;
    # the next allocation must move on to 0x4012, never reuse 0x4011.
    b = resolve("VAR_NEW_TWO", rules, tmp_path, {})
    assert b.lines == ["#define VAR_NEW_TWO 0x4012"]


def test_load_rules_rejects_a_half_stated_rule(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"rules": [{"name": "x", "match": "y"}]}),
                 encoding="utf-8")
    with pytest.raises(ValueError):
        load_rules(p)
