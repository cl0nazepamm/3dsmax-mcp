"""Focused builder v2 contract tests against a deterministic fake bridge.

The fake exercises the real ledger, census, gate, capture, and review code.  It
keeps captures in one TemporaryDirectory per test so the suite leaves no files
behind.
"""

from __future__ import annotations

import copy
import base64
import inspect
import json
import os
import re
import tempfile
import unittest

import maxmcp.tools.builder as b


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def node(
    name,
    dims,
    pos,
    mat="",
    matclass="",
    mods=(),
    layer="_builder",
    cls="Box",
    sup="GeometryClass",
    tris=12,
    verts=8,
    scale=(1, 1, 1),
    boolops=(),
    modcls=(),
    modenabled=None,
    baseclass="",
    parent="BLD_test",
    area=None,
    volume=None,
    hidden=False,
    renderable=True,
    wirecolor=(128, 128, 128),
    rotation=(0, 0, 0, 1),
    handle="",
):
    mods = list(mods)
    modcls = list(modcls)
    modifier_count = max(len(mods), len(modcls))
    while len(mods) < modifier_count:
        mods.append(f"{modcls[len(mods)]}_form")
    while len(modcls) < modifier_count:
        modcls.append("Edit_Poly")
    enabled = [True] * modifier_count if modenabled is None else list(modenabled)
    while len(enabled) < modifier_count:
        enabled.append(True)
    dx, dy, dz = (float(value) for value in dims)
    measured_area = 2.0 * (dx * dy + dx * dz + dy * dz)
    measured_volume = dx * dy * dz
    return {
        "name": name,
        "class": cls,
        "super": sup,
        "parent": parent,
        "layer": layer,
        "pos": list(pos),
        "dims": list(dims),
        "mat": mat,
        "matclass": matclass,
        "mods": mods,
        "tris": tris,
        "verts": verts,
        "scale": list(scale),
        "boolops": list(boolops),
        "modcls": modcls,
        "modenabled": enabled,
        "baseclass": baseclass or cls,
        "area": measured_area if area is None else area,
        "volume": measured_volume if volume is None else volume,
        "hidden": hidden,
        "renderable": renderable,
        "wirecolor": list(wirecolor),
        "rotation": list(rotation),
        "handle": handle or f"node-{name.lower()}",
    }


VALID_SPEC = {
    "object": "straight knife with blade, guard, and handle",
    "units": "cm",
    "complexity": "simple",
    "tolerance_pct": 8,
    "components": [
        {
            "name": "blade",
            "dims": [3, 0.5, 30],
            "center": [0, 0, 26],
            "material": "steel",
            "symmetry": "x",
            "ratios": {"handle": 3.0},
        },
        {
            "name": "guard",
            "dims": [5, 1.5, 1],
            "center": [0, 0, 10.5],
            "material": "steel",
            "touches": ["blade", "handle"],
        },
        {
            "name": "handle",
            "dims": [3, 3, 10],
            "center": [0, 0, 5],
            "material": "steel",
            "ground": True,
        },
    ],
    "materials": [
        {"name": "steel", "class": "PhysicalMaterial", "params": {"roughness": 0.25}}
    ],
    "details": [{"id": "fuller", "on": "blade", "via": "modifier"}],
    "budget": {"tris": 20000},
}


def moderate_spec():
    spec = copy.deepcopy(VALID_SPEC)
    spec["complexity"] = "moderate"
    spec["components"].extend(
        {
            "name": f"part{i}",
            "dims": [1, 1, 1],
            "center": [10 + i * 2, 0, 0.5],
            "material": "steel",
            "parent": "root",
        }
        for i in range(3)
    )
    spec["details"] = [
        {
            "id": "fuller",
            "on": "blade",
            "via": "modifier",
            "count": 1,
            "description": "long recessed groove along the blade",
            "priority": "critical",
        }
    ]
    spec["details"].extend(
        {
            "id": f"groove{i}",
            "on": "handle",
            "via": "map",
            "count": 1,
            "description": f"observed grip groove number {i}",
            "priority": "important" if i < 2 else "support",
        }
        for i in range(5)
    )
    return spec


