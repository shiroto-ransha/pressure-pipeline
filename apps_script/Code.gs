/**
 * Google Apps Script 版（サーバー不要・無料）。
 * - 毎朝の時間主導トリガーで updateAll() を実行し、GDELT の集計と
 *   Gemini によるニュース枠組みの下書きを、スクリプトのプロパティに JSON で保存する。
 * - ウェブアプリとして公開すると、doGet() が JSON を返す（?d=gdelt または ?d=news）。
 *   サイトの index.html から fetch して表示できる。
 * 設定（プロジェクトの設定 › スクリプト プロパティ）:
 *   GEMINI_API_KEY : Google AI Studio で発行したキー（無料枠）
 *   GEMINI_MODEL   : 例 gemini-2.5-flash（AI Studio で使えるモデル名に合わせる）
 * 注意: 無料枠では送った内容が Google のサービス改善に使われることがある。個人情報は送らない。
 */
const GDELT = 'https://api.gdeltproject.org/api/v2/doc/doc';
const JP = 'sourcecountry:japan';
const REF = '(domain:bbc.co.uk OR domain:bbc.com OR domain:reuters.com OR domain:bloomberg.com OR domain:nytimes.com) Japan';
const PRESSURES = {
  inequality: ['経済状況（社会上の格差）', '(inequality OR poverty OR "wealth gap")'],
  scarcity:   ['デフレ・低成長下の経済不安', '("rising prices" OR inflation OR "real wages")'],
  ethnic:     ['同化圧力・民族的ステレオタイプ', '(foreigners OR immigrants OR "foreign residents")'],
  isolation:  ['孤立・居場所のなさ', '(loneliness OR hikikomori OR "social isolation")'],
  objectify:  ['性的対象化のまなざし', '("sexual harassment" OR voyeurism OR molestation)'],
  service:    ['接客・感情労働での見下し', '("customer harassment" OR kasuhara)'],
  harass:     ['ハラスメント被害の経験', '("power harassment" OR "workplace harassment")'],
  marital:    ['結婚・出産規範', '("declining birthrate" OR "marriage rate")']
};

function gdelt_(params) {
  const url = GDELT + '?' + Object.keys(params).map(k => k + '=' + encodeURIComponent(params[k])).join('&');
  for (let a = 0; a < 4; a++) {
    Utilities.sleep(a ? 30000 * a : 12000);          // 5秒に1回の制限＋混雑時の余裕
    const r = UrlFetchApp.fetch(url, {muteHttpExceptions: true});
    if (r.getResponseCode() === 429) continue;
    const t = r.getContentText();
    if (!t.trim().startsWith('{')) return {error: t.slice(0, 80)};
    return JSON.parse(t);
  }
  return {error: 'rate limited'};
}

function tone_(q) {
  const j = gdelt_({query: q, mode: 'tonechart', timespan: '1m', format: 'json'});
  if (j.error) return j;
  let n = 0, s = 0;
  (j.tonechart || []).forEach(b => { n += b.count; s += b.bin * b.count; });
  return {n: n, tone: n ? Math.round(s / n * 100) / 100 : null};
}

function collectGdelt() {
  const out = {asof: Utilities.formatDate(new Date(), 'Asia/Tokyo', 'yyyy-MM-dd'), window: '1m',
               source: 'The GDELT Project (https://www.gdeltproject.org/)', items: {}};
  Object.keys(PRESSURES).forEach(k => {
    const [name, q] = PRESSURES[k];
    out.items[k] = {name: name, query: q, jp: tone_(q + ' ' + JP), ref: tone_(q + ' ' + REF)};
  });
  PropertiesService.getScriptProperties().setProperty('gdelt', JSON.stringify(out));
}

function draftNews() {
  const pool = [];
  [['芸能・ゴシップ', '(celebrity OR entertainer OR idol) (apology OR criticism OR backlash) ' + JP],
   ['事件', '(arrested OR sentenced OR verdict) ' + JP]].forEach(([kind, q]) => {
    const j = gdelt_({query: q, mode: 'ArtList', maxrecords: 40, timespan: '1d', format: 'json'});
    (j.articles || []).forEach(a => pool.push({kind: kind, title: a.title, url: a.url, domain: a.domain}));
  });
  if (!pool.length) return;
  const listing = pool.map((a, i) => `[${i}] (${a.kind}) ${a.title} — ${a.domain}`).join('\n');
  const rules = '見出しとメタデータに書かれたことだけを事実とし、実名を使わず人物を記号化し、推定無罪を守り、自殺・自傷・未成年が当事者のもの・性犯罪の被害の詳細・一般人が中心のものは選ばない。' +
    '当事者の内心は扱わず、世間の反応が公共空間に供給する枠組み（暗黙の規範）だけを、評価を入れずに書く。各枠組みに、刺激される正当性への過敏さ（victim/observer/beneficiary/perpetrator）を付ける。';
  const prompt = rules + '\n\n芸能・ゴシップと事件から最大3件ずつ選び、1件につき枠組みを2つ書く。JSON {"cases":[{"kind","symbol","title","summary","source_index","frames":[{"name","text","norm","places":[],"effect","js":{"f","t"}}]}]} で出力。\n\n' + listing;
  const p = PropertiesService.getScriptProperties();
  const model = p.getProperty('GEMINI_MODEL') || 'gemini-2.5-flash';
  const res = UrlFetchApp.fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, {
    method: 'post', contentType: 'application/json', muteHttpExceptions: true,
    headers: {'x-goog-api-key': p.getProperty('GEMINI_API_KEY')},
    payload: JSON.stringify({contents: [{role: 'user', parts: [{text: prompt}]}],
                             generationConfig: {responseMimeType: 'application/json', temperature: 0.4}})
  });
  const body = JSON.parse(res.getContentText());
  const result = JSON.parse(body.candidates[0].content.parts[0].text);
  const today = Utilities.formatDate(new Date(), 'Asia/Tokyo', 'yyyy-MM-dd');
  const cases = (result.cases || []).filter(c => pool[c.source_index]).map(c => {
    const a = pool[c.source_index]; delete c.source_index;
    c.sources = [{name: `${a.domain}（${today}）`, url: a.url}]; c.review_required = true; return c;
  });
  let days = JSON.parse(p.getProperty('news') || '[]').filter(d => d.date !== today);
  days.unshift({date: today, generated_by: 'gemini', cases: cases});
  p.setProperty('news', JSON.stringify(days.slice(0, 14)));   // プロパティの容量に合わせて14日分
}

function updateAll() { collectGdelt(); draftNews(); }

/** 初回に一度だけ実行: 毎朝6時台に updateAll を動かすトリガーを作る */
function installTrigger() {
  ScriptApp.newTrigger('updateAll').timeBased().atHour(6).nearMinute(45).everyDays(1).inTimezone('Asia/Tokyo').create();
}

function doGet(e) {
  const key = (e && e.parameter && e.parameter.d) === 'news' ? 'news' : 'gdelt';
  const v = PropertiesService.getScriptProperties().getProperty(key) || (key === 'news' ? '[]' : '{}');
  return ContentService.createTextOutput(v).setMimeType(ContentService.MimeType.JSON);
}
