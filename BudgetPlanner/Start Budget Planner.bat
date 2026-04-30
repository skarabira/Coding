@echo off
echo Starting Budget Planner...
call conda activate budgetplanner
python -m streamlit run "%~dp0app.py"
pause
