"""Build distributable ZIPs; exclude tokens, local databases, logs and caches."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT/'dist'
DIST.mkdir(exist_ok=True)
with ZipFile(DIST/'comment-guard-extension.zip', 'w', ZIP_DEFLATED) as archive:
    for path in sorted((ROOT/'extension').rglob('*')):
        if path.is_file(): archive.write(path, path.relative_to(ROOT/'extension'))

with ZipFile(DIST/'comment-guard-project.zip', 'w', ZIP_DEFLATED) as archive:
    for path in sorted(ROOT.rglob('*')):
        relative = path.relative_to(ROOT)
        if path.is_file() and not any(part in ('.local', '.git', '__pycache__', 'dist', 'reports') for part in relative.parts):
            archive.write(path, Path('youtube-spam-filter')/relative)
print(DIST/'comment-guard-extension.zip')
print(DIST/'comment-guard-project.zip')
