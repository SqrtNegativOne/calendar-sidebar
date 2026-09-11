Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = scriptDir

' --no-sync skips uv's implicit re-resolve on every launch, which costs
' several seconds. Fall back to a full sync on first run, or if .venv was
' removed, so the silent launch never fails without a console to explain it.
If fso.FolderExists(fso.BuildPath(scriptDir, ".venv")) Then
  sh.Run "cmd /c uv run --no-sync widget.py", 0, False
Else
  sh.Run "cmd /c uv run widget.py", 0, False
End If
