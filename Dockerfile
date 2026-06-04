FROM python:3.12-slim

WORKDIR /app

COPY . .

RUN pip install uv && uv sync --extra dev

CMD ["uv", "run", "python", "-m", "planner.main"]
