@echo off
REM ============================================================
REM Auto Trading Startup Script
REM IB Gateway -> wait 60s -> webhook + gold_ewmac + fade_2h
REM ============================================================

echo [%date% %time%] Startup begin >> C:\Users\Riku\Desktop\tv_data\startup.log

REM --- 1. IB Gateway via IBC ---
echo [%date% %time%] Starting IB Gateway... >> C:\Users\Riku\Desktop\tv_data\startup.log
start "" "C:\IBC\StartGateway.bat"

REM --- 2. Wait 60s for Gateway ---
echo [%date% %time%] Waiting 60s... >> C:\Users\Riku\Desktop\tv_data\startup.log
timeout /t 60 /nobreak

REM --- 3. Webhook server ---
echo [%date% %time%] Starting webhook server >> C:\Users\Riku\Desktop\tv_data\startup.log
start "Webhook Server" cmd /k "cd /d C:\Users\Riku\Desktop\tv_data && python webhook_server.py"
timeout /t 5 /nobreak

REM --- 4. Gold EWMAC engine ---
echo [%date% %time%] Starting Gold EWMAC engine >> C:\Users\Riku\Desktop\tv_data\startup.log
start "Gold EWMAC" cmd /k "cd /d C:\Users\Riku\Desktop\tv_data && python gold_ewmac_engine.py"
timeout /t 5 /nobreak

REM --- 5. Fade 2H engine ---
echo [%date% %time%] Starting Fade 2H engine >> C:\Users\Riku\Desktop\tv_data\startup.log
start "Fade 2H" cmd /k "cd /d C:\Users\Riku\Desktop\tv_data && python fade_2h_engine.py"

echo [%date% %time%] All started >> C:\Users\Riku\Desktop\tv_data\startup.log
