"""Builder-mode tests against a faked bridge: spec floors, census parsing,
every gate family, pass gating, and ledger round-tripping."""

import json
import os
import re
import tempfile
import unittest

import src.tools.builder as b


def node(name, dims, pos, mat="", matclass="", mods=(), layer="_builder",
         cls="Box", sup="GeometryClass", tris=12, scale=(1, 1, 1)):
    return {
        "name": name, "class": cls, "super": sup, "parent": "BLD_test", "layer": layer,
        "pos": pos, "dims": dims, "mat": mat, "matclass": matclass,
        "mods": list(mods), "tris": tris, "scale": list(scale),
    }


VALID_SPEC = {
    "complexity": "simple",
    "components": [
        {"name": "blade", "dims": [3, 0.5, 30], "material": "steel",
         "symmetry": "x", "ratios": {"handle": 3.0}},
        {"name": "guard", "dims": [5, 1.5, 1], "material": "steel",
         "touches": ["blade", "handle"]},
        {"name": "handle", "dims": [3, 3, 10], "material": "steel", "ground": True},
    ],
    "materials": [{"name": "steel", "class": "PhysicalMaterial", "params": {"roughness": 0.25}}],
    "details": [{"id": "fuller", "on": "blade", "via": "modifier"}],
    "budget": {"tris": 20000},
}


class FakeClient:
    """Emulates the bridge for builder scripts from a python scene dict."""

    def __init__(self):
        self.scene = {
            "appdata": None, "nodes": [], "maps": {}, "mparams": {},
            "scene_roots": [],
        }

    def _census_text(self):
        lines = ["ROOT|BLD_test|0.0,0.0,0.0"]
        for n in self.scene["nodes"]:
            px, py, pz = n["pos"]
            dx, dy, dz = n["dims"]
            lines.append(
                "NODE|{0}|{1}|{2}|{3}|{4}|{5},{6},{7}|{8},{9},{10}|{11},{12},{13}|{14}|{15}|{16}|{17}|{18},{19},{20}".format(
                    n["name"], n["class"], n["super"], n["parent"], n["layer"],
                    px, py, pz, px - dx / 2, py - dy / 2, pz,
                    px + dx / 2, py + dy / 2, pz + dz, n["tris"],
                    n["mat"], n["matclass"], ",".join(n["mods"]),
                    n["scale"][0], n["scale"][1], n["scale"][2],
                )
            )
            for m in self.scene["maps"].get(n["name"].lower(), []):
                lines.append(f"MAP|{n['name']}|{m['name']}|{m['class']}")
        for (mat, key), val in self.scene["mparams"].items():
            lines.append(f"MPARAM|{mat}|{key}|{val}")
        for s in self.scene["scene_roots"]:
            lines.append(f"SROOT|{s['name']}|{s['class']}")
        lines.append("LEDGER|" + (self.scene["appdata"] or ""))
        return "\n".join(lines)

    def send_command(self, script, cmd_type=None, **kwargs):
        if cmd_type == "native:capture_multi_view":
            fd, path = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            return {"result": json.dumps({"file": path, "views": ["front"]})}
        if "setAppData root" in script:
            m = re.search(r'setAppData root \d+ "(.*)"\s*\n\s*"OK"', script, re.DOTALL)
            self.scene["appdata"] = m.group(1).replace('\\"', '"').replace("\\\\", "\\")
            return {"result": "OK"}
        if "Dummy name:" in script:
            roots = "|".join(s["name"] for s in self.scene["scene_roots"])
            if self.scene["appdata"]:
                return {"result": f"resumed\n{roots}\n" + self.scene["appdata"]}
            return {"result": f"created\n{roots}\n"}
        if "deleteAppData root" in script and 'format "NODE|' not in script:
            self.scene["appdata"] = None
            return {"result": "OK"}
        if 'format "NODE|' in script:
            return {"result": self._census_text()}
        return {"result": ""}


class BuilderTestCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeClient()
        self._real_client = b.client
        b.client = self.fake

    def tearDown(self):
        b.client = self._real_client

    def start(self, complexity="simple"):
        return b.builder_session(action="start", name="test", complexity=complexity)


