# 圧力と、その応じ方 — 自動更新サイト（デザインA）

社会的背景と対処方略の推定システム（簡略版）を、**費用ゼロ**で毎朝自動更新する静的サイトです。

- **推定ツール**（01 属性 → 02 質問 → 03 推定 → 04 報道）は、ブラウザの中だけで動きます。サーバーは不要で、入力は外部に送りません。
- **報道環境**：GDELT の GKG 生ファイルから、圧力ごとの前日の報道量・論調・直近7日の推移を集計します。日本の媒体が本集計で、BBC・ロイター・Bloomberg・NYTimes の日本関連記事は参考値です。
- **世間の反応が公共空間に供給する枠組み**：前日の日本の媒体の見出しから、LLM（無料枠）で下書きします。既定では PR で人が確認してから公開します。

GitHub の公開リポジトリ＋GitHub Actions＋GitHub Pages＋各社の無料枠だけで動きます。どれか1社の無料枠が止まっても、LLM は次の社に回り、どの工程が失敗しても前日までのデータで公開を続けます。

## 構成

| ファイル | 役割 |
|---|---|
| `collect.py` | GDELT の集計 → `data/gdelt.json`、`data/history/YYYY-MM-DD.json`、`data/articles.json`（ニュースの候補） |
| `gkg.py` | GKG の列の解析・媒体の判定・ファイルの列挙と取得（`collect.py` と scripts が使う） |
| `news_frames.py` | ニュースの枠組みの下書き → `data/news.json`（新しい順に30日分） |
| `llm.py` | 無料枠 LLM のフォールバック連鎖（Gemini → Groq → Cerebras → Cloudflare → OpenRouter） |
| `build.py` | `data/*.json` を検査して `site/data/` に置く（壊れていれば前回のファイルを残す） |
| `config/pressures.json` | 圧力ごとの GKG テーマ（`gkg_themes`）とキーワード、ニュースのカテゴリと除外条件 |
| `config/media.json` | 日本の媒体・参考媒体・除外するドメイン |
| `config/llm.json` | LLM の接続先と既定のモデル名（環境変数で上書き） |
| `scripts/check_themes.py` | `gkg_themes` が GDELT 公式のテーマ一覧に実在するか確認 |
| `scripts/check_media.py` | 媒体ドメインが実際に GKG に現れるか確認 |
| `.github/workflows/daily.yml` | 毎朝 6:47（JST）に実行 → コミット → Pages に公開 |
| `.github/workflows/test.yml` | PR と main で pytest と `build.py --check` |
| `site/index.html` | 表示ページ（`site/data/*.json` が取れなければ埋め込みの初期データで動く） |
| `tests/`, `e2e/` | pytest と Playwright |
| `collect_gdelt.py` | 旧来の DOC API 方式（`GDELT_BACKEND=doc` のときだけ使う） |
| `apps_script/Code.gs` | Apps Script 版（参考。今回の主経路ではない） |

Python 3.10 以上の標準ライブラリだけで動きます（テストに pytest、画面のテストに Playwright、BigQuery を使うときだけ `google-cloud-bigquery`）。

## 使い方

### 1. GitHub に置く

1. このフォルダを GitHub の**公開**リポジトリにする（公開リポジトリなら Actions の実行時間は無料）。
2. Settings › Pages の Source を「GitHub Actions」にする。
3. Settings › Actions › General › Workflow permissions で「Read and write permissions」と「Allow GitHub Actions to create and approve pull requests」をオンにする（`REVIEW_MODE=pr` で PR を作るため）。
4. Settings › Secrets and variables › Actions に、下の Secrets を**1つ以上**登録する。
5. Actions タブで `daily-update` を手動実行して確かめる。以降は毎朝自動で動く。

### 2. Secrets（どれも任意。1つ以上あればニュースの下書きが動く）

