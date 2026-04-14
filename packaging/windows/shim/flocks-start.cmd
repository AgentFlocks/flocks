@echo off
setlocal
rem Install root = parent of bin\ (this file lives in bin\)
pushd "%~dp0.."
set "FLOCKS_INSTALL_ROOT=%CD%"
popd

if not exist "%FLOCKS_INSTALL_ROOT%\flocks\.venv\Scripts\python.exe" (
  echo [flocks] First run: installing Python and JS dependencies (uv sync, npm^)...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%FLOCKS_INSTALL_ROOT%\flocks\scripts\bootstrap-windows.ps1" -InstallRoot "%FLOCKS_INSTALL_ROOT%"
  if errorlevel 1 exit /b 1
)

if not exist "%FLOCKS_INSTALL_ROOT%\bin\flocks.cmd" (
  echo [flocks] bootstrap did not create bin\flocks.cmd. Check logs.
  exit /b 1
)

call "%FLOCKS_INSTALL_ROOT%\bin\flocks.cmd" start %*