class TestSpecValidation(BuilderTestCase):
    def test_start_lands_on_spec_pass(self):
        r = self.start()
        self.assertEqual(r["state"]["pass"], "spec")

    def test_shallow_spec_rejected_by_complexity_floor(self):
        self.start()
        shallow = dict(VALID_SPEC, complexity="moderate")
        r = b.builder_session(action="spec", name="test", spec=shallow)
        self.assertFalse(r["valid"])
        self.assertTrue(any("complexity needs" in v["message"] for v in r["violations"]))

    def test_relational_constraint_required_at_moderate(self):
        self.start()
        spec = json.loads(json.dumps(VALID_SPEC))
        spec["complexity"] = "moderate"
        spec["components"] += [
            {"name": f"part{i}", "dims": [1, 1, 1], "material": "steel"} for i in range(4)
        ]
        spec["details"] += [
            {"id": f"det{i}", "on": "blade", "via": "map"} for i in range(5)
        ]
        r = b.builder_session(action="spec", name="test", spec=spec)
        self.assertFalse(r["valid"])
        self.assertTrue(any("relational constraint" in v["message"] for v in r["violations"]))

    def test_valid_spec_unlocks_blockout(self):
        self.start()
        r = b.builder_session(action="spec", name="test", spec=VALID_SPEC)
        self.assertTrue(r["valid"])
        self.assertEqual(r["pass"], "blockout")

    def test_bad_refs_rejected(self):
        self.start()
        spec = json.loads(json.dumps(VALID_SPEC))
        spec["components"][0]["material"] = "gold"
        spec["details"][0]["on"] = "pommel"
        r = b.builder_session(action="spec", name="test", spec=spec)
        self.assertFalse(r["valid"])
        messages = " / ".join(v["message"] for v in r["violations"])
        self.assertIn("gold", messages)
        self.assertIn("pommel", messages)

    def test_spec_as_json_string_accepted(self):
        self.start()
        r = b.builder_session(action="spec", name="test", spec=json.dumps(VALID_SPEC))
        self.assertTrue(r["valid"])


