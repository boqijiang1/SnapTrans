@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [setup] 创建虚拟环境...
python -m venv .venv || (echo venv 创建失败 & pause & exit /b 1)
echo [setup] 安装依赖（首次约 200MB，耐心等待）...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt
echo [setup] 完成！以后双击 run.bat 启动即可。
pause
