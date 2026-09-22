import test from 'node:test';
import assert from 'node:assert/strict';
import { ProjectFiles, droppedProject } from '../../static/editor/project_file.mjs';

const doc = { id: 'one', name: 'Proyecto' };
const blob = new Blob(['new package']);
const storage = () => {
  const values = new Map();
  return { get: async id => values.get(id), set: async (id, value) => values.set(id, value) };
};
function file(name = 'original.silhouettes', options = {}) {
  const log = [];
  const handle = {
    name, kind: 'file', log,
    getFile: async () => ({ name }),
    queryPermission: async () => options.permission || 'granted',
    requestPermission: async () => { log.push('permission'); return options.answer || 'granted'; },
    isSameEntry: async other => other === handle,
    createWritable: async () => {
      log.push('create');
      return {
        write: async value => { log.push(value); if (options.failWrite) throw new Error('write failed'); },
        close: async () => { if (options.failClose) throw new Error('close failed'); log.push('close'); },
        abort: async () => log.push('abort'),
      };
    },
  };
  return handle;
}

test('opened file saves in place, also after reload, without another picker', async () => {
  const saved = storage();
  const handle = file();
  const files = new ProjectFiles({ storage: saved, browser: {} });
  await files.bind(doc.id, handle);
  await files.save(doc, async () => blob);
  const restored = new ProjectFiles({ storage: saved, browser: {} });
  await restored.restore(doc.id);
  assert.deepEqual(await restored.save(doc, async () => blob), { downloaded: false, name: handle.name });
  assert.deepEqual(handle.log, ['create', blob, 'close', 'create', blob, 'close']);
});

test('first save picks before packaging, next save reuses the destination', async () => {
  const handle = file();
  const calls = [];
  const files = new ProjectFiles({ storage: storage(), browser: {
    showSaveFilePicker: async options => { calls.push('picker'); assert.equal(options.suggestedName, 'Proyecto.silhouettes'); return handle; },
  } });
  await files.save(doc, async () => { calls.push('package'); return blob; });
  await files.save(doc, async () => { calls.push('package'); return blob; });
  assert.deepEqual(calls, ['picker', 'package', 'package']);
});

test('new documents never inherit the previous document destination', async () => {
  let picks = 0;
  const original = file();
  const next = file('next.silhouettes');
  const files = new ProjectFiles({ storage: storage(), browser: { showSaveFilePicker: async () => { picks++; return next; } } });
  await files.bind(doc.id, original);
  await files.save({ id: 'two', name: 'New' }, async () => blob);
  assert.equal(picks, 1);
  assert.deepEqual(original.log, []);
  assert.equal(files.handles.get('two'), next);
});

for (const failure of ['cancel', 'write', 'close', 'package']) {
  test(`Save As ${failure} keeps previous destination and does not report success`, async () => {
    const original = file();
    const candidate = file('copy.silhouettes', { failWrite: failure === 'write', failClose: failure === 'close' });
    const files = new ProjectFiles({ storage: storage(), browser: {
      showSaveFilePicker: async () => { if (failure === 'cancel') throw new DOMException('Cancelled', 'AbortError'); return candidate; },
    } });
    await files.bind(doc.id, original);
    await assert.rejects(files.save(doc, async () => { if (failure === 'package') throw new Error('package failed'); return blob; }, { saveAs: true }));
    assert.equal(files.handles.get(doc.id), original);
    assert.deepEqual(original.log, []);
    if (['write', 'close'].includes(failure)) assert.equal(candidate.log.at(-1), 'abort');
    if (failure === 'package') assert.deepEqual(candidate.log, []);
  });
}

test('successful Save As becomes the next Save target', async () => {
  const original = file();
  const copy = file('copy.silhouettes');
  let count = 0;
  const files = new ProjectFiles({ storage: storage(), browser: { showSaveFilePicker: async () => { count++; return copy; } } });
  await files.bind(doc.id, original);
  await files.save(doc, async () => blob, { saveAs: true });
  await files.save(doc, async () => blob);
  assert.equal(count, 1);
  assert.deepEqual(original.log, []);
  assert.equal(copy.log.filter(x => x === 'close').length, 2);
});

