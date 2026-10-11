# Windows packaging template

From a clean Python environment on Windows, install `requirements.txt` and PyInstaller, then run from the repository root:

```powershell
python -m PyInstaller --noconfirm --distpath dist --workpath build packaging/windows/Caustiq.spec
dist/Caustiq/Caustiq.exe --self-test self_test_report.json
```

The self-test report is local output and must not be committed. To compile the optional installer, open `packaging/windows/setup.iss` with Inno Setup after the `dist/Caustiq` folder has been built.

These files are a **build starting point only**. The executable and installer have not been built or verified as part of this GitHub source-preparation step. A distributable binary requires a fresh dependency-license audit, required bundled license texts and notices, LGPL compliance, clean-machine testing, and review of all included files. Do not publish a binary just because this template compiles.