class FakeClient:
    """Emulate just the bridge surfaces emitted by ``maxmcp.tools.builder``."""

    def __init__(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="builder-test-")
        self.scene = {
            "appdata": None,
            "nodes": [],
            "maps": {},
            "mparams": {},
            "scene_roots": [],
            "root_pos": [0.0, 0.0, 0.0],
            "root_scale": [1.0, 1.0, 1.0],
            "root_rotation": [0.0, 0.0, 0.0, 1.0],
            "root_hidden": False,
            "root_layer": "_builder",
            "unit_scale": 1.0,
            "system_units": "Centimeters",
            "root_collision": "",
        }
        self.capture_failure = False
        self.capture_mode = "ok"
        self.capture_calls = 0
        self.census_calls = 0
        self.ledger_reads = 0
        self.last_capture = ""
        self.last_capture_payload = {}
        self.last_start_script = ""

    def cleanup(self):
        self.tempdir.cleanup()

    def _census_text(self):
        scene = self.scene
        lines = [
            f"UNIT|{scene['unit_scale']}|{scene['system_units']}",
            "ROOT|BLD_test|"
            + ",".join(str(value) for value in scene["root_pos"])
            + "|"
            + ",".join(str(value) for value in scene["root_scale"])
            + "|"
            + ",".join(str(value) for value in scene["root_rotation"])
            + f"|{str(scene['root_hidden']).lower()}|{scene['root_layer']}",
        ]
        for item in scene["nodes"]:
            px, py, pz = item["pos"]
            dx, dy, dz = item["dims"]
            fields = [
                item["name"],
                item["class"],
                item["super"],
                item["parent"],
                item["layer"],
                f"{px},{py},{pz}",
                f"{px - dx / 2},{py - dy / 2},{pz}",
                f"{px + dx / 2},{py + dy / 2},{pz + dz}",
                str(item["tris"]),
                item["mat"],
                item["matclass"],
                ",".join(item["mods"]),
                ",".join(str(value) for value in item["scale"]),
                ",".join(item.get("boolops", [])),
                ",".join(item.get("modcls", [])),
                item.get("baseclass") or item["class"],
                str(item.get("verts", 0)),
                ",".join("1" if value else "0" for value in item.get("modenabled", [])),
                str(item.get("area", 0.0)),
                str(item.get("volume", 0.0)),
                str(bool(item.get("hidden"))).lower(),
                str(bool(item.get("renderable", True))).lower(),
                ",".join(str(value) for value in item.get("wirecolor", [0, 0, 0])),
                ",".join(str(value) for value in item.get("rotation", [0, 0, 0, 1])),
                str(item.get("handle") or ""),
            ]
            lines.append("NODE|" + "|".join(fields))
            for texture in scene["maps"].get(item["name"].lower(), []):
                lines.append(f"MAP|{item['name']}|{texture['name']}|{texture['class']}")
        for (owner, material, key), value in scene["mparams"].items():
            lines.append(f"MPARAM|{owner}|{material}|{key}|{value}")
        for scene_root in scene["scene_roots"]:
            handle = scene_root.get("handle") or f"root-{scene_root['name'].lower()}"
            lines.append(f"SROOT|{handle}|{scene_root['name']}|{scene_root['class']}")
        return "\n".join(lines)

    def send_command(self, script, cmd_type=None, **kwargs):
        if cmd_type == "native:capture_multi_view":
            self.capture_calls += 1
            if self.capture_failure or self.capture_mode == "missing":
                return {"result": "{}"}
            payload = json.loads(script)
            self.last_capture_payload = payload
            path = os.path.join(self.tempdir.name, f"capture-{self.capture_calls}.png")
            content = PNG_BYTES
            if self.capture_mode == "invalid":
                content = b"this is not a png"
            elif self.capture_mode == "empty":
                content = b""
            elif self.capture_mode == "unstable" and self.capture_calls % 2 == 0:
                content = PNG_BYTES + b"changed appearance"
            with open(path, "wb") as stream:
                stream.write(content)
            self.last_capture = path
            returned_views = payload.get("views", ["front"])
            if self.capture_mode == "wrong_views":
                returned_views = ["front"]
            framed_root = payload.get("frame_root")
            if self.capture_mode == "wrong_root":
                framed_root = "BLD_someone_else"
            return {
                "result": json.dumps(
                    {
                        "file": path,
                        "views": returned_views,
                        "framed_root": framed_root,
                        "size_bytes": len(content),
                    }
                )
            }

        if "setAppData root" in script:
            match = re.search(
                r'setAppData root \d+\s+("(?:\\.|[^"\\])*")', script, re.DOTALL
            )
            if match is None:
                raise AssertionError("fake could not parse builder ledger write")
            self.scene["appdata"] = json.loads(match.group(1))
            return {"result": "OK"}

        if "local matches = for o in objects" in script and "Dummy name:" in script:
            self.last_start_script = script
            collision = self.scene["root_collision"]
            if collision == "multiple":
                return {"result": "__ERROR__|Multiple nodes are named BLD_test"}
            if collision == "non-builder" and self.scene["appdata"] is None:
                return {"result": "__ERROR__|A non-builder node is already named BLD_test"}
            roots = "|".join(
                item.get("handle") or f"root-{item['name'].lower()}"
                for item in self.scene["scene_roots"]
            )
            if self.scene["appdata"]:
                return {"result": f"resumed\n{roots}\n{self.scene['appdata']}"}
            return {"result": f"created\n{roots}\n"}

        if "local archived = uniqueName" in script:
            self.scene["appdata"] = None
            return {"result": "OK|ABANDONED_BLD_test_001"}

        if "local doomed" in script and "Could not delete builder root" in script:
            self.scene["appdata"] = None
            self.scene["nodes"] = []
            return {"result": "OK"}

        if "deleteAppData root" in script and 'format "NODE|' not in script:
            self.scene["appdata"] = None
            return {"result": "OK"}

        if 'format "NODE|' in script:
            self.census_calls += 1
            return {"result": self._census_text()}

        if "has no builder ledger" in script and "local ad = getAppData root" in script:
            self.ledger_reads += 1
            if self.scene["appdata"] is None:
                return {"result": "__ERROR__|BLD_test has no builder ledger"}
            return {"result": self.scene["appdata"]}

        return {"result": ""}


class BuilderTestCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeClient()
        self.real_client = b.client
        b.client = self.fake

    def tearDown(self):
        b.client = self.real_client
        self.fake.cleanup()

    def start(self, complexity="simple"):
        return b.builder_session(
            action="start",
            name="test",
            object_desc="straight knife",
            complexity=complexity,
        )

    def start_and_spec(self, spec=None):
        self.start()
        result = b.builder_session(
            action="spec", name="test", spec=copy.deepcopy(spec or VALID_SPEC)
        )
        self.assertTrue(result["valid"], result.get("violations"))
        return result

    def ledger(self):
        return json.loads(self.fake.scene["appdata"])

    def set_raw_blockout(self, blade_height=30):
        self.fake.scene["nodes"] = [
            node("handle", (3, 3, 10), (0, 0, 0)),
            node("guard", (5, 1.5, 1), (0, 0, 10)),
            node("blade", (3, 0.5, blade_height), (0, 0, 11)),
        ]

    def add_modifier(self, item, name, modifier_class="Taper", enabled=True):
        item["mods"].append(name)
        item["modcls"].append(modifier_class)
        item["modenabled"].append(enabled)

    def add_form_work(self, names=("handle", "guard", "blade")):
        wanted = {name.lower() for name in names}
        for item in self.fake.scene["nodes"]:
            if item["name"].lower() in wanted:
                self.add_modifier(item, f"{item['name']}_form", "Taper")

    def assign_materials(self, material_class="PhysicalMaterial"):
        for item in self.fake.scene["nodes"]:
            if item["name"].lower() in {"handle", "guard", "blade"}:
                item["mat"] = "steel"
                item["matclass"] = material_class
                self.fake.scene["mparams"][(item["name"].lower(), "steel", "roughness")] = "0.25"

    def check_clean(self, **kwargs):
        result = b.builder_gate(action="check", name="test", **kwargs)
        self.assertTrue(result["clean"], result.get("violations"))
        self.assertTrue(result["review_ready"], result)
        self.assertTrue(result["review_id"].startswith("r"), result)
        self.assertTrue(result["check_id"].startswith("c"), result)
        self.assertTrue(os.path.isfile(result["capture"]["file"]))
        self.assertTrue(b._png_digest(result["capture"]["file"]))
        self.assertEqual(self.fake.last_capture_payload["frame_root"], "BLD_test")
        self.assertEqual(result["capture"]["views"], b.PASS_VIEWS[result["pass"]])
        self.assertEqual(result["capture"]["size_bytes"], len(PNG_BYTES))
        return result

    def record_continue(self, check, evidence=None, reviewed=None, visual_score=0.9):
        pass_name = check["pass"]
        if reviewed is None and pass_name == "detail":
            reviewed = [detail["id"] for detail in self.ledger()["details"]]
        if evidence is None and pass_name == "detail":
            names = ", ".join(reviewed or [])
            evidence = f"isolated detail capture confirms {names} placement, scale, and shape"
        result = b.builder_gate(
            action="record",
            name="test",
            verdict="continue",
            evidence=evidence or f"{pass_name} capture matches the specified reference targets",
            review_id=check["review_id"],
            visual_score=visual_score,
            reviewed=reviewed,
        )
        self.assertNotEqual(result.get("status"), "error", result)
        return result

    def advance(self, **check_kwargs):
        check = self.check_clean(**check_kwargs)
        return check, self.record_continue(check)

    def to_form(self):
        self.set_raw_blockout()
        self.advance()

    def to_material(self):
        self.to_form()
        self.add_form_work()
        self.advance()

    def to_detail(self):
        self.to_material()
        self.assign_materials()
        self.advance()


