# トヨタ「片道GO!」関東・出発監視

無料範囲で GitHub Actions から5分間隔で監視し、変更があったときだけ ntfy でAndroidへ通知します。

## 仕様

- トヨタ「片道GO!」
- 「出発」
- 「関東」
- 5分ごと
- 初回は通知せず、現在の状態を基準として保存
- 2回目以降、表示内容が変わったら通知
- Android側はntfyアプリを起動し続ける必要なし

## セットアップ

1. GitHubで新しい**公開リポジトリ**を作成。
2. このフォルダのファイルをアップロード。
3. Androidにntfyアプリをインストール。
4. 推測されにくいランダムなTopic名を作る（例: `toyota_go_7f3c...`）。
5. ntfyアプリでそのTopicを購読。
6. GitHubリポジトリの Settings → Secrets and variables → Actions → New repository secret から
   `NTFY_TOPIC` を作成し、Topic名を登録。
7. Actions → Toyota One-way GO monitor → Run workflow を1回手動実行。
8. 以後、GitHub Actionsが5分間隔で監視。

## 注意

GitHubの公開リポジトリでは、`state.json` に監視対象ページのハッシュだけが保存されます。
ntfyのTopic名は公開しないでください。公開Topicは第三者から送信される可能性があります。

GitHub Actionsのscheduled workflowは最短5分間隔です。また、公開リポジトリでは60日間活動がない場合にscheduled workflowが自動無効化される仕様があります。
