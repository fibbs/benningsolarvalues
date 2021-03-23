# For more information, please refer to https://aka.ms/vscode-docker-python
FROM python:3.9-slim-buster

# Keeps Python from generating .pyc files in the container
ENV PYTHONDONTWRITEBYTECODE=1

# Turns off buffering for easier container logging
ENV PYTHONUNBUFFERED=1

# Install pip requirements
COPY requirements.txt .
RUN python -m pip install --upgrade pip
RUN python -m pip install -r requirements.txt

WORKDIR /app
COPY . /app
VOLUME [ "/config" ]

RUN useradd appuser && chown -R appuser /app
USER appuser

ENTRYPOINT [ "python", "app.py" ]
CMD ["-c", "/config/configuration.yaml"]
