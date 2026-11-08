import subprocess
from threading import Event

import speech_recognition as sr
from pynput import keyboard

from assistant import build_system_prompt, run_assistant

r = sr.Recognizer()
r.pause_threshold = 1.5


def _speak_answer(answer: str) -> None:
    stop_requested = Event()
    command = ["say", answer]
    command_pressed = False

    def on_press(key: keyboard.Key | keyboard.KeyCode) -> bool | None:
        nonlocal command_pressed
        if key in (keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r):
            command_pressed = True
        elif (
            command_pressed
            and isinstance(key, keyboard.KeyCode)
            and key.char is not None
            and key.char.lower() == "l"
        ):
            stop_requested.set()
            return False
        return None

    def on_release(key: keyboard.Key | keyboard.KeyCode) -> None:
        nonlocal command_pressed
        if key in (keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r):
            command_pressed = False

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()
    process: subprocess.Popen[bytes] | None = None

    try:
        listener.wait()
        if stop_requested.is_set():
            return

        process = subprocess.Popen(command)
        while process.poll() is None:
            if stop_requested.wait(0.1):
                if process.poll() is None:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass
                break

        return_code = process.wait()
        if not stop_requested.is_set() and return_code != 0:
            raise subprocess.CalledProcessError(return_code, command)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait()
        listener.stop()
        listener.join()


def listen_and_recognize():
    history = [{"role": "system", "content": build_system_prompt()}]

    while True:
        try:
            answer=""
            with sr.Microphone() as source:
                print("Listening...")
                
                r.adjust_for_ambient_noise(source, duration=0.2)
                audio = r.listen(source)
                text = r.recognize_google(audio)
                text = text.lower()  
                print("You said:", text)
                
                if "exit" in text:
                    print("Exiting program...")
                    break

                history.append({"role": "user", "content": text})
                answer = run_assistant(history)
                print(f"assistant> {answer}\n")                
            
            _speak_answer(answer)


        except sr.RequestError as e:
            print("Could not request results; {0}".format(e))

        except sr.UnknownValueError:
            print("Could not understand audio")

        except KeyboardInterrupt:
            print("Program terminated by user")
            break