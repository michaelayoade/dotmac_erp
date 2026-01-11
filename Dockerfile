# Stage 1: Build CSS with Node.js
FROM node:20-alpine AS css-builder

WORKDIR /build

COPY package.json package-lock.json* ./
RUN npm ci --silent

COPY tailwind.config.js postcss.config.js ./
COPY src/css ./src/css
COPY templates ./templates

RUN npm run build:css

# Stage 2: Python application
FROM python:3.12-slim

WORKDIR /app

RUN pip install poetry && poetry config virtualenvs.create false

COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-interaction --no-ansi

COPY . .

# Copy compiled CSS from builder stage
COPY --from=css-builder /build/static/css/app.css ./static/css/app.css

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
