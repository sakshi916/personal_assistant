from email.message import Message
import json
import os
import unittest
from unittest.mock import MagicMock, patch

import app_tools
from apps import browser_tools


class BrowserToolsTests(unittest.TestCase):
    def test_web_search_returns_at_most_five_organic_results(self) -> None:
        response = {
            "web": {
                "results": [
                    {"title": f"Result {index}", "url": f"https://site{index}.example"}
                    for index in range(7)
                ]
            }
        }
        with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "test-key"}):
            with patch.object(browser_tools, "_request_json", return_value=response) as request:
                results = json.loads(browser_tools.search_web({"query": "weather"}))

        self.assertTrue(results["results_are_organic"])
        self.assertEqual(len(results["results"]), 5)
        self.assertEqual(results["results"][0]["rank"], 1)
        self.assertFalse(results["results"][0]["sponsored"])
        self.assertIn("count=5", request.call_args.args[0])

    def test_web_search_requires_provider_configuration(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = browser_tools.dispatch("search_web", {"query": "weather"})

        self.assertTrue(result.startswith("error: search_web failed:"))
        self.assertIn("BRAVE_SEARCH_API_KEY", result)

    def test_youtube_search_enriches_first_five_results_with_duration(self) -> None:
        search_results = {
            "items": [
                {
                    "id": {"videoId": "abcdefghijk"},
                    "snippet": {
                        "title": "Python tutorial",
                        "channelTitle": "Example Channel",
                        "description": "A tutorial.",
                        "publishedAt": "2026-01-01T00:00:00Z",
                    },
                }
            ]
        }
        video_details = {
            "items": [
                {
                    "id": "abcdefghijk",
                    "contentDetails": {"duration": "PT1H2M3S"},
                    "statistics": {"viewCount": "1234"},
                }
            ]
        }
        with patch.dict(os.environ, {"YOUTUBE_API_KEY": "test-key"}):
            with patch.object(
                browser_tools,
                "_request_json",
                side_effect=[search_results, video_details],
            ):
                results = json.loads(
                    browser_tools.search_youtube_videos({"query": "Python"})
                )

        video = results["results"][0]
        self.assertEqual(video["url"], "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertEqual(video["duration"], "1:02:03")
        self.assertEqual(video["view_count"], "1234")
        self.assertIn("not verification", results["limitation"])

    def test_youtube_search_requires_api_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = browser_tools.dispatch(
                "search_youtube_videos", {"query": "math lecture"}
            )

        self.assertTrue(result.startswith("error: search_youtube_videos failed:"))
        self.assertIn("YOUTUBE_API_KEY", result)

    def test_url_validation_rejects_non_web_local_and_credential_urls(self) -> None:
        for url in (
            "file:///etc/passwd",
            "http://127.0.0.1/private",
            "http://user:password@example.com/",
            "http://localhost/",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    browser_tools._validate_public_url(url)

    def test_url_validation_rejects_hostnames_resolving_to_private_addresses(self) -> None:
        with patch.object(
            browser_tools.socket,
            "getaddrinfo",
            return_value=[(None, None, None, None, ("10.0.0.4", 443))],
        ):
            with self.assertRaisesRegex(ValueError, "non-public"):
                browser_tools._validate_public_url("https://example.com/")

    def test_read_page_blocks_urls_reported_unsafe(self) -> None:
        with patch.object(browser_tools, "_validate_public_url", return_value="https://bad.example"):
            with patch.object(browser_tools, "_check_url_reputation", return_value="unsafe"):
                with patch.object(browser_tools, "_read_html") as read_html:
                    result = browser_tools.dispatch(
                        "read_web_page", {"url": "https://bad.example"}
                    )

        self.assertIn("reported a threat", result)
        read_html.assert_not_called()

    def test_safe_browsing_sends_json_payload(self) -> None:
        with patch.dict(os.environ, {"GOOGLE_SAFE_BROWSING_API_KEY": "test-key"}):
            with patch.object(
                browser_tools, "_request_json", return_value={}
            ) as request:
                status = browser_tools._check_url_reputation("https://example.com/")

        self.assertEqual(status, "clear")
        self.assertEqual(
            request.call_args.kwargs["headers"]["Content-Type"],
            "application/json",
        )
        self.assertEqual(
            json.loads(request.call_args.kwargs["data"])["threatInfo"][
                "threatEntries"
            ][0]["url"],
            "https://example.com/",
        )

    def test_html_reader_refuses_attachment_responses(self) -> None:
        headers = Message()
        headers["Content-Type"] = "text/html"
        headers["Content-Disposition"] = "attachment; filename=page.html"
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = headers
        opener = MagicMock()
        opener.open.return_value = response

        with patch.object(
            browser_tools, "_validate_public_url", return_value="https://example.com/"
        ):
            with patch.object(
                browser_tools.urllib.request, "build_opener", return_value=opener
            ):
                with self.assertRaisesRegex(ValueError, "served as an attachment"):
                    browser_tools._read_html("https://example.com/")

        response.read.assert_not_called()

    def test_read_page_reports_missing_reputation_configuration(self) -> None:
        page = {
            "url": "https://example.com/",
            "title": "Example",
            "text": "Page text",
        }
        with patch.object(browser_tools, "_validate_public_url", return_value=page["url"]):
            with patch.object(
                browser_tools, "_check_url_reputation", return_value="not_configured"
            ):
                with patch.object(browser_tools, "_read_html", return_value=page):
                    result = json.loads(
                        browser_tools.read_web_page({"url": page["url"]})
                    )

        self.assertEqual(result["reputation"], "not_configured")
        self.assertIn("was not checked", result["safety_notice"])

    def test_html_extractor_omits_script_content(self) -> None:
        parser = browser_tools._ReadableHTML()
        parser.feed(
            "<html><title>Safe title</title><body><main><p>Visible text</p>"
            "<script>unsafe hidden text</script></main></body></html>"
        )

        self.assertIn("Safe title", " ".join(parser.title_parts))
        self.assertIn("Visible text", " ".join(parser.text_parts))
        self.assertNotIn("unsafe hidden text", " ".join(parser.text_parts))

    def test_redirect_handler_rejects_reputation_matches(self) -> None:
        handler = browser_tools._SafeRedirectHandler()
        with patch.object(
            browser_tools, "_validate_public_url", return_value="https://bad.example/"
        ):
            with patch.object(
                browser_tools, "_check_url_reputation", return_value="unsafe"
            ):
                with self.assertRaisesRegex(ValueError, "Blocked redirect"):
                    handler.redirect_request(
                        None,
                        None,
                        302,
                        "Found",
                        {},
                        "https://bad.example/",
                    )

    def test_safari_receives_url_as_argument_not_script_source(self) -> None:
        with patch.object(browser_tools.sys, "platform", "darwin"):
            with patch.object(
                browser_tools, "_validate_public_url", return_value="https://example.com/"
            ):
                with patch.object(
                    browser_tools, "_check_url_reputation", return_value="clear"
                ):
                    with patch.object(browser_tools.subprocess, "run") as run:
                        run.return_value.stdout = "https://example.com/\n"
                        result = browser_tools.open_in_safari(
                            {"url": "https://example.com/"}
                        )

        self.assertIn("Opened https://example.com/ in Safari", result)
        command = run.call_args.args[0]
        self.assertEqual(command[0:2], ["osascript", str(browser_tools.SAFARI_SCRIPT_PATH)])
        self.assertEqual(command[2], "https://example.com/")
        self.assertTrue(run.call_args.kwargs["check"])

    def test_browser_app_exposes_browser_tools_after_selection(self) -> None:
        context = app_tools.ToolContext()
        app_tools.dispatch_tool("select_app", {"app_id": "browser"}, context)

        names = {
            tool["function"]["name"]
            for tool in app_tools.get_available_tools(context)
        }
        self.assertEqual(
            names,
            {
                "select_app",
                "search_web",
                "read_web_page",
                "search_youtube_videos",
                "open_in_safari",
            },
        )

    def test_safari_blocks_urls_reported_unsafe(self) -> None:
        with patch.object(browser_tools, "_validate_public_url", return_value="https://bad.example"):
            with patch.object(browser_tools, "_check_url_reputation", return_value="unsafe"):
                with patch.object(browser_tools.subprocess, "run") as run:
                    with self.assertRaisesRegex(ValueError, "reported a threat"):
                        browser_tools.open_in_safari({"url": "https://bad.example"})

        run.assert_not_called()

    def test_dispatch_rejects_non_object_arguments(self) -> None:
        self.assertEqual(
            browser_tools.dispatch("search_web", "[]"),
            "error: search_web failed: tool arguments must be a JSON object",
        )


if __name__ == "__main__":
    unittest.main()
