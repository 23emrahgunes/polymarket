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
COPY data/ ./data/

# Create the data directory if it doesn't exist
RUN mkdir -p data

# Ensure PYTHONPATH handles src module correctly
ENV PYTHONPATH="${PYTHONPATH}:/app"

# Run the bot when the container launches
CMD ["python3", "-m", "src.main"]
