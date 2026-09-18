const state = {
  repos: [],
  selectedRepoId: null,
  activeTab: "search",
};

// ── API ──────────────────────────────────────────────────────────────────────

async function api(method, path, body) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

// ── Repos ────────────────────────────────────────────────────────────────────

async function loadRepos() {
  state.repos = await api("GET", "/api/repos");
  renderSidebar();
}

async function addRepo(owner, name) {
  const btn = document.getElementById("add-repo-btn");
  btn.disabled = true;
  btn.textContent = "Adding…";
  try {
    await api("POST", "/api/repos", { owner, name });
    document.getElementById("repo-owner").value = "";
    document.getElementById("repo-name").value = "";
    await loadRepos();
    showToast(`Added ${owner}/${name}`);
  } catch (e) {
    showToast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "Add Repository";
  }
}

async function deleteRepo(id) {
  await api("DELETE", `/api/repos/${id}`);
  if (state.selectedRepoId === id) state.selectedRepoId = null;
  await loadRepos();
  showToast("Repository removed");
}

async function syncRepo(id) {
  const btn = document.querySelector(`[data-sync="${id}"]`);
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="sync-spinner">↻</span> Syncing…';
  }
  try {
    const res = await api("POST", `/api/repos/${id}/sync`);
    await loadRepos();
    showToast(`Synced ${res.synced} issues`);
  } catch (e) {
    showToast(e.message, true);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Sync";
    }
  }
}

function selectRepo(id) {
  state.selectedRepoId = state.selectedRepoId === id ? null : id;
  renderSidebar();
}

// ── Render sidebar ───────────────────────────────────────────────────────────

function renderSidebar() {
  const list = document.getElementById("repo-list");

  if (state.repos.length === 0) {
    list.innerHTML = '<div class="empty-repos">No repos yet. Add one above.</div>';
    return;
  }

  list.innerHTML = state.repos.map(r => {
    const selected = state.selectedRepoId === r.id ? "selected" : "";
    const synced = r.last_synced_at
      ? `${r.issue_count} issues · synced ${timeAgo(r.last_synced_at)}`
      : "Not synced yet";
    return `
      <div class="repo-item ${selected}" onclick="selectRepo(${r.id})">
        <div class="repo-name">${r.owner}/${r.name}</div>
        <div class="repo-meta">${synced}</div>
        <div class="repo-actions" onclick="event.stopPropagation()">
          <button class="btn btn-ghost btn-sm" data-sync="${r.id}" onclick="syncRepo(${r.id})">↻ Sync</button>
          <button class="btn btn-ghost btn-sm btn-danger" onclick="confirmDelete(${r.id}, '${r.owner}/${r.name}')">✕</button>
        </div>
      </div>
    `;
  }).join("");
}

function confirmDelete(id, name) {
  if (confirm(`Remove ${name} and all its cached issues?`)) deleteRepo(id);
}

// ── Search ───────────────────────────────────────────────────────────────────

async function doSearch(query) {
  const state_val = document.querySelector('input[name="search-state"]:checked').value;
  const params = new URLSearchParams({ q: query, state: state_val });
  if (state.selectedRepoId) params.set("repo_id", state.selectedRepoId);

  setStatus("search", "Searching…");
  clearEl("search-results");

  try {
    const results = await api("GET", `/api/issues/search?${params}`);
    renderSearchResults(results, query);
  } catch (e) {
    setStatus("search", `Error: ${e.message}`);
  }
}

function renderSearchResults(results, query) {
  const el = document.getElementById("search-results");

  if (results.length === 0) {
    setStatus("search", "");
    el.innerHTML = `
      <div class="empty-state">
        <div class="icon">🔍</div>
        <h3>No matching issues</h3>
        <p>Try rephrasing, or sync a repo first.</p>
      </div>`;
    return;
  }

  setStatus("search", `${results.length} result${results.length !== 1 ? "s" : ""} for "${query}"`);

  el.innerHTML = `<div class="results-grid">${results.map(r => {
    const pct = Math.round(r.score * 100);
    const scoreClass = pct >= 40 ? "score-high" : pct >= 15 ? "score-mid" : "score-low";
    return `
      <div class="result-card">
        <div class="score-badge ${scoreClass}">${pct}%</div>
        <div class="result-body">
          <div class="result-title">${escHtml(r.title)}</div>
          <div class="result-meta">
            <span class="state-badge state-${r.state}">● ${r.state}</span>
            <span>${r.owner}/${r.repo_name} #${r.number}</span>
          </div>
        </div>
        <a class="result-link" href="${r.html_url}" target="_blank" rel="noopener">
          View ↗
        </a>
      </div>`;
  }).join("")}</div>`;
}

