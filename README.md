# 圧力と、その応じ方 — 自動更新パイプライン

社会的背景と対処方略の推定システム（簡略版）を、毎朝自動で更新するためのコード一式です。

- **報道環境**：GDELT から、圧力ごとの報道量と論調（トーン）を集計する。
  - 本集計は日本の媒体（`sourcecountry:japan`）に限る。
  - BBC・ロイター・Bloomberg・NYTimes の日本関連記事は、参考値として別に取得する。
- **ニュースの枠組み**：その日の日本の媒体の見出し（GDELT のメタデータ）から事例を選び、「世間の反応が公共空間に供給する枠組み」を LLM で下書きする。
  - LLM は Gemini（無料枠）または Claude を選べる。
- **サイト**：`site/index.html` が `site/data/*.json` を読み込んで表示する（デザインA：エディトリアル）。

## 構成

| ファイル | 役割 |
|---|---|
| `config/pressures.json` | 圧力キーと GDELT の検索語、媒体の絞り込み条件 |
| `collect_gdelt.py` | GDELT の集計 → `data/gdelt.json` |
| `news_frames.py` | ニュースの枠組みの下書き → `data/news.json`（30日分） |
| `.github/workflows/daily.yml` | 毎朝 6:47（日本時間）に上の2つを実行し、GitHub Pages に公開 |
| `apps_script/Code.gs` | Google Apps Script 版（サーバー不要。JSON をウェブアプリとして配信） |
| `site/index.html` | 表示用のページ（データがなくても初期値で動く） |

外部ライブラリは使っていません。Python 3.10 以上の標準ライブラリだけで動きます。

## 使い方 A：GitHub Actions ＋ GitHub Pages

1. このフォルダを GitHub のリポジトリにする。
2. Settings › Secrets and variables › Actions に、次を登録する。
   - Secrets `GEMINI_API_KEY`：Google AI Studio で発行したキー
   - 任意で Variables `GEMINI_MODEL`：AI Studio で使えるモデル名。既定は `gemini-2.5-flash`
   - Claude を使う場合：Secrets `ANTHROPIC_API_KEY` と、Variables `LLM_PROVIDER=anthropic`
3. Settings › Pages の Source を「GitHub Actions」にする。
4. Actions タブで `daily-update` を手動実行して、動作を確かめる。以降は毎朝自動で動く。

## 使い方 B：Google Apps Script（無料・サーバー不要）

1. script.google.com で新しいプロジェクトを作り、`apps_script/Code.gs` を貼り付ける。
2. プロジェクトの設定 › スクリプト プロパティに `GEMINI_API_KEY`（任意で `GEMINI_MODEL`）を登録する。
3. `installTrigger` を一度だけ実行し、毎朝の実行を登録する。
4. 「デプロイ › ウェブアプリ」で公開し、URL に `?d=gdelt` または `?d=news` を付けると JSON が返る。
   `site/index.html` の `fetch('data/…')` を、この URL に書き換えて使う。

Apps Script の1回の実行は6分までです。GDELT の待ち時間が長い日は、`PRESSURES` の数を減らしてください。

## 注意

- **GDELT の制限**：「5秒に1回まで」の制限があり、混雑時は 429 が続きます。コードは12秒間隔で問い合わせ、429 のときは30秒以上待って再試行します。データを表示するときは、The GDELT Project の名前とサイトへのリンクを必ず表示してください。
- **検索語は英語**：GDELT は記事を機械翻訳して検索するため、英語の検索語で日本の媒体を対象にしています。件数が少ない圧力は、`config/pressures.json` で言い換えを増やしてください。
- **ニュースは「下書き」**：LLM の出力には `review_required: true` が付きます。LLM が見るのは記事の見出しと媒体名だけで、本文は取得しません。それでも、事件や実在の人物を扱うため、公開前に人が確認する運用にしてください（記号化、推定無罪、名誉・プライバシーへの配慮）。
- **Gemini の無料枠**：送った内容が Google のサービス改善に使われることがあります。個人情報は送らないでください。無料枠の上限とモデル名は時期によって変わるため、AI Studio で確認してください。
- **推定は仮説**：診断・評価・選別には用いません。
