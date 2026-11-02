# PyInstaller spec for Blendora (one-folder build).
# Build:  pyinstaller packaging/blendora.spec --noconfirm
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
WIN = sys.platform.startswith("win")

datas = [(os.path.join(ROOT, "web"), "web")]
datas += collect_data_files("librosa")          # example registry + lazy_loader .pyi stubs
datas += collect_data_files("lazy_loader")
hiddenimports = collect_submodules("librosa") + collect_submodules("uvicorn") + [
    "app.main", "app.selftest", "app.synth", "multipart", "python_multipart",
    "sklearn.utils._typedefs", "sklearn.neighbors._partition_nodes",
]
if WIN:
    hiddenimports += ["webview.platforms.edgechromium", "webview.platforms.winforms", "clr"]

binaries = []
ff = os.environ.get("BLENDORA_BUNDLE_FFMPEG")  # path to ffmpeg(.exe) with Rubber Band, copied next to the exe
if ff and os.path.isfile(ff):
    binaries.append((ff, "."))

a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["torch", "torchaudio", "demucs", "triton", "tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Blendora",
    console=False,
    icon=os.path.join(SPECPATH, "blendora.ico") if os.path.exists(os.path.join(SPECPATH, "blendora.ico")) else None,
    version=os.path.join(SPECPATH, "version.txt") if WIN and os.path.exists(os.path.join(SPECPATH, "version.txt")) else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Blendora")
