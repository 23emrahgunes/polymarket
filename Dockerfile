# Use official Python image as a parent image
FROM python:3.12-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY src/ ./src/

# Zero-Manual-Setup: Ensure data directory exists internally
RUN mkdir -p /app/data

# Ensure PYTHONPATH handles src module correctly
ENV PYTHONPATH="/app"

# Zero-Failure: Run the module correctly as an entrypoint
CMD ["python3", "-m", "src.main"]
