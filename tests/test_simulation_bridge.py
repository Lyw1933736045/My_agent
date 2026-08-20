import json
import tempfile
import unittest
from pathlib import Path

from My_agent.simulation_bridge import SimulationBridge, SimulationBridgeError


class SimulationBridgeWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.artifacts = Path(self.tmp.name)
        self.bridge = SimulationBridge()
        self.bridge.artifacts = self.artifacts
        self.bridge.python = Path("/missing/python")

    def tearDown(self):
        self.tmp.cleanup()

    def test_prefers_existing_case_key_artifacts(self):
        seed_dir = self.artifacts / "case1"
        seed_dir.mkdir()
        (seed_dir / "seed.json").write_text(
            json.dumps({"case_id": "case1", "facts": []}), encoding="utf-8"
        )
        ref = self.bridge.artifact_ref_for("case1", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        self.assertEqual(ref, "case1")

    def test_new_brief_uses_case_key_not_case1(self):
        ref = self.bridge.artifact_ref_for("case-unitree", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        self.assertEqual(ref, "case-unitree")

    def test_overview_without_graph_is_empty_not_error(self):
        payload = self.bridge.overview("case-unitree")
        self.assertFalse(payload["graph_ready"])
        self.assertFalse(payload["can_simulate"])
        self.assertIsNone(payload["latest_run"])

    def test_wipe_removes_seed_and_runs(self):
        case_dir = self.artifacts / "case-x"
        run_dir = self.artifacts / "cases" / "case-x" / "runs" / "sim_1"
        case_dir.mkdir()
        run_dir.mkdir(parents=True)
        (case_dir / "seed.json").write_text("{}", encoding="utf-8")
        (run_dir / "run_state.json").write_text("{}", encoding="utf-8")
        self.bridge.wipe_case_artifacts("case-x")
        self.assertFalse(case_dir.exists())
        self.assertFalse((self.artifacts / "cases" / "case-x").exists())

    def test_run_dir_points_at_simulation_folder(self):
        run_dir = self.artifacts / "cases" / "case-x" / "runs" / "sim_abc"
        run_dir.mkdir(parents=True)
        self.assertEqual(self.bridge.run_dir("case-x", "sim_abc"), run_dir)

    def test_retain_only_run_deletes_previous_runs(self):
        runs = self.artifacts / "cases" / "case-x" / "runs"
        old_run = runs / "sim_old"
        new_run = runs / "sim_new"
        old_run.mkdir(parents=True)
        new_run.mkdir()
        (old_run / "run_state.json").write_text("{}", encoding="utf-8")
        (new_run / "run_state.json").write_text("{}", encoding="utf-8")
        self.bridge._retain_only_run("case-x", "sim_new")
        self.assertFalse(old_run.exists())
        self.assertTrue(new_run.exists())

    def test_start_graph_build_rejects_short_question(self):
        self.bridge.database_url = "postgresql://example/db"
        with self.assertRaises(SimulationBridgeError):
            self.bridge.start_graph_build(
                "case-x",
                question="短",
                as_of="2026-08-19T18:00:00+08:00",
                horizon_hours=48,
                source_case="id",
            )


if __name__ == "__main__":
    unittest.main()
