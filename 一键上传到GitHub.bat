@echo off
chcp 65001 >nul
title 财务单据智能风险审核系统 - 一键上传 GitHub

echo ========================================================
echo     财务单据智能风险审核系统 - Git 一键提交与推送工具
echo ========================================================
echo.

:: 1. 检查 Git 是否安装
where git >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [错误] 未检测到 Git 环境！
    echo 请先安装 Git 并配置环境变量。
    echo 推荐安装方式：在 PowerShell 中运行 winget install --id Git.Git -e --source winget
    echo 或者访问官网下载：https://git-scm.com/download/win
    echo.
    pause
    exit /b 1
)

:: 2. 检查是否已初始化本地仓库
if not exist ".git" (
    echo [提示] 检测到当前目录尚未初始化 Git 仓库。
    echo 正在为您执行本地初始化：git init ...
    git init
    echo.
    echo 请确认您是否已经在 GitHub 上创建好了空的远程仓库？
    set /p REPO_URL="请输入您的 GitHub 仓库地址 (如 https://github.com/用户名/仓库名.git) : "
    if defined REPO_URL (
        git branch -M main
        git remote add origin %REPO_URL%
        echo [成功] 远程仓库关联完成！
    ) else (
        echo [提示] 未输入仓库地址，稍后您可手动关联：git remote add origin [仓库地址]
    )
    echo.
)

:: 3. 显示当前修改状态
echo [1/3] 正在检查文件变更状态 (已自动应用 .gitignore 过滤敏感与无用文件)...
git status -s
echo.

:: 4. 提示输入提交说明
set "COMMIT_MSG="
set /p COMMIT_MSG="请输入本次提交说明 (直接回车默认: update: 代码更新同步): "
if "%COMMIT_MSG%"=="" set COMMIT_MSG=update: 代码更新同步

echo.
echo [2/3] 正在暂存并提交代码...
git add .
git commit -m "%COMMIT_MSG%"

echo.
echo [3/3] 正在推送到 GitHub 远程仓库 (main 分支)...
git push origin main
if %ERRORLEVEL% equ 0 (
    echo.
    echo ========================================================
    echo   [恭喜] 代码已成功一键推送到 GitHub！
    echo ========================================================
) else (
    echo.
    echo [提示] 推送遇到问题？
    echo 1. 若是首次推送且未绑定上游分支，可尝试在命令行运行: git push -u origin main
    echo 2. 若提示网络连接被重置，可多尝试一次推送或检查网络代理设置。
)

echo.
pause