| Secret | 取得先 |
|---|---|
| `GEMINI_API_KEY` | Google AI Studio |
| `GROQ_API_KEY` | console.groq.com |
| `CEREBRAS_API_KEY` | cloud.cerebras.ai |
| `CF_ACCOUNT_ID` と `CF_API_TOKEN` | Cloudflare ダッシュボード（Workers AI の権限を持つトークン） |
| `OPENROUTER_API_KEY` | openrouter.ai |
| `GCP_SA_KEY` | BigQuery を使う場合だけ。サービスアカウント JSON（Workload Identity でもよい） |

キーがまったくなくても、報道環境の集計（GDELT）は動きます。ニュースの下書きだけが前日のまま残ります。

### 3. Variables（任意）

| Variable | 既定 | 意味 |
|---|---|---|
| `REVIEW_MODE` | `pr` | `pr`：ニュースは PR で確認してから公開。`auto`：確認なしで公開 |
| `LLM_ORDER` | `gemini,groq,cerebras,cloudflare,openrouter` | 試す順番 |
| `GEMINI_MODEL` ほか `*_MODEL` | `config/llm.json` の `default_model` | 使うモデル名 |
| `GDELT_BACKEND` | `gkg-files` | `gkg-files` / `bigquery` / `doc` |
| `GKG_SAMPLE` | `1` | GKG を N 本に1本だけ読む（重いとき） |
| `BQ_PROJECT` | — | BigQuery の課金先プロジェクト（サンドボックス可） |

### 4. 手元で動かす

```bash
python collect.py --date 2026-11-18
```

```bash
python news_frames.py --dry-run
```

```bash
python news_frames.py --provider groq
```

```bash
python build.py
```

```bash
python -m pytest
```

Playwright のテスト（`e2e/`）は、`pip install playwright` と `python -m playwright install chromium` のあとに `python -m pytest e2e` で動きます。390px と 1280px の画面のスクリーンショットを `e2e/screenshots/` に保存します。

## 画面と言葉づかい

- 最初の画面は「01 ニュース」です。続いて「02 立場 → 03 質問 → 04 結果」で、自分の立場でのかわし方を考えられます。
- 文言は高校生にも分かる言葉にしています（専門用語を使わない）。AI の下書きにも、同じ決まりを `news_frames.py` の指示で守らせています。
- スマホ（390px 幅）では、手順のボタンが1行に並び、横にはみ出さないことを Playwright で確かめています。日本と海外の比較は、開いて見る形にしています。

## SEO（検索エンジン向け）

公開の直前（`daily.yml` の deploy）に `python build.py --seo` が次を書きます。コミットはしません。

- `<title>`・説明文・canonical・OGP（`site/og.png`）・Twitter カード・構造化データ（JSON-LD：WebSite / WebApplication / Dataset）
- JavaScript を動かさない検索エンジンにも読める本文（その日のテーマ別の記事数と、ニュースの解説の要約）。JavaScript が動くと、いつもの画面に置きかわります。
- `sitemap.xml`（更新日つき）と `robots.txt`

サイトの URL・タイトル・説明文は `config/site.json` で変えられます。独自ドメインにしたら `url` を変えてください。共有用の画像は `python scripts/make_og.py` で作り直せます。Google Search Console に `sitemap.xml` を登録すると、検索に早く載ります（登録はサイトの持ち主の作業です）。

## データ源

### 報道環境：GDELT GKG 2.x の生ファイル（主経路）

