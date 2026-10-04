@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [build] 开始打包（首次约 3~8 分钟）...
".venv\Scripts\pyinstaller.exe" --noconfirm --clean --onefile --windowed --icon app.ico --name SnapTrans --collect-all rapidocr_onnxruntime launch.py
if errorlevel 1 (echo [build] 失败 & pause & exit /b 1)
copy /y config.json dist\ >nul 2>&1
copy /y glossary.txt dist\ >nul 2>&1
echo [build] 完成：dist\SnapTrans.exe（config.json 和 glossary.txt 已复制到旁边）
pause
