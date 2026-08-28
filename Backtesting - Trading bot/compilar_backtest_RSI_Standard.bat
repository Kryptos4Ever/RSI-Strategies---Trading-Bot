@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title Compilar Backtest RSI Standard

REM ═══════════════════════════════════════════════════════════════════════════
REM  compilar_backtest_RSI_Standard.bat
REM  ─────────────────────────────────────────────────────────────────────────
REM  Genera un ejecutable autónomo (.exe, un solo archivo) del backtest
REM  RSI Standard a partir del código ACTUAL de esta carpeta.
REM
REM  IMPORTANTE — CÓMO FUNCIONA LA "CONGELACIÓN":
REM    Este script NUNCA copia archivos de seguridad. Compila DIRECTAMENTE
REM    desde el código en su ubicación actual. PyInstaller empaqueta dentro
REM    del .exe el bytecode de todos los .py en el MOMENTO de la compilación.
REM    Por lo tanto, si luego modificas cualquier .py del proyecto, el .exe
REM    ya generado conservará la configuración y lógica que tenía al momento
REM    de ser compilado.
REM
REM    Para obtener un ejecutable actualizado, simplemente vuelve a correr
REM    este script: generará uno nuevo con la fecha y hora de compilación.
REM
REM  REQUISITOS PREVIOS:
REM    1. Tener Python instalado y disponible en el PATH (comando "python").
REM    2. Tener instaladas las dependencias del backtest:
REM          pip install -r requirements.txt
REM       (Este script instala PyInstaller automáticamente si hiciera falta.)
REM
REM  RESULTADO:
REM    Se genera:  "Backtesting - Trading bot\Backtest_RSI_Standard_YYYY-MM-DD_THH-MM-SS.exe"
REM
REM  USO DEL EJECUTABLE GENERADO:
REM    El .exe DEBE ejecutarse estando dentro de la carpeta
REM    "Backtesting - Trading bot" (porque busca la base de datos DB\btc_1h.db
REM    y escribe backtest_results.json relativos al directorio de trabajo).
REM    La base de datos DB\btc_1h.db debe existir previamente (se genera con
REM    los scripts de descarga de la carpeta DB\).
REM
REM  Ejemplo de uso:
REM      cd "Backtesting - Trading bot"
REM      Backtest_RSI_Standard_2026-08-09_T01-00-00.exe
REM ═══════════════════════════════════════════════════════════════════════════

echo.
echo  ═══════════════════════════════════════════════════════════════
echo   COMPILADOR - Backtest RSI Standard
echo  ═══════════════════════════════════════════════════════════════
echo.

REM ── 0. Nos posicionamos en la carpeta de este .bat (Backtesting - Trading bot) ──
cd /d "%~dp0"
set "BOT_DIR=%~dp0"

if not exist "%BOT_DIR%Backtest_RSI_Standard.py" (
    echo  [ERROR] No se encontró "%BOT_DIR%Backtest_RSI_Standard.py"
    echo          Asegúrate de que este .bat esté en la carpeta "Backtesting - Trading bot".
    echo.
    pause
    exit /b 1
)

REM ── 1. Verificar Python ─────────────────────────────────────────────────────
echo  [1/6] Verificando Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python no está instalado o no está en el PATH.
    echo          Descárgalo de https://www.python.org/downloads/
    echo          y marca la opción "Add Python to PATH" durante la instalación.
    echo.
    pause
    exit /b 1
)
python --version
echo.

REM ── 2. Verificar / instalar PyInstaller ────────────────────────────────────
echo  [2/6] Verificando PyInstaller...
pyinstaller --version >nul 2>&1
if errorlevel 1 (
    echo  [INFO] PyInstaller no está instalado. Instalando...
    pip install pyinstaller
    if errorlevel 1 (
        echo  [ERROR] No se pudo instalar PyInstaller.
        echo.
        pause
        exit /b 1
    )
) else (
    echo  [OK] PyInstaller ya está disponible.
)
echo.

