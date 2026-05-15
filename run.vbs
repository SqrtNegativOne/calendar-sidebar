Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\Users\arkma\Documents\GitHub\calendar-sidebar"
sh.Run "cmd /c uv run widget.py", 0, False