- 15分ごとの `*.gkg.csv.zip`（英語系）と `*.translation.gkg.csv.zip`（翻訳系）を、前日（JST 0:00〜24:00＝UTC の前日 15:00〜当日 15:00）の96本ずつ、計192本読みます。**日本語の記事は翻訳系に入っている**ので、両方が必要です。
- URL は15分刻みの命名規則から組み立てます（数百MBの `masterfilelist.txt` を毎日読まないため）。まだ公開されていないファイル（404）は飛ばします。並列は2本まで、リトライ付き。
- 読むのは `DATE, SourceCommonName, DocumentIdentifier, V2Themes, V2Tone, V2Locations, TranslationInfo, Extras` だけです。まず URL だけを切り出して媒体を判定し、対象の行だけを全列に分けるので、手元では192本で約4分でした。
- **日本の媒体の判定**（`gkg.classify_media`）：記事 URL のホスト名が `jp_domains` に当たるか `.jp` で終わるもの。ただし `exclude_domains`（`go.jp`・`ac.jp`・ブログ・政党など）は外します。`jp_domains` は 2026-11-18 の全192本で実際に出現を確かめたドメインだけです（`scripts/check_media.py` で再確認できます）。
- **参考媒体**：`ref_domains` のうち、`V2Locations` の国コード（FIPS）に `JA` を含む記事だけを数えます。
- **圧力の判定**：`gkg_themes`（GDELT 公式のテーマ一覧で実在を確認済み）に当たるか、見出し・URL が `keywords` に当たる記事を数えます。翻訳系の見出しは日本語のままなので、キーワードは日本語と英語の両方を持たせています。
- **集計値**：圧力ごと・媒体群ごとに `n`（記事数）、`tone`（V2Tone の第1要素の平均、小数2桁）、`trend`（直近7日の日別の n。未取得の日は `null`）。
- 日本の媒体の件数が0の圧力が半数を超えると警告を出します（Actions では `::warning::`）。

**代替経路**：`GDELT_BACKEND=bigquery` で `gdelt-bq.gdeltv2.gkg_partitioned` を読みます。パーティションで2日分に絞り、必要な列だけを SELECT します。実行前に dry run でスキャン量を表示し、`BQ_MAX_GB`（既定 20）を超えたら中止します。**最後の手段**は `GDELT_BACKEND=doc`（DOC 2.0 API、12秒間隔＋429 時の待機。候補記事は作りません）。

### ニュースの候補記事

- GKG から前日の日本の媒体の記事を取り、`news_categories`（芸能・ゴシップ／事件）のテーマとキーワードで振り分け、それぞれ上位40件を LLM に渡します。
- 渡すのは**見出し・URL・媒体名・論調**だけです。記事本文は取得しません。地方紙への転載の重複は除きます。
- `news_exclude`（自殺・自傷、未成年、性犯罪など）に当たる記事は、LLM に渡す前に外します。最終的には LLM の規則と人の確認でも外します。

### 解説の下書き：LLM の無料枠のフォールバック連鎖

- 上から順に試し、429・5xx・タイムアウト・通信障害なら次に回します。キーがないものは飛ばします。
- 5xx・タイムアウト（混雑）のときは、20秒待って同じモデルでもう1回試します（`transient_retries`）。429 は待たずに次へ回ります。
- `*_MODEL` にはカンマ区切りで複数のモデルを書けます（例：`GEMINI_MODEL=gemini-3.8-flash,gemini-3.7-flash`）。前のモデルが駄目なら、同じ社の次のモデルを試します。
- 2026-11-19 の試験では、Gemini の無料枠で 503「high demand」や応答の停止がときどき起きました。成功したときは候補40件で約75秒でした。安定させるには、Groq など別の社のキーも1つ登録しておくのが確実です。
- 出力は JSON スキーマで検証し、不合格なら1回だけ「スキーマに合わせて直して」と再依頼し、それでも駄目なら次に回します。出典番号（`source_index`）が候補にない、見出しからの引用が15字以上・2か所以上、といった規則違反も再依頼の対象です。
- 呼び出しは1日にカテゴリごと1回（計2回）＋再依頼までです。
- 起動時に各社のモデル一覧 API でモデルの存在を確かめ、なければ警告して次に回ります。
- どのプロバイダー・モデルで作ったかを `generated_by` に残し、全件に `review_required: true` を付けます。
- **データ利用の注意**：Gemini の無料枠では、送った内容が Google の製品改善に使われることがあります。このため LLM には公開ニュースの見出し・URL・媒体名だけを送り、推定ツールの回答は一切送りません（推定ツールはブラウザの外に何も送りません）。

