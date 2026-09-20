import json
import os
import tempfile
import unittest

from robot_link.daemon import write_status


class DaemonStatusTest(unittest.TestCase):
    def test_writes_connection_and_voltage_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "robot-link", "status.json")
            write_status(path, {"connected": True, "voltage": 11.77})
            with open(path) as fh:
                status = json.load(fh)
            self.assertTrue(status["connected"])
            self.assertEqual(status["voltage"], 11.77)
            self.assertIn("updated", status)
            self.assertFalse(os.path.exists(path + ".tmp"))
