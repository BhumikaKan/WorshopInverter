import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

import main


class LoadManagerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.old_path = main.DB_PATH
        main.DB_PATH = Path(self.folder.name) / "test.db"
        self.client = TestClient(main.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        main.DB_PATH = self.old_path
        self.folder.cleanup()

    def register(self, name, watts, priority):
        response = self.client.post(
            "/api/applianceForm",
            json={"name": name, "wattage": watts, "priority": priority},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["appliance"]["id"]

    def switch(self, appliance_id, action, status=200):
        response = self.client.post(f"/api/appliances/{appliance_id}/{action}")
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def picture(self):
        response = self.client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        picture = response.json()
        self.assertLessEqual(picture["current_load"], 800)
        return picture

    def test_pdf_walkthrough_and_restart(self):
        fridge = self.register("Fridge", 300, 1)
        television = self.register("TV", 200, 2)
        fan = self.register("Fan", 100, 3)
        iron = self.register("Iron", 700, 2)
        for appliance_id in [fridge, television, fan]:
            self.switch(appliance_id, "on")
        before = self.picture()
        self.assertEqual(before["current_load"], 600)
        self.switch(iron, "on", 409)
        self.assertEqual(self.picture(), before)
        self.switch(television, "off")
        self.assertEqual(self.picture()["current_load"], 400)
        self.switch(fridge, "off")
        self.switch(iron, "on")
        self.assertEqual(self.picture()["current_load"], 800)
        result = self.switch(fridge, "on")
        self.assertIn("Iron shed", result["message"])
        self.switch(fan, "off")
        before_restart = self.picture()
        self.assertEqual(before_restart["current_load"], 300)
        self.assertEqual(
            [item["state"] for item in before_restart["appliances"]],
            ["running", "off", "off", "shed"],
        )
        self.client.__exit__(None, None, None)
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.assertEqual(self.picture(), before_restart)

    def test_restore_and_cancel_waiting(self):
        fan = self.register("Fan", 400, 3)
        fridge = self.register("Fridge", 500, 1)
        self.switch(fan, "on")
        self.switch(fridge, "on")
        self.assertEqual(self.picture()["appliances"][0]["state"], "shed")
        result = self.switch(fridge, "off")
        self.assertIn("Fan restored", result["message"])
        self.switch(fridge, "on")
        self.switch(fan, "off")
        self.switch(fridge, "off")
        self.assertEqual(self.picture()["current_load"], 0)
        self.assertTrue(all(item["state"] == "off" for item in self.picture()["appliances"]))

    def test_delete_restores_and_missing_id_returns_404(self):
        fan = self.register("Fan", 400, 3)
        fridge = self.register("Fridge", 500, 1)
        self.switch(fan, "on")
        self.switch(fridge, "on")
        response = self.client.delete(f"/api/appliances/{fridge}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Fan restored", response.json()["message"])
        self.assertEqual(self.picture()["current_load"], 400)
        self.assertEqual(self.client.delete(f"/api/appliances/{fridge}").status_code, 404)

    def test_restore_priority_ties_and_skip_non_fitting(self):
        large = self.register("Large", 600, 2)
        small = self.register("Small", 200, 3)
        urgent = self.register("Urgent", 500, 1)
        unused = self.register("Unused", 10, 4)
        self.switch(large, "on")
        self.switch(small, "on")
        self.switch(urgent, "on")
        self.client.delete(f"/api/appliances/{unused}")
        picture = self.picture()
        self.assertEqual(picture["appliances"][0]["state"], "shed")
        self.assertEqual(picture["appliances"][1]["state"], "running")
        self.assertEqual(picture["current_load"], 700)

    def test_equal_priority_restores_oldest_first(self):
        first = self.register("First", 400, 2)
        second = self.register("Second", 400, 2)
        urgent = self.register("Urgent", 800, 1)
        self.switch(first, "on")
        self.switch(second, "on")
        self.switch(urgent, "on")
        result = self.switch(urgent, "off")
        self.assertLess(
            result["message"].index("First restored"),
            result["message"].index("Second restored"),
        )

    def test_lowest_priority_sheds_first(self):
        important = self.register("Important", 350, 2)
        medium = self.register("Medium", 250, 3)
        lowest = self.register("Lowest", 200, 4)
        urgent = self.register("Urgent", 100, 1)
        for appliance_id in [important, medium, lowest]:
            self.switch(appliance_id, "on")
        result = self.switch(urgent, "on")
        self.assertEqual(
            [item["state"] for item in result["appliances"]],
            ["running", "running", "shed", "running"],
        )

    def test_fractional_watts_do_not_cross_limit(self):
        first = self.register("First", 799.99, 1)
        second = self.register("Second", 0.01, 1)
        extra = self.register("Extra", 0.01, 1)
        self.switch(first, "on")
        self.switch(second, "on")
        self.assertEqual(self.picture()["current_load"], 800)
        before = self.picture()
        self.switch(extra, "on", 409)
        self.assertEqual(self.picture(), before)

    def test_concurrent_equal_priority_requests(self):
        first = self.register("First", 500, 2)
        second = self.register("Second", 500, 2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(
                lambda appliance_id: self.client.post(f"/api/appliances/{appliance_id}/on"),
                [first, second],
            ))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        self.assertEqual(self.picture()["current_load"], 500)

    def test_validation_cors_and_oversized_appliance(self):
        for priority in [0, -1, 1.5, "high", True]:
            response = self.client.post(
                "/api/applianceForm",
                json={"name": "Fan", "wattage": 100, "priority": priority},
            )
            self.assertEqual(response.status_code, 422)
        appliance_id = self.register("Too large", 801, 1)
        self.switch(appliance_id, "on", 409)
        self.assertEqual(self.picture()["current_load"], 0)
        response = self.client.options(
            "/api/appliances/1",
            headers={
                "Origin": "http://localhost:3001",
                "Access-Control-Request-Method": "DELETE",
            },
        )
        self.assertEqual(response.status_code, 200)

    def test_legacy_migration(self):
        legacy_path = Path(self.folder.name) / "legacy.db"
        with closing(sqlite3.connect(legacy_path)) as connection, connection:
            connection.execute(
                "CREATE TABLE appliances (id INTEGER PRIMARY KEY, name TEXT, "
                "wattage REAL, priority INTEGER, is_on INTEGER DEFAULT 0)"
            )
            connection.executemany(
                "INSERT INTO appliances VALUES (?, ?, ?, ?, ?)",
                [(1, "Fridge", 300, 1, 1), (2, "Fan", 100, 3, 0)],
            )
        main.DB_PATH = legacy_path
        with TestClient(main.app) as client:
            states = [item["state"] for item in client.get("/api/state").json()["appliances"]]
        self.assertEqual(states, ["running", "off"])


if __name__ == "__main__":
    unittest.main()