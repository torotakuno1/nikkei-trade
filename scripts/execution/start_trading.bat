@echo off
REM ============================================================
REM NIKKEI Auto Trading Startup Script
REM IB Gateway -> wait 60s -> v6 + CaseC + Gold EWMAC
REM ============================================================

echo [%date% %time%] Startup begin >> C:\Users\Riku\Desktop\tv_data\startup.log

REM --- 1. IB Gateway via IBC ---
echo [%date% %time%] Starting IB Gateway... >> C:\Users\Riku\Desktop\tv_data\startup.log
start "" "C:\IBC\StartGateway.bat"

REM --- 2. Wait 60s for Gateway ---
echo [%date% %time%] Waiting 60s... >> C:\Users\Riku\Desktop\tv_data\startup.log
timeout /t 60 /nobreak

REM --- 3. v6 engine ---
echo [%date% %time%] Starting v6 engine >> C:\Users\Riku\Desktop\tv_data\startup.log
start "v6 Engine" cmd /k "cd /d C:\Users\Riku\Desktop\tv_data && python v6_realtime_engine.py"

REM --- 4. Wait 5s then CaseC engine ---
timeout /t 5 /nobreak

echo [%date% %time%] Starting CaseC engine >> C:\Users\Riku\Desktop\tv_data\startup.log
start "CaseC Engine" cmd /k "cd /d C:\Users\Riku\Desktop\tv_data && python caseC_realtime_engine.py"

REM --- 5. Gold EWMAC engine ---
timeout /t 5 /nobreak
echo [%date% %time%] Starting Gold EWMAC engine >> C:\Users\Riku\Desktop\tv_data\startup.log
start "Gold EWMAC" cmd /k "cd /d C:\Users\Riku\Desktop\tv_data && python gold_ewmac_engine.py"

REM --- 6. N225ミニ 2H フェードエンジン ---
timeout /t 5 /nobreak
echo [%date% %time%] Starting Fade 2H engine >> C:\Users\Riku\Desktop\tv_data\startup.log
start "Fade 2H" cmd /k "cd /d C:\Users\CH07\nikkei-trade\scripts\execution && python fade_2h_engine.py"

echo [%date% %time%] All started >> C:\Users\Riku\Desktop\tv_data\startup.log