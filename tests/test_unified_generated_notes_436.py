"""#436 formal entry points, provenance home, failure recovery and preservation."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner
from kairo.cli import app
from kairo.engine import step
from kairo.generated_note import ensure_generated_note, generation_path, read_generation
from kairo.models import ProductState
from kairo.notes import add_note, pin_note, show_notes
from kairo.provider import StubProvider
from kairo.refs import create_tag, global_home, add_tag, set_include_tags
from kairo.web.server import create_app
from kairo.workspace import Workspace
from test_run_ref_generated_note_423 import _NoteProvider

runner = CliRunner()


def setup(tmp_path, monkeypatch):
    root = tmp_path / 'root'; root.mkdir()
    ws = Workspace.init(root / 'energy', topic='energy')
    create_tag(root, 'energy')
    set_include_tags(root, 'energy', ['energy'])
    monkeypatch.setenv('KAIRO_SERVE_ROOT', str(root)); monkeypatch.chdir(ws.root)
    monkeypatch.setattr('kairo.cli.select_provider', lambda **_: StubProvider())
    monkeypatch.setattr('kairo.generated_note.select_note_provider', lambda: _NoteProvider('自动 note 事实'))
    return root, ws


def add(ws, tmp_path, name, digest=False):
    src = tmp_path / name; src.write_text('明确事实和决定。')
    rid = ws.add([src])
    if ws.root.name != 'global-home':
        add_tag(ws.root.parent, home=ws.root.name, ref_id=rid, tag='energy')
    if digest:
        (ws.references_dir()/rid/'digest.md').write_text('# 已有 digest\n\n明确事实。')
    return rid


def tower(root, ws, rid):
    home = 'global' if ws.root.name == 'global-home' else ws.root.name
    return show_notes(root, ref_id=rid, home=home)['items']


def test_s1_run_global_actual_home_and_no_duplicates(tmp_path, monkeypatch):
    root, ws = setup(tmp_path, monkeypatch)
    g = global_home(root); rid = add(g, tmp_path, 'global.txt')
    add_tag(root, home='', ref_id=rid, tag='energy')
    human = add_note(root, ref_id=rid, home='global', content='人工决定')
    pin_note(root, ref_id=rid, home='global', note_id=human['stable_id'].split('/')[-1])
    before = (g.references_dir()/rid/'note-pin.json').read_bytes()
    for _ in range(2):
        result = runner.invoke(app, ['run', '--ref', rid])
        assert result.exit_code == 0, result.output
    assert len(tower(root,g,rid)) == 2
    assert tower(root,g,rid)[0]['content'] == '人工决定'
    assert (g.references_dir()/rid/'note-pin.json').read_bytes() == before
    assert not (ws.references_dir()/rid).exists()
    assert read_generation(g,rid)['status'] == 'succeeded'


def test_s1_step_retry_and_view_entrypoints(tmp_path, monkeypatch):
    root, ws = setup(tmp_path, monkeypatch)
    rid = add(ws, tmp_path, 'local.txt')
    first = runner.invoke(app, ['step'])
    assert first.exit_code == 0, first.output
    assert len(tower(root,ws,rid)) == 1
    again = runner.invoke(app, ['retry-ref', rid])
    assert again.exit_code == 0, again.output
    assert len(tower(root,ws,rid)) == 1
    other = add(ws, tmp_path, 'view.txt')
    ws.read_manifest(other)
    result = runner.invoke(app, ['run-view', str(root), '--day', __import__('datetime').date.today().isoformat(), '--yes', '--json'])
    assert result.exit_code == 0, result.output
    assert len(tower(root,ws,other)) == 1


@pytest.mark.parametrize('note', [RuntimeError('timeout password=secret'), '', '超'*801])
def test_s2_failure_persistent_and_note_only_recovery(tmp_path, monkeypatch, note):
    root, ws = setup(tmp_path, monkeypatch); rid = add(ws,tmp_path,'fail.txt')
    provider = _NoteProvider(note)
    monkeypatch.setattr('kairo.generated_note.select_note_provider',lambda:provider)
    result = runner.invoke(app,['run','--ref',rid])
    assert result.exit_code == 1, result.output
    assert read_generation(ws,rid)['status'] == 'failed'
    assert 'password=secret' not in generation_path(ws,rid).read_text()
    assert len(provider.contexts) == 1
    runner.invoke(app,['run','--ref',rid])
    assert len(provider.contexts) == 1  # no endless automatic retries
    files=[ws.references_dir()/rid/'digest.md',ws.references_dir()/rid/'manifest.yaml',ws.root/'.kairo/state.json']
    before=[p.read_bytes() for p in files]
    monkeypatch.setattr('kairo.generated_note.select_note_provider',lambda:_NoteProvider('恢复事实'))
    result=runner.invoke(app,['notes','generate',rid,'--home','energy','--json'])
    assert result.exit_code == 0,result.output
    assert [p.read_bytes() for p in files] == before
    assert len(tower(root,ws,rid)) == 1
    assert read_generation(ws,rid)['status'] == 'succeeded'


def test_s2_concurrent_and_interrupted_recovery(tmp_path,monkeypatch):
    root,ws=setup(tmp_path,monkeypatch);rid=add(ws,tmp_path,'concurrent.txt',True)
    generation_path(ws,rid).write_text(json.dumps({'schema_version':1,'status':'running'}))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:ensure_generated_note(ws,rid),range(2)))
    assert sorted(r['status'] for r in results)==['already-generated','succeeded']
    assert len(tower(root,ws,rid))==1


@pytest.mark.parametrize("broken", ["invalid json", "{}", "[]"])
def test_s3_ref_fallback_global_pin_and_error(tmp_path,monkeypatch,broken):
    root,ws=setup(tmp_path,monkeypatch);rid=add(ws,tmp_path,'empty.txt',True)
    g=global_home(root);gr=add(g,tmp_path,'global-empty.txt',True);add_tag(root,home='',ref_id=gr,tag='energy')
    existing=add(ws,tmp_path,'existing.txt',True)
    n=add_note(root,ref_id=existing,home='energy',content='置顶正文')
    pin_note(root,ref_id=existing,home='energy',note_id=n['stable_id'].split('/')[-1])
    client=TestClient(create_app(root)); client.headers.update({"Accept-Language":"zh-CN"}); page=client.get('/w/energy').text
    assert f'hx-get="/w/energy/ref/{quote(rid,safe="")}"' in page
    assert f'hx-get="/w/energy/ref/{quote(gr,safe="")}?home=global"' in page
    assert f'hx-get="/w/energy/ref/{quote(existing,safe="")}?panel=notes"' in page
    assert '置顶正文' in client.get(f'/w/energy/ref/{existing}?panel=notes').text
    assert '明确事实' in client.get(f'/w/energy/ref/{gr}?home=global').text
    (ws.references_dir()/rid/'notes.jsonl').write_text(broken)
    page=client.get('/w/energy').text
    assert f'hx-get="/w/energy/ref/{quote(rid,safe="")}?panel=notes"' in page
    error=client.get(f'/w/energy/ref/{rid}?panel=notes').text
    assert 'notes 无法读取' in error or '洞察 notes' in error


def test_s4_preview_no_writes_apply_preserve_and_repeat(tmp_path,monkeypatch):
    root,ws=setup(tmp_path,monkeypatch);a=add(ws,tmp_path,'one.txt',True);b=add(ws,tmp_path,'two.txt',True)
    missing=add(ws,tmp_path,'no.txt');blocked=add(ws,tmp_path,'blocked.txt',True)
    state=ws.read_state();state.products[f'references/{blocked}/digest.md']=ProductState(input_hash='x',status='blocked',reason='digest-degraded');ws.write_state(state)
    before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    preview=runner.invoke(app,['notes','backfill','--topic','energy','--json'])
    assert preview.exit_code==0,preview.output
    assert {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}==before
    payload=json.loads(preview.stdout)
    assert [r['status'] for r in payload['items']].count('ready')==2
    calls=[]
    def select():
        calls.append(1);return _NoteProvider(RuntimeError('down') if len(calls)==1 else '成功正文')
    monkeypatch.setattr('kairo.generated_note.select_note_provider',select)
    done=runner.invoke(app,['notes','backfill','--topic','energy','--apply','--json'])
    assert done.exit_code==1,done.output
    assert json.loads(done.stdout)['written']==1 and json.loads(done.stdout)['failed']==1
    monkeypatch.setattr('kairo.generated_note.select_note_provider',lambda:_NoteProvider('恢复正文'))
    done=runner.invoke(app,['notes','backfill','--topic','energy','--apply','--json'])
    assert done.exit_code==0,done.output
    assert json.loads(done.stdout)['written']==1
    repeated=runner.invoke(app,['notes','backfill','--topic','energy','--apply','--json'])
    assert json.loads(repeated.stdout)['written']==0
    assert len(tower(root,ws,a))==len(tower(root,ws,b))==1
    assert not generation_path(ws,missing).exists() and not generation_path(ws,blocked).exists()


def test_s2_note_written_before_state_crash_recovers_without_model(tmp_path, monkeypatch):
    root, ws = setup(tmp_path, monkeypatch)
    rid = add(ws, tmp_path, 'written-before-crash.txt', True)
    from kairo.notes import append_generated_note
    append_generated_note(root, ref_id=rid, home='energy', content='已成功写入的事实')
    generation_path(ws, rid).write_text(json.dumps({'schema_version':1, 'status':'running'}))
    def unexpected():
        pytest.fail('已落盘 note 不能再次调用模型')
    monkeypatch.setattr('kairo.generated_note.select_note_provider', unexpected)
    result = runner.invoke(app, ['notes', 'generate', rid, '--home', 'energy', '--json'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)['written'] == 0
    assert read_generation(ws, rid)['status'] == 'succeeded'
    assert len(tower(root, ws, rid)) == 1


@pytest.mark.parametrize('entry', ['step', 'retry-ref', 'run-view'])
def test_s2_every_entry_reports_note_failure(tmp_path, monkeypatch, entry):
    root, ws = setup(tmp_path, monkeypatch)
    rid = add(ws, tmp_path, 'entry-failure.txt')
    monkeypatch.setattr('kairo.generated_note.select_note_provider', lambda: _NoteProvider(RuntimeError('note down')))
    args = [entry] if entry == 'step' else [entry, rid] if entry == 'retry-ref' else [entry, str(root), '--day', __import__('datetime').date.today().isoformat(), '--yes', '--json']
    result = runner.invoke(app, args)
    assert result.exit_code == 1, result.output
    assert read_generation(ws, rid)['status'] == 'failed'
    assert not tower(root, ws, rid)
    if entry == 'run-view':
        assert json.loads(result.stdout)['failed'] == ['energy']


def test_s2_crlf_digest_failure_converges_and_status_is_readable(tmp_path, monkeypatch):
    root, ws = setup(tmp_path, monkeypatch)
    rid = add(ws, tmp_path, 'crlf.txt', True)
    (ws.references_dir()/rid/'digest.md').write_bytes(b'# digest\r\nfacts\r\n')
    provider = _NoteProvider(RuntimeError('timeout password=secret'))
    monkeypatch.setattr('kairo.generated_note.select_note_provider', lambda: provider)
    assert ensure_generated_note(ws, rid)['status'] == 'failed'
    from kairo.generated_note import note_stale
    assert not note_stale(ws, rid)
    result = runner.invoke(app, ['notes', 'status', '--ref', rid, '--home', 'energy'])
    assert result.exit_code == 0
    assert 'note failed' in result.output and 'timeout' in result.output
    assert 'failed 1' in result.output and 'password=secret' not in result.output
    assert len(provider.contexts) == 1


@pytest.mark.parametrize('kind', ['no-digest', 'empty', 'corpus', 'blocked'])
def test_s2_generate_ineligible_is_not_success(tmp_path, monkeypatch, kind):
    root, ws = setup(tmp_path, monkeypatch)
    rid = add(ws, tmp_path, 'ineligible.txt', True)
    path = ws.references_dir()/rid/'digest.md'
    if kind == 'no-digest':
        path.unlink()
    elif kind == 'empty':
        path.write_text('   ')
    elif kind == 'corpus':
        manifest = ws.read_manifest(rid); manifest.source_class = 'corpus'; ws.write_manifest(rid, manifest)
    else:
        state = ws.read_state(); state.products[f'references/{rid}/digest.md'] = ProductState(input_hash='x', status='blocked', reason='digest-degraded'); ws.write_state(state)
    def unexpected():
        pytest.fail('不合格材料不得调用模型')
    monkeypatch.setattr('kairo.generated_note.select_note_provider', unexpected)
    result = runner.invoke(app, ['notes', 'generate', rid, '--home', 'energy', '--json'])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload['ok'] is False and payload['written'] == 0 and payload['failed'] == 1
    assert not tower(root, ws, rid)


@pytest.mark.parametrize('entry', ['all', 'view'])
def test_s2_shared_ref_failure_reported_by_clean_batch_branch(tmp_path, monkeypatch, entry):
    root, ws = setup(tmp_path, monkeypatch)
    beta = Workspace.init(root/'beta', topic='beta'); create_tag(root, 'beta'); set_include_tags(root, 'beta', ['energy'])
    for topic in [ws, beta]:
        con = topic.constitution; con.targets = []; topic.write_constitution(con)
    g = global_home(root); rid = add(g, tmp_path, 'shared-failure.txt'); add_tag(root, home='', ref_id=rid, tag='energy')
    provider = _NoteProvider(RuntimeError('note down'))
    monkeypatch.setattr('kairo.generated_note.select_note_provider', lambda: provider)
    args = ['run', '--all'] if entry == 'all' else ['run-view', str(root), '--day', __import__('datetime').date.today().isoformat(), '--yes', '--json']
    result = runner.invoke(app, args)
    assert result.exit_code == 1, result.output
    assert len(provider.contexts) == 1  # beta处理失败，energy已clean但必须报告原失败
    if entry == 'view':
        assert json.loads(result.stdout)['failed'] == ['beta', 'energy']
    else:
        assert 'failed 2' in result.output and 'energy: note failed' in result.output
        repeated = runner.invoke(app, args)
        assert repeated.exit_code == 1 and len(provider.contexts) == 1


@pytest.mark.parametrize('payload', [{'schema_version':1}, {'schema_version':1,'status':[]}, {'schema_version':1,'status':'failed','reason':{}}])
def test_s2_malformed_generation_state_has_readable_diagnostic(tmp_path, monkeypatch, payload):
    root, ws = setup(tmp_path, monkeypatch); rid = add(ws, tmp_path, 'state.txt', True)
    generation_path(ws, rid).write_text(json.dumps(payload))
    result = runner.invoke(app, ['notes', 'status', '--ref', rid, '--home', 'energy'])
    assert result.exit_code == 0, result.output
    assert 'note failed' in result.output and '状态无法读取' in result.output


@pytest.mark.parametrize("broken", ["invalid json", json.dumps({"schema_version":1,"status":"failed"}), json.dumps({"schema_version":1,"status":"failed","digest_hash":"bogus"})])
def test_s2_unreadable_state_waits_for_explicit_retry(tmp_path, monkeypatch, broken):
    root, ws = setup(tmp_path, monkeypatch); rid = add(ws, tmp_path, 'explicit-recovery.txt', True)
    path = generation_path(ws, rid); path.write_text(broken); before = path.read_bytes()
    from kairo.generated_note import note_stale
    assert not note_stale(ws, rid)
    provider = _NoteProvider('显式恢复事实')
    monkeypatch.setattr('kairo.generated_note.select_note_provider', lambda: provider)
    from kairo.rules import GeneratedNoteRule
    items = GeneratedNoteRule(ws, None).discover(ws.read_state())
    assert not any(item.is_stale(ws.read_state()) for item in items)
    assert path.read_bytes() == before and not provider.contexts
    ordinary = runner.invoke(app, ["run", "--ref", rid])
    assert ordinary.exit_code == 1, ordinary.output
    assert path.read_bytes() == before and not provider.contexts
    result = runner.invoke(app, ['notes', 'generate', rid, '--home', 'energy', '--json'])
    assert result.exit_code == 0, result.output
    assert len(provider.contexts) == 1 and read_generation(ws, rid)['status'] == 'succeeded'


def test_s1_known_refs_do_not_rescan_notes_catalog(tmp_path, monkeypatch):
    root, ws = setup(tmp_path, monkeypatch)
    refs = [add(ws, tmp_path, f'known-{i}.txt', True) for i in range(8)]
    def unexpected(*args, **kwargs):
        pytest.fail('已解析 Ref 的检查/生成/左侧导航不得再次扫描全库')
    monkeypatch.setattr('kairo.notes.list_all_refs', unexpected)
    for rid in refs:
        assert ensure_generated_note(ws, rid)['status'] == 'succeeded'
    from kairo.web.views import _split_refs
    assert len(_split_refs(ws)[0]) == len(refs)
    client = TestClient(create_app(root)); page = client.get('/w/energy')
    assert page.status_code == 200
    for rid in refs:
        assert f'hx-get="/w/energy/ref/{rid}?panel=notes"' in page.text
        assert len((ws.references_dir()/rid/'notes.jsonl').read_text().splitlines()) == 1


def test_s1_resolved_write_does_not_resurrect_deleted_ref(tmp_path, monkeypatch):
    root, ws = setup(tmp_path, monkeypatch); rid = add(ws, tmp_path, 'deleted.txt', True)
    from kairo.generated_note import _ref_record
    from kairo.notes import append_generated_note_to_ref, NotesError
    import shutil
    record = _ref_record(ws, rid); shutil.rmtree(record.dir)
    with pytest.raises(NotesError):
        append_generated_note_to_ref(record, content='不应写入已删除材料')
    assert not record.dir.exists()
