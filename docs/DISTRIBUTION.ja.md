# Windowsポータブル版

[English](DISTRIBUTION.md)

Windows 11 x64向けの署名なしポータブル版です。インストーラーではありません。
利用先へのPython・uv導入は不要です。署名がないため、Windowsの評価に関する確認画面が出る場合があります。

現在のZIPはローカル試験用です。公開前に同梱依存のライセンス表示・対応ソースなどを確認してください。
[サードパーティの扱い](../THIRD_PARTY_NOTICES.md) に状況を記載しています。

## 導入と起動

1. `Tocktail-Windows-x64.zip` 内の `Tocktail` フォルダー全体を、移動せず使える書き込み可能な場所に展開します。
   `Tocktail.exe`、`TocktailTools.exe`、`tocktail-native-host.exe`、`_internal` は同じフォルダーに置いてください。
2. 既存のTocktailを終了し、`Tocktail.exe` を起動します。パレットを閉じてもトレイで計測を続けます。
3. **Dashboard** で1・7・30日分の履歴を確認できます。終了日を選べ、表示中は5秒ごとに更新します。
4. 必要ならトレイの **Start with Windows** を有効にします。既定では無効です。
   現在ユーザーの `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\Tocktail` だけに登録し、管理者権限は不要です。

DB・設定は `%LOCALAPPDATA%\Tocktail\tocktail.sqlite3` に保存します。
独自のDBは `Tocktail.exe --db C:\path\activity.sqlite3` で指定し、Host登録・自動起動でも同じDBを指定してください。
開発版と配布版も、同じDBへの二重起動を防ぎます。

## Chrome・Edge

同梱の `browser-extension` を、各ブラウザの拡張管理画面でデベロッパーモードを有効にして読み込みます。
実際の拡張IDを取得し、以下のIDを置き換えてください。使わないブラウザの引数は省略します。
拡張を導入したフォルダーは、その後移動・削除しないでください。

展開先でPowerShellを開いて実行します。

```powershell
.\TocktailTools.exe browser-host prepare --chrome-id YOUR_CHROME_ID --edge-id YOUR_EDGE_ID
Get-Content .browser-host\browser-host.json
Get-Content .browser-host\com.tocktail.bridge.json
.\TocktailTools.exe browser-host install --from .browser-host
```

manifestの実行先が同じフォルダーの `tocktail-native-host.exe` であることを確認してから登録します。
Tocktailを再起動し、拡張を再読み込みします。現在ユーザーのChrome・Edge用NativeMessagingHostsへ登録し、
ブラウザプロファイルや拡張自体の導入設定は変更しません。
開発版から移行する場合は、Tocktail終了と拡張の無効化／再読み込みで古いHostも終了してから登録し直します。
起動中の古い配布ファイルを上書きしないでください。

```powershell
.\TocktailTools.exe sites
.\TocktailTools.exe startup status
.\TocktailTools.exe startup enable
.\TocktailTools.exe startup disable
```

複数ウィンドウではプロセス・フォーカス・タイトルの一意性を検証します。同名・取得不能なタイトルではアプリ単位に戻します。
パレットには検証済みドメインを表示します。URLのパス・検索語、入力内容、プライベートブラウズは収集しません。タイトル保存は任意です。

## 更新・削除

更新前に古い版で自動起動を解除します。Tocktailを終了し、拡張を無効にしてHostの実行ファイルも解放してください。
新しい版を別フォルダーに展開し、実IDでHostを登録し直します。必要なら自動起動を再度有効にします。
DBは起動時にマイグレーションします。更新前にTocktailを終了してデータフォルダーをバックアップしてください。
起動中のSQLiteファイルをWAL抜きでコピーしないでください。

削除時もTocktailを終了し、拡張を無効化／再読み込みしてから、この版のフォルダーで実行します。

```powershell
.\TocktailTools.exe startup disable
.\TocktailTools.exe browser-host unregister
```

その後、ブラウザ拡張と展開フォルダーを削除します。登録解除はこの版が所有する項目だけが対象です。
LocalAppDataの履歴・設定・Host JSONは保持します。すべて消したい場合だけ `%LOCALAPPDATA%\Tocktail` を手動で削除してください。

## ソースからのビルド

Windowsでは起動中のexeがロックされます。Tocktailを終了し、拡張も無効にしてから実行してください。

```powershell
uv sync --dev --group build
uv run --group build python scripts/build_windows.py
```

`dist/Tocktail` と `dist/Tocktail-Windows-x64.zip` を作成します。ビルド専用依存はPyInstallerです。
作成処理はインストール・レジストリ変更を行いません。ランタイム依存は増やしていません。

実際のスリープ・休止、Windowsログオン時の起動、クリーンPC、署名、実時間24時間の連続動作は手動のリリース検証が必要です。
通知の模擬送信や加速テストだけで、それらを成功したとは扱いません。
