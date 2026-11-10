from __future__ import annotations

import unittest
from unittest.mock import patch

from savva.tools.system import SystemInspector


class SystemInspectorTests(unittest.TestCase):
    def test_summary_combines_hardware_and_disk_data(self) -> None:
        inspector = SystemInspector()
        with (
            patch.object(
                inspector,
                "_get_os",
                return_value={
                    "name": "Windows",
                    "release": "11",
                    "version": "10.0.22631",
                    "build": "22631",
                    "architecture": "AMD64",
                },
            ),
            patch.object(
                inspector,
                "_get_cpu",
                return_value={
                    "model": "Test CPU",
                    "physical_cores": 8,
                    "logical_processors": 16,
                    "threads": 16,
                },
            ),
            patch.object(
                inspector,
                "_get_memory",
                return_value={
                    "total_bytes": 32 * 1024**3,
                    "available_bytes": 16 * 1024**3,
                },
            ),
            patch.object(
                inspector, "_get_gpus", return_value=[{"name": "Test GPU"}]
            ),
            patch.object(
                inspector,
                "_get_disks",
                return_value=[
                    {"device": "C:\\", "total_bytes": 1000, "free_bytes": 400}
                ],
            ),
        ):
            result = inspector.summary()

        self.assertEqual(result["os"]["release"], "11")
        self.assertEqual(result["cpu"]["threads"], 16)
        self.assertEqual(result["gpus"][0]["name"], "Test GPU")
        self.assertEqual(result["disk_total_bytes"], 1000)
        self.assertEqual(result["disk_free_bytes"], 400)


if __name__ == "__main__":
    unittest.main()