class TestSpecValidation(BuilderTestCase):
    def test_start_lands_on_compact_spec_state(self):
        result = self.start()
        self.assertEqual(result["state"], {
            "pass": "spec",
            "completed": False,
            "blocked": False,
            "spec_revision": 0,
        })

    def test_start_rejects_non_builder_and_duplicate_name_collisions(self):
        for collision, fragment in (
            ("non-builder", "non-builder node"),
            ("multiple", "Multiple nodes"),
        ):
            with self.subTest(collision=collision):
                self.fake.scene["root_collision"] = collision
                result = self.start()
                self.assertEqual(result["status"], "error")
                self.assertIn(fragment, result["error"])
                self.fake.scene["root_collision"] = ""
        self.assertIn("matches.count > 1", self.fake.last_start_script)
        self.assertIn("getAppData root", self.fake.last_start_script)

    def test_valid_spatial_spec_unlocks_blockout(self):
        self.start()
        result = b.builder_session(action="spec", name="test", spec=VALID_SPEC)
        self.assertTrue(result["valid"], result.get("violations"))
        self.assertEqual(result["pass"], "blockout")
        self.assertEqual(result["spec_revision"], 1)

    def test_missing_object_and_missing_center_are_rejected(self):
        b.builder_session(action="start", name="test")
        spec = copy.deepcopy(VALID_SPEC)
        spec.pop("object")
        spec["components"][0].pop("center")
        result = b.builder_session(action="spec", name="test", spec=spec)
        messages = " / ".join(item["message"] for item in result["violations"])
        self.assertFalse(result["valid"])
        self.assertIn("object description or reference", messages)
        self.assertIn("needs center", messages)

    def test_self_touch_is_rejected(self):
        self.start()
        spec = copy.deepcopy(VALID_SPEC)
        spec["components"][0]["touches"] = ["blade"]
        result = b.builder_session(action="spec", name="test", spec=spec)
        self.assertFalse(result["valid"])
        self.assertTrue(
            any(item.get("code") == "self-relation" for item in result["violations"])
        )

    def test_zero_infinite_nan_and_huge_tolerances_are_rejected(self):
        self.start()
        for tolerance in (0, float("inf"), float("nan"), 26, 1e100):
            with self.subTest(tolerance=tolerance):
                spec = copy.deepcopy(VALID_SPEC)
                spec["tolerance_pct"] = tolerance
                result = b.builder_session(action="spec", name="test", spec=spec)
                self.assertFalse(result["valid"])
                self.assertTrue(
                    any("tolerance_pct" in item["message"] for item in result["violations"])
                )

    def test_parent_cycles_and_non_boolean_flags_are_rejected(self):
        self.start()
        cyclic = copy.deepcopy(VALID_SPEC)
        cyclic["components"][0]["parent"] = "guard"
        cyclic["components"][1]["parent"] = "blade"
        result = b.builder_session(action="spec", name="test", spec=cyclic)
        self.assertTrue(any(v.get("code") == "parent-cycle" for v in result["violations"]))

        for key, value in (
            ("ground", 1),
            ("floating", "false"),
            ("nested", 1),
            ("primitive", "true"),
        ):
            with self.subTest(key=key):
                strict = copy.deepcopy(VALID_SPEC)
                strict["components"][0][key] = value
                result = b.builder_session(action="spec", name="test", spec=strict)
                self.assertTrue(any(key in v["message"] for v in result["violations"]))

    def test_low_client_review_threshold_is_rejected(self):
        self.start()
        spec = copy.deepcopy(VALID_SPEC)
        spec["review"] = {"threshold": 0.1}
        result = b.builder_session(action="spec", name="test", spec=spec)
        self.assertFalse(result["valid"])
        self.assertTrue(any("0.8" in v["message"] for v in result["violations"]))

    def test_unknown_nested_spec_fields_are_rejected_instead_of_stored(self):
        self.start()
        spec = copy.deepcopy(VALID_SPEC)
        spec["components"][0]["looks_about_right"] = True
        spec["materials"][0]["notes"] = "unused token ballast"
        spec["details"][0]["confidence"] = 0.2
        spec["budget"]["maybe"] = 1

        result = b.builder_session(action="spec", name="test", spec=spec)
        messages = [item["message"] for item in result["violations"]]
        self.assertEqual(sum("unknown field" in message for message in messages), 4)

    def test_physical_camera_token_is_not_physical_material(self):
        self.start()
        spec = copy.deepcopy(VALID_SPEC)
        spec["materials"][0]["class"] = "Physical"
        result = b.builder_session(action="spec", name="test", spec=spec)
        self.assertFalse(result["valid"])
        violation = next(
            item for item in result["violations"] if item.get("code") == "material-class"
        )
        self.assertEqual(violation["actual"], "Physical")
        self.assertEqual(violation["target"], "PhysicalMaterial")
        result = b.builder_session(action="spec", name="test", spec=VALID_SPEC)
        self.assertTrue(result["valid"], result.get("violations"))

    def test_complexity_floors_and_primitive_abuse_are_rejected(self):
        self.start()
        shallow = copy.deepcopy(VALID_SPEC)
        shallow["complexity"] = "moderate"
        result = b.builder_session(action="spec", name="test", spec=shallow)
        self.assertTrue(any("complexity needs" in v["message"] for v in result["violations"]))

        spec = moderate_spec()
        for component in spec["components"][:4]:
            component["primitive"] = True
        result = b.builder_session(action="spec", name="test", spec=spec)
        self.assertTrue(any(v.get("code") == "primitive-abuse" for v in result["violations"]))

    def test_moderate_details_and_triangle_budgets_require_real_integers(self):
        self.start()
        spec = moderate_spec()
        spec["details"][0].pop("count")
        spec["budget"]["tris"] = 20_000.5

        result = b.builder_session(action="spec", name="test", spec=spec)
        messages = [item["message"] for item in result["violations"]]
        self.assertTrue(any("count is required" in message for message in messages))
        self.assertTrue(any("budget.tris must be a positive integer" in message for message in messages))

    def test_spec_json_string_is_accepted(self):
        self.start()
        result = b.builder_session(action="spec", name="test", spec=json.dumps(VALID_SPEC))
        self.assertTrue(result["valid"], result.get("violations"))


