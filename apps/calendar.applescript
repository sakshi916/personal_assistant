on run argv
    global locatedCalendarName
    if (count of argv) < 2 then error "Expected an operation and default calendar identifier."
    set operationName to item 1 of argv
    set defaultCalendarID to item 2 of argv

    tell application "Calendar"
        set targetCalendar to my findCalendar(defaultCalendarID)

        if operationName is "create" then
            if (count of argv) is not 8 then error "Invalid create arguments."
            set eventTitle to item 3 of argv
            set eventStart to my parseDate(item 4 of argv, item 5 of argv)
            set eventEnd to my parseDate(item 4 of argv, item 6 of argv)
            set eventNotes to item 7 of argv
            set reminderMinutes to item 8 of argv as integer
            set newEvent to make new event at end of events of targetCalendar with properties {summary:eventTitle, start date:eventStart, end date:eventEnd, description:eventNotes}
            make new display alarm at end of display alarms of newEvent with properties {trigger interval:(0 - reminderMinutes)}
            set locatedCalendarName to name of targetCalendar
            return my eventJSON(newEvent)

        else if operationName is "list" then
            if (count of argv) is not 4 then error "Invalid list arguments."
            set hasDateRange to (item 3 of argv is not "") and (item 4 of argv is not "")
            if hasDateRange then
                set rangeStart to my parseDate(item 3 of argv, "00:00")
                set rangeEnd to my parseDate(item 4 of argv, "23:59")
            end if
            set eventJSONs to {}
            repeat with currentCalendar in (calendars whose writable is true)
                if hasDateRange then
                    set currentEvents to (every event of currentCalendar whose start date is greater than or equal to rangeStart and start date is less than or equal to rangeEnd)
                else
                    set currentEvents to every event of currentCalendar
                end if
                repeat with currentEvent in currentEvents
                    set locatedCalendarName to name of currentCalendar
                    set end of eventJSONs to my eventJSON(currentEvent)
                end repeat
            end repeat
            return "[" & my joinText(eventJSONs, ",") & "]"

        else if operationName is "read" then
            if (count of argv) is not 3 then error "Invalid read arguments."
            return my eventJSON(my findEvent(item 3 of argv))

        else if operationName is "update" then
            if (count of argv) is not 9 then error "Invalid update arguments."
            set targetEvent to my findEvent(item 3 of argv)
            set eventTitle to item 4 of argv
            set eventDate to item 5 of argv
            set eventStartTime to item 6 of argv
            set eventEndTime to item 7 of argv
            set eventNotes to item 8 of argv
            set notesEnabled to item 9 of argv
            if eventTitle is not "" then set summary of targetEvent to eventTitle
            if eventDate is not "" then
                if eventStartTime is "" then set eventStartTime to my timeText(start date of targetEvent)
                if eventEndTime is "" then set eventEndTime to my timeText(end date of targetEvent)
                set start date of targetEvent to my parseDate(eventDate, eventStartTime)
                set end date of targetEvent to my parseDate(eventDate, eventEndTime)
            else if eventStartTime is not "" or eventEndTime is not "" then
                set currentDate to my dateText(start date of targetEvent)
                if eventStartTime is not "" then set start date of targetEvent to my parseDate(currentDate, eventStartTime)
                if eventEndTime is not "" then set end date of targetEvent to my parseDate(currentDate, eventEndTime)
            end if
            if notesEnabled is "1" then set description of targetEvent to eventNotes
            return my eventJSON(targetEvent)

        else if operationName is "delete" then
            if (count of argv) is not 3 then error "Invalid delete arguments."
            set targetEvent to my findEvent(item 3 of argv)
            delete targetEvent
            return "{\"deleted\":" & my jsonQuote(item 3 of argv) & "}"

        end if

        error "Unknown Calendar operation: " & operationName
    end tell
end run

on findCalendar(calendarID)
    tell application "Calendar"
        return calendar id calendarID
    end tell
end findCalendar

