# Chrome / Edgeの導入手順

[English](BROWSER_SETUP.md) · [Phase 3実装メモ](PHASE3.md)

拡張機能とNative Messaging Hostを登録すると、デスクトップ版で検証済みのサイト使用時間を記録できます。
管理者権限は不要です。今回の自動テストでは、実際のブラウザへの拡張導入や実HKCUへの登録は行っていません。

## 1. 仮想環境と拡張機能

```powershell
uv sync --dev
```

Chromeでは `chrome://extensions`、Edgeでは `edge://extensions` を開き、デベロッパーモードを有効にします。
「パッケージ化されていない拡張機能を読み込む」から、このリポジトリの `browser-extension` フォルダーを選択してください。
同じフォルダーを両ブラウザで利用できます。Chromium 120以上を想定しています。

拡張カードまたは拡張の設定ページから、各ブラウザの拡張IDをコピーします。IDはa～pの32文字です。
両方で同じIDになる場合も、登録コマンドには各ブラウザの指定を含めます。
シークレット／InPrivateでの動作はmanifestで禁止しています。

権限は、アクティブタブの取得、Native Messaging、再接続アラーム、現在のメディア文書IDの確認、HTTP(S)ページのメディア検出に使います。
フォーム・入力欄・ページ本文・キーボード・マウス操作の内容は読みません。

## 2. 登録ファイルの作成と確認

起動中のTocktailを終了してください。次のIDは、実際に取得したものに置き換えます。使用しないブラウザの引数は省略できます。

```powershell
uv run tocktail-browser-host prepare `
  --chrome-id YOUR_CHROME_EXTENSION_ID `
  --edge-id YOUR_EDGE_EXTENSION_ID

Get-Content .browser-host\browser-host.json
Get-Content .browser-host\com.tocktail.bridge.json
```

`prepare` は `.browser-host` に確認用JSONを作るだけで、レジストリを変更しません。
実行先がこの環境の `.venv\Scripts\tocktail-native-host.exe` であること、許可originが指定した拡張IDだけであることを確認します。

独自のDBを使う場合は `prepare` に `--db C:\path\to\your.sqlite3` を追加し、デスクトップ起動時にも同じDBを指定します。
登録した構成は1つのDBに対応し、それ以外の開発用DBではアプリ単位の計測を続けます。

## 3. 現在のWindowsユーザーに登録

確認したファイルを登録してから、デスクトップ版を起動します。

```powershell
uv run tocktail-browser-host install --from .browser-host
uv run tocktail-desktop
```

登録先は次のとおりです。

- 設定：`%LOCALAPPDATA%\Tocktail\browser-host.json`
- manifest：`%LOCALAPPDATA%\Tocktail\native-host\com.tocktail.bridge.json`
- Chrome：`HKCU\Software\Google\Chrome\NativeMessagingHosts\com.tocktail.bridge`
- Edge：`HKCU\Software\Microsoft\Edge\NativeMessagingHosts\com.tocktail.bridge`

HKCUのみを使い、ブラウザプロファイルやWindows自動起動の設定は変更しません。
プロジェクト／仮想環境を移動した場合は、`uv sync` とprepare・installをやり直してください。
Tocktailは起動時に通信設定を読むため、登録変更後は再起動します。

拡張を再読み込みするか、最大約30秒の再接続アラームを待ちます。
拡張のツールバーアイコンから接続ページを開き、**Connected to your desktop app** を確認してください。
これは通信経路の接続を示し、サイトとHWNDの対応付け成功まで保証する表示ではありません。

## 4. 記録の確認

複数ウィンドウのまま利用できます。前面ページのタイトルが、同じブラウザ種別のWindowsウィンドウの中で一意に一致する必要があります。
別ウィンドウ（最小化・別プロファイルを含む）に同名タイトルがある場合や、タイトルを取得できない場合はアプリ単位に戻します。
通常のHTTP(S)ページを数秒間フォアグラウンドにし、チェックポイントのため5秒以上待ってから確認します。

```powershell
uv run tocktail-sites
uv run tocktail-sites --day 2026-11-06
uv run tocktail-sites --db C:\path\to\your.sqlite3
```

