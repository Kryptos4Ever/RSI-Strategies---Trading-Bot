@echo off
title Dashboard Server - Gzip habilitado
echo ==========================================================
echo Iniciar Servidor Local con compresion GZIP
echo ==========================================================
echo.
echo Iniciando servidor en puerto 7999
echo.

:: Ir al directorio raíz del repositorio (un nivel arriba de este .bat)
cd /d "%~dp0"

:: Iniciar el servidor con compresion gzip en segundo plano
start /B python "serve_dashboard.py" 7999

:: Esperar 1.5 segundos a que el puerto se active
timeout /t 1 >nul
timeout /t 1 >nul 2>nul


echo.
echo ==========================================================
echo Servidor en ejecucion (con compresion GZIP activa).
echo No cierres esta ventana si quieres seguir visualizando
echo el dashboard.
echo ==========================================================
exit
