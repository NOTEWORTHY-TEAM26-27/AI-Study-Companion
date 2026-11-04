"""Run browser logic with Node's built-in VM when Node is available."""

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node is required for the optional browser logic check")
class InterfaceTests(unittest.TestCase):
    def test_document_list_upload_and_selection(self):
        page = Path(__file__).parents[1] / "noteworthy_app" / "static" / "index.html"
        html = page.read_text(encoding="utf-8")
        script = html.split("<script>", 1)[1].split("</script>", 1)[0]
        page_ids = re.findall(r'id="([^"]+)"', html)
        harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const pageIds = new Set(JSON.parse(process.env.PAGE_IDS));
function element() {
  return {
    children: [], textContent: '', disabled: false, style: {},
    replaceChildren() { this.children = []; },
    append(...nodes) { this.children.push(...nodes); }
  };
}
const elements = new Map();
const store = new Map([['noteworthy-my-documents', JSON.stringify(['mine1'])]]);
const server = {
  documents: [
    {id: 'mine1', name: 'stored.pdf', passages: 1, status: 'ready'},
    {id: 'other', name: 'someone-else.pdf', passages: 3, status: 'ready'}
  ],
  uploadMode: 'ok', uploadGate: null, askGate: null, posts: 0,
  quizCount: 0, submissions: [], submitFails: false, submitGate: null,
  progress: {mine1: {'Page 1': {correct: 1, total: 2}}},
  pendingProgress: null
};
const selectedAnswers = new Map([[0, {value: '1'}]]);
const context = {
  // getRandomValues is available on HTTP LAN addresses where randomUUID is absent.
  crypto: {getRandomValues: bytes => bytes.fill(0xaa)},
  document: {
    // Like a browser: an id that is not on the page returns null.
    getElementById(id) {
      if (!pageIds.has(id)) return null;
      if (!elements.has(id)) elements.set(id, element());
      return elements.get(id);
    },
    createElement() { return element(); },
    querySelector(selector) { return selectedAnswers.get(Number(selector.match(/q(\d+)/)[1])) || null; }
  },
  localStorage: {
    getItem(key) { return store.has(key) ? store.get(key) : null; },
    setItem(key, value) { store.set(key, String(value)); }
  },
  FileReader: class {
    readAsDataURL() {
      this.result = 'data:application/pdf;base64,QQ==';
      Promise.resolve().then(() => this.onload());
    }
  },
  async fetch(path, options = {}) {
    if (path === '/api/documents') return {ok: true, json: async () => ({documents: server.documents})};
    if (path.startsWith('/api/progress?')) {
      const params = new URL('http://local' + path).searchParams;
      assert.equal(params.get('learner_id'), null);
      assert.equal(options.headers['X-Learner-Id'], 'a'.repeat(32));
      if (server.pendingProgress) {
        const pending = server.pendingProgress; server.pendingProgress = null;
        await pending;
        return {ok: true, json: async () => ({topic_scores: {}})};
      }
      return {ok: true, json: async () => ({topic_scores: server.progress[params.get('document_id')] || {}})};
    }
    server.posts++;
    const body = JSON.parse(options.body);
    if (path === '/api/upload') {
      if (server.uploadGate) await server.uploadGate;
      if (server.uploadMode === 'fail') {
        return {ok: false, json: async () => ({error: 'The file could not be read as a PDF.'})};
      }
      if (server.uploadMode === 'down') {
        return {ok: false, json: async () => { throw new SyntaxError('Unexpected end of JSON input'); }};
      }
      const uploaded = {id: 'new123456', name: body.name, passages: 2, status: 'ready'};
      server.documents = server.documents.concat(uploaded);
      return {ok: true, json: async () => uploaded};
    }
    if (path === '/api/ask') {
      if (server.askGate) await server.askGate;
      return {ok: true, json: async () => ({answer: 'Answer for ' + body.document_id, sources: []})};
    }
    if (path === '/api/quiz') {
      return {ok: true, json: async () => ({quiz_id: String(++server.quizCount).padStart(32, 'c'), questions: [
        {question: 'First?', options: ['Wrong', 'Right'], topic: 'Page 1'},
        {question: 'Second?', options: ['Right', 'Wrong'], topic: 'Page 1'}
      ]})};
    }
    if (path === '/api/quiz/submit') {
      server.submissions.push(body);
      if (server.submitGate) await server.submitGate;
      if (server.submitFails) return {ok: false, json: async () => ({error: 'Could not save data.'})};
      server.progress.mine1 = {'Page 1': {correct: 1, total: 2}};
      return {ok: true, json: async () => ({correct: 1, total: 2, results: [
        {correct: true, expected_answer: 'Right', source_id: 'p1-1', page: 1},
        {correct: false, expected_answer: 'Right', source_id: 'p1-1', page: 1}
      ]})};
    }
    throw new Error('Unexpected request ' + path);
  }
};
const flush = () => new Promise(resolve => setImmediate(resolve));
const el = id => context.document.getElementById(id);
const rows = () => el('documents').children;
const rowText = () => rows().map(row => row.children[0].textContent);
const choose = index => rows()[index].children[1];
const scriptSource = fs.readFileSync(0, 'utf8');