class TestSessionHardening(BuilderTestCase):
    def test_forward_and_corrupt_ledgers_are_never_overwritten_on_start(self):
        payloads = (
            json.dumps({"kind": "builder", "v": b.LEDGER_VERSION + 1}),
            "{corrupt-json",
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                self.fake.scene["appdata"] = payload
                result = self.start()
                self.assertEqual(result["status"], "error")
                self.assertIn("invalid or newer", result["error"])
                self.assertEqual(self.fake.scene["appdata"], payload)

    def test_abandon_archives_by_default_and_deletes_only_when_requested(self):
        self.start_and_spec()
        self.set_raw_blockout()
        capture_file = self.check_clean()["capture"]["file"]
        self.assertTrue(os.path.isfile(capture_file))
        archived = b.builder_session(action="abandon", name="test")
        self.assertTrue(archived["abandoned"])
        self.assertFalse(archived["nodes_deleted"])
        self.assertEqual(archived["archived_root"], "ABANDONED_BLD_test_001")
        self.assertEqual(len(self.fake.scene["nodes"]), 3)
        self.assertIsNone(self.fake.scene["appdata"])
        self.assertFalse(os.path.exists(capture_file))

        self.start()
        deleted = b.builder_session(action="abandon", name="test", delete_nodes=True)
        self.assertTrue(deleted["nodes_deleted"])
        self.assertNotIn("archived_root", deleted)
        self.assertEqual(self.fake.scene["nodes"], [])

    def test_scene_root_litter_baseline_uses_handles_not_mutable_names(self):
        self.fake.scene["scene_roots"] = [
            {"handle": "100P", "name": "studio", "class": "Box"}
        ]
        self.start_and_spec()
        self.assertEqual(self.ledger()["baseline_root_handles"], ["100P"])
        self.assertIn("getHandleByAnim", self.fake.last_start_script)
        self.set_raw_blockout()

        self.fake.scene["scene_roots"][0]["name"] = "renamed_studio"
        renamed = self.check_clean()
        self.assertNotIn("warnings", renamed)

        self.fake.scene["scene_roots"].append(
            {"handle": "200P", "name": "studio", "class": "Box"}
        )
        new_handle = b.builder_gate(action="check", name="test")
        self.assertTrue(new_handle["clean"])
        self.assertTrue(any("studio" in warning for warning in new_handle["warnings"]))


class TestSessionAndSpecPatching(BuilderTestCase):
    def setUp(self):
        super().setUp()
        self.start_and_spec()

    def test_spec_is_locked_until_snapshot_bound_refine_spec_then_patch_reopens(self):
        self.set_raw_blockout()
        clean = self.check_clean()
        old_review_id = clean["review_id"]
        self.record_continue(clean)

        locked = b.builder_session(
            action="spec",
            name="test",
            spec={"patch": {"components": [{"name": "blade", "dims": [3, 0.5, 32]}]}},
        )
        self.assertEqual(locked["status"], "error")
        self.assertIn("locked", locked["error"])

        dirty = b.builder_gate(action="check", name="test")
        self.assertFalse(dirty["clean"])
        self.assertNotIn("review_id", dirty)
        self.assertTrue(dirty["check_id"].startswith("c"))
        unlocked = b.builder_gate(
            action="record",
            name="test",
            verdict="refine-spec",
            evidence="blade target needs two more centimeters of length",
            changes=["blade height 30 -> 32; center z 26 -> 27"],
            check_id=dirty["check_id"],
        )
        self.assertEqual(unlocked["recorded"], "refine-spec")

        patched = b.builder_session(
            action="spec",
            name="test",
            spec={
                "patch": {
                    "components": [
                        {"name": "blade", "dims": [3, 0.5, 32], "center": [0, 0, 27]}
                    ]
                }
            },
        )
        self.assertTrue(patched["valid"], patched.get("violations"))
        self.assertEqual(patched["patched"], {"components": 1})
        self.assertEqual(patched["pass"], "blockout")
        blade = next(c for c in self.ledger()["components"] if c["name"] == "blade")
        self.assertEqual(blade["dims"], [3, 0.5, 32])
        self.assertEqual(blade["material"], "steel", "keyed patch must preserve omitted keys")

        stale = b.builder_gate(
            action="record",
            name="test",
            verdict="continue",
            evidence="old blockout review must no longer authorize progress",
            review_id=old_review_id,
            visual_score=0.9,
        )
        self.assertEqual(stale["status"], "error")

    def test_status_is_compact_and_history_is_opt_in(self):
        before = self.fake.census_calls
        compact = b.builder_session(action="status", name="test")
        self.assertNotIn("history", compact["state"])
        self.assertNotIn("component_names", compact["spec_summary"])
        self.assertEqual(self.fake.census_calls, before)

        with_history = b.builder_session(
            action="status", name="test", include_history=True
        )
        self.assertIn("history", with_history["state"])
        self.assertGreaterEqual(len(with_history["state"]["history"]), 2)
        self.assertEqual(self.fake.census_calls, before)

        recovered = b.builder_session(action="status", name="test", include_spec=True)
        self.assertEqual(recovered["spec"]["components"], VALID_SPEC["components"])
        self.assertEqual(recovered["spec"]["materials"], VALID_SPEC["materials"])
        self.assertEqual(recovered["spec"]["details"], VALID_SPEC["details"])
        self.assertEqual(self.fake.census_calls, before)

        verbose = b.builder_session(action="status", name="test", verbose=True)
        self.assertEqual(verbose["nodes_under_root"], 0)
        self.assertIn("component_names", verbose["spec_summary"])
        self.assertEqual(self.fake.census_calls, before + 1)

    def test_resume_and_abandon_round_trip(self):
        resumed = self.start()
        self.assertTrue(resumed["resumed"])
        abandoned = b.builder_session(
            action="abandon", name="test", delete_nodes=True
        )
        self.assertTrue(abandoned["abandoned"])
        self.assertIsNone(self.fake.scene["appdata"])


class TestBlockoutAndFormGates(BuilderTestCase):
    def setUp(self):
        super().setUp()
        self.start_and_spec()

    def test_empty_scene_fails_coverage_without_capture(self):
        result = b.builder_gate(action="check", name="test")
        self.assertFalse(result["clean"])
        self.assertEqual(sum(v["gate"] == "coverage" for v in result["violations"]), 3)
        self.assertIn("check_id", result)
        self.assertNotIn("capture", result)
        self.assertEqual(self.fake.capture_calls, 0)

    def test_exact_centers_units_and_vertex_census_are_reported(self):
        self.set_raw_blockout()
        self.fake.scene["nodes"][2]["verts"] = 42
        result = self.check_clean(report="full")
        metrics = result["metrics"]
        self.assertEqual(metrics["units"], "cm")
        self.assertEqual(metrics["system_units"], "Centimeters")
        self.assertEqual(metrics["components"]["blade"]["center"], [0.0, 0.0, 26.0])
        self.assertEqual(metrics["components"]["blade"]["verts"], 42)

    def test_proportion_placement_and_ratio_are_deterministic(self):
        self.set_raw_blockout(blade_height=40)
        result = b.builder_gate(action="check", name="test", report="full")
        blade = [v for v in result["violations"] if v.get("component") == "blade"]
        self.assertTrue(any(v.get("code") == "dims" for v in blade))
        self.assertTrue(any(v.get("code") == "center" for v in blade))
        self.assertTrue(any("ratio" in v["message"] for v in blade))

    def test_identical_overlapping_component_bounds_fail(self):
        self.set_raw_blockout()
        self.fake.scene["nodes"][1]["dims"] = [3, 3, 10]
        self.fake.scene["nodes"][1]["pos"] = [0, 0, 0]
        result = b.builder_gate(action="check", name="test")
        duplicates = [
            v for v in result["violations"] if v.get("code") == "duplicate-bounds"
        ]
        self.assertEqual(len(duplicates), 1, result["violations"])

    def test_nonidentical_deep_overlap_requires_exact_nested_in(self):
        spec = copy.deepcopy(VALID_SPEC)
        blade_spec = next(c for c in spec["components"] if c["name"] == "blade")
        guard_spec = next(c for c in spec["components"] if c["name"] == "guard")
        blade_spec["center"] = [0, 0, 25]
        guard_spec.update({"dims": [2.5, 2.5, 8], "center": [0, 0, 5]})
        guard_spec.pop("touches")

        b.builder_session(action="abandon", name="test", delete_nodes=True)
        self.start_and_spec(spec)
        self.fake.scene["nodes"] = [
            node("handle", (3, 3, 10), (0, 0, 0)),
            node("guard", (2.5, 2.5, 8), (0, 0, 1)),
            node("blade", (3, 0.5, 30), (0, 0, 10)),
        ]
        overlapping = b.builder_gate(action="check", name="test")
        self.assertTrue(any(v.get("code") == "overpenetration"
                            for v in overlapping["violations"]))

        b.builder_session(action="abandon", name="test", delete_nodes=True)
        guard_spec["nested"] = True
        guard_spec["nested_in"] = "handle"
        self.start_and_spec(spec)
        self.fake.scene["nodes"] = [
            node("handle", (3, 3, 10), (0, 0, 0)),
            node("guard", (2.5, 2.5, 8), (0, 0, 1)),
            node("blade", (3, 0.5, 30), (0, 0, 10)),
        ]
        nested = self.check_clean()
        self.assertFalse(any(v["gate"] == "overlap" for v in nested["violations"]))
        self.assertFalse(any("floating" in v["message"] for v in nested["violations"]))

        b.builder_session(action="abandon", name="test", delete_nodes=True)
        guard_spec["dims"] = [3, 3, 10]
        guard_spec["center"] = [0, 0, 5]
        self.start_and_spec(spec)
        self.fake.scene["nodes"] = [
            node("handle", (3, 3, 10), (0, 0, 0)),
            node("guard", (3, 3, 10), (0, 0, 0)),
            node("blade", (3, 0.5, 30), (0, 0, 10)),
        ]
        duplicate = b.builder_gate(action="check", name="test", report="full")
        duplicate_codes = {item.get("code") for item in duplicate["violations"]}
        self.assertIn("nested-containment", duplicate_codes)
        self.assertIn("duplicate-bounds", duplicate_codes)

    def test_builder_root_rotation_and_scale_must_stay_stable(self):
        self.set_raw_blockout()
        self.fake.scene["root_scale"] = [2.0, 1.0, 1.0]
        self.fake.scene["root_rotation"] = [0.0, 0.0, 0.7071068, 0.7071068]

        result = b.builder_gate(action="check", name="test", report="full")
        codes = {item.get("code") for item in result["violations"]}
        self.assertIn("root-scale", codes)
        self.assertIn("root-rotation", codes)

        self.fake.scene["root_scale"] = [1.0, 1.0, 1.0]
        self.fake.scene["root_rotation"] = [0.0, 0.0, 0.0, 1.0]
        self.fake.scene["nodes"][0]["renderable"] = False
        nonrenderable = b.builder_gate(action="check", name="test", report="full")
        self.assertTrue(
            any(v.get("code") == "nonrenderable-component" for v in nonrenderable["violations"])
        )

    def test_planar_kind_shape_accepts_zero_thickness_spline(self):
        spec = copy.deepcopy(VALID_SPEC)
        spec["components"].append(
            {
                "name": "profile",
                "kind": "shape",
                "dims": [10, 0, 5],
                "center": [0, 0, 2.5],
            }
        )
        b.builder_session(action="abandon", name="test", delete_nodes=True)
        self.start_and_spec(spec)
        self.set_raw_blockout()
        self.fake.scene["nodes"].append(
            node(
                "profile",
                (10, 0, 5),
                (0, 0, 0),
                cls="SplineShape",
                sup="Shape",
                tris=0,
                verts=4,
            )
        )
        result = self.check_clean(report="full")
        self.assertEqual(result["metrics"]["found_components"], 4)

    def test_census_mesh_metrics_use_world_transform_and_release_snapshot(self):
        source = inspect.getsource(b._census)
        self.assertIn("local objectTM = n.objectTransform", source)
        self.assertIn("(getVert snapMesh (face.x as integer)) * objectTM", source)
        self.assertIn("delete snapMesh", source)
        self.assertIn("fn bldWalkMaterial", source)
        self.assertIn("getNumSubMtls mat", source)
        self.assertIn("bldWalkMaterial n.name n.material", source)

    def test_editable_poly_cubes_and_premature_modifiers_fail_blockout(self):
        self.set_raw_blockout()
        for item in self.fake.scene["nodes"]:
            item["class"] = "Editable_Poly"
            item["baseclass"] = "Editable_Poly"
        result = b.builder_gate(action="check", name="test")
        premature = [v for v in result["violations"] if v.get("code") == "premature-form"]
        self.assertEqual(len(premature), 3)

        self.set_raw_blockout()
        self.add_modifier(self.fake.scene["nodes"][2], "early_taper", "Taper")
        result = b.builder_gate(action="check", name="test")
        premature = [v for v in result["violations"] if v.get("code") == "premature-form"]
        self.assertEqual([v["component"] for v in premature], ["blade"])

    def test_form_requires_measured_change_or_real_operation_after_blockout(self):
        self.to_form()
        raw = b.builder_gate(action="check", name="test", report="full")
        self.assertEqual(sum(v["gate"] == "shaping" for v in raw["violations"]), 3)

        nodes = self.fake.scene["nodes"]
        nodes[0]["boolops"].append("grip_cut")
        self.add_modifier(nodes[1], "guard_bend", "Bend")
        # Conversion/subdivision alone changes class/topology but not form.
        nodes[2]["class"] = "Editable_Poly"
        nodes[2]["baseclass"] = "Editable_Poly"
        nodes[2]["verts"] = 16
        nodes[2]["tris"] = 24
        unchanged = b.builder_gate(action="check", name="test", report="full")
        self.assertTrue(
            any(v["gate"] == "shaping" and v.get("component") == "blade"
                for v in unchanged["violations"])
        )

        nodes[2]["area"] *= 1.006
        shaped = self.check_clean(report="full")
        methods = {
            name: metric["shaped"]
            for name, metric in shaped["metrics"]["components"].items()
        }
        self.assertEqual(
            methods,
            {"blade": "geometry-change", "guard": "modifier", "handle": "boolean"},
        )

    def test_disabled_form_modifier_does_not_count(self):
        self.to_form()
        self.add_form_work(("guard", "blade"))
        handle = self.fake.scene["nodes"][0]
        self.add_modifier(handle, "disabled_taper", "Taper", enabled=False)
        result = b.builder_gate(action="check", name="test")
        self.assertTrue(
            any(v["gate"] == "shaping" and v.get("component") == "handle"
                for v in result["violations"])
        )
        handle["modenabled"][0] = True
        self.check_clean()

    def test_one_legitimate_primitive_exemption_does_not_exempt_identity_masses(self):
        spec = copy.deepcopy(VALID_SPEC)
        next(c for c in spec["components"] if c["name"] == "guard")["primitive"] = True
        b.builder_session(
            action="abandon", name="test", delete_nodes=True
        )
        self.start_and_spec(spec)
        self.to_form()
        self.add_form_work(("handle", "blade"))
        result = self.check_clean(report="full")
        self.assertEqual(
            result["metrics"]["components"]["guard"]["shaped"],
            "declared-primitive",
        )


class TestReviewProtocol(BuilderTestCase):
    def setUp(self):
        super().setUp()
        self.start_and_spec()
        self.set_raw_blockout()

    def test_capture_false_and_capture_failure_cannot_authorize_continue(self):
        without_capture = b.builder_gate(
            action="check", name="test", capture=False
        )
        self.assertTrue(without_capture["clean"])
        self.assertFalse(without_capture["review_ready"])
        self.assertIn("check_id", without_capture)
        self.assertNotIn("review_id", without_capture)
        denied = b.builder_gate(
            action="record",
            name="test",
            verdict="continue",
            evidence="deterministic gates alone do not prove the visual result",
            visual_score=0.9,
        )
        self.assertEqual(denied["status"], "error")
        denied_unlock = b.builder_gate(
            action="record",
            name="test",
            verdict="refine-spec",
            evidence="clean deterministic gates cannot excuse a blueprint rewrite",
            check_id=without_capture["check_id"],
        )
        self.assertEqual(denied_unlock["status"], "error")
        self.assertIn("dirty hard-gate", denied_unlock["error"])

    def test_invalid_or_empty_png_and_one_view_override_never_create_review_proof(self):
        for mode in ("invalid", "empty", "wrong_views", "wrong_root"):
            with self.subTest(mode=mode):
                self.fake.capture_mode = mode
                result = b.builder_gate(action="check", name="test")
                self.assertTrue(result["clean"])
                self.assertFalse(result["review_ready"])
                self.assertNotIn("review_id", result)
                self.assertFalse(os.path.exists(self.fake.last_capture))

        self.fake.capture_mode = "ok"
        before = self.fake.capture_calls
        one_view = b.builder_gate(
            action="check", name="test", views=["front"]
        )
        self.assertEqual(one_view["status"], "error")
        self.assertIn("exactly four distinct views", one_view["error"])
        self.assertEqual(self.fake.capture_calls, before)

    def test_record_freshness_recapture_uses_stable_png_bytes(self):
        check = self.check_clean()
        self.assertEqual(self.fake.capture_calls, 1)
        accepted = self.record_continue(check)
        self.assertEqual(accepted["pass"], "form")
        self.assertEqual(self.fake.capture_calls, 2)

    def test_changed_freshness_recapture_rejects_unreviewed_appearance(self):
        check = self.check_clean()
        self.fake.capture_mode = "unstable"
        changed = b.builder_gate(
            action="record",
            name="test",
            verdict="continue",
            evidence="the original reviewed silhouette matched all targets",
            review_id=check["review_id"],
            visual_score=0.9,
        )
        self.assertEqual(changed["status"], "error")
        self.assertIn("appearance changed", changed["error"])

        self.fake.capture_failure = True
        failed_capture = b.builder_gate(action="check", name="test")
        self.assertTrue(failed_capture["clean"])
        self.assertFalse(failed_capture["review_ready"])
        self.assertIn("multi-view capture returned no file", failed_capture["capture_error"])
        denied = b.builder_gate(
            action="record",
            name="test",
            verdict="continue",
            evidence="failed capture cannot be used for a visual verdict",
            review_id="invented-review-id",
            visual_score=0.9,
        )
        self.assertEqual(denied["status"], "error")

    def test_scene_change_and_overwritten_capture_make_review_stale(self):
        scene_check = self.check_clean()
        self.fake.scene["nodes"][0]["tris"] += 1
        stale_scene = b.builder_gate(
            action="record",
            name="test",
            verdict="continue",
            evidence="the scene changed after the reviewed capture was made",
            review_id=scene_check["review_id"],
            visual_score=0.9,
        )
        self.assertEqual(stale_scene["status"], "error")
        self.assertIn("scene changed", stale_scene["error"])

        fresh = self.check_clean()
        with open(fresh["capture"]["file"], "wb") as stream:
            stream.write(b"overwritten")
        stale_file = b.builder_gate(
            action="record",
            name="test",
            verdict="continue",
            evidence="the stored image no longer matches the reviewed bytes",
            review_id=fresh["review_id"],
            visual_score=0.9,
        )
        self.assertEqual(stale_file["status"], "error")
        self.assertIn("capture file changed", stale_file["error"])

    def test_visual_threshold_and_review_id_are_required(self):
        check = self.check_clean()
        missing_score = b.builder_gate(
            action="record",
            name="test",
            verdict="continue",
            evidence="capture matches all blockout targets in every view",
            review_id=check["review_id"],
        )
        self.assertEqual(missing_score["status"], "error")

        low_score = b.builder_gate(
            action="record",
            name="test",
            verdict="continue",
            evidence="capture matches all blockout targets in every view",
            review_id=check["review_id"],
            visual_score=0.79,
        )
        self.assertEqual(low_score["status"], "error")
        self.assertIn("below threshold", low_score["error"])

        accepted = self.record_continue(check, visual_score=0.8)
        self.assertEqual(accepted["pass"], "form")

    def test_server_floor_keeps_runtime_threshold_at_point_eight(self):
        ledger = self.ledger()
        ledger["review"]["threshold"] = 0.1
        self.fake.scene["appdata"] = json.dumps(ledger)
        check = self.check_clean()
        self.assertEqual(check["threshold"], 0.8)
        low = b.builder_gate(
            action="record",
            name="test",
            verdict="continue",
            evidence="capture matches the measured silhouette and placement",
            review_id=check["review_id"],
            visual_score=0.79,
        )
        self.assertEqual(low["status"], "error")

    def test_hedged_continue_is_rejected(self):
        check = self.check_clean()
        for evidence in (
            "proportions are stylized but otherwise fine",
            "boxes are placeholder masses for now",
            "chunky but acceptable for blockout",
        ):
            with self.subTest(evidence=evidence):
                result = b.builder_gate(
                    action="record",
                    name="test",
                    verdict="continue",
                    evidence=evidence,
                    review_id=check["review_id"],
                    visual_score=0.9,
                )
                self.assertEqual(result["status"], "error")
                self.assertIn("refine words", result["error"])

    def test_explicitly_negated_hedges_are_accepted(self):
        check = self.check_clean()
        result = self.record_continue(
            check,
            evidence=(
                "Capture has no placeholder geometry and no proxy masses; "
                "all measured proportions and placements match"
            ),
            visual_score=0.8,
        )
        self.assertEqual(result["pass"], "form")

    def test_one_census_per_check_and_record(self):
        before = self.fake.census_calls
        check = self.check_clean()
        self.assertEqual(self.fake.census_calls, before + 1)
        self.record_continue(check)
        self.assertEqual(self.fake.census_calls, before + 2)


class TestFailureStreak(BuilderTestCase):
    def setUp(self):
        super().setUp()
        self.start_and_spec()

    def test_changed_defect_resets_streak_and_three_identical_defects_block(self):
        first = b.builder_gate(action="check", name="test")
        self.assertEqual(first["attempts"]["same_defect"], 1)

        self.fake.scene["nodes"] = [node("handle", (3, 3, 10), (0, 0, 0))]
        changed = b.builder_gate(action="check", name="test")
        self.assertEqual(changed["attempts"]["same_defect"], 1)
        self.assertNotEqual(changed["defect_id"], first["defect_id"])

        second_same = b.builder_gate(action="check", name="test")
        third_same = b.builder_gate(action="check", name="test")
        self.assertEqual(second_same["attempts"]["same_defect"], 2)
        self.assertEqual(third_same["attempts"]["same_defect"], 3)
        self.assertEqual(third_same["next"]["verdict"], "request-input")

        blocked = b.builder_gate(action="check", name="test")
        self.assertEqual(blocked["status"], "error")
        status = b.builder_session(action="status", name="test")
        self.assertTrue(status["state"]["blocked"])

    def test_blocked_session_cannot_resume_without_single_use_server_token(self):
        for _ in range(3):
            blocked_check = b.builder_gate(action="check", name="test")
        self.assertEqual(blocked_check["attempts"]["same_defect"], 3)
        request = b.builder_gate(
            action="record",
            name="test",
            verdict="request-input",
            evidence="three unchanged coverage defects require user direction",
        )
        token = request["resume_token"]
        self.assertTrue(token.startswith("q"))

        for kwargs in (
            {"resume": True},
            {"resume": True, "resume_token": "invented"},
            {"resume_token": token},
        ):
            with self.subTest(kwargs=kwargs):
                denied = b.builder_gate(action="check", name="test", **kwargs)
                self.assertEqual(denied["status"], "error")

        resumed = b.builder_gate(
            action="check", name="test", resume=True, resume_token=token
        )
        self.assertFalse(resumed["clean"])
        self.assertEqual(resumed["attempts"]["same_defect"], 1)
        reused = b.builder_gate(
            action="check", name="test", resume=True, resume_token=token
        )
        self.assertEqual(reused["status"], "error")

    def test_three_state_oscillation_is_detected_as_no_progress(self):
        self.fake.scene["nodes"] = []
        first = b.builder_gate(action="check", name="test")
        self.fake.scene["nodes"] = [node("handle", (3, 3, 10), (0, 0, 0))]
        second = b.builder_gate(action="check", name="test")
        self.fake.scene["nodes"].append(node("guard", (5, 1.5, 1), (0, 0, 10)))
        third = b.builder_gate(action="check", name="test")
        self.fake.scene["nodes"] = []
        looped = b.builder_gate(action="check", name="test")

        self.assertEqual(len({first["defect_id"], second["defect_id"], third["defect_id"]}), 3)
        self.assertEqual(looped["defect_id"], first["defect_id"])
        self.assertGreaterEqual(looped["attempts"]["same_defect"], 3)
        self.assertEqual(looped["next"]["verdict"], "request-input")

    def test_two_state_oscillation_blocks_on_the_third_dirty_result(self):
        self.fake.scene["nodes"] = []
        first = b.builder_gate(action="check", name="test")
        self.fake.scene["nodes"] = [node("handle", (3, 3, 10), (0, 0, 0))]
        second = b.builder_gate(action="check", name="test")
        self.fake.scene["nodes"] = []
        looped = b.builder_gate(action="check", name="test")

        self.assertNotEqual(first["defect_id"], second["defect_id"])
        self.assertEqual(looped["defect_id"], first["defect_id"])
        self.assertEqual(looped["attempts"]["same_defect"], 3)
        self.assertEqual(looped["next"]["verdict"], "request-input")


class TestMaterialDetailAndFinish(BuilderTestCase):
    def setUp(self):
        super().setUp()
        self.start_and_spec()

    def test_material_gate_requires_exact_assigned_class(self):
        self.to_material()
        self.assign_materials("PhysicalMaterial")
        guard = next(n for n in self.fake.scene["nodes"] if n["name"] == "guard")
        guard["matclass"] = "Physical"
        wrong = b.builder_gate(action="check", name="test")
        class_errors = [v for v in wrong["violations"] if v.get("code") == "material-class"]
        self.assertEqual(len(class_errors), 1)
        self.assertEqual(class_errors[0]["target"], "PhysicalMaterial")
        self.assertEqual(class_errors[0]["actual"], [{"node": "guard", "class": "Physical"}])

        guard["matclass"] = "PhysicalMaterial"
        self.fake.scene["mparams"][("guard", "steel", "roughness")] = "0.8"
        wrong_param = b.builder_gate(action="check", name="test")
        owner_errors = [v for v in wrong_param["violations"] if v.get("component") == "guard"]
        self.assertTrue(any("roughness" in v["message"] for v in owner_errors))
        self.fake.scene["mparams"][("guard", "steel", "roughness")] = "0.25"
        self.check_clean()

    def test_global_substring_wrong_owner_and_low_count_cannot_satisfy_detail(self):
        spec = copy.deepcopy(VALID_SPEC)
        spec["details"] = [
            {"id": "fuller", "on": "blade", "via": "geometry", "count": 2}
        ]
        b.builder_session(action="abandon", name="test", delete_nodes=True)
        self.start_and_spec(spec)
        self.to_detail()

        scene = self.fake.scene["nodes"]
        scene.append(node("notfullerthing", (0.2, 0.2, 1), (0, 0, 20), parent="blade"))
        scene.append(node("fuller_left", (0.2, 0.2, 1), (0, 0, 20), parent="handle"))
        missing = b.builder_gate(action="check", name="test", report="full")
        self.assertEqual(missing["metrics"]["details"]["fuller"], {
            "via": "geometry", "found": 0, "expected": 2
        })

        scene[-1]["parent"] = "blade"
        one = b.builder_gate(action="check", name="test", report="full")
        self.assertEqual(one["metrics"]["details"]["fuller"]["found"], 1)
        scene[-1]["hidden"] = True
        hidden = b.builder_gate(action="check", name="test", report="full")
        self.assertEqual(hidden["metrics"]["details"]["fuller"]["found"], 0)
        scene[-1]["hidden"] = False
        scene.append(node("fuller-right", (0.2, 0.2, 1), (0.3, 0, 20), parent="blade"))
        clean = self.check_clean(report="full")
        self.assertEqual(clean["metrics"]["details"]["fuller"]["found"], 2)

        scene.append(node("fuller_extra", (0.2, 0.2, 1), (-0.3, 0, 20), parent="blade"))
        extra = b.builder_gate(action="check", name="test", report="full")
        detail_error = next(v for v in extra["violations"] if v.get("code") == "detail-geometry")
        self.assertEqual((detail_error["actual"], detail_error["target"]), (3, 2))
        scene.pop()
        clean = self.check_clean(report="full")

        omitted = b.builder_gate(
            action="record",
            name="test",
            verdict="continue",
            evidence="both isolated grooves match their positions and scale",
            review_id=clean["review_id"],
            visual_score=0.9,
            reviewed=[],
        )
        self.assertEqual(omitted["status"], "error")
        self.assertEqual(omitted["details"]["missing_reviewed"], ["fuller"])
        accepted = self.record_continue(
            clean,
            evidence="isolated fuller pair matches position, count, scale, and shape",
            reviewed=["fuller"],
        )
        self.assertEqual(accepted["pass"], "finish")

    def test_duplicate_named_node_outside_detail_owner_fails_finish_hygiene(self):
        spec = copy.deepcopy(VALID_SPEC)
        spec["details"] = [
            {"id": "fuller", "on": "blade", "via": "geometry", "count": 1}
        ]
        b.builder_session(action="abandon", name="test", delete_nodes=True)
        self.start_and_spec(spec)
        self.to_detail()
        self.fake.scene["nodes"].extend(
            [
                node("fuller_01", (0.2, 0.2, 1), (0, 0, 20), parent="blade", handle="detail-good"),
                node("fuller_01", (0.2, 0.2, 1), (0, 0, 5), parent="handle", handle="detail-impostor"),
            ]
        )
        detail = self.check_clean(report="full")
        self.record_continue(
            detail,
            evidence="isolated fuller anchor matches its reference placement and shape",
            reviewed=["fuller"],
        )

        finish = b.builder_gate(action="check", name="test", report="full")
        hygiene = [item for item in finish["violations"] if item["gate"] == "hygiene"]
        self.assertTrue(any("matches no component or detail id" in item["message"] for item in hygiene))

    def test_owned_projection_camera_and_live_boolean_cutter_are_finish_hygiene(self):
        spec = copy.deepcopy(VALID_SPEC)
        spec["details"] = [
            {"id": "fuller", "on": "blade", "via": "boolean", "count": 1},
            {"id": "badge", "on": "blade", "via": "projection", "count": 1},
        ]
        b.builder_session(action="abandon", name="test", delete_nodes=True)
        self.start_and_spec(spec)
        self.to_detail()
        blade = next(n for n in self.fake.scene["nodes"] if n["name"] == "blade")
        self.add_modifier(blade, "fuller_boolean", "BooleanMod")
        blade["boolops"].append("fuller_cutter")
        self.fake.scene["nodes"].extend(
            [
                node(
                    "fuller_cutter", (0.2, 0.2, 5), (0, 0, 20),
                    parent="blade", hidden=True,
                ),
                node(
                    "badge_camera",
                    (1, 1, 1),
                    (0, -5, 20),
                    cls="Freecamera",
                    sup="Camera",
                    tris=0,
                    verts=0,
                    parent="blade",
                    hidden=True,
                ),
            ]
        )
        self.fake.scene["maps"]["blade"] = [
            {"name": "badge_projection", "class": "Camera_Map_Per_Pixel"}
        ]
        camera = next(item for item in self.fake.scene["nodes"] if item["name"] == "badge_camera")
        camera["hidden"] = False
        visible_support = b.builder_gate(action="check", name="test", report="full")
        self.assertTrue(any(v.get("code") == "visible-support" for v in visible_support["violations"]))
        camera["hidden"] = True
        detail = self.check_clean(report="full")
        self.assertEqual(detail["metrics"]["details"]["fuller"]["found"], 1)
        self.assertEqual(detail["metrics"]["details"]["badge"]["found"], 1)
        self.record_continue(
            detail,
            evidence="isolated fuller cut and badge projection match their reference targets",
            reviewed=["fuller", "badge"],
        )
        finish = self.check_clean(report="full")
        self.assertFalse(any(v["gate"] == "hygiene" for v in finish["violations"]))

    def test_disabled_detail_modifier_does_not_anchor(self):
        self.to_detail()
        blade = next(n for n in self.fake.scene["nodes"] if n["name"] == "blade")
        self.add_modifier(blade, "fuller_groove", "Edit_Poly", enabled=False)
        dirty = b.builder_gate(action="check", name="test")
        self.assertTrue(any(v["gate"] == "detail" for v in dirty["violations"]))
        blade["modenabled"][-1] = True
        self.check_clean()

    def test_full_pipeline_requires_reviewed_detail_and_finishes_cleanly(self):
        self.set_raw_blockout()
        self.advance()
        self.add_form_work()
        self.advance()
        self.assign_materials()
        self.advance()

        blade = next(n for n in self.fake.scene["nodes"] if n["name"] == "blade")
        self.add_modifier(blade, "fuller_groove", "Edit_Poly")
        detail_check = self.check_clean()
        self.record_continue(
            detail_check,
            evidence="isolated fuller groove matches the reference depth and taper",
            reviewed=["fuller"],
        )

        self.fake.scene["scene_roots"] = [
            {"name": "review_camera", "class": "Freecamera"}
        ]
        litter = b.builder_gate(action="check", name="test")
        self.assertTrue(any("session litter" in v["message"] for v in litter["violations"]))
        self.fake.scene["scene_roots"] = []

        self.fake.scene["root_layer"] = "Default"
        off_layer_root = b.builder_gate(action="check", name="test")
        self.assertTrue(any("builder root is not" in v["message"] for v in off_layer_root["violations"]))
        self.fake.scene["root_layer"] = "_builder"

        finish = self.check_clean()
        completed = self.record_continue(
            finish,
            evidence="final grid preserves silhouette, identity detail, material read, and hygiene",
        )
        self.assertTrue(completed["completed"])
        self.assertEqual(completed["pass"], "complete")

        resumed = self.start()
        self.assertTrue(resumed["resumed"])
        self.assertEqual(resumed["next"]["do"], "present-final")
        final_review = resumed["state"]["final_review"]
        self.assertEqual(final_review["review_id"], finish["review_id"])
        self.assertEqual(final_review["views"], b.PASS_VIEWS["finish"])
        self.assertEqual(final_review["visual_score"], 0.9)
        self.assertTrue(os.path.isfile(final_review["file"]))

        history = self.ledger()["state"]["history"]
        verdicts = [item for item in history if item.get("event") == "verdict"]
        self.assertEqual(len([v for v in verdicts if v.get("verdict") == "continue"]), 5)
        self.assertTrue(all(v.get("review_id") for v in verdicts))
        self.assertTrue(all(v.get("visual_score", 0) >= 0.8 for v in verdicts))

    def test_finish_minimum_triangle_floor_detects_underbuild(self):
        spec = copy.deepcopy(VALID_SPEC)
        spec["budget"] = {"tris": 20000, "min_tris": 4000}
        b.builder_session(action="abandon", name="test", delete_nodes=True)
        self.start_and_spec(spec)
        self.set_raw_blockout()
        self.advance()
        self.add_form_work()
        self.advance()
        self.assign_materials()
        self.advance()
        blade = next(n for n in self.fake.scene["nodes"] if n["name"] == "blade")
        self.add_modifier(blade, "fuller_groove", "Edit_Poly")
        detail = self.check_clean()
        self.record_continue(detail, reviewed=["fuller"])

        underbuilt = b.builder_gate(action="check", name="test")
        self.assertTrue(any("min_tris" in v["message"] for v in underbuilt["violations"]))
        for item in self.fake.scene["nodes"]:
            item["tris"] = 2000
        self.check_clean()


class TestParamCompare(unittest.TestCase):
    def test_float_tolerance(self):
        self.assertTrue(b._compare_param(0.25, "0.26"))
        self.assertFalse(b._compare_param(0.25, "0.8"))

    def test_color_triplet(self):
        self.assertTrue(b._compare_param([30, 30, 40], "(color 30 30 42)"))
        self.assertFalse(b._compare_param([30, 30, 40], "(color 200 30 40)"))

    def test_string_fallback(self):
        self.assertTrue(b._compare_param("Metal", "metal"))
        self.assertFalse(b._compare_param("Metal", "plastic"))

    def test_compact_modifier_tokens_round_trip_commas_and_percents(self):
        self.assertEqual(b._decode_list_token("groove%2C left%25deep"), "groove, left%deep")


if __name__ == "__main__":
    unittest.main()
