// Adopt an existing docsync project (docs/primer/start.html adoptProject +
// commitRegistry). The counterpart to scaffolding: a project whose files
// already live in a repo is registered by reading its own manifest — nothing
// is written into the project's repo, only the host's projects.json gains an
// entry. The registry always commits to the HOST repo, even when the project
// lives elsewhere.
const { test, expect } = require('./fixtures/editor-test');

// A gh() mock that serves one project's manifest from the Contents API and
// captures whatever projects.json gets committed, per repo it was sent to.
function installGh(page, { manifest, manifestRepo, manifestBranch = 'main', manifestPath }) {
  return page.evaluate(({ manifest, manifestRepo, manifestBranch, manifestPath }) => {
    window.__calls = { registryWrites: [], trees: [] };
    window.askToken = async () => true;
    window.gh = async (repo, path, opts) => {
      // The adopted project's staged manifest, base64 like the real API.
      if (path === `contents/${manifestPath}/engine/manifest.json?ref=${manifestBranch}`
          && repo === manifestRepo) {
        return { content: btoa(JSON.stringify(manifest)) };
      }
      if (path.startsWith('contents/')) throw new Error('GitHub 404: Not Found');
      if (/git\/ref\/heads\//.test(path)) return { object: { sha: 'base' } };
      if (path.startsWith('git/commits/')) return { tree: { sha: 't0' } };
      if (path === 'git/trees') {
        const tree = JSON.parse(opts.body).tree;
        window.__calls.trees.push({ repo, tree });
        const pj = tree.find(t => t.path === 'docs/primer/projects.json');
        if (pj) window.__calls.registryWrites.push({ repo, json: JSON.parse(pj.content) });
        return { sha: 't1' };
      }
      if (path === 'git/commits') return { sha: 'c1' };
      return {};
    };
  }, { manifest, manifestRepo, manifestBranch, manifestPath });
}

test.describe('adopt an existing project', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('start.html');
    await page.waitForFunction(() => typeof adoptProject === 'function');
    // The list loads by fetch AFTER the script defines adoptProject; a test
    // that asks "is this slug already listed?" must not ask an empty list.
    await page.waitForFunction(() => REGISTRY && Object.keys(REGISTRY).length > 0);
  });

  test('the Adopt button opens a modal that asks where the project lives', async ({ page }) => {
    await page.click('#adopt');
    await expect(page.locator('#np-title')).toHaveText('Adopt an existing project');
    await expect(page.locator('#np-path-l')).toBeVisible();
    await expect(page.locator('#np-slug-l')).toBeHidden();     // slug comes from the manifest
    await expect(page.locator('#np-size-l')).toBeHidden();     // page size too
    await expect(page.locator('#np-create')).toHaveText('Adopt');
    await expect(page.locator('#np-path')).toHaveValue('docs/primer');
  });

  test('a same-repo project registers with a path base and its own deploy target', async ({ page }) => {
    await installGh(page, {
      manifest: { id: 'housing', repo: 'o/host', branch: 'release' },
      manifestRepo: 'o/host', manifestPath: 'docs/primer/projects/housing',
    });
    const out = await page.evaluate(async () => {
      window.currentRepo = () => 'o/host';       // the editor's own repo
      const slug = await adoptProject('o/host', 'main', 'docs/primer/projects/housing', 'Housing');
      return { slug, writes: window.__calls.registryWrites };
    });
    expect(out.slug).toBe('housing');
    // The registry was written to the host repo, once.
    expect(out.writes).toHaveLength(1);
    expect(out.writes[0].repo).toBe('o/host');
    const entry = out.writes[0].json.housing;
    expect(entry.base).toBe('projects/housing');     // reachable next to the editor
    expect(entry.repo).toBe('o/host');               // from the manifest
    expect(entry.branch).toBe('release');            // from the manifest
    expect(entry.name).toBe('Housing');
  });

  test('a project in another repo registers with that repo Pages URL as its base', async ({ page }) => {
    await installGh(page, {
      manifest: { id: 'other-report', repo: 'other/reports', branch: 'main' },
      manifestRepo: 'other/reports', manifestPath: 'docs/primer',
    });
    const out = await page.evaluate(async () => {
      window.currentRepo = () => 'o/host';       // NOT where the project lives
      const slug = await adoptProject('other/reports', 'main', 'docs/primer', '');
      return { slug, writes: window.__calls.registryWrites };
    });
    expect(out.slug).toBe('other-report');
    // Registry still committed to the HOST, but the base points cross-origin.
    expect(out.writes[0].repo).toBe('o/host');
    expect(out.writes[0].json['other-report'].base)
      .toBe('https://other.github.io/reports/docs/primer');
    expect(out.writes[0].json['other-report'].repo).toBe('other/reports');
  });

  test('a path with no manifest is refused with a clear message and no commit', async ({ page }) => {
    await installGh(page, {
      manifest: { id: 'x' }, manifestRepo: 'o/host', manifestPath: 'docs/primer/projects/housing',
    });
    const out = await page.evaluate(async () => {
      window.currentRepo = () => 'o/host';
      let msg = null;
      try { await adoptProject('o/host', 'main', 'nowhere', ''); }
      catch (e) { msg = e.message; }
      return { msg, writes: window.__calls.registryWrites.length };
    });
    expect(out.msg).toMatch(/no docsync project at/);
    expect(out.writes).toBe(0);
  });

  test('adopting a slug already in the list is refused', async ({ page }) => {
    await installGh(page, {
      manifest: { id: 'budget-primer', repo: 'o/host', branch: 'main' },
      manifestRepo: 'o/host', manifestPath: 'docs/primer',
    });
    const msg = await page.evaluate(async () => {
      window.currentRepo = () => 'o/host';
      try { await adoptProject('o/host', 'main', 'docs/primer', ''); return null; }
      catch (e) { return e.message; }
    });
    // budget-primer is already in the real projects.json this page loaded.
    expect(msg).toMatch(/already in your list/);
  });

  test('scaffolding into another repo commits the registry to the host, not the target', async ({ page }) => {
    const out = await page.evaluate(async () => {
      window.__seen = { treesByRepo: {} };
      window.askToken = async () => true;
      window.currentRepo = () => 'o/host';
      window.gh = async (repo, path, opts) => {
        if (path === '') return { permissions: { push: true } };
        if (/git\/ref\/heads\//.test(path)) return { object: { sha: 'base' } };
        if (path.startsWith('git/commits/')) return { tree: { sha: 't0' } };
        if (path === 'git/trees') {
          (window.__seen.treesByRepo[repo] ||= []).push(JSON.parse(opts.body).tree);
          return { sha: 't1' };
        }
        if (path === 'git/commits') return { sha: 'c1' };
        return {};
      };
      await createProject('Away', 'away', 'letter', 'main', 'other/away-repo');
      return window.__seen.treesByRepo;
    });
    // The scaffold (content.md, render_report.py, staged engine) went to the
    // target repo; that commit must NOT carry projects.json.
    const targetTree = out['other/away-repo'].flat();
    expect(targetTree.some(t => t.path === 'projects/away/content.md')).toBe(true);
    expect(targetTree.some(t => t.path === 'docs/primer/projects.json')).toBe(false);
    // The registry entry was committed to the HOST repo instead.
    const hostTree = out['o/host'].flat();
    const pj = hostTree.find(t => t.path === 'docs/primer/projects.json');
    expect(pj).toBeTruthy();
    const entry = JSON.parse(pj.content).away;
    expect(entry.repo).toBe('other/away-repo');
    expect(entry.base).toBe('https://other.github.io/away-repo/docs/primer/projects/away');
  });
});
