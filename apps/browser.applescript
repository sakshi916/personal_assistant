on run argv
    if (count of argv) is not 1 then error "Expected one URL."
    set pageURL to item 1 of argv

    tell application "Safari"
        activate
        if (count of documents) is 0 then make new document
        set URL of front document to pageURL
    end tell

    return pageURL
end run
