/* Holds common js code accessed by most pages */

  let selectedDocument = null;
  let currentQuizId = null;
  let storedDocuments = [];
  let pendingUploads = [];
  let askBusy = false;
  let quizBusy = false;
  let scoreRequest = 0;
  const MAX_PDF_BYTES = 12 * 1024 * 1024;
  const MY_DOCUMENTS_KEY = 'noteworthy-my-documents';
  const SELECTED_DOCUMENT_KEY = 'noteworthy-selected-document';
  const LEARNER_KEY = 'noteworthy-learner-id';
  let learnerId;
  try { learnerId = localStorage.getItem(LEARNER_KEY); } catch (error) { /* storage unavailable */ }
  if (!/^[0-9a-f]{32}$/.test(learnerId || '')) {
    learnerId = [...crypto.getRandomValues(new Uint8Array(16))].map(byte => byte.toString(16).padStart(2, '0')).join('');
  }
  try { localStorage.setItem(LEARNER_KEY, learnerId); } catch (error) { /* this session only */ }
  const STATUS_LABELS = {processing: 'Processing…', ready: 'Ready', failed: 'Failed'};
  const $ = id => document.getElementById(id);


  async function api(path, body) {
    const response = await fetch(path, {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'The server is not responding. Try again in a minute.');
    return data;
  }