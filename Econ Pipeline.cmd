@echo off
rem Opens the Econ data pipeline window. Double-click this file.
cd /d "%~dp0"
start "" "%~dp0.venv\Scripts\pythonw.exe" -m pipeline.app
