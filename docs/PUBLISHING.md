# Publication checklist

## Source repository

- [x] Adopt MIT for original Tocktail code; add root `LICENSE`, Python metadata and extension metadata.
- [x] Use the copyright holder name `icchi5160` selected by the project owner.
- [x] Distinguish dependency licenses in `THIRD_PARTY_NOTICES.md` and both READMEs.
- [x] Initialize the local Git repository.
- [x] Exclude local activity databases, native-host configuration, virtual environments, test/build outputs,
  `.env` files, common private-key files and local account/tool directories.
- [x] Replace the real extension ID in the setup example with a placeholder.
- [x] Check source publication candidates for recognizable private keys/service tokens, personal absolute
  paths and the known local extension ID. No matches were found in that scan.
- [x] Use sample activity data for the committed UI previews.
- [x] Document the beta status and outstanding hardware/release tests.
- [x] Confirm the source publication target: `https://github.com/icchi5160/tocktail`.

The scan checks recognizable patterns in the Git publication candidates; it is not a guarantee that
every possible secret format is detected. `dist`, local configuration and activity databases must remain
excluded. Never use `git add -f` to bypass these exclusions for a release.

## Binary release (not approved by source publication)

- [ ] Audit exact bundled libraries/plugins and include their applicable copyright/license notices.
- [ ] Provide corresponding library sources and replacement/installation information as required.
- [ ] Validate packaged Chrome/Edge Native Messaging and reconnect/playback behavior.
- [ ] Verify physical Windows lock/sleep/hibernate/Modern Standby.
- [ ] Verify actual Windows logon startup.
- [ ] Verify extraction, upgrade and removal on a clean Windows 11 x64 PC.
- [ ] Complete a real-time 24-hour run; accelerated simulation is a separate accounting check.

Existing automated results: 196 Python tests including opt-in Windows tests and 12 JavaScript tests passed
in Phase 4. These results do not imply that the unchecked release tests have passed. The portable ZIP is
currently a local test artifact, not a reviewed public binary release.

## 日本語

ソースのMITライセンス、依存ライブラリとの区別、Git初期化、除外設定、秘密情報のパターン検索、個人拡張IDの置換、
未検証事項の明記まで準備しています。初回コミットの内容と公開先を確認してから公開してください。
ソースの公開先は `https://github.com/icchi5160/tocktail` です。ReleasesへのZIP添付は別途判断します。

ソースを公開しても、exeの同梱ライセンス対応や実機検証が自動的に完了するわけではありません。
配布ZIPを公開する前に、上記の未完了項目を確認してください。