class TestPipeline(BuilderTestCase):
    def setUp(self):
        super().setUp()
        self.start()
        b.builder_session(action="spec", name="test", spec=VALID_SPEC)

    def blockout(self, blade_height=30):
        self.fake.scene["nodes"] = [
            node("handle", (3, 3, 10), (0, 0, 0)),
            node("guard", (5, 1.5, 1), (0, 0, 10)),
            node("blade", (3, 0.5, blade_height), (0, 0, 11)),
        ]

    def advance(self, evidence="grid vs reference judged: matches within tolerance"):
        r = b.builder_gate(action="check", name="test")
        self.assertTrue(r["clean"], r.get("violations"))
        r = b.builder_gate(action="record", name="test", verdict="continue", evidence=evidence)
        self.assertNotIn("error", r)
        return r

    def test_empty_scene_fails_coverage(self):
        r = b.builder_gate(action="check", name="test")
        self.assertFalse(r["clean"])
        self.assertEqual(sum(1 for v in r["violations"] if v["gate"] == "coverage"), 3)
        self.assertNotIn("capture", r, "no capture while dirty")

    def test_proportion_and_ratio_flagged(self):
        self.blockout(blade_height=40)
        r = b.builder_gate(action="check", name="test")
        blade = [v for v in r["violations"] if v.get("component") == "blade"]
        self.assertEqual(len(blade), 2)
        self.assertTrue(all(v["gate"] == "proportion" for v in blade))

    def test_continue_refused_while_dirty_and_without_check(self):
        self.blockout(blade_height=40)
        b.builder_gate(action="check", name="test")
        r = b.builder_gate(action="record", name="test", verdict="continue",
                           evidence="attempting to advance past a dirty gate")
        self.assertEqual(r["status"], "error")
        self.blockout()  # fixed, but no clean check recorded yet
        r = b.builder_gate(action="record", name="test", verdict="continue",
                           evidence="fixed the blade but skipped the check step")
        self.assertEqual(r["status"], "error")

    def test_thin_evidence_rejected(self):
        self.blockout()
        b.builder_gate(action="check", name="test")
        r = b.builder_gate(action="record", name="test", verdict="continue", evidence="ok")
        self.assertEqual(r["status"], "error")

    def test_clean_check_captures_and_attempts_escalate(self):
        self.blockout(blade_height=40)
        for expected in (1, 2, 3):
            r = b.builder_gate(action="check", name="test")
            self.assertEqual(r["attempts"], expected)
        self.assertIn("request-input", r["hint"]["message"])
        self.blockout()
        r = b.builder_gate(action="check", name="test")
        self.assertTrue(r["clean"])
        self.assertTrue(os.path.isfile(r["capture"]["file"]))

    def test_full_pipeline_to_complete(self):
        scene = self.fake.scene
        self.blockout()
        self.advance()  # blockout -> form

        scene["nodes"][0]["scale"] = [1.5, 1, 1]
        r = b.builder_gate(action="check", name="test")
        self.assertTrue(any(v["gate"] == "degenerate" for v in r["violations"]))
        scene["nodes"][0] = node("handle", (3, 3, 10), (0, 0, 0))
        self.advance()  # form -> material

        r = b.builder_gate(action="check", name="test")
        self.assertTrue(any(v["gate"] == "material" for v in r["violations"]))
        for n in scene["nodes"]:
            n["mat"], n["matclass"] = "steel", "PhysicalMaterial"
        scene["mparams"][("steel", "roughness")] = "0.8"
        r = b.builder_gate(action="check", name="test")
        self.assertTrue(any("roughness" in v["message"] for v in r["violations"]))
        scene["mparams"][("steel", "roughness")] = "0.25"
        self.advance()  # material -> detail

        r = b.builder_gate(action="check", name="test")
        self.assertTrue(any(v["gate"] == "detail" for v in r["violations"]))
        scene["nodes"][2]["mods"] = ["fuller_groove"]
        self.advance(evidence="isolated blade: fuller groove runs the upper third, "
                              "depth and taper match the reference")  # detail -> finish

        scene["nodes"].append(
            node("scratch_helper", (1, 1, 1), (5, 5, 0), layer="0", tris=99999))
        r = b.builder_gate(action="check", name="test")
        gates = {v["gate"] for v in r["violations"]}
        self.assertLessEqual({"budget", "hygiene"}, gates)
        scene["nodes"].pop()
        r = self.advance(evidence="final grid vs reference: matches; budget and hygiene clean")
        self.assertTrue(r["completed"])

        r = b.builder_gate(action="check", name="test")
        self.assertEqual(r["status"], "error")

        history = json.loads(scene["appdata"])["state"]["history"]
        verdicts = [h for h in history if h.get("event") == "verdict" and h.get("evidence")]
        self.assertGreaterEqual(len(verdicts), 5)

    def test_hedge_words_refuse_continue(self):
        self.blockout()
        b.builder_gate(action="check", name="test")
        for hedge in ("proportions are stylized but fine",
                      "boxes are placeholder masses for now",
                      "chunky but acceptable for blockout"):
            r = b.builder_gate(action="record", name="test", verdict="continue", evidence=hedge)
            self.assertEqual(r["status"], "error", hedge)
            self.assertIn("refine words", r["error"])

    def test_hedge_words_allowed_on_refine_verdicts(self):
        self.blockout()
        b.builder_gate(action="check", name="test")
        r = b.builder_gate(action="record", name="test", verdict="refine-scene",
                           evidence="grip reads as a placeholder box, not the reference's raked grip")
        self.assertEqual(r["pass"], "blockout")

    def test_detail_evidence_must_name_every_id(self):
        scene = self.fake.scene
        self.blockout()
        self.advance()
        self.advance()  # form
        for n in scene["nodes"]:
            n["mat"], n["matclass"] = "steel", "PhysicalMaterial"
        scene["mparams"][("steel", "roughness")] = "0.25"
        self.advance()  # material -> detail
        scene["nodes"][2]["mods"] = ["fuller_groove"]
        r = b.builder_gate(action="check", name="test")
        self.assertEqual(r["details_to_review"], ["fuller"])
        r = b.builder_gate(action="record", name="test", verdict="continue",
                           evidence="everything present and matching the reference nicely")
        self.assertEqual(r["status"], "error")
        self.assertIn("fuller", r["error"])

    def test_scene_litter_gate(self):
        scene = self.fake.scene
        self.blockout()
        for _ in range(4):  # blockout..finish, detail needs the id named
            state = json.loads(scene["appdata"])["state"]["pass"]
            if state == "material":
                for n in scene["nodes"]:
                    n["mat"], n["matclass"] = "steel", "PhysicalMaterial"
                scene["mparams"][("steel", "roughness")] = "0.25"
            if state == "detail":
                scene["nodes"][2]["mods"] = ["fuller_groove"]
                self.advance(evidence="isolated blade: fuller groove matches reference depth")
                continue
            self.advance()
        scene["scene_roots"] = [{"name": "magwell_logo_cam", "class": "Freecamera"}]
        r = b.builder_gate(action="check", name="test")
        litter = [v for v in r["violations"] if "litter" in v["message"]]
        self.assertEqual(len(litter), 1)
        self.assertIn("magwell_logo_cam", litter[0]["message"])
        scene["scene_roots"] = []
        r = b.builder_gate(action="check", name="test")
        self.assertTrue(r["clean"], r.get("violations"))

    def test_litter_warns_before_finish(self):
        self.blockout()
        self.fake.scene["scene_roots"] = [{"name": "stray_cam", "class": "Freecamera"}]
        r = b.builder_gate(action="check", name="test")
        self.assertTrue(r["clean"])
        self.assertTrue(any("stray_cam" in w for w in r.get("warnings", [])))

    def test_preexisting_scene_roots_are_not_litter(self):
        self.fake.scene["scene_roots"] = [{"name": "studio_backdrop", "class": "Box"}]
        b.builder_session(action="abandon", name="test", delete_nodes=True)
        self.start()
        b.builder_session(action="spec", name="test", spec=VALID_SPEC)
        self.blockout()
        r = b.builder_gate(action="check", name="test")
        self.assertTrue(r["clean"], r.get("violations"))
        self.assertNotIn("warnings", r)

    def test_min_tris_floor(self):
        spec = json.loads(json.dumps(VALID_SPEC))
        spec["budget"] = {"tris": 20000, "min_tris": 4000}
        r = b.builder_session(action="spec", name="test", spec=spec)
        self.assertTrue(r["valid"], r.get("violations"))
        scene = self.fake.scene
        self.blockout()
        for _ in range(4):
            state = json.loads(scene["appdata"])["state"]["pass"]
            if state == "material":
                for n in scene["nodes"]:
                    n["mat"], n["matclass"] = "steel", "PhysicalMaterial"
                scene["mparams"][("steel", "roughness")] = "0.25"
            if state == "detail":
                scene["nodes"][2]["mods"] = ["fuller_groove"]
                self.advance(evidence="isolated blade: fuller groove matches reference depth")
                continue
            self.advance()
        r = b.builder_gate(action="check", name="test")  # 36 tris total
        self.assertTrue(any("min_tris" in v["message"] for v in r["violations"]),
                        str(r["violations"]))
        for n in scene["nodes"]:
            n["tris"] = 2000
        r = b.builder_gate(action="check", name="test")
        self.assertTrue(r["clean"], r.get("violations"))

    def test_min_tris_must_be_below_budget(self):
        spec = json.loads(json.dumps(VALID_SPEC))
        spec["budget"] = {"tris": 1000, "min_tris": 5000}
        r = b.builder_session(action="spec", name="test", spec=spec)
        self.assertFalse(r["valid"])
        self.assertTrue(any("min_tris" in v["message"] for v in r["violations"]))

    def test_resume_and_abandon(self):
        self.blockout()
        r = b.builder_session(action="start", name="test")
        self.assertTrue(r["resumed"])
        r = b.builder_session(action="abandon", name="test", delete_nodes=True)
        self.assertTrue(r["abandoned"])
        self.assertIsNone(self.fake.scene["appdata"])


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


if __name__ == "__main__":
    unittest.main()
