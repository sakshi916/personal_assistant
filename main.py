"""Interactive local assistant that drives Apple Notes through Ollama.

Uses the local Ollama server (`ollama` Python client) for chat + tool
calling. The four notes tools from `notes.py` are passed to the model;
when the model emits tool calls we dispatch them and feed the results
back until it produces a final answer.
"""

from __future__ import annotations

import json

import ollama

import notes

MODEL  = "gemma4:latest" 
#MODEL = "qwen3.8:27b-mlx"  

SYSTEM_PROMPT = (
    "You are a helpful assistant that helps the user manage their Apple "
    "Notes on this Mac. You have tools to create, edit, delete and open "
    "notes. Use a tool when the user asks you to do something to their "
    "notes. When you finish, reply with a short, friendly confirmation of "
    "what you did. Ask for a title if it is missing for edit/delete/show."
)


def run_assistant(history, max_tool_rounds: int = 10):
    """Run one turn of the chat/tool-call loop and return the assistant's final text.

    `history` is the list of chat messages; it is extended in place with the
    new assistant and tool messages so the conversation persists across turns.
    `max_tool_rounds` caps how many consecutive tool-call rounds are allowed
    before we bail out, preventing infinite loops from a misbehaving model.
    """
    for _ in range(max_tool_rounds):
        response = ollama.chat(
            model=MODEL,
            messages=history,
            tools=notes.TOOLS,
        )
        message = response["message"]
        history.append(message)

        # If the model did not request any tools, its content is the final
        # answer for this turn.
        tool_calls = message.get("tool_calls")
        if not tool_calls:
            return message.get("content") or ""

        # Dispatch each requested tool and feed the results back as tool
        # messages so the model can continue / confirm.
        for call in tool_calls:
            fn = call["function"]
            name = fn["name"]
            args = fn.get("arguments", {}) or {}
            if isinstance(args, str):
                arguments_json = args
            else:
                arguments_json = json.dumps(args)

            result = notes.dispatch(name, arguments_json)
            print(f"  [{name}] -> {result}")

            history.append(
                {
                    "role": "tool",
                    "content": result,
                    "name": name,
                }
            )

        # Loop again: the model reads the tool results and either calls more
        # tools or produces the final text.

    # Hit the cap without the model producing a final answer.
    history.append({
        "role": "assistant",
        "content": "Hit the maximum number of tool-call rounds. Please rephrase or check that Notes.app is responsive.",
    })
    return history[-1]["content"]


def main():
    history = [{"role": "system", "content": SYSTEM_PROMPT}]

    print(f"Notes assistant (model: {MODEL}). Type 'exit' to quit.\n")

    while True:
        try:
            user_input = input("you> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break

        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", ":q"):
            break

        history.append({"role": "user", "content": user_input})
        answer = run_assistant(history)
        print(f"assistant> {answer}\n")


if __name__ == "__main__":
    main()
