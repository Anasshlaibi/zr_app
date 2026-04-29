@echo off
echo Starting ZineControl Web...

start cmd /k "cd backend && .venv\Scripts\activate && uvicorn server:app --reload"
start cmd /k "cd frontend && npm run dev"

echo Backend and Frontend are starting in separate windows.
echo Frontend will be at http://localhost:5173
echo API Docs at http://localhost:8000/docs