test('denied write permission neither fetches nor downloads nor writes', async () => {
  const handle = file('denied.silhouettes', { permission: 'prompt', answer: 'denied' });
  const files = new ProjectFiles({ storage: storage(), browser: {}, download: () => assert.fail('unexpected download') });
  await files.bind(doc.id, handle);
  await assert.rejects(files.save(doc, () => assert.fail('unexpected package')), /permiso/);
  assert.deepEqual(handle.log, ['permission']);
});

test('persistence failure does not hide successful save or forget session handle', async () => {
  const handle = file();
  const files = new ProjectFiles({ storage: { get: async () => { throw Error(); }, set: async () => { throw Error(); } }, browser: {} });
  await files.restore(doc.id);
  await files.bind(doc.id, handle);
  assert.equal((await files.save(doc, async () => blob)).downloaded, false);
  assert.equal(files.handles.get(doc.id), handle);
});

test('unsupported browser explicitly returns a download, not an in-place save', async () => {
  const downloads = [];
  const files = new ProjectFiles({ storage: storage(), browser: {}, download: (...args) => downloads.push(args) });
  assert.equal((await files.save(doc, async () => blob)).downloaded, true);
  assert.deepEqual(downloads, [[blob, 'Proyecto.silhouettes']]);
  assert.equal(files.handles.has(doc.id), false);
});

test('drop captures handle synchronously and ignores image-only batches', async () => {
  const handle = file();
  let captured = false;
  const opened = droppedProject({ files: [{ name: handle.name }], items: [{ kind: 'file', getAsFileSystemHandle: () => { captured = true; return Promise.resolve(handle); } }] });
  assert.equal(captured, true);
  assert.equal((await opened).handle, handle);
  assert.equal(droppedProject({ files: [{ name: 'a.png' }] }), null);
  assert.throws(() => droppedProject({ files: [{ name: handle.name }, { name: 'b.silhouettes' }] }), /solo proyecto/);
});

test('drop without a handle can still open a portable project', async () => {
  const result = await droppedProject({ files: [{ name: 'test.silhouettes' }], items: [] });
  assert.equal(result.handle, null);
  assert.equal(result.file.name, 'test.silhouettes');
});

function folder(source, preview, otherSource = source) {
  const log = [];
  return {
    kind: 'directory', log,
    queryPermission: async () => 'granted',
    getFileHandle: async (name, options) => {
      log.push([name, options]);
      if (name === source.name) return otherSource;
      assert.equal(name, 'vista-plato.png');
      return preview;
    },
  };
}

test('preview overwrites the same sibling PNG; folder permission survives reload', async () => {
  const saved = storage(), project = file(), preview = file('vista-plato.png');
  const directory = folder(project, preview);
  let picks = 0;
  const browser = { showDirectoryPicker: async options => { picks++; assert.equal(options.startIn, project); return directory; } };
  const files = new ProjectFiles({ storage: saved, browser });
  await files.bind(doc.id, project);
  await files.savePreview(doc, async () => blob);
  const restored = new ProjectFiles({ storage: saved, browser });
  await restored.restore(doc.id);
  await restored.savePreview(doc, async () => blob);
  assert.equal(picks, 1);
  assert.equal(preview.log.filter(x => x === 'close').length, 2);
  assert.deepEqual(project.log, []);
});

test('same named project in another folder is rejected before generating or writing', async () => {
  const project = file(), preview = file('vista-plato.png');
  const wrong = folder(project, preview, file(project.name));
  const files = new ProjectFiles({ storage: storage(), browser: { showDirectoryPicker: async () => wrong } });
  await files.bind(doc.id, project);
  await assert.rejects(files.savePreview(doc, () => assert.fail('must not render')), /carpeta que contiene/);
  assert.deepEqual(preview.log, []);
});

test('Save As clears the old preview folder so it cannot overwrite another project preview', async () => {
  const project = file(), preview = file('vista-plato.png'), next = file('new.silhouettes');
  const directory = folder(project, preview);
  const files = new ProjectFiles({ storage: storage(), browser: {
    showDirectoryPicker: async () => directory, showSaveFilePicker: async () => next,
  } });
  await files.bind(doc.id, project);
  await files.savePreview(doc, async () => blob);
  await files.save(doc, async () => blob, { saveAs: true });
  assert.equal(files.directories.has(doc.id), false);
});

