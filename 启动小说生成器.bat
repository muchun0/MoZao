@echo off
chcp 65001 >nul
title AI 小说生成器
echo ============================================
echo    AI 小说生成器 - 启动中...
echo ============================================
echo.
cd /d "%~dp0"

echo [1/2] 检查依赖...
python -c "import fastapi, uvicorn" 2>nul
if errorlevel 1 (
    echo      正在安装依赖，请稍候...
    python -m pip install fastapi uvicorn
)

echo [2/2] 启动服务...
echo.
echo   服务地址: http://127.0.0.1:8000
echo   关闭本窗口即可停止服务
echo.
python -m uvicorn webapp.server:app --host 127.0.0.1 --port 8000

pause
