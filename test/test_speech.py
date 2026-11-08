import sys
import types
import unittest
from typing import Callable
from unittest.mock import patch

try:
    import assistant
except ModuleNotFoundError as error:
    if error.name != "ollama":
        raise
    ollama_stub = types.ModuleType("ollama")
    ollama_stub.chat = lambda **kwargs: None
    sys.modules["ollama"] = ollama_stub
    import assistant

import speech


class SpeechTests(unittest.TestCase):
    def test_waits_for_speech_before_listening_again(self) -> None:
        events: list[str] = []
        recognized_text = iter(("hello", "exit"))

        class FakeMicrophone:
            def __enter__(self) -> object:
                events.append("listening")
                return object()

            def __exit__(
                self, *args: object
            ) -> None:
                if len(args) != 3:
                    raise AssertionError("microphone context expects exception details")
                return None

        def fake_recognize(_audio: object) -> str:
            if _audio is None:
                raise AssertionError("recognizer requires audio")
            return next(recognized_text)

        def fake_popen(command: list[str]) -> object:
            if command != ["say", "answer"]:
                raise AssertionError("speech playback must invoke macOS say")
            events.append("speaking")
            return FakeProcess()

        class FakeProcess:
            def poll(self) -> int:
                return 0

            def wait(self) -> int:
                events.append("finished")
                return 0

        class FakeListener:
            def __init__(self, on_press: object, on_release: object) -> None:
                self.on_press = on_press
                self.on_release = on_release

            def start(self) -> None:
                events.append("shortcut-listening")

            def wait(self) -> None:
                pass

            def stop(self) -> None:
                events.append("shortcut-stopped")

            def join(self) -> None:
                pass

        with (
            patch.object(speech.sr, "Microphone", FakeMicrophone),
            patch.object(speech.r, "adjust_for_ambient_noise"),
            patch.object(speech.r, "listen", return_value=object()),
            patch.object(speech.r, "recognize_google", side_effect=fake_recognize),
            patch.object(speech, "run_assistant", return_value="answer"),
            patch.object(speech.subprocess, "Popen", side_effect=fake_popen) as popen,
            patch.object(speech.keyboard, "Listener", FakeListener),
            patch("builtins.print"),
            patch.object(
                speech, "build_system_prompt", return_value="session snapshot"
            ) as build_prompt,
        ):
            speech.listen_and_recognize()

        self.assertEqual(
            events,
            [
                "listening",
                "shortcut-listening",
                "speaking",
                "finished",
                "shortcut-stopped",
                "listening",
            ],
        )
        popen.assert_called_once_with(["say", "answer"])
        build_prompt.assert_called_once_with()

    def test_command_l_stops_narration_and_resumes_listening(self) -> None:
        events: list[str] = []
        recognized_text = iter(("hello", "exit"))
        listeners: list[tuple[Callable[[object], object], Callable[[object], object]]] = []

        class FakeMicrophone:
            def __enter__(self) -> object:
                events.append("listening")
                return object()

            def __exit__(self, *args: object) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                self.returncode: int | None = None

            def poll(self) -> int | None:
                return self.returncode

            def terminate(self) -> None:
                events.append("stopped")
                self.returncode = -15

            def wait(self) -> int:
                events.append("finished")
                return self.returncode or 0

        def fake_popen(command: list[str]) -> FakeProcess:
            if command != ["say", "answer"]:
                raise AssertionError("speech playback must invoke macOS say")
            events.append("speaking")
            press, release = listeners[0]
            press(speech.keyboard.Key.cmd)
            press(speech.keyboard.KeyCode.from_char("l"))
            release(speech.keyboard.Key.cmd)
            return FakeProcess()

        class FakeListener:
            def __init__(
                self,
                on_press: Callable[[object], object],
                on_release: Callable[[object], object],
            ) -> None:
                listeners.append((on_press, on_release))

            def start(self) -> None:
                events.append("shortcut-listening")

            def wait(self) -> None:
                pass

            def stop(self) -> None:
                events.append("shortcut-stopped")

            def join(self) -> None:
                pass

        def fake_recognize(_audio: object) -> str:
            return next(recognized_text)

        with (
            patch.object(speech.sr, "Microphone", FakeMicrophone),
            patch.object(speech.r, "adjust_for_ambient_noise"),
            patch.object(speech.r, "listen", return_value=object()),
            patch.object(speech.r, "recognize_google", side_effect=fake_recognize),
            patch.object(speech, "run_assistant", return_value="answer"),
            patch.object(speech.subprocess, "Popen", side_effect=fake_popen),
            patch.object(speech.keyboard, "Listener", FakeListener),
            patch("builtins.print"),
        ):
            speech.listen_and_recognize()

        self.assertEqual(
            events,
            [
                "listening",
                "shortcut-listening",
                "speaking",
                "stopped",
                "finished",
                "shortcut-stopped",
                "listening",
            ],
        )


if __name__ == "__main__":
    unittest.main()