(async () => {
  vm.createContext(context);
  vm.runInContext(scriptSource, context);
  await flush(); await flush();

  // The list loads when the page opens and shows only documents uploaded from this browser.
  assert.deepEqual(rowText(), ['stored.pdf · 1 passages · Ready']);
  assert.equal(choose(0).textContent, 'Selected');
  assert.equal(el('topicScores').children[0].children[0].textContent, 'Page 1: 50% (1/2)');

  // Files are checked before upload, and a rejected file sends no request.
  assert.equal(context.checkFile(undefined), 'Choose a PDF first.');
  assert.equal(context.checkFile({name: 'notes.txt', size: 10}), 'Choose a PDF file.');
  assert.equal(context.checkFile({name: 'empty.PDF', size: 0}), 'The file is empty. Choose a PDF that has content.');
  assert.equal(
    context.checkFile({name: 'big.pdf', size: 12 * 1024 * 1024 + 1}),
    'The PDF is larger than 12 MB. Choose a smaller file.'
  );
  assert.equal(context.checkFile({name: 'limit.pdf', size: 12 * 1024 * 1024}), '');
  el('pdf').files = [{name: 'notes.txt', size: 10}];
  await el('upload').onclick();
  assert.equal(el('status').textContent, 'Choose a PDF file.');
  assert.equal(server.posts, 0);

  // A successful upload shows Processing, then Ready, and becomes the selected document.
  let releaseUpload;
  server.uploadGate = new Promise(resolve => { releaseUpload = resolve; });
  el('pdf').files = [{name: 'notes.pdf', size: 10}];
  const uploading = el('upload').onclick();
  await flush();
  assert.deepEqual(rowText(), ['stored.pdf · 1 passages · Ready', 'notes.pdf · Processing…']);
  assert.equal(el('upload').disabled, true);
  releaseUpload(); await uploading;
  server.uploadGate = null;
  assert.deepEqual(rowText(), ['stored.pdf · 1 passages · Ready', 'notes.pdf · 2 passages · Ready']);
  assert.equal(choose(1).textContent, 'Selected');
  assert.equal(choose(1).disabled, true);
  assert.equal(el('ask').disabled, false);
  assert.equal(el('makeQuiz').disabled, false);
  assert.equal(el('status').textContent, 'Ready: 2 passages from notes.pdf.');
  assert.ok(JSON.parse(store.get('noteworthy-my-documents')).includes('new123456'));

  // A failed upload stays in the list with the server's reason.
  server.uploadMode = 'fail';
  el('pdf').files = [{name: 'bad.pdf', size: 10}];
  await el('upload').onclick();
  assert.equal(rowText().at(-1), 'bad.pdf · Failed: The file could not be read as a PDF.');
  assert.equal(el('status').textContent, 'The file could not be read as a PDF.');

  // A response with no JSON body (for example during a redeploy) shows a clear message.
  server.uploadMode = 'down';
  el('pdf').files = [{name: 'down.pdf', size: 10}];
  await el('upload').onclick();
  assert.equal(rowText().at(-1), 'down.pdf · Failed: The server is not responding. Try again in a minute.');
  server.uploadMode = 'ok';

  // Switching documents while Ask is running clears the old answer and drops the late reply.
  let releaseAsk;
  server.askGate = new Promise(resolve => { releaseAsk = resolve; });
  const asking = el('ask').onclick();
  assert.equal(el('ask').disabled, true);
  choose(0).onclick();
  assert.equal(el('status').textContent, 'Selected: stored.pdf.');
  assert.equal(el('ask').disabled, true);
  assert.equal(el('answer').textContent, '');
  releaseAsk(); await asking;
  server.askGate = null;
  assert.equal(el('answer').textContent, '');
  assert.equal(el('ask').disabled, false);
  await el('ask').onclick();
  assert.equal(el('answer').textContent, 'Answer for mine1');

  // Submit to the server, keep a failed save retryable, and never award local credit.
  await el('makeQuiz').onclick();
  let submit = el('quiz').children.at(-1);
  server.submitFails = true;
  await submit.onclick();
  assert.equal(server.submissions.length, 1);
  assert.deepEqual(server.submissions[0].answers, [1, null]);
  assert.equal(server.submissions[0].learner_id, 'a'.repeat(32));
  assert.equal('score' in server.submissions[0], false);
  assert.equal(el('quizResult').textContent, 'Could not save data.');
  assert.equal(submit.disabled, false);
  assert.equal(store.has('noteworthy-topic-scores'), false);

  // An older read for the same PDF must not replace the scores loaded after submission.
  let releaseProgress;
  server.pendingProgress = new Promise(resolve => { releaseProgress = resolve; });
  const staleProgress = context.loadScores('mine1');
  server.submitFails = false;
  await submit.onclick();
  await flush();
  assert.equal(server.submissions[1].quiz_id, server.submissions[0].quiz_id);
  assert.equal(submit.disabled, true);
  assert.equal(el('quizResult').textContent, 'Score: 1 / 2');
  assert.equal(el('topicScores').children[0].children[0].textContent, 'Page 1: 50% (1/2)');
  releaseProgress(); await staleProgress;
  assert.equal(el('topicScores').children[0].children[0].textContent, 'Page 1: 50% (1/2)');

  // Server progress is restored on selecting the document, independent of legacy local scores.
  context.showScores({});
  context.selectDocument(server.documents[0]);
  await flush();
  assert.equal(el('topicScores').children[0].children[0].textContent, 'Page 1: 50% (1/2)');

  // A late submission response must not append the old quiz's feedback to another document.
  await el('makeQuiz').onclick();
  submit = el('quiz').children.at(-1);
  let releaseSubmit;
  server.submitGate = new Promise(resolve => { releaseSubmit = resolve; });
  const submitting = submit.onclick();
  context.selectDocument(server.documents[1]);
  releaseSubmit(); await submitting; await flush();
  assert.equal(el('quizResult').textContent, '');
  assert.equal(el('quiz').children.length, 0);
  server.submitGate = null;

  // Reload restores the last selected owned PDF and its server scores, without a click.
  server.progress.new123456 = {'Page 2': {correct: 2, total: 3}};
  context.selectDocument(server.documents.find(item => item.id === 'new123456'));
  await flush();
  elements.clear();
  const reloaded = vm.createContext({...context});
  vm.runInContext(scriptSource, reloaded);
  await flush(); await flush();
  assert.equal(choose(1).textContent, 'Selected');
  assert.equal(el('topicScores').children[0].children[0].textContent, 'Page 2: 67% (2/3)');

  // A stale/foreign saved selection falls back to an owned document.
  store.set('noteworthy-selected-document', 'other');
  elements.clear();
  const staleSelection = vm.createContext({...context});
  vm.runInContext(scriptSource, staleSelection);
  await flush(); await flush();
  assert.equal(choose(0).textContent, 'Selected');
  assert.equal(el('topicScores').children[0].children[0].textContent, 'Page 1: 50% (1/2)');

  // Documents with the same name are told apart by a short ID.
  vm.runInContext('pendingUploads = []', context);
  context.renderDocuments([
    {id: 'aaaaaa111', name: 'x.pdf', passages: 1, status: 'ready'},
    {id: 'bbbbbb222', name: 'x.pdf', passages: 4, status: 'ready'}
  ]);
  assert.deepEqual(rowText(), ['x.pdf · ID aaaaaa · 1 passages · Ready', 'x.pdf · ID bbbbbb · 4 passages · Ready']);
})().catch(error => { console.error(error); process.exit(1); });
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", harness],
            input=script,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=10,
            env={**os.environ, "PAGE_IDS": json.dumps(page_ids)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
