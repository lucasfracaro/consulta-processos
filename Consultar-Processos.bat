@echo off
rem Abre a tela da Consulta de Processos usando o Python instalado no Windows.
rem Para computadores SEM Python, use o pacote portatil (montar_windows.py -> "Consulta de Processos.exe").
cd /d "%~dp0"
where pyw >nul 2>nul && (start "" pyw -3 -m consulta_processos.gui & exit /b 0)
where pythonw >nul 2>nul && (start "" pythonw -m consulta_processos.gui & exit /b 0)
echo Python nao encontrado. Use o pacote portatil "Consulta de Processos.exe" ou instale o Python em python.org.
pause
