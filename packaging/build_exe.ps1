# Baut dist\NoRoadsMissionMaker.exe (Windows, Python 3.10+)
$ErrorActionPreference = "Stop"
python -m pip install --upgrade pip
python -m pip install -e ".[gui]" pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name NoRoadsMissionMaker `
  --paths src `
  --add-data "src/nrmm/web;nrmm/web" `
  --collect-all webview `
  --hidden-import scipy.spatial.transform._rotation_groups `
  packaging/entry.py
Write-Host "Fertig: dist\NoRoadsMissionMaker.exe"
