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
    children: [], textContent: '', disabled: false,
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
  uploadMode: 'ok', uploadGate: null, askGate: null, posts: 0
};
const context = {
  document: {
    // Like a browser: an id that is not on the page returns null.
    getElementById(id) {
      if (!pageIds.has(id)) return null;
      if (!elements.has(id)) elements.set(id, element());
      return elements.get(id);
    },
    createElement() { return element(); }
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
    throw new Error('Unexpected request ' + path);
  }
};
const flush = () => new Promise(resolve => setImmediate(resolve));
const el = id => context.document.getElementById(id);
const rows = () => el('documents').children;
const rowText = () => rows().map(row => row.children[0].textContent);
const choose = index => rows()[index].children[1];

(async () => {
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(0, 'utf8'), context);
  await flush(); await flush();

  // The list loads when the page opens and shows only documents uploaded from this browser.
  assert.deepEqual(rowText(), ['stored.pdf · 1 passages · Ready']);
  assert.equal(choose(0).textContent, 'Use');

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