サイト集計は読み取り専用で、ローカル日付のACTIVE＋PASSIVEをドメイン別に表示します。
保存済みチェックポイントを使うため、進行中の表示は最大約5秒遅れる場合があります。
サイト時間はアプリ時間の内訳であり、合計へ再加算しません。従来の `uv run tocktail` CLIはアプリ単位の計測です。

対応付けでは、拡張のフォーカス、WindowsのHWND、ブラウザの親プロセス、タイトル、両側のウィンドウ数を確認します。
タイトルは標準の ` - Google Chrome` または ` - Microsoft Edge` を付けた形式との完全一致が必要です。
同名タイトル、特殊タイトル、プロセス不一致、情報の欠損・遅延などで不確実なら、サイトを割り当てずアプリ単位で計測します。

MV3サービスワーカーのNative Messagingでは親HWNDが0になるため、browser window IDをHWNDとして扱っていません。
この制約は [Chromeの公式仕様](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging) に基づきます。

PASSIVEは、フォアグラウンドタブのHTML audio/videoの再生が検出でき、現在の文書IDと一致し、転送遅延込みで2.5秒以内の根拠がある場合だけです。
`tab.audible` からは推測しません。内部ページ、取得不能iframe、バッファリング・シーク・終了状態、特殊なプレーヤーなどでは判定できない場合があります。
再生状態は利用継続の推定であり、視聴していることの保証ではありません。

## プライバシーと除外

- 拡張から送るURLはorigin（スキーム・ホスト・ポート）までです。認証情報・パス・クエリ・フラグメントは送信しません。
  DBにはドメインを保存し、`contents.url` はNULLです。設計上は任意のURL保存が許容されていますが、今回はプライバシーを優先して完全URL・パス保存を実装していません。
- タイトルは対応付けのため一時的に利用します。保存は既定で無効です。保存する場合はデスクトップの **Save window titles** を有効にします。
  有効でも、対応付けが未検証のブラウザタイトルはイベントログに残しません。
- デスクトップ設定の **Excluded sites** に `example.com` を指定すると、そのサイトとサブドメインを除外します。
  検証済みの除外期間にはサイト・タイトル・アプリの識別情報を残しません。
  対応付けが不確実な間はサイトや除外を推測せず、タイトルを保存せずにアプリ時間だけを計測します。
- 変更は今後のデスクトップ記録へ適用し、過去の履歴は削除しません。CLIにはサイト除外は適用されません。
- スナップショットは最大4秒で失効し、切断通知は次の監視サンプルで反映します。再生情報の欠損でPASSIVEを延長し続けることはありません。
- Named Pipeはリモート接続を拒否し、現在ユーザーSIDだけを許可する保護DACLを設定します。
  同じWindowsユーザーで既に実行中の悪意あるプロセス同士まで隔離するものではありません。

## 切断と登録解除

拡張やブラウザの終了・再読み込み時はサイト／メディア情報を無効にし、通常のアプリ計測を続けます。
デスクトップ終了中は未接続状態を表示し、後続のハートビートで接続を再試行します。未送信の活動履歴は蓄積しません。

解除する場合は、まずTocktailを終了してから実行します。

```powershell
uv run tocktail-browser-host unregister
```

拡張の再読み込み・無効化、またはブラウザの再起動で、既存のNative Host接続も閉じてください。
解除対象は、このインストール先manifestを指すHKCU項目だけです。設定ファイル・manifest・活動履歴は保持します。
このコマンドだけで既に動作中の接続を閉じたり、活動DBを削除したりはしません。

## 検証範囲

JavaScriptはブラウザAPIのモック、Pythonは通信検証、実Windows Named Pipeと実DACL、模擬ブラウザの識別情報を使ったSQLite保存までを検証しています。
**今回の開発セッションでは、実際のChrome・Edgeへ拡張を導入した接続試験は未実施です。**
実IDで登録後、上記の手動確認を行ってください。対応付けを確認できない構成では、アプリ単位へのフォールバックを維持します。