// ── Groups ───────────────────────────────────────────────────────────────────

async function loadGroups() {
  const threshold = document.getElementById("threshold-slider").value;
  const state_val = document.querySelector('input[name="groups-state"]:checked').value;
  const params = new URLSearchParams({ threshold, state: state_val });
  if (state.selectedRepoId) params.set("repo_id", state.selectedRepoId);

  setStatus("groups", "Loading groups…");
  clearEl("groups-results");

  try {
    const groups = await api("GET", `/api/issues/groups?${params}`);
    renderGroups(groups);
  } catch (e) {
    setStatus("groups", `Error: ${e.message}`);
  }
}

function renderGroups(groups) {
  const el = document.getElementById("groups-results");

  if (groups.length === 0) {
    setStatus("groups", "");
    el.innerHTML = `
      <div class="empty-state">
        <div class="icon">⬡</div>
        <h3>No groups found</h3>
        <p>Sync a repo first, or lower the similarity threshold.</p>
      </div>`;
    return;
  }

  const multiCount = groups.filter(g => g.issues.length > 1).length;
  const totalIssues = groups.reduce((s, g) => s + g.issues.length, 0);
  setStatus(
    "groups",
    `${totalIssues} issues in ${groups.length} groups · ${multiCount} potential duplicate${multiCount !== 1 ? "s" : ""}`
  );

  el.innerHTML = `<div class="results-grid">${groups.map((g, i) => {
    const isMulti = g.issues.length > 1;
    const tokens = g.common_tokens.map(t => `<span class="token-chip">${escHtml(t)}</span>`).join("");
    const label = isMulti
      ? `${g.issues.length} similar issues`
      : "Unique issue";

    return `
      <div class="group-card ${isMulti ? "multi" : ""}">
        <div class="group-header">
          <span class="group-count">${g.issues.length}</span>
          <span class="group-title">${label}</span>
          ${tokens ? `<div class="token-chips">${tokens}</div>` : ""}
        </div>
        <div class="group-issues">
          ${g.issues.map(issue => `
            <a class="group-issue-row" href="${issue.html_url}" target="_blank" rel="noopener">
              <span class="issue-number">#${issue.number}</span>
              <span class="state-badge state-${issue.state}">●</span>
              <span class="issue-title">${escHtml(issue.title)}</span>
              <span class="issue-repo">${issue.owner}/${issue.repo_name}</span>
            </a>
          `).join("")}
        </div>
      </div>`;
  }).join("")}</div>`;
}

// ── Tabs ─────────────────────────────────────────────────────────────────────

function switchTab(name) {
  state.activeTab = name;
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".view").forEach(v => v.classList.toggle("active", v.id === `view-${name}`));
}

// ── Utilities ────────────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function setStatus(view, msg) {
  const el = document.getElementById(`${view}-status`);
  el.textContent = msg;
  el.classList.toggle("hidden", !msg);
}

function clearEl(id) {
  document.getElementById(id).innerHTML = "";
}

function timeAgo(iso) {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

let toastTimer;
function showToast(msg, isError = false) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.toggle("error", isError);
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 3000);
}

// ── Init ─────────────────────────────────────────────────────────────────────

function init() {
  // Tab switching
  document.querySelectorAll(".tab").forEach(btn => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  // Add repo form
  document.getElementById("add-repo-form").addEventListener("submit", e => {
    e.preventDefault();
    const owner = document.getElementById("repo-owner").value.trim();
    const name = document.getElementById("repo-name").value.trim();
    if (owner && name) addRepo(owner, name);
  });

  // Search form
  document.getElementById("search-form").addEventListener("submit", e => {
    e.preventDefault();
    const q = document.getElementById("search-input").value.trim();
    if (q) doSearch(q);
  });

  // Groups threshold slider
  const slider = document.getElementById("threshold-slider");
  const thresholdVal = document.getElementById("threshold-value");
  slider.addEventListener("input", () => {
    thresholdVal.textContent = parseFloat(slider.value).toFixed(2);
  });

  // Load groups button
  document.getElementById("load-groups-btn").addEventListener("click", loadGroups);

  loadRepos();
}

document.addEventListener("DOMContentLoaded", init);
