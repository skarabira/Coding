@echo off
echo Starting Budget Management Assistant...
call conda run -n budgetplanner python -m streamlit run "%~dp0app.py"
pause
