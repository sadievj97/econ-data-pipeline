@echo off
rem Runs the pipeline with this project's own Python.
rem Usage, from PowerShell in this folder:  .\pipeline check-spec specs\example_uzb_kaz.yaml
setlocal
set "PYTHONPATH=%~dp0"
"%~dp0.venv\Scripts\python.exe" -m pipeline %*
exit /b %ERRORLEVEL%
