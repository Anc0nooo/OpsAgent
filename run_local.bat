@echo off
chcp 65001 >nul
title OpsAgent 本地启动

REM ============================================================
REM OpsAgent 本地一键启动脚本（多用户架构：MySQL + JWT + 管理员）
REM 用法：双击运行，或命令行执行 run_local.bat
REM 前提：1) 已安装 Python 3.11+ 与 Node.js 18+ 与 MySQL 8.0
REM       2) 已编辑 .env 填入 MYSQL_PASSWORD 和 JWT_SECRET
REM       3) 已运行 python scripts\init_mysql.py 创建 opsagent 数据库
REM ============================================================

echo [1/3] 检查后端虚拟环境...
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 首次运行：创建虚拟环境并安装后端依赖（可能需要几分钟）...
    python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
)

echo [2/3] 启动后端 FastAPI (http://127.0.0.1:8000)...
start "OpsAgent-Backend" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"

echo [3/3] 启动前端 Vite Dev (http://localhost:5173)...
cd frontend
if not exist "node_modules" (
    echo 首次运行：安装前端依赖（可能需要几分钟）...
    call npm install --registry=https://registry.npmmirror.com
)
start "OpsAgent-Frontend" cmd /k "npm run dev"

echo.
echo 启动完成！
echo   后端接口文档: http://127.0.0.1:8000/docs
echo   前端页面:     http://localhost:5173
echo 关闭方式：关闭弹出的两个命令行窗口即可。
pause