on findEvent(eventID)
    global locatedCalendarName
    tell application "Calendar"
        repeat with currentCalendar in (calendars whose writable is true)
            set matchingEvents to (every event of currentCalendar whose uid is eventID)
            if (count of matchingEvents) > 0 then
                set locatedCalendarName to name of currentCalendar
                return first item of matchingEvents
            end if
        end repeat
    end tell
    error "Calendar event not found: " & eventID
end findEvent

on parseDate(dateText, timeText)
    set previousDelimiters to AppleScript's text item delimiters
    set AppleScript's text item delimiters to "-"
    set dateParts to text items of dateText
    set AppleScript's text item delimiters to ":"
    set timeParts to text items of timeText
    set AppleScript's text item delimiters to previousDelimiters

    if (count of dateParts) is not 3 or (count of timeParts) is not 2 then error "Expected YYYY-MM-DD and HH:MM."
    set yearNumber to item 1 of dateParts as integer
    set monthNumber to item 2 of dateParts as integer
    set dayNumber to item 3 of dateParts as integer
    set hourNumber to item 1 of timeParts as integer
    set minuteNumber to item 2 of timeParts as integer

    set resultDate to current date
    set hours of resultDate to 0
    set minutes of resultDate to 0
    set seconds of resultDate to 0
    set year of resultDate to yearNumber
    set month of resultDate to monthNumber
    set day of resultDate to dayNumber
    set hours of resultDate to hourNumber
    set minutes of resultDate to minuteNumber
    return resultDate
end parseDate

on dateText(theDate)
    set yearNumber to year of theDate as integer
    set monthNumber to month of theDate as integer
    set dayNumber to day of theDate as integer
    return (yearNumber as text) & "-" & my pad2(monthNumber) & "-" & my pad2(dayNumber)
end dateText

on timeText(theDate)
    set hourNumber to hours of theDate as integer
    set minuteNumber to minutes of theDate as integer
    return my pad2(hourNumber) & ":" & my pad2(minuteNumber)
end timeText

on pad2(valueNumber)
    if valueNumber < 10 then return "0" & (valueNumber as text)
    return valueNumber as text
end pad2

on eventJSON(theEvent)
    global locatedCalendarName
    tell application "Calendar"
        set eventID to uid of theEvent
        set eventTitle to summary of theEvent
        set eventDate to my dateText(start date of theEvent)
        set eventStart to my timeText(start date of theEvent)
        set eventEnd to my timeText(end date of theEvent)
        set eventNotes to description of theEvent
        set calendarName to locatedCalendarName
        set alarmValue to "null"
        set currentAlarms to display alarms of theEvent
        if (count of currentAlarms) > 0 then
            set alarmValue to (0 - (trigger interval of item 1 of currentAlarms)) as text
        end if
    end tell
    if eventNotes is missing value then set eventNotes to ""

    if alarmValue is "null" then
        set reminderArgument to ""
    else
        set reminderArgument to alarmValue
    end if
    set shellCode to "import json,sys; print(json.dumps({'id':sys.argv[1],'title':sys.argv[2],'date':sys.argv[3],'start_time':sys.argv[4],'end_time':sys.argv[5],'notes':sys.argv[6],'reminder_minutes':None if not sys.argv[7] else int(sys.argv[7]),'calendar':sys.argv[8]}))"
    return do shell script "/usr/bin/python3 -c " & quoted form of shellCode & " " & quoted form of (eventID as text) & " " & quoted form of (eventTitle as text) & " " & quoted form of eventDate & " " & quoted form of eventStart & " " & quoted form of eventEnd & " " & quoted form of (eventNotes as text) & " " & quoted form of reminderArgument & " " & quoted form of (calendarName as text)
end eventJSON

on jsonQuote(theText)
    if theText is missing value then set theText to ""
    set shellCode to "import json,sys; print(json.dumps(sys.argv[1]))"
    return do shell script "/usr/bin/python3 -c " & quoted form of shellCode & " " & quoted form of (theText as text)
end jsonQuote

on joinText(theItems, separatorText)
    set previousDelimiters to AppleScript's text item delimiters
    set AppleScript's text item delimiters to separatorText
    set joinedValue to theItems as text
    set AppleScript's text item delimiters to previousDelimiters
    return joinedValue
end joinText
