import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import patch

import memory_tools
import server_time


class MemoryToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.memory_root = Path(self.temporary_directory.name)
        self.memory_patch = patch.object(memory_tools, "MEMORY_ROOT", self.memory_root)
        self.memory_patch.start()
        self.addCleanup(self.memory_patch.stop)
        self.addCleanup(self.temporary_directory.cleanup)

    def test_update_creates_dynamic_topic_and_retrieval_finds_it(self) -> None:
        result = memory_tools.dispatch(
            "update_memory",
            {
                "category": "tech",
                "topic": "dsa/progress",
                "content": "Practicing graph traversal.",
                "description": "Data structures and algorithms progress.",
            },
        )

        self.assertEqual(result, "Updated memory: tech/dsa/progress.md")
        self.assertEqual(
            memory_tools.read_memory({"path": "tech/dsa/progress.md"}),
            "Practicing graph traversal.\n",
        )
        matches = json.loads(memory_tools.retrieve_memory({"query": "graph algorithms"}))
        self.assertEqual(matches[0]["path"], "tech/dsa/progress.md")
        index = json.loads((self.memory_root / memory_tools.INDEX_NAME).read_text())
        self.assertEqual(index["entries"][0]["category"], "tech")

    def test_recent_history_retains_only_twenty_messages(self) -> None:
        for number in range(25):
            memory_tools.append_recent_history(
                {"category": "tech", "role": "user", "content": f"message {number}"}
            )

        history = json.loads((self.memory_root / "tech/recentHistory.json").read_text())
        self.assertEqual(len(history), 20)
        self.assertEqual(history[0]["content"], "message 5")
        self.assertEqual(history[-1]["content"], "message 24")

    def test_memory_timestamps_use_server_local_timezone(self) -> None:
        local_now = datetime(2026, 11, 9, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata"))
        with patch.object(server_time, "local_now", return_value=local_now):
            memory_tools.update_memory(
                {
                    "category": "tech",
                    "topic": "progress",
                    "content": "Working locally.",
                    "description": "Progress update.",
                }
            )
            memory_tools.append_recent_history(
                {"category": "tech", "role": "user", "content": "Remember this."}
            )

        index = json.loads((self.memory_root / memory_tools.INDEX_NAME).read_text())
        history = json.loads((self.memory_root / "tech/recentHistory.json").read_text())
        index_timestamp = datetime.fromisoformat(index["entries"][0]["updated_at"])
        history_timestamp = datetime.fromisoformat(history[0]["timestamp"])

        self.assertEqual(index_timestamp, local_now)
        self.assertEqual(history_timestamp, local_now)

    def test_person_summary_is_created_and_indexed(self) -> None:
        result = memory_tools.update_person_summary({"content": "Prefers concise answers."})

        self.assertEqual(result, "Updated memory: personSummary.txt")
        self.assertEqual(
            memory_tools.read_memory({"path": "personSummary.txt"}),
            "Prefers concise answers.\n",
        )
        matches = json.loads(memory_tools.retrieve_memory({"query": "concise answers"}))
        self.assertEqual(matches[0]["path"], "personSummary.txt")

    def test_update_person_summary_guides_read_before_full_replacement(self) -> None:
        tool = next(
            tool
            for tool in memory_tools.TOOLS
            if tool["function"]["name"] == "update_person_summary"
        )
        description = tool["function"]["description"]
        content_description = tool["function"]["parameters"]["properties"]["content"][
            "description"
        ]

        self.assertIn("read_memory", description)
        self.assertIn("personSummary.txt", description)
        self.assertIn("preserve relevant facts", description)
        self.assertIn("complete revised summary", content_description)

    def test_memory_write_tool_descriptions_discourage_overcollection(self) -> None:
        descriptions = {
            tool["function"]["name"]: tool["function"]["description"]
            for tool in memory_tools.TOOLS
        }

        self.assertIn("Do not save routine messages or sessions", descriptions["update_memory"])
        self.assertIn("not every message or session", descriptions["update_person_summary"])
        self.assertIn("do not log every exchange or session", descriptions["append_recent_history"])
        self.assertIn("distinct ongoing topic", descriptions["append_recent_history"])

    def test_invalid_path_and_missing_file_are_reported(self) -> None:
        traversal_result = memory_tools.dispatch(
            "update_memory",
            {
                "category": "../outside",
                "topic": "secret",
                "content": "no",
                "description": "no",
            },
        )
        missing_result = memory_tools.dispatch("read_memory", {"path": "missing.md"})

        self.assertTrue(traversal_result.startswith("error:"))
        self.assertIn("invalid category segment", traversal_result)
        self.assertTrue(missing_result.startswith("error:"))
        self.assertIn("memory file not found", missing_result)
        self.assertFalse((self.memory_root.parent / "outside").exists())

    def test_invalid_tool_json_is_reported(self) -> None:
        result = memory_tools.dispatch("read_memory", "{")

        self.assertTrue(result.startswith("error:"))
        self.assertIn("invalid JSON", result)

    def test_symlink_cannot_escape_memory_root(self) -> None:
        outside_temp = tempfile.TemporaryDirectory()
        self.addCleanup(outside_temp.cleanup)
        outside_directory = Path(outside_temp.name)
        (self.memory_root / "escape").symlink_to(outside_directory, target_is_directory=True)

        result = memory_tools.dispatch(
            "update_memory",
            {
                "category": "escape",
                "topic": "secret",
                "content": "not written",
                "description": "test",
            },
        )

        self.assertTrue(result.startswith("error:"))
        self.assertIn("outside the memory folder", result)
        self.assertFalse((outside_directory / "secret.md").exists())


if __name__ == "__main__":
    unittest.main()
