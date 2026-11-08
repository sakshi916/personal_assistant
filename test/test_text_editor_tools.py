import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apps import text_editor_tools


class ObsidianToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.vault_root = Path(self.temporary_directory.name)
        self.vault_patch = patch.object(text_editor_tools, "VAULT_ROOT", self.vault_root)
        self.vault_patch.start()
        self.addCleanup(self.vault_patch.stop)
        self.addCleanup(self.temporary_directory.cleanup)

    def test_create_read_and_edit_unique_passage(self) -> None:
        result = text_editor_tools.dispatch(
            "create_obsidian_document",
            {
                "path": "Stories/chapter-one.md",
                "content": "The door was blue.\nShe entered.\n",
            },
        )
        self.assertEqual(result, "Created Obsidian document: Stories/chapter-one.md")

        document = json.loads(
            text_editor_tools.dispatch(
                "read_obsidian_document", {"path": "Stories/chapter-one.md"}
            )
        )
        self.assertEqual(document["content"], "The door was blue.\nShe entered.\n")

        result = text_editor_tools.dispatch(
            "edit_obsidian_document",
            {
                "path": "Stories/chapter-one.md",
                "old_text": "The door was blue.",
                "new_text": "The door was red.",
            },
        )
        self.assertEqual(result, "Updated Obsidian document: Stories/chapter-one.md")
        self.assertEqual(
            (self.vault_root / "Stories/chapter-one.md").read_text(),
            "The door was red.\nShe entered.\n",
        )

    def test_edit_requires_an_exact_unique_match(self) -> None:
        path = self.vault_root / "draft.md"
        path.write_text("A repeated line.\nA repeated line.\n")

        result = text_editor_tools.dispatch(
            "edit_obsidian_document",
            {"path": "draft.md", "old_text": "A repeated line.", "new_text": "Changed."},
        )

        self.assertIn("occurs more than once", result)
        self.assertEqual(path.read_text(), "A repeated line.\nA repeated line.\n")

    def test_edit_preserves_crlf_line_endings(self) -> None:
        path = self.vault_root / "draft.md"
        path.write_bytes(b"First line.\r\nSecond line.\r\n")

        result = text_editor_tools.dispatch(
            "edit_obsidian_document",
            {
                "path": "draft.md",
                "old_text": "Second line.",
                "new_text": "A new line.",
            },
        )

        self.assertFalse(result.startswith("error:"))
        self.assertEqual(path.read_bytes(), b"First line.\r\nA new line.\r\n")

    def test_path_traversal_and_external_symlinks_are_rejected(self) -> None:
        outside = Path(self.temporary_directory.name).parent / (
            Path(self.temporary_directory.name).name + "-outside"
        )
        outside.mkdir()
        self.addCleanup(lambda: outside.rmdir())
        (self.vault_root / "escape.md").symlink_to(outside / "secret.md")

        traversal_result = text_editor_tools.dispatch(
            "create_obsidian_document",
            {"path": "../outside.md", "content": "no"},
        )
        symlink_result = text_editor_tools.dispatch(
            "read_obsidian_document", {"path": "escape.md"}
        )

        self.assertIn("cannot contain hidden, '.' or '..' segments", traversal_result)
        self.assertIn("outside the Obsidian vault", symlink_result)

    def test_list_and_search_exclude_hidden_obsidian_files(self) -> None:
        (self.vault_root / "Stories").mkdir()
        (self.vault_root / "Stories/chapter.md").write_text("The silver key.")
        (self.vault_root / ".obsidian").mkdir()
        (self.vault_root / ".obsidian/private.md").write_text("silver key")

        paths = json.loads(text_editor_tools.list_documents({}))
        matches = json.loads(
            text_editor_tools.search_documents({"query": "silver key"})
        )

        self.assertEqual(paths, ["Stories/chapter.md"])
        self.assertEqual([match["path"] for match in matches], ["Stories/chapter.md"])
        self.assertIn("The silver key.", matches[0]["excerpt"])

    def test_read_supports_bounded_ranges(self) -> None:
        (self.vault_root / "draft.md").write_text("0123456789")

        document = json.loads(
            text_editor_tools.read_document(
                {"path": "draft.md", "start": 3, "end": 7}
            )
        )

        self.assertEqual(document["content"], "3456")
        self.assertEqual((document["start"], document["end"]), (3, 7))

    def test_delete_requires_confirmation(self) -> None:
        path = self.vault_root / "draft.md"
        path.write_text("Keep me until confirmation.")

        declined = text_editor_tools.dispatch(
            "delete_obsidian_document", {"path": "draft.md", "confirm": False}
        )
        self.assertIn("explicitly confirms deletion", declined)
        self.assertTrue(path.exists())

        deleted = text_editor_tools.dispatch(
            "delete_obsidian_document", {"path": "draft.md", "confirm": True}
        )
        self.assertFalse(deleted.startswith("error:"))
        self.assertFalse(path.exists())

    def test_delete_folder_recursively_after_confirmation(self) -> None:
        folder = self.vault_root / "Stories/unused"
        folder.mkdir(parents=True)
        (folder / "chapter.md").write_text("Draft")
        (folder / "notes.txt").write_text("Research")

        declined = text_editor_tools.dispatch(
            "delete_obsidian_folder",
            {"path": "Stories/unused", "confirm": False},
        )
        self.assertIn("explicitly confirms", declined)
        self.assertTrue(folder.exists())

        deleted = text_editor_tools.dispatch(
            "delete_obsidian_folder",
            {"path": "Stories/unused", "confirm": True},
        )
        self.assertEqual(
            deleted, "Deleted Obsidian folder and its contents: Stories/unused"
        )
        self.assertFalse(folder.exists())
        self.assertTrue((self.vault_root / "Stories").is_dir())

    def test_delete_folder_protects_vault_root_hidden_paths_and_symlinks(self) -> None:
        hidden = self.vault_root / ".obsidian"
        hidden.mkdir()
        root_result = text_editor_tools.dispatch(
            "delete_obsidian_folder", {"path": ".", "confirm": True}
        )
        hidden_result = text_editor_tools.dispatch(
            "delete_obsidian_folder",
            {"path": ".obsidian", "confirm": True},
        )
        self.assertIn("hidden, '.' or '..'", root_result)
        self.assertIn("hidden, '.' or '..'", hidden_result)
        self.assertTrue(hidden.exists())

        outside = Path(self.temporary_directory.name).parent / (
            Path(self.temporary_directory.name).name + "-outside-folder"
        )
        outside.mkdir()
        (outside / "keep.txt").write_text("Keep")
        self.addCleanup(outside.rmdir)
        self.addCleanup(lambda: (outside / "keep.txt").unlink(missing_ok=True))
        (self.vault_root / "linked").symlink_to(outside, target_is_directory=True)

        symlink_result = text_editor_tools.dispatch(
            "delete_obsidian_folder",
            {"path": "linked", "confirm": True},
        )
        self.assertIn("cannot include symbolic links", symlink_result)
        self.assertEqual((outside / "keep.txt").read_text(), "Keep")

    def test_show_opens_the_document_in_obsidian(self) -> None:
        path = self.vault_root / "draft.md"
        path.write_text("A story.")

        with patch.object(text_editor_tools.subprocess, "run") as run:
            result = text_editor_tools.show_document({"path": "draft.md"})

        self.assertEqual(result, "Opened in Obsidian: draft.md")
        run.assert_called_once_with(
            ["open", "-a", "Obsidian", str(path.resolve())],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )

    def test_dispatch_reports_invalid_json_and_unknown_tools(self) -> None:
        invalid_json = text_editor_tools.dispatch("read_obsidian_document", "{")
        unknown_tool = text_editor_tools.dispatch("rename_obsidian_document", {})

        self.assertIn("invalid JSON", invalid_json)
        self.assertIn("unknown tool", unknown_tool)


if __name__ == "__main__":
    unittest.main()
