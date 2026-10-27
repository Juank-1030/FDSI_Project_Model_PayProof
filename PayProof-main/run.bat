@echo off
call .venv\Scripts\activate.bat
streamlit run app.py --server.headless true