REM ── 3. Generar timestamp (YYYY-MM-DD_THH-MM-SS) ───────────────────────────
echo  [3/6] Generando nombre con fecha y hora...
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "Get-Date -Format \"yyyy-MM-dd'_T'HH-mm-ss\""`) do set "TIMESTAMP=%%i"
set "EXE_NAME=Backtest_RSI_Standard_%TIMESTAMP%"
echo  [OK] Nombre del ejecutable: %EXE_NAME%.exe
echo.

REM ── 4. Definir rutas temporales para artefactos de compilación ────────────
REM  - DISTPATH : carpeta DONDE se deja el .exe final (esta misma carpeta).
REM  - WORKPATH : carpeta temporal de trabajo de PyInstaller (build\).
REM  - SPECPATH : carpeta temporal para el archivo .spec.
REM  Usamos %TEMP%\pyinstaller_RSI_Standard para que NO se ensucien la carpeta
REM  del bot con artefactos de compilación.
REM  IMPORTANTE: %BOT_DIR% termina con "\" (viene de %~dp0). Al usarlo entre
REM  comillas ("%DIST_DIR%") la secuencia \" escaparía la comilla de cierre y
REM  rompería el comando de PyInstaller. Por eso quitamos la barra final.
set "DIST_DIR=%BOT_DIR:~0,-1%"
set "WORK_DIR=%TEMP%\pyinstaller_RSI_Standard\build"
set "SPEC_DIR=%TEMP%\pyinstaller_RSI_Standard\spec"

REM Limpiar temporales de compilaciones anteriores
if exist "%TEMP%\pyinstaller_RSI_Standard" rmdir /s /q "%TEMP%\pyinstaller_RSI_Standard"

REM ── 5. Compilar con PyInstaller directamente a la ubicación final ─────────
echo  [5/6] Compilando (puede tardar un poco)...
cd /d "%BOT_DIR%"
REM
REM  NOTA SOBRE --exclude-module:
REM  El backtest solo necesita numpy, sqlite3 (estándar) y loguru en su ruta
REM  de ejecución. PyInstaller arrastraría automáticamente librerías pesadas
REM  (pandas, matplotlib, numpy, tkinter, Pillow, numba, etc.) a través de
REM  imports transitivos, inflando el .exe a ~100MB. Estas opciones excluyen
REM  esas librerías para obtener un ejecutable mucho más liviano y rápido.
REM
REM  --distpath "%DIST_DIR%": genera el .exe DIRECTAMENTE en la carpeta final.
REM  --workpath "%WORK_DIR%" : los archivos temporales van a %TEMP%.
REM  --specpath "%SPEC_DIR%": el archivo .spec va a %TEMP%.
REM
REM  NOTA: Se usa "python -m PyInstaller" (más confiable que el comando
REM  "pyinstaller" directo) y el scriptname va en la MISMA línea que el
REM  último argumento para evitar problemas con la continuación de línea (^).
REM
python -m PyInstaller --onefile --clean --noconfirm --name "%EXE_NAME%" --distpath "%DIST_DIR%" --workpath "%WORK_DIR%" --specpath "%SPEC_DIR%" --exclude-module pandas --exclude-module matplotlib --exclude-module PIL --exclude-module tkinter --exclude-module numba --exclude-module llvmlite --exclude-module pytest --exclude-module requests --exclude-module aiohttp Backtest_RSI_Standard.py
if errorlevel 1 (
    echo  [ERROR] La compilación falló. Revisa los mensajes de PyInstaller.
    echo.
    pause
    exit /b 1
)
echo.

REM ── 6. Verificar que el .exe quedó en la ubicación final ─────────────────
echo  [6/6] Verificando ejecutable en "Backtesting - Trading bot\"...
if exist "%DIST_DIR%\%EXE_NAME%.exe" (
    echo  [OK] Generado: "%DIST_DIR%\%EXE_NAME%.exe"
) else (
    echo  [ERROR] No se encontró el ejecutable en "%DIST_DIR%\%EXE_NAME%.exe".
    echo.
    pause
    exit /b 1
)

echo.
echo  ═══════════════════════════════════════════════════════════════
echo   ¡COMPILACIÓN EXITOSA!
echo  ═══════════════════════════════════════════════════════════════
echo.
echo   Ejecutable creado:
echo     "%DIST_DIR%\%EXE_NAME%.exe"
echo.
echo   IMPORTANTE - PARA EJECUTARLO:
echo     Abre una terminal DENTRO de "Backtesting - Trading bot" y escribe:
echo       %EXE_NAME%.exe
echo.
echo     Requisitos en tiempo de ejecución:
echo       - La base de datos DB\btc_1h.db debe existir (se genera con los
echo         scripts de descarga de la carpeta DB\).
echo       - El .exe escribe backtest_results.json en la carpeta actual.
echo.
echo   Para compilar una versión NUEVA (si modificas el código), vuelve a
echo   ejecutar este .bat. El resultado llevará la fecha y hora de compilación.
echo.
pause