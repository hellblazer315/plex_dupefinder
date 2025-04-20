# Use Ubuntu as base image
FROM ubuntu:latest

# Set the working directory
WORKDIR /app

# Install Python 3, pip, and venv
# Print out Python and pip versions
RUN echo "[ ] Updating package lists..." && \
    apt-get update && \
    echo "[ ] Installing Python 3, pip, and venv..." && \
    apt-get install -y python3 python3-pip python3-venv && \
    echo "[ ] Cleaning up package cache..." && \
    apt-get clean && \
    echo "[ ] Creating a symbolic link for Python 3..." && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    echo "[ ] Verifying Python and pip versions..." && \
    python --version && \
    pip --version

# Create and activate a virtual environment
RUN python3 -m venv /venv
ENV PATH="/venv/bin:$PATH"

# Copy requirements.txt to the root directory of the image
COPY requirements.txt /requirements.txt

# Install Python dependencies from requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

# Create a symbolic link from /app/plex_dupefinder.py to /plex_dupefinder
RUN ln -s /app/plex_dupefinder.py /plex_dupefinder

# Define a volume for the Python application
VOLUME /app

# Define default command
ENTRYPOINT ["/plex_dupefinder"]