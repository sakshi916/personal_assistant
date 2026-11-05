import notes, json

for label, name, args in [
    ("create_note", "create_note", {"title": "Smoke Test", "body": "helloworld"}),
    ("list_notes",  "list_notes",  {}),
    ("delete_note", "delete_note", {"title": "Smoke Test"}),
]:
    print(f"--- {label} ---")
    print(notes.dispatch(name, json.dumps(args)))