test('ZIP export picker starts in the current project folder and is opened before writing', async () => {
  const project = file('my-project.silhouettes');
  const zip = file('silhouettes_export_my-project.zip');
  const calls = [];
  const files = new ProjectFiles({ storage: storage(), browser: {
    showSaveFilePicker: async options => {
      calls.push(['picker', options]);
      assert.equal(options.startIn, project);
      assert.equal(options.suggestedName, 'silhouettes_export_my-project.zip');
      return zip;
    },
  } });
  await files.bind(doc.id, project);
  const target = await files.prepareExport(doc);
  calls.push(['prepared']);
  await files.writeExport(target, blob, 'fallback.zip');
  calls.push(['written']);
  assert.deepEqual(calls.map(entry => entry[0]), ['picker', 'prepared', 'written']);
  assert.deepEqual(zip.log, ['create', blob, 'close']);
});

test('cancelling the ZIP picker does not write or change the project destination', async () => {
  const project = file();
  const files = new ProjectFiles({ storage: storage(), browser: {
    showSaveFilePicker: async () => { throw new DOMException('Cancelled', 'AbortError'); },
  } });
  await files.bind(doc.id, project);
  await assert.rejects(() => files.prepareExport(doc), error => error?.name === 'AbortError');
  assert.equal(files.handles.get(doc.id), project);
  assert.deepEqual(project.log, []);
});

test('writing a ZIP never replaces the associated silhouettes handle', async () => {
  const project = file('source.silhouettes');
  const zip = file('export.zip');
  const files = new ProjectFiles({ storage: storage(), browser: {
    showSaveFilePicker: async () => zip,
  } });
  await files.bind(doc.id, project);
  const target = await files.prepareExport(doc);
  assert.deepEqual(await files.writeExport(target, blob, 'fallback.zip'), {
    downloaded: false, name: zip.name,
  });
  assert.equal(files.handles.get(doc.id), project);
  assert.deepEqual(project.log, []);
  assert.deepEqual(zip.log, ['create', blob, 'close']);
});

test('each project uses its own handle as the ZIP picker start location', async () => {
  const first = file('first.silhouettes');
  const second = file('second.silhouettes');
  const starts = [];
  const files = new ProjectFiles({ storage: storage(), browser: {
    showSaveFilePicker: async options => {
      starts.push(options.startIn);
      return file(options.suggestedName);
    },
  } });
  await files.bind('one', first);
  await files.bind('two', second);
  await files.prepareExport({ id: 'one', name: 'First' });
  await files.prepareExport({ id: 'two', name: 'Second' });
  assert.deepEqual(starts, [first, second]);
});

test('after Save As the ZIP picker starts from the new project handle', async () => {
  const original = file('old.silhouettes');
  const replacement = file('new.silhouettes');
  const zip = file('silhouettes_export_new.zip');
  const pickerOptions = [];
  let pick = 0;
  const files = new ProjectFiles({ storage: storage(), browser: {
    showSaveFilePicker: async options => {
      pickerOptions.push(options);
      pick += 1;
      return pick === 1 ? replacement : zip;
    },
  } });
  await files.bind(doc.id, original);
  await files.save(doc, async () => blob, { saveAs: true });
  await files.prepareExport(doc);
  assert.equal(pickerOptions[0].startIn, original);
  assert.equal(pickerOptions[1].startIn, replacement);
  assert.equal(pickerOptions[1].suggestedName, 'silhouettes_export_new.zip');
});

test('ZIP export falls back to download when the picker API is unavailable', async () => {
  const downloads = [];
  const project = file('portable.silhouettes');
  const files = new ProjectFiles({
    storage: storage(), browser: {}, download: (...args) => downloads.push(args),
  });
  await files.bind(doc.id, project);
  const target = await files.prepareExport(doc);
  assert.equal(target, null);
  assert.deepEqual(await files.writeExport(target, blob, 'silhouettes_export_portable.zip'), {
    downloaded: true, name: 'silhouettes_export_portable.zip',
  });
  assert.deepEqual(downloads, [[blob, 'silhouettes_export_portable.zip']]);
  assert.equal(files.handles.get(doc.id), project);
  assert.deepEqual(project.log, []);
});