## 公開前の確認（REVIEW_MODE=pr）

- 報道環境の集計（数値）は、毎朝そのまま main に入って公開されます。
- ニュースの下書きは `news-review` ブランチに積まれ、PR「ニュースの枠組みの下書き（要確認）」が開きます。確認が済むまで、日々の下書きはこの1つの PR に積み重なります（翌朝の生成は、ブランチ上の編集を引き継ぎます）。
- 確認した事例は `"review_required": false` にします（サイトの「要確認」表示が消えます）。不適切な事例は削除します。チェック項目は PR の本文（`.github/review_checklist.md`）にあります。
- マージすると main の `site/` が変わり、Pages に公開し直されます。

## データ形式（site/data/）

```jsonc
// gdelt.json
{"asof":"2026-11-18","window":"1d","backend":"gkg-files",
 "source":"The GDELT Project (https://www.gdeltproject.org/)",
 "trend_dates":["2026-11-12", …, "2026-11-18"],
 "items":{"inequality":{"name":"経済状況（社会上の格差）",
   "jp":{"n":30,"tone":-1.07,"trend":[47,30,9,25,34,31,30]},
   "ref":{"n":0,"tone":null,"trend":[…]}}, …},
 "candidates":{…新しい圧力の候補（同じ形）…},
 "totals":{"jp":2141,"ref":9}, "totals_trend":{"jp":[…],"ref":[…]}}
// news.json：日付の新しい順に30日分。各 case に sources, review_required, generated_by
```

## 注意（確認が必要なもの）

外部の上限値や仕様は変わります。コードには固定せず設定に出しています。使う前に各社の画面で確かめてください。

- **無料枠の上限**：Gemini（AI Studio）、Groq、Cerebras、Cloudflare Workers AI（1日の Neuron 数）、OpenRouter の `:free` モデルは、上限と対象モデルが時期によって変わります。1日2回＋再依頼の呼び出しなので、通常はどれでも収まる見込みですが、**確認が必要**です。
- **モデル名**：`config/llm.json` の `default_model` は執筆時点の例です。Gemini の既定は `gemini-3.8-flash` です（2026-11-19 の時点で `gemini-2.5-flash` は新規ユーザーに提供されておらず、2.5 系は 2026年12月1日に廃止予定）。変わったら Variables の `GEMINI_MODEL` で差し替えてください。各社のモデル名も変わります（起動時の確認で警告が出ます）。
- **Cloudflare のモデル一覧**：`models/search` API で確認しています。OpenAI 互換エンドポイントの URL 形式と合わせて、**確認が必要**です。
- **BigQuery**：`gdelt-bq.gdeltv2.gkg_partitioned` に翻訳系の記事が入っていること、1日あたりのスキャン量（列の選び方で数GB程度の見込み）は、dry run の表示で**確認が必要**です。サンドボックスの無料枠は月1TiB。
- **ロイター**：2026-11-18 の GKG にはロイター（reuters.com）の記事が0件でした。GDELT 側で収集されていない可能性があり、参考媒体の件数は実質的に BBC・Bloomberg・NYTimes に依存します。参考媒体の日本関連記事は1日10件前後と少ないので、サイトでは直近7日の合計の割合で比べています。
- **日本の媒体の網羅**：NHK・Yahoo!ニュース・ジャパンタイムズなど、主要でも GKG に現れない媒体があります（`config/media.json` の `watch_domains`）。GDELT が集めている範囲での集計です。
- **GDELT の利用条件**：データを表示するときは、The GDELT Project の名前とリンクを必ず表示します（サイトの「04 報道」に表示しています）。

## 前提

- 推定は「はい／いいえ」にもとづく仮説で、診断・評価・選別には用いません。
- ニュースの解説は「世間の反応（報道を含む）が公共空間に供給する枠組み」だけを扱い、当事者の内心や対処方略は扱いません。
