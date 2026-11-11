# Third-party software

The MIT license in `LICENSE` covers Tocktail's original source, extension, scripts and documentation.
It does not relicense dependencies or other third-party materials. Dependency code is not vendored
in the source repository. `uv.lock` records the Python dependency versions used for reproducible builds.

| Component | Role | Upstream licensing information |
| --- | --- | --- |
| PySide6 Essentials / Shiboken6 | Desktop UI bindings and runtime | [Qt for Python](https://doc.qt.io/qtforpython-6/), [license notices](https://doc.qt.io/qtforpython-6/licenses.html) |
| Qt libraries/plugins included by PyInstaller | Native desktop runtime | [Qt licensing](https://doc.qt.io/qt-6/licensing.html), [third-party code](https://doc.qt.io/qt-6/licenses-used-in-qt.html) |
| CPython and its bundled libraries | Embedded Python runtime in binary builds | [Python license](https://docs.python.org/3/license.html) |
| PyInstaller bootloader/runtime hooks | Binary packaging | [PyInstaller license and bootloader exception](https://pyinstaller.org/en/stable/license.html) |

Qt/PySide6 community components are used under their applicable upstream licenses, including LGPLv3
where available. Tocktail's use of these components does not change their licenses. Any binary release
must include the notices/license texts and satisfy source-availability and library-replacement requirements
that apply to the actual bundled versions/components. Qt's own dependencies also retain their licenses.

## Binary-release status

The current `dist` archive is a local test build. This source notice is an inventory and is not a complete
binary redistribution notice set or a source offer. Before publishing the ZIP, audit the files actually
included, supply their license/copyright notices, and arrange the corresponding library sources and
installation/replacement instructions as required. Do not label the entire binary bundle “MIT”.

## 日本語

MITはTocktail独自のソース・拡張・スクリプト・ドキュメントに適用します。依存ライブラリのライセンスは変更しません。
ソースリポジトリには依存ライブラリ本体を含めません。
現在の `dist` はローカル試験用です。ZIPを公開する前に、実際の同梱物に応じたライセンス文・著作権表示・
対応ソースの提供方法・ライブラリ差し替え方法を確認して同梱する必要があります。この一覧だけでその対応を完了したとは扱いません。
