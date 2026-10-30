#!/bin/sh
# Development host only. Installs no tools, services, or files on MiSTer.
set -eu
cd "$(dirname "$0")/.."
PREVIEW_PYTHON=${PREVIEW_PYTHON:-backend/.venv/bin/python}
PREVIEW_PYTHON=$("$PREVIEW_PYTHON" -c 'import sys; print(sys.executable)')
"$PREVIEW_PYTHON" -m pip install -r mister/requirements-preview.txt
if ! command -v dialog >/dev/null 2>&1 && [ ! -x mister/.preview-tools/dialog-1.3-20200327/dialog ]; then
  "$PREVIEW_PYTHON" - <<'PY'
import tarfile,urllib.request
from pathlib import Path
root=Path('mister/.preview-tools').resolve();root.mkdir(parents=True,exist_ok=True)
archive=root/'dialog.tgz'
urllib.request.urlretrieve('https://invisible-mirror.net/archives/dialog/dialog-1.3-20200327.tgz',archive)
with tarfile.open(archive) as source:
    # Extract only ordinary files/directories below the named source root.
    for member in source.getmembers():
        target=(root/member.name).resolve()
        if root not in target.parents or not (member.isfile() or member.isdir()):
            raise RuntimeError('Unexpected dialog archive member')
    source.extractall(root)
PY
  (
    cd mister/.preview-tools/dialog-1.3-20200327
    if [ "$(uname)" = Darwin ]; then
      # Apple's tsearch is unbalanced: dialog's address-keyed wide-character
      # cache becomes quadratic for large menus. Hash pointer order on the host;
      # rendering and Unicode behavior remain unchanged. Never patch MiSTer.
      "$PREVIEW_PYTHON" - <<'PY_PATCH'
from pathlib import Path
p=Path('inputstr.c')
old='\tresult = CMP(p->string_at, q->string_at);'
new='    {\n        size_t pa = (size_t) p->string_at;\n        size_t qa = (size_t) q->string_at;\n        pa ^= pa >> 33; pa *= 0xff51afd7ed558ccdULL; pa ^= pa >> 33;\n        qa ^= qa >> 33; qa *= 0xff51afd7ed558ccdULL; qa ^= qa >> 33;\n        result = CMP(pa, qa);\n        if (result == 0) result = CMP(p->string_at, q->string_at);\n    }'
source=p.read_text()
if old not in source: raise RuntimeError('Unexpected dialog cache implementation')
p.write_text(source.replace(old,new))
PY_PATCH
      CFLAGS="${CFLAGS:--O2 -Wno-error=implicit-function-declaration}" ./configure --disable-nls --with-curses-dir="$(xcrun --show-sdk-path)/usr"
    else
      ./configure --disable-nls
    fi
    make -j2
  )
fi
printf '\nHost preview ready. See docs/mister-mvp.md for snapshot and run commands.\n'
