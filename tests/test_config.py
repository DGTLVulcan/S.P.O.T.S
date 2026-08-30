"""config.yaml loading, .env parsing, and environment overrides."""

import os
import sys

# Import the app regardless of where the runner sets its working directory
# (Visual Studio Test Explorer, `python -m unittest`, and pytest all differ).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import os
import shutil
import tempfile
import unittest

import yaml

from spots.config import Settings, load_dotenv


class DotenvTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.saved_env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved_env)
        shutil.rmtree(self.dir, ignore_errors=True)

    def write_env(self, text):
        path = os.path.join(self.dir, ".env")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_parses_pairs_comments_and_quotes(self):
        path = self.write_env(
            "# a comment\n"
            "\n"
            "SPOTS_TEST_PLAIN=value\n"
            "SPOTS_TEST_QUOTED=\"quoted value\"\n"
            "SPOTS_TEST_SINGLE='single'\n"
            "export SPOTS_TEST_EXPORTED=exported\n"
            "SPOTS_TEST_SPACES  =  padded  \n"
            "not_a_pair\n"
        )
        applied = load_dotenv(path)
        self.assertEqual(applied["SPOTS_TEST_PLAIN"], "value")
        self.assertEqual(applied["SPOTS_TEST_QUOTED"], "quoted value")
        self.assertEqual(applied["SPOTS_TEST_SINGLE"], "single")
        self.assertEqual(applied["SPOTS_TEST_EXPORTED"], "exported")
        self.assertEqual(applied["SPOTS_TEST_SPACES"], "padded")
        self.assertNotIn("not_a_pair", applied)

    def test_real_environment_wins(self):
        os.environ["SPOTS_TEST_PLAIN"] = "from-shell"
        path = self.write_env("SPOTS_TEST_PLAIN=from-file\n")
        load_dotenv(path)
        self.assertEqual(os.environ["SPOTS_TEST_PLAIN"], "from-shell")

    def test_missing_file_is_not_an_error(self):
        self.assertEqual(load_dotenv(os.path.join(self.dir, "nope.env")), {})


class EnvOverrideTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.saved_env = dict(os.environ)
        self.config_path = os.path.join(self.dir, "config.yaml")
        with open(self.config_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(
                {
                    "camera": {"source": "zcam", "ip": "192.168.10.192"},
                    "web": {"host": "0.0.0.0", "port": 8080},
                    "storage": {"db_path": "spots.db", "snapshot_dir": "snapshots"},
                },
                handle,
            )
        for key in list(os.environ):
            if key.startswith("SPOTS_"):
                del os.environ[key]

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved_env)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_without_env_the_file_wins(self):
        settings = Settings.load(self.config_path, env_path=None)
        self.assertEqual(settings.camera.source, "zcam")
        self.assertEqual(settings.web.port, 8080)

    def test_env_overrides_are_applied_and_typed(self):
        os.environ["SPOTS_CAMERA_SOURCE"] = "synthetic"
        os.environ["SPOTS_WEB_PORT"] = "5001"
        os.environ["SPOTS_WEB_STREAM_FPS"] = "15.5"
        settings = Settings.load(self.config_path, env_path=None)
        self.assertEqual(settings.camera.source, "synthetic")
        self.assertEqual(settings.web.port, 5001)
        self.assertIsInstance(settings.web.port, int)
        self.assertAlmostEqual(settings.web.stream_fps, 15.5)

    def test_unparseable_override_is_ignored_not_fatal(self):
        os.environ["SPOTS_WEB_PORT"] = "not-a-number"
        settings = Settings.load(self.config_path, env_path=None)
        self.assertEqual(settings.web.port, 8080)

    def test_saving_does_not_write_env_overrides_into_the_file(self):
        """The whole point of .env is local-only overrides; if saving from
        the Settings page baked them into config.yaml, a dev machine's
        synthetic camera would follow the config to the Pi."""
        os.environ["SPOTS_CAMERA_SOURCE"] = "synthetic"
        settings = Settings.load(self.config_path, env_path=None)
        self.assertEqual(settings.camera.source, "synthetic")

        settings.target.width_units = 42.0  # an ordinary edit from the UI
        settings.save(self.config_path)

        with open(self.config_path, "r", encoding="utf-8") as handle:
            written = yaml.safe_load(handle)
        self.assertEqual(written["camera"]["source"], "zcam", "env override leaked into config.yaml")
        self.assertAlmostEqual(written["target"]["width_units"], 42.0)
        # ...and the running app still uses the override.
        self.assertEqual(settings.camera.source, "synthetic")

    def test_spots_config_selects_the_file(self):
        other = os.path.join(self.dir, "other.yaml")
        with open(other, "w", encoding="utf-8") as handle:
            yaml.safe_dump({"web": {"port": 9999}}, handle)
        os.environ["SPOTS_CONFIG"] = other
        self.assertEqual(Settings.load(env_path=None).web.port, 9999)


if __name__ == "__main__":
    unittest.main()
