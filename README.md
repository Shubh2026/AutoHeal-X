🚀 AutoHealX AI — Server Setup Guide

This guide explains how to run the AutoHealX AI server and dashboard locally using Docker.

📂 Project Structure
AutoHealX AI/
│
├── server/          # Backend server
├── dashboard/       # Frontend dashboard
└── README.md
⚙️ Prerequisites

Make sure the following are installed:

✅ Docker

✅ Docker Compose

✅ Python 3.x

Check installation:

docker --version
docker compose version
python --version
🖥️ Running the Server
✅ Normal Daily Workflow

Whenever you start working:

Step 1 — Navigate to Server Folder
cd D:\AutoHealX AI\server
Step 2 — Start Docker Container
docker compose up

This will start the backend server normally.

🔄 When to Rebuild Containers

Use the command below only if you modified:

Dockerfile

requirements.txt

server.py

Rebuild Command
docker compose up --build

📊 Running the Dashboard

Navigate to the dashboard folder and start a local server:

python -m http.server 8000

Then open your browser:

http://localhost:8000/dashboard_real.html

✅ Recommended Workflow
Task	Command
Start backend	docker compose up
Rebuild backend	docker compose up --build
Run dashboard	python -m http.server 8000
🛑 Stopping the Server

Press:

CTRL + C

or run:

docker compose down