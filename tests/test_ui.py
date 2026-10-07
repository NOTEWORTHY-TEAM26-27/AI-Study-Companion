"""Run browser logic with Node's built-in VM when Node is available."""

import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node is required for the optional browser logic check")
class InterfaceTests(unittest.TestCase):
    def test_uploaded_documents_are_rendered(self):
        page = Path(__file__).parents[1] / "noteworthy_app" / "static" / "index.html"
        script = page.read_text().split("<script>", 1)[1].split("</script>", 1)[0]
        harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
function element() {
  return {
    children: [], textContent: '',
    replaceChildren() { this.children = []; },
    append(...nodes) { this.children.push(...nodes); }
  };
}
const elements = new Map();
const context = {
  document: {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, element());
      return elements.get(id);
    },
    createElement() { return element(); }
  },
  localStorage: { getItem() { return null; } }
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(0, 'utf8'), context);
context.renderDocuments([{name: 'biology.pdf', passages: 2}]);
assert.equal(elements.get('documents').children.length, 1);
assert.equal(elements.get('documents').children[0].textContent, 'biology.pdf · 2 passages');
context.renderDocuments([]);
assert.equal(elements.get('documents').children.length, 0);
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", harness],
            input=script,
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