test('each normal Save refreshes vista-plato.png without picking the folder again', async () => {
  const project = file('repeat.silhouettes');
  const preview = file('vista-plato.png');
  const directory = folder(project, preview);
  let picks = 0;
  let renders = 0;
  const files = new ProjectFiles({ storage: storage(), browser: {
    showDirectoryPicker: async options => {
      picks += 1;
      assert.equal(options.startIn, project);
      return directory;
    },
  } });
  await files.bind(doc.id, project);
  const getPreviewBlob = async () => { renders += 1; return new Blob([`preview-${renders}`]); };
  assert.equal((await files.save(doc, async () => blob, { getPreviewBlob })).previewSaved, true);
  assert.equal((await files.save(doc, async () => blob, { getPreviewBlob })).previewSaved, true);
  assert.equal(picks, 1);
  assert.equal(renders, 2);
  assert.equal(preview.log.filter(value => value === 'close').length, 2);
  assert.equal(project.log.filter(value => value === 'close').length, 2);
});

test('first automatic preview obtains folder write permission before packaging', async () => {
  const events = [];
  const project = file('permission.silhouettes');
  const preview = file('vista-plato.png');
  const directory = folder(project, preview);
  directory.queryPermission = async () => { events.push('query-folder'); return 'prompt'; };
  directory.requestPermission = async () => { events.push('permission-folder'); return 'granted'; };
  const files = new ProjectFiles({ storage: storage(), browser: {
    showDirectoryPicker: async () => { events.push('picker-folder'); return directory; },
  } });
  await files.bind(doc.id, project);
  await files.save(doc, async () => { events.push('package'); return blob; }, {
    getPreviewBlob: async () => { events.push('render'); return blob; },
  });
  assert.ok(events.indexOf('permission-folder') < events.indexOf('package'));
  assert.ok(events.indexOf('package') < events.indexOf('render'));
});

test('preview rendering failure keeps the saved project and reports a warning', async () => {
  const project = file('warning.silhouettes');
  const preview = file('vista-plato.png');
  const directory = folder(project, preview);
  const files = new ProjectFiles({ storage: storage(), browser: {
    showDirectoryPicker: async () => directory,
  } });
  await files.bind(doc.id, project);
  const result = await files.save(doc, async () => blob, {
    getPreviewBlob: async () => { throw new Error('render roto'); },
  });
  assert.equal(result.downloaded, false);
  assert.equal(result.previewSaved, false);
  assert.match(result.previewError, /render roto/);
  assert.deepEqual(project.log, ['create', blob, 'close']);
  assert.deepEqual(preview.log, []);
});

test('failed project write never renders or updates the automatic preview', async () => {
  const project = file('broken.silhouettes', { failWrite: true });
  const preview = file('vista-plato.png');
  const directory = folder(project, preview);
  let renders = 0;
  const files = new ProjectFiles({ storage: storage(), browser: {
    showDirectoryPicker: async () => directory,
  } });
  await files.bind(doc.id, project);
  await assert.rejects(() => files.save(doc, async () => blob, {
    getPreviewBlob: async () => { renders += 1; return blob; },
  }), /write failed/);
  assert.equal(renders, 0);
  assert.deepEqual(preview.log, []);
});

test('Save As writes the automatic preview beside the new project destination', async () => {
  const oldProject = file('old.silhouettes');
  const newProject = file('new.silhouettes');
  const oldPreview = file('vista-plato.png');
  const newPreview = file('vista-plato.png');
  const oldDirectory = folder(oldProject, oldPreview);
  const newDirectory = folder(newProject, newPreview);
  const starts = [];
  const files = new ProjectFiles({ storage: storage(), browser: {
    showSaveFilePicker: async () => newProject,
    showDirectoryPicker: async options => {
      starts.push(options.startIn);
      return options.startIn === newProject ? newDirectory : oldDirectory;
    },
  } });
  await files.bind(doc.id, oldProject);
  await files.savePreview(doc, async () => blob);
  const result = await files.save(doc, async () => blob, {
    saveAs: true, getPreviewBlob: async () => blob,
  });
  assert.equal(result.previewSaved, true);
  assert.deepEqual(starts, [oldProject, newProject]);
  assert.equal(oldPreview.log.filter(value => value === 'close').length, 1);
  assert.equal(newPreview.log.filter(value => value === 'close').length, 1);
  assert.equal(files.handles.get(doc.id), newProject);
  assert.equal(files.directories.get(doc.id), newDirectory);
});
