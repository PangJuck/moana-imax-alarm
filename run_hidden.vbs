' 콘솔 창 없이(백그라운드) run_service.bat 실행
Set fso = CreateObject("Scripting.FileSystemObject")
d = fso.GetParentFolderName(WScript.ScriptFullName)
CreateObject("WScript.Shell").Run """" & d & "\run_service.bat""", 0, False
