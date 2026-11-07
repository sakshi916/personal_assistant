from notes import SYSTEM_PROMPT
import speech_recognition as sr
r = sr.Recognizer()
r.pause_threshold = 1.5
from notes import run_assistant
import pyttsx3


def listen_and_recognize():
    history = [{"role": "system", "content": SYSTEM_PROMPT}]
    while True:
        try:
            engine = pyttsx3.init()
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
            
        
            engine.say(answer)
            engine.runAndWait()

#            engine.save_to_file(answer, "output_audio.mp3")
 #           engine.runAndWait()


        except sr.RequestError as e:
            print("Could not request results; {0}".format(e))

        except sr.UnknownValueError:
            print("Could not understand audio")

        except KeyboardInterrupt:
            print("Program terminated by user")
            break