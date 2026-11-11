# Tocktail — Software Design Document

Version: 1.0  
Target OS: Windows 11  
Application Type: System Tray Activity Tracker

## 1. Overview

### 1.1 Purpose

Tocktailは、PCの使用状況を自動記録・可視化するWindows向けデスクトップアプリケーションである。

システムトレイから小さなポップアップを開くことで、現在の作業状況や使用時間を素早く確認できる。

### 1.2 Design Principles

- **Lightweight:** バックグラウンドでのCPU・メモリ使用量を抑える。
- **Privacy-first:** 記録データはローカルで管理する。
- **Reliable:** スリープ・異常終了・通信切断に対応する。
- **Extensible:** 将来的なAI分析やプロジェクト別分類に対応する。
- **Non-intrusive:** ユーザーの通常作業を妨げない。

## 2. Technology Stack

| Component | Technology |
|---|---|
| Desktop Application | Python 3.12+ |
| GUI | PySide6 |
| Windows Integration | Win32 API / pywin32 |
| Process Information | psutil |
| Database | SQLite |
| Browser Extension | JavaScript / Manifest V3 |
| Browser Communication | Native Messaging |
| Local IPC | Windows Named Pipe |
| Testing | pytest |
| Packaging | PyInstaller |

初期段階では、ネットワーク接続や外部クラウドサービスを必須としない。

## 3. Functional Requirements

### 3.1 System Tray

- Windowsのシステムトレイに常駐する。
- 左クリックでポップアップの表示・非表示を切り替える。
- 右クリックでコンテキストメニューを表示する。
- メニューには計測停止・再開、設定、終了を設ける。
- Windowsログイン時の自動起動をサポートする。

### 3.2 Popup

ポップアップには以下を表示する。

- 今日の合計使用時間
- ACTIVE / PASSIVE / IDLE の内訳
- 現在のアプリと継続時間
- 検証済みのブラウザ計測では、現在のサイトのドメイン（アプリ名の下に表示）
- 使用時間上位5アプリ
- 詳細ダッシュボードへのリンク

ポップアップの動作：

- 通常時は外部クリックで閉じる。
- ピン留め時は外部クリックでも閉じない。
- ピン留めと最前面表示を独立して制御する。
- ドラッグ移動をサポートする。
- ポップアップを閉じても計測を継続する。
- Tocktail自身を通常の使用時間に含めない。

初期サイズは幅約320pxとする。

### 3.3 Application Tracking

アクティブウィンドウを監視する。

取得情報：

- Window Handle
- Process ID
- Process Name
- Executable Path
- Window Title
- Timestamp

監視方式：

- `SetWinEventHook` によるフォアグラウンド変更検出
- 1秒間隔の補助ポーリング
- `GetLastInputInfo` による最終入力時刻取得

イベント検出処理は軽量に保ち、データ処理はワーカーへ委譲する。

### 3.4 Browser Tracking

Chrome・Edge向けの自作ブラウザ拡張機能を実装する。

収集情報：

- ブラウザ種別
- アクティブタブID
- ブラウザウィンドウID
- ページタイトル
- ドメイン
- URL（オプション）
- メディア再生状態
- イベント時刻

主な使用API：

- `chrome.tabs`
- `chrome.windows`
- `chrome.runtime.connectNative`
- Content Scripts

ブラウザ拡張機能とWindowsアプリはNative Messaging Hostを介して通信する。

Native Messaging Hostは独立プロセスとし、Windows Named Pipeを通じてメインアプリへイベントを転送する。

ブラウザ情報を利用する場合は、Windows側のフォアグラウンド情報との整合性を検証する。

複数のブラウザウィンドウが存在して対応付けが不確実な場合、サイト情報を推測で割り当ててはならない。

ブラウザ情報を取得できない場合はアプリ単位の記録を継続する。

### 3.5 Activity State

活動状態は次のように定義する。

| State | Definition |
|---|---|
| ACTIVE | 最後の入力から指定閾値未満 |
| PASSIVE | 入力がないが、利用継続の根拠がある |
| IDLE | 入力がなく、利用継続の根拠がない |
| PAUSED | ユーザーが計測を停止 |
| LOCKED | Windowsがロックされている |
| SUSPENDED | PCがスリープしている |
| UNTRACKED | 計測不能な期間 |

デフォルトのアイドル閾値は300秒。

ACTIVEとPASSIVEのみを「PC使用時間」に加算する。

MVPのPASSIVE判定は、対応ブラウザのフォアグラウンドタブでメディア再生が確認された場合を中心とする。

メディア再生は利用継続の推定材料であり、実際の視聴を保証するものではない。

### 3.6 Session Management

同一の計測対象・活動状態が連続する区間を1セッションと定義する。

セッションを分割する条件：

- アプリ変更
- ウィンドウ変更
- コンテンツ変更
- 活動状態変更
- 計測停止・再開

セッションは半開区間 `[start, end)` として扱う。

重複する時間を計上してはならない。

保存済みの最終時刻から現在までの未観測期間を、自動的にACTIVEとして補完してはならない。

### 3.7 Event Log

計測イベントと集計済みセッションを分離する。

Event Logには、以下のようなイベントを保存する。

- FOREGROUND_CHANGED
- WINDOW_TITLE_CHANGED
- BROWSER_TAB_CHANGED
- BROWSER_NAVIGATED
- MEDIA_STATE_CHANGED
- IDLE_THRESHOLD_REACHED
- ACTIVITY_RESUMED
- SESSION_LOCKED
- SESSION_UNLOCKED
- SYSTEM_SUSPENDED
- SYSTEM_RESUMED
- TRACKING_PAUSED
- TRACKING_RESUMED

イベントには次の情報を持たせる。

- Event ID
- Event Type
- Timestamp
- Source
- Payload
- Schema Version

イベントログはセッションの検証や再計算に利用する。

ただし、キーボード入力内容、マウス座標、全入力イベントを保存してはならない。

最終入力時刻の更新など、状態再構成に必要な最小限の情報のみ記録する。

イベント欠損時に完全な再構成が保証されるわけではないため、データ欠損を識別可能にする。

## 4. System Architecture

システムを以下のレイヤーに分割する。

**Monitor Layer**

Windows APIやブラウザからイベントを取得する。

**Core Layer**

イベントの正規化、活動状態判定、セッション管理を行う。

**Storage Layer**

イベントログ、セッション、設定をSQLiteに保存する。

**Presentation Layer**

システムトレイ、ポップアップ、ダッシュボードを管理する。

**Browser Integration Layer**

ブラウザ拡張機能との通信を担当する。

Core Layerは可能な限りOS・GUI・DBから独立させ、単体テスト可能にする。

## 5. Database Schema

SQLiteを使用する。

主要テーブル：

### applications

- id
- process_name
- executable_path
- display_name
- is_excluded

### contents

- id
- app_id
- content_type
- title
- domain
- url

### activity_events

- id
- event_type
- occurred_at
- source
- payload_json
- schema_version

### sessions

- id
- app_id
- content_id
- activity_state
- started_at
- ended_at
- is_open
- end_reason

### settings

- key
- value

内部時刻はUTCのUnix millisecondsを基本とする。

時間計測においては単調時計による経過時間も利用し、システム時計の変更に耐えられる設計とする。

SQLiteにはWALモードを使用する。

セッション情報はイベント発生時に保存し、継続中のセッションは5秒間隔でチェックポイントを更新する。

DBスキーマにはバージョン管理とマイグレーション機構を用意する。

## 6. Time Accounting

集計対象は指定期間とセッションの重なりから算出する。

日付をまたぐセッションは、表示時に日付ごとに分割集計する。

1つの時間区間は同時に複数のアプリへ計上しない。

アプリ使用時間、ドメイン使用時間、コンテンツ使用時間は階層的な集計として扱う。

ACTIVEとPASSIVEの合計をTotal Usageとする。

IDLE、PAUSED、LOCKED、SUSPENDED、UNTRACKEDはTotal Usageに含めない。

## 7. Recovery and Reliability

### Crash Recovery

起動時に未終了セッションを検出し、最後に保存されたチェックポイント時刻で終了する。

終了理由を `CRASH_RECOVERY` とする。

### Sleep / Resume

Windowsの電源イベントを監視し、スリープ中の時間を使用時間に含めない。

復帰時にイベントを再取得して新しいセッションを開始する。

### Lock / Unlock

Windowsロック中の時間を使用時間に含めない。

### Browser Disconnect

Native Messaging接続が失われた場合、ブラウザ詳細情報を無効化し、Windows側のアプリ監視を継続する。

### Database Failure

DBへの書き込み失敗時は、エラーを記録して安全なリトライを行う。

際限のないメモリ蓄積は避け、記録できない期間が発生した場合は欠損として扱う。

## 8. Privacy and Security

- 保存はローカルを原則とする。
- 外部APIへ計測情報を送信しない。
- 完全なURL保存はデフォルト無効。
- タイトル保存は設定で無効化できる。
- 指定アプリ・指定ドメインを除外できる。
- ブラウザ拡張機能の権限を最小限にする。
- シークレットモードの詳細追跡を標準で有効化しない。
- Named Pipeは同一ユーザーにアクセスを制限する。
- Native Messaging Hostは接続元とメッセージ形式を検証する。
- ログに機密性の高いURLやタイトルを不用意に出力しない。

除外対象と未計測期間は区別するが、プライバシー上の除外設定によってコンテンツ情報を復元できるようにしてはならない。

## 9. Non-Functional Requirements

### Performance

- 通常時は低CPU使用率を目指す。
- 計測処理がUIをブロックしない。
- ポップアップは素早く表示できるようにする。
- 不要なDB書き込みを抑制する。

性能目標は実測値に基づいて設定する。

### Stability

- 24時間以上の連続動作を検証する。
- スリープ・ロック・再接続をテストする。
- セッションの重複・負の時間を防止する。

### Maintainability

- 型ヒントを使用する。
- コンポーネントごとに責務を分割する。
- Core Layerには単体テストを用意する。
- 外部ライブラリは必要最小限とする。

## 10. Repository Structure

```text
tocktail/
├── desktop/
│   ├── src/tocktail/
│   │   ├── core/
│   │   ├── monitors/
│   │   ├── browser/
│   │   ├── storage/
│   │   ├── ui/
│   │   └── config/
│   └── tests/
├── browser-extension/
│   ├── manifest.json
│   ├── background.js
│   └── content.js
├── native-host/
├── docs/
│   └── DESIGN.md
├── scripts/
├── README.md
└── pyproject.toml
```

## 11. Development Phases

### Phase 1 — Monitoring Core

- Windowsフォアグラウンド監視
- 入力状態監視
- 状態判定
- イベントログ
- セッション管理
- SQLiteへの保存

### Phase 2 — Desktop UI

- システムトレイ常駐
- コンパクトポップアップ
- ピン留め
- 最前面表示
- 使用時間集計
- 基本設定

### Phase 3 — Browser Integration

- Chrome / Edge拡張機能
- Native Messaging Host
- Named Pipe通信
- サイト別計測
- メディア再生状態の取得

### Phase 4 — Stability and Distribution

- ロック・スリープ対応
- クラッシュ復旧
- 詳細ダッシュボード
- 自動起動
- 実行ファイル化
- インストール・アンインストール手順

## 12. Acceptance Criteria

以下を満たしたとき、MVP完成とする。

1. Windows 11で常駐できる。
2. アプリ別使用時間を記録できる。
3. Chrome・Edgeのサイト別時間を記録できる。
4. ACTIVE / PASSIVE / IDLEを区別できる。
5. トレイからポップアップを表示できる。
6. ポップアップをピン留めできる。
7. 履歴がSQLiteに保存される。
8. アプリの再起動後も履歴が残る。
9. スリープ・ロック中の時間が使用時間に加算されない。
10. 拡張機能が切断されても通常アプリの計測が継続する。
11. 計測対象の除外と一時停止ができる。
12. セッションの時間重複が発生しない。

## 13. Future Extensions

MVP完成後に検討する機能：

- プロジェクト別の作業時間分類
- VS Code・Gitとの連携
- AIコーディングエージェントの活動分析
- 作業中断・再開コスト分析
- LLMによる日報生成
- 週次・月次レポート
- ActivityWatchからのデータインポート

これらは初期実装の必須要件には含めない。
