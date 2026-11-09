FROM python:3.12-slim
WORKDIR /app
# CPU-only torch keeps the image a few gigabytes smaller.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY pyproject.toml ./
COPY drifttrace ./drifttrace
RUN pip install --no-cache-dir .
COPY . .
RUN pip install --no-cache-dir -e .
EXPOSE 8000
