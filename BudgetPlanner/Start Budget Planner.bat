@echo off
echo Starting Budget Planner...
call conda run -n budgetplanner python -m streamlit run "%~dp0app.py"
pause
