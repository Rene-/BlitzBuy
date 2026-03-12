@echo off
:: BlitzBuy full build script
:: Rebuilds blitzbuy.exe (Python) then the Electron installer
:: Run from the BlitzBuy\ folder: build.bat

echo [1/2] Building blitzbuy.exe...
python -m PyInstaller ^
  --onefile ^
  --name blitzbuy ^
  --hidden-import playwright ^
  --hidden-import playwright.async_api ^
  --hidden-import greenlet ^
  --hidden-import playwright_stealth ^
  --hidden-import fake_useragent ^
  --hidden-import tenacity ^
  --hidden-import twocaptcha ^
  --collect-all playwright ^
  --collect-all playwright_stealth ^
  --collect-all fake_useragent ^
  blitzbuy.py
if errorlevel 1 (echo [ERROR] Python build failed & exit /b 1)

echo [2/2] Building Electron installer...
cd frontend
set CSC_IDENTITY_AUTO_DISCOVERY=false
npm run electron:build
if errorlevel 1 (echo [ERROR] Electron build failed & exit /b 1)
cd ..

echo Done. Installer: frontend\release\BlitzBuy Setup 1.0.0.exe
