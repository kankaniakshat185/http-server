FROM python:3.13-slim

WORKDIR /workspace

# Copy the app directory as a package folder
COPY app/ ./app/

EXPOSE 4221

# Set the Python path to /workspace so "from app.core..." works inside container
ENV PYTHONPATH=/workspace

CMD ["python3", "-u", "app/main.py", "--directory", "/data"]