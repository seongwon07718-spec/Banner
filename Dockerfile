CMD sh -c "python -m uvicorn main:api --host 0.0.0.0 --port ${PORT:-8000} & python -u main.py"
