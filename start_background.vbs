Set fso = CreateObject("Scripting.FileSystemObject")
strPath = fso.GetParentFolderName(WScript.ScriptFullName)
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = strPath
WshShell.Run "cmd.exe /c " & Chr(34) & strPath & "\run_hidden.bat" & Chr(34), 0, False
Set WshShell = Nothing
Set fso = Nothing
