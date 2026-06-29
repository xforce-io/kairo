# 会议=多形态 reference,合并 digest 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让一条会议 reference 承载多形态素材(音频/文档×N/图片×N),合并产出一份 digest,加素材后指纹变化触发全量重算。

**Architecture:** 引擎层打破「单源」假设——`TransformRule` 按源逐个派生、`DigestRule` 拼接全部正文并把图片目录授给多模态 agent 读;摄入层 `add()` 支持追加到已有 ref;Web 层加 ref 详情「附加素材」入口。digest 是指纹驱动的全量重算,增量只发生在上层 understanding 折叠。

**Tech Stack:** Python 3.11+, pydantic v2, FastAPI + Jinja2 + htmx, pytest。后端 whisper(本机命令)/ markitdown(进程内)/ claude-code(多模态 agent)。

## Global Constraints

- 设计文档(source of truth):`docs/design/44-meeting-multiform-digest.md`(issue #44)。
- role 名:图片 = `attachment`;它不在 `body_roles`、不被任何 transform 消费。
- `body_roles` 保持 `["transcript", "source_text"]`。
- digest 全量重算;`input_hash` = 对会议当前全部输入的指纹,相同则跳过(`step` 幂等)。
- 不引入新依赖、不引入视觉 backend;图片靠多模态 agent 的 Read「看」(stub/codex 降级为仅文本,不报错)。
- 测试命令:`source .venv/bin/activate && python -m pytest`。
- 提交信息以 `#44` 起头;Co-Authored-By 行见仓库约定。
- 分支:`feat/44-meeting-multiform-digest`(已基于含 #42 的 main)。

---

## File Structure

- `src/kairo/models.py` — `_default_roles_by_ext` 增加图片扩展名 → `attachment`。
- `src/kairo/workspace.py` — `add()` 追加到已有 ref(去重不覆盖)。
- `src/kairo/rules.py` — `TransformRule` 多源派生;`DigestRule` 多正文拼接 + 看图 + 指纹。
- `src/kairo/web/views.py` — 新路由 `POST /w/{slug}/ref/{ref_id}/attach`;`_save_upload_to` 复制进 ref 目录。
- `src/kairo/web/templates/_ref_meta.html` — ref 详情加「附加素材」弹框入口。
- `src/kairo/web/i18n.py` — 新增字符串(en+zh)。
- 测试:`tests/test_workspace*.py`(或就近)、`tests/test_rules*.py`、`tests/test_web_write.py`。

---

## Task 1: 图片扩展名 → attachment role

**Files:**
- Modify: `src/kairo/models.py`(`_default_roles_by_ext`,约 74-78 行)
- Test: `tests/test_models_roles.py`(新建)

**Interfaces:**
- Produces: `Constitution().roles_by_ext` 含 `{".png": "attachment", ".jpg": "attachment", ".jpeg": "attachment", ".webp": "attachment", ".heic": "attachment"}`;`Workspace.guess_role(Path("x.png")) == "attachment"`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_models_roles.py
from kairo.models import Constitution

def test_image_exts_map_to_attachment():
    rbe = Constitution().roles_by_ext
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".heic"):
        assert rbe[ext] == "attachment", ext

def test_audio_and_document_roles_unchanged():
    rbe = Constitution().roles_by_ext
    assert rbe[".m4a"] == "audio"
    assert rbe[".pdf"] == "document"
```

- [ ] **Step 2: 运行,确认失败**

Run: `source .venv/bin/activate && python -m pytest tests/test_models_roles.py -v`
Expected: FAIL（KeyError: '.png'）

- [ ] **Step 3: 实现**

`src/kairo/models.py` 顶部扩展名常量区(`_DOCUMENT_EXTS` 之后)加:

```python
# 图片:作附件 form 挂在会议下,不转文本、由多模态 agent 在 digest 时 Read 看图(#44)。
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".heic")
```

`_default_roles_by_ext` 改为:

```python
def _default_roles_by_ext() -> dict[str, str]:
    return {
        **{e: "audio" for e in _AUDIO_EXTS},
        **{e: "document" for e in _DOCUMENT_EXTS},
        **{e: "attachment" for e in _IMAGE_EXTS},
    }
```

- [ ] **Step 4: 运行,确认通过**

Run: `source .venv/bin/activate && python -m pytest tests/test_models_roles.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/kairo/models.py tests/test_models_roles.py
git commit -m "feat: #44 图片扩展名映射到 attachment role"
```

---

## Task 2: `add()` 支持追加到已有 reference(去重不覆盖)

**Files:**
- Modify: `src/kairo/workspace.py`(`add`,约 85-123 行)
- Test: `tests/test_workspace_append.py`(新建)

**Interfaces:**
- Consumes: `Workspace.add(files, ref_id=None, role=None, title=None, source_class=None)`。
- Produces: 当 `ref_id` 指向已存在 ref 时,`add` **追加** forms 到其 manifest(按 `location` 去重),保留既有 forms,返回该 `ref_id`;新 ref 行为不变。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_workspace_append.py
from kairo.workspace import Workspace

def test_add_appends_to_existing_ref_without_clobber(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    a = tmp_path / "a.txt"; a.write_text("aaa")
    rid = ws.add([a])                      # 建一条 ref(transcript 兜底 role)
    b = tmp_path / "b.png"; b.write_bytes(b"\x89PNG\r\n")
    rid2 = ws.add([b], ref_id=rid)         # 追加图片到同一条
    assert rid2 == rid
    man = ws.read_manifest(rid)
    roles = sorted(f.role for f in man.forms)
    assert roles == ["attachment", "transcript"]  # 原有未被覆盖

def test_add_dedups_by_location(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    a = tmp_path / "a.txt"; a.write_text("aaa")
    rid = ws.add([a])
    ws.add([a], ref_id=rid)                # 同一文件再加 → 不重复
    man = ws.read_manifest(rid)
    assert sum(1 for f in man.forms if f.location == str(a)) == 1
```

- [ ] **Step 2: 运行,确认失败**

Run: `source .venv/bin/activate && python -m pytest tests/test_workspace_append.py -v`
Expected: FAIL（第一个断言 roles 只有 ["attachment"]，因为旧 manifest 被覆盖）

- [ ] **Step 3: 实现**

`src/kairo/workspace.py` 的 `add()`,把构造 manifest 那段(从 `forms = [` 到 `write_text(...)`)替换为「先建新 forms,再与既有合并去重」:

```python
        ref_dir = self.references_dir() / ref_id
        existing = ref_dir / "manifest.yaml"
        new_forms = [
            Form(
                role=role or self.guess_role(f),
                location=str(f),
                hash=hashlib.sha256(f.read_bytes()).hexdigest()[:12],
                origin="added",
            )
            for f in files
        ]
        if existing.is_file():
            # 追加到已有 ref:保留既有 forms,按 location 去重
            man = self.read_manifest(ref_id)
            have = {fm.location for fm in man.forms}
            man.forms.extend(fm for fm in new_forms if fm.location not in have)
        else:
            ref_dir.mkdir(parents=True, exist_ok=True)
            man = Manifest(
                id=ref_id,
                title=title or files[0].stem,
                source_class=source_class or self.constitution.default_class,
                forms=new_forms,
            )
        self.write_manifest(ref_id, man)
        return ref_id
```

（注意:删除原来从 `ref_dir.mkdir(...)` 到旧 `(ref_dir / "manifest.yaml").write_text(...)` 的整段,用上面替换。`hashlib`/`Form`/`Manifest` 已在文件顶部导入。)

- [ ] **Step 4: 运行,确认通过**

Run: `source .venv/bin/activate && python -m pytest tests/test_workspace_append.py -v`
Expected: PASS

- [ ] **Step 5: 跑既有 workspace 测试防回归**

Run: `source .venv/bin/activate && python -m pytest tests/ -k "workspace or web_write" -q`
Expected: PASS（新建 ref 行为不变）

- [ ] **Step 6: 提交**

```bash
git add src/kairo/workspace.py tests/test_workspace_append.py
git commit -m "feat: #44 add() 支持追加到已有 reference,按 location 去重不覆盖"
```

---

## Task 3: `TransformRule` 多源派生

**Files:**
- Modify: `src/kairo/rules.py`(`TransformRule.discover` 约 80-91、`_make` 约 93-135)
- Test: `tests/test_rules_multisource.py`(新建)

**Interfaces:**
- Consumes: `_slug`（rules.py 已有的 slug 函数;若无则用 `kairo.workspace._slug`——见 Step 3 说明）。
- Produces: 一条 ref 有 N 个同 consumed-role 源 → 各派生一份 `references/{ref_id}/{produces}.{slug(stem)}.md`;已存在旧 `{produces}.md`(legacy 单源)视为已派生不重跑。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_rules_multisource.py
import os
from kairo.workspace import Workspace

def test_multiple_documents_each_get_source_text(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.engine import step
    from kairo.provider import select_provider
    ws = Workspace.init(tmp_path / "ws", topic="t")
    d1 = tmp_path / "deck.pdf"; d1.write_bytes(b"%PDF-1.4 a")
    d2 = tmp_path / "notes.pdf"; d2.write_bytes(b"%PDF-1.4 b")
    rid = ws.add([d1])
    ws.add([d2], ref_id=rid)               # 同一 ref 两个 document
    step(ws, select_provider())
    man = ws.read_manifest(rid)
    st_locs = sorted(f.location for f in man.forms if f.role == "source_text")
    assert len(st_locs) == 2, st_locs      # 两份各自派生
    assert any("deck" in l for l in st_locs) and any("notes" in l for l in st_locs)
```

- [ ] **Step 2: 运行,确认失败**

Run: `source .venv/bin/activate && python -m pytest tests/test_rules_multisource.py -v`
Expected: FAIL（只有 1 份 source_text，第二个 document 被忽略）

- [ ] **Step 3: 实现**

在 `src/kairo/rules.py` 顶部确保有 slug:文件已 `from kairo.workspace import ...`?若没有,加：

```python
from kairo.workspace import _slug
```

`TransformRule.discover` 改为按源逐个判断(替换原 80-91 的 discover 主体循环内逻辑):

```python
    def discover(self, state: State | None = None) -> list[WorkItem]:
        items: list[WorkItem] = []
        for ref_id in self.ws.list_reference_ids():
            man = self.ws.read_manifest(ref_id)
            sc = self.ws.constitution.source_classes.get(man.source_class)
            if sc is not None and not sc.fold:
                continue
            srcs = [f for f in man.forms if f.role in self.consumes]
            if not srcs:
                continue
            roles = {f.role for f in man.forms}
            produced_locs = {f.location for f in man.forms if f.role == self.produces}
            legacy = f"references/{ref_id}/{self.produces}.md"
            if len(srcs) == 1:
                # 单源:与原逻辑一致——produces role 已存在(不论来源)则跳过,产 legacy 名
                if self.produces not in roles:
                    items.append(self._make(ref_id, srcs[0], legacy))
            else:
                # 多源:每源独立派生,用 keyed 名;legacy(若存在)归属第一个源
                for i, src in enumerate(srcs):
                    keyed = f"references/{ref_id}/{self.produces}.{_slug(Path(src.location).stem)}.md"
                    done = keyed in produced_locs
                    if not done and i == 0 and legacy in produced_locs:
                        done = True  # 迁移:legacy {produces}.md 归属第一个源
                    if not done:
                        items.append(self._make(ref_id, src, keyed))
        return items

> **实现修正(commit 5728bb5)**:单源必须沿用 legacy `{produces}.md`(否则破坏既有 transcript.md 与 ~17 个测试);多源分支的 legacy 识别须加 `i == 0` guard,否则单→多迁移会把第二个源静默丢弃或重复第一个源。`_make(ref_id, src: Form, key)` 签名亦随之调整。
```

`_make` 改为接收具体 src 与目标 key(替换原 `_make(self, ref_id)`):

```python
    def _make(self, ref_id: str, src, key: str) -> WorkItem:
        input_hash = src.hash
        loc = Path(src.location)
        src_path = loc if loc.is_absolute() else self.ws.root / loc

        def run(state: State) -> None:
            if not src_path.exists():
                state.products[key] = ProductState(
                    input_hash=input_hash, status="blocked", reason="missing-source"
                )
                return
            if os.environ.get("KAIRO_STUB"):
                content = (
                    f"⚠️ STUB {self.produces.upper()}\n"
                    f"(source: {src.location}, hash: {src.hash})\n"
                    f"[stub 占位:无真实 {self.backend} 后端]\n"
                )
                self._emit(ref_id, key, content, f"{self.backend}-from:{src.hash}")
                state.products[key] = ProductState(
                    input_hash=input_hash,
                    produced_by={"provider": self.backend, "model": "stub"},
                )
                return
            outcome = run_backend(self.backend, src_path, src.hash)
            if outcome[0] == "blocked":
                state.products[key] = ProductState(
                    input_hash=input_hash, status="blocked", reason=outcome[1]
                )
                return
            _, text, origin = outcome
            self._emit(ref_id, key, text, origin)
            state.products[key] = ProductState(
                input_hash=input_hash,
                produced_by={"provider": self.backend, "model": origin},
            )

        def is_stale(state: State) -> bool:
            ps = state.products.get(key)
            return ps is None or ps.input_hash != input_hash

        return WorkItem(key, input_hash, run, is_stale)
```

（`_emit` 已是 `_emit(ref_id, key, content, origin)`,写 `ws.root/key` 并往 manifest 追加 `Form(role=produces, location=key, ...)`,无需改。）

- [ ] **Step 4: 运行,确认通过 + 防回归**

Run: `source .venv/bin/activate && python -m pytest tests/test_rules_multisource.py tests/ -k "rules or transform or web_tasks or asr" -q`
Expected: PASS（单源音频仍走 legacy `transcript.md`,既有测试不破）

- [ ] **Step 5: 提交**

```bash
git add src/kairo/rules.py tests/test_rules_multisource.py
git commit -m "feat: #44 TransformRule 按源逐个派生,支持同 role 多源"
```

---

## Task 4: `DigestRule._read_body` 拼接全部正文 form

**Files:**
- Modify: `src/kairo/rules.py`(`DigestRule._read_body` 约 263-270)
- Test: `tests/test_rules_digest_body.py`(新建)

**Interfaces:**
- Produces: `DigestRule._read_body(man)` 返回该 ref **全部** body_role form 的拼接(每段以 `# <文件名>` 起),无正文返回 `None`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_rules_digest_body.py
from kairo.workspace import Workspace
from kairo.models import Form
from kairo.provider import StubProvider
from kairo.rules import DigestRule

def test_read_body_concatenates_all_body_forms(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    rdir = ws.references_dir() / "m"; rdir.mkdir(parents=True)
    (rdir / "transcript.md").write_text("会议口语转写")
    (rdir / "source_text.deck.md").write_text("PPT 正文要点")
    man = ws.read_manifest("m") if (rdir / "manifest.yaml").is_file() else None
    from kairo.models import Manifest
    man = Manifest(id="m", title="m", forms=[
        Form(role="transcript", location="references/m/transcript.md", hash="x"),
        Form(role="source_text", location="references/m/source_text.deck.md", hash="y"),
    ])
    body = DigestRule(ws, StubProvider())._read_body(man)
    assert "会议口语转写" in body and "PPT 正文要点" in body
    assert body.index("会议口语转写") < body.index("PPT 正文要点")  # transcript 在前
```

- [ ] **Step 2: 运行,确认失败**

Run: `source .venv/bin/activate && python -m pytest tests/test_rules_digest_body.py -v`
Expected: FAIL（只含 transcript，source_text 被丢）

- [ ] **Step 3: 实现**

`DigestRule._read_body` 替换为:

```python
    def _read_body(self, man) -> str | None:
        chunks: list[str] = []
        for role in self.ws.constitution.body_roles:
            forms = sorted(
                (f for f in man.forms if f.role == role),
                key=lambda f: f.location,
            )
            for f in forms:
                loc = Path(f.location)
                p = loc if loc.is_absolute() else self.ws.root / loc
                if p.is_file():
                    chunks.append(f"# {p.name}\n\n{p.read_text()}")
        return "\n\n".join(chunks) if chunks else None
```

- [ ] **Step 4: 运行,确认通过 + 防回归**

Run: `source .venv/bin/activate && python -m pytest tests/test_rules_digest_body.py tests/ -k "digest" -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/kairo/rules.py tests/test_rules_digest_body.py
git commit -m "feat: #44 DigestRule 拼接全部正文 form(转写+多份 source_text)"
```

---

## Task 5: DigestRule 指纹纳入 attachment + 看图(read_dirs)

**Files:**
- Modify: `src/kairo/rules.py`(`DigestRule.discover` 约 272-284、`_make` 约 286-311)
- Test: `tests/test_rules_digest_attach.py`(新建)

**Interfaces:**
- Consumes: `_run_agent(provider, persona, context, artifact, read_dirs=None)`(已有)。
- Produces: digest `input_hash` = `_hash(prompt + "\n" + body + "\n" + "".join(sorted(attachment.hash)))`;有 attachment 时 `_make` 给 agent 传 `read_dirs=[ref_dir]` 且 persona 列出图片绝对路径。新增图片 → 指纹变 → stale。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_rules_digest_attach.py
from kairo.workspace import Workspace
from kairo.models import Manifest, Form
from kairo.provider import StubProvider
from kairo.rules import DigestRule

def _wi_hash(ws, man):
    # discover 出该 ref 的 digest WorkItem,取其 input_hash
    items = DigestRule(ws, StubProvider()).discover()
    return next(i for i in items if i.key == f"references/{man.id}/digest.md").input_hash

def test_attachment_changes_digest_fingerprint(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    rdir = ws.references_dir() / "m"; rdir.mkdir(parents=True)
    (rdir / "transcript.md").write_text("转写正文")
    ws.write_manifest("m", Manifest(id="m", title="m", forms=[
        Form(role="transcript", location="references/m/transcript.md", hash="t1"),
    ]))
    h1 = _wi_hash(ws, ws.read_manifest("m"))
    # 加一张图片 form → 指纹必须变
    man = ws.read_manifest("m")
    man.forms.append(Form(role="attachment", location="references/m/board.png", hash="img9"))
    ws.write_manifest("m", man)
    h2 = _wi_hash(ws, ws.read_manifest("m"))
    assert h1 != h2
```

- [ ] **Step 2: 运行,确认失败**

Run: `source .venv/bin/activate && python -m pytest tests/test_rules_digest_attach.py -v`
Expected: FAIL（指纹只含 body，加图不变）

- [ ] **Step 3: 实现**

`DigestRule.discover` 把 `self._make(key, body)` 改为传 ref_id+man:

```python
            body = self._read_body(man)
            key = f"references/{ref_id}/digest.md"
            if body is not None and not (self.ws.root / key).exists():
                items.append(self._make(ref_id, key, man, body))
```

> 注:`not (...).exists()` 保留原「无产物才产」语义;真正的「变了重算」由 reconcile 比对 `input_hash`(is_stale)负责——既有引擎对已存在产物会用 is_stale 判断,无需改动此处。

`DigestRule._make` 替换为(纳入图片指纹 + 看图):

```python
    def _make(self, ref_id: str, key: str, man, body: str) -> WorkItem:
        atts = sorted(
            (f for f in man.forms if f.role == "attachment"),
            key=lambda f: f.location,
        )
        fingerprint = f"{self.prompt}\n\n---正文---\n{body}" + "".join(f.hash for f in atts)
        input_hash = _hash(fingerprint)
        ref_dir = self.ws.references_dir() / ref_id
        img_lines = []
        for f in atts:
            loc = Path(f.location)
            p = loc if loc.is_absolute() else self.ws.root / loc
            img_lines.append(str(p))

        def run(state: State) -> None:
            persona = self.prompt + self.ws.constitution.glossary_reference()
            if img_lines:
                persona += (
                    "\n\n[现场图片]本会议另有以下图片,请用 Read 工具逐一查看,"
                    "把其中与会议相关的信息(白板/幻灯/截图)并入纪要:\n"
                    + "\n".join(f"- {p}" for p in img_lines)
                )
            persona += _OUTPUT_DISCIPLINE
            content = _run_agent(
                self.provider,
                persona,
                body,
                "digest.md",
                read_dirs=[ref_dir] if img_lines else None,
            )
            (self.ws.root / key).write_text(content)
            state.products[key] = ProductState(
                input_hash=input_hash,
                produced_by={
                    "provider": self.provider.name,
                    "model": self.provider.model,
                },
            )

        def is_stale(state: State) -> bool:
            ps = state.products.get(key)
            return ps is None or ps.input_hash != input_hash

        return WorkItem(key, input_hash, run, is_stale)
```

（原 `_make` 里 persona 拼接是在 `_run_agent` 调用处用 `self.prompt + glossary + _OUTPUT_DISCIPLINE`;此版把图片提示插在中间,语义不变。）

- [ ] **Step 4: 运行,确认通过 + 防回归**

Run: `source .venv/bin/activate && python -m pytest tests/test_rules_digest_attach.py tests/ -k "digest or compose or web_tasks" -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/kairo/rules.py tests/test_rules_digest_attach.py
git commit -m "feat: #44 digest 指纹纳入 attachment,带图时授 agent 读图(read_dirs)"
```

---

## Task 6: Web 路由 `POST /w/{slug}/ref/{ref_id}/attach`

**Files:**
- Modify: `src/kairo/web/views.py`(在 `add_ref` 之后新增路由;新增 `_save_upload_to`)
- Test: `tests/test_web_write.py`(追加)

**Interfaces:**
- Consumes: `Workspace.add(files, ref_id=...)`(Task 2)。
- Produces: `POST /w/{slug}/ref/{ref_id}/attach`,Form 字段 `path` 或文件 `file`;把素材**复制进** `references/{ref_id}/` 后 `add(ref_id=ref_id)`;404 当 ref 不存在;400 当坏路径(沿用 `AddError`);成功返回该 ref 的元信息片段(复用 `ref_view` 的渲染:返回 `_ref_meta.html`)。

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_web_write.py（文件已 import io / TestClient / Workspace）
def test_attach_to_existing_ref_by_path(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    a = tmp_path / "a.txt"; a.write_text("转写")
    rid = ws.add([a])
    img = tmp_path / "board.png"; img.write_bytes(b"\x89PNG\r\n")
    r = _client(tmp_path).post(f"/w/ws/ref/{rid}/attach", data={"path": str(img)})
    assert r.status_code == 200
    man = Workspace.open(tmp_path / "ws").read_manifest(rid)
    atts = [f for f in man.forms if f.role == "attachment"]
    assert len(atts) == 1
    # 复制进 ref 目录(自包含)
    assert atts[0].location.startswith(f"references/{rid}/")
    assert (tmp_path / "ws" / atts[0].location).is_file()

def test_attach_unknown_ref_404(tmp_path):
    Workspace.init(tmp_path / "ws", topic="t")
    r = _client(tmp_path).post("/w/ws/ref/nope/attach", data={"path": "/x"})
    assert r.status_code == 404

def test_attach_bad_path_400(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    rid = ws.add([(lambda p: (p.write_text('x'), p)[1])(tmp_path / "a.txt")])
    r = _client(tmp_path).post(f"/w/ws/ref/{rid}/attach", data={"path": str(tmp_path / "no.png")})
    assert r.status_code == 400
```

- [ ] **Step 2: 运行,确认失败**

Run: `source .venv/bin/activate && python -m pytest tests/test_web_write.py -k attach -v`
Expected: FAIL（404 Not Found：路由不存在）

- [ ] **Step 3: 实现**

`src/kairo/web/views.py` 在 `_save_upload` 之后加复制进指定目录的辅助:

```python
def _save_upload_to(dest_dir: Path, upload: UploadFile) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(upload.filename or "upload.bin").name
    dest.write_bytes(upload.file.read())
    return dest
```

在 `add_ref` 路由之后加新路由:

```python
@router.post("/w/{slug}/ref/{ref_id}/attach", response_class=HTMLResponse)
def attach_to_ref(
    request: Request,
    slug: str,
    ref_id: str,
    path: str = Form(None),
    file: UploadFile = File(None),
) -> HTMLResponse:
    ws = _open(request, slug)
    if ref_id not in ws.list_reference_ids():
        raise HTTPException(status_code=404, detail="reference not found")
    ref_dir = ws.references_dir() / ref_id
    if file is not None and file.filename:
        src = _save_upload_to(ref_dir, file)
    elif path:
        p = Path(path)
        if not p.exists():
            raise HTTPException(status_code=400, detail=f"路径不存在:{p}")
        src = ref_dir / p.name
        src.write_bytes(p.read_bytes())  # 复制进 ref 目录(自包含)
    else:
        raise HTTPException(status_code=400, detail="need file or path")
    try:
        ws.add([src], ref_id=ref_id)
    except AddError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 复用 ref 详情渲染,刷新右栏元信息
    return ref_view(request, slug, ref_id)
```

（`ref_view` 已是模块级函数 `def ref_view(request, slug, ref_id)`,可直接调用复用渲染。）

- [ ] **Step 4: 运行,确认通过**

Run: `source .venv/bin/activate && python -m pytest tests/test_web_write.py -k attach -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/kairo/web/views.py tests/test_web_write.py
git commit -m "feat: #44 新增 ref attach 路由,素材复制进 ref 目录并追加"
```

---

## Task 7: ref 详情「附加素材」UI + i18n

**Files:**
- Modify: `src/kairo/web/templates/_ref_meta.html`(在 `.meta-label`/forms 表之上加附加入口)
- Modify: `src/kairo/web/i18n.py`(en+zh 新字符串;`role.attachment`)
- Test: `tests/test_web_api.py`(追加渲染断言)

**Interfaces:**
- Consumes: `attach_to_ref` 路由(Task 6);`_ref_forms` 已含 attachment form(role_label 经 `role.attachment`)。
- Produces: ref 详情含一个 `hx-post=".../attach"` 的上传/路径入口;`attachment` 角色有中英 label。

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_web_api.py
def test_ref_view_has_attach_entry(tmp_path):
    from kairo.workspace import Workspace
    ws = Workspace.init(tmp_path / "ws", topic="t")
    a = tmp_path / "a.txt"; a.write_text("x")
    rid = ws.add([a])
    r = TestClient(create_app(tmp_path)).get(f"/w/ws/ref/{rid}")
    assert r.status_code == 200
    assert f'/w/ws/ref/{rid}/attach' in r.text
    assert 'type="file"' in r.text
```

- [ ] **Step 2: 运行,确认失败**

Run: `source .venv/bin/activate && python -m pytest tests/test_web_api.py -k attach_entry -v`
Expected: FAIL（无 attach 入口）

- [ ] **Step 3: 实现**

`src/kairo/web/i18n.py` 的 en 表加(在 `role.prose` 附近):

```python
        "role.attachment": "Attachment",
        "ref.attach_btn": "+ Attach material",
        "ref.attach_path_placeholder": "Local file path (image / audio / doc)",
```

zh 表对应加:

```python
        "role.attachment": "附件",
        "ref.attach_btn": "+ 附加素材",
        "ref.attach_path_placeholder": "本地文件路径(图片 / 音频 / 文档)",
```

`src/kairo/web/templates/_ref_meta.html` 在 `<div class="meta-label">{{ t('forms.label') }}</div>` 之前插入附加入口(复用 #42 的弹框样式类,目标刷新右栏 `#meta`):

```html
  <button type="button" class="btn btn-step btn-add-ref"
          onclick="document.getElementById('attach-dlg').showModal()">{{ t('ref.attach_btn') }}</button>
  <dialog id="attach-dlg" class="ref-dialog" onclick="if(event.target===this)this.close()">
    <div class="dlg-head">
      <h3 class="dlg-title">{{ t('ref.attach_btn') }}</h3>
      <button type="button" class="dlg-x" aria-label="close" onclick="this.closest('dialog').close()">×</button>
    </div>
    <form class="dlg-form" hx-post="/w/{{ slug | urlencode }}/ref/{{ ref_id }}/attach"
          hx-target="#meta" hx-swap="innerHTML"
          hx-on::after-request="if(event.detail.successful)this.closest('dialog').close()">
      <div class="dlg-row">
        <input type="text" name="path" placeholder="{{ t('ref.attach_path_placeholder') }}" required>
        <button type="submit" class="btn btn-step dlg-go">{{ t('ref.add_path_btn') }}</button>
      </div>
    </form>
    <div class="dlg-or"><span>{{ t('ref.or') }}</span></div>
    <form class="dlg-form" hx-post="/w/{{ slug | urlencode }}/ref/{{ ref_id }}/attach" hx-encoding="multipart/form-data"
          hx-target="#meta" hx-swap="innerHTML"
          hx-on::after-request="if(event.detail.successful)this.closest('dialog').close()">
      <div class="dlg-row">
        <input type="file" name="file" required>
        <button type="submit" class="btn btn-step dlg-go">{{ t('ref.upload_btn') }}</button>
      </div>
    </form>
  </dialog>
```

> 复用的 i18n 键 `ref.add_path_btn` / `ref.upload_btn` / `ref.or` 由 #42 引入,已在 catalog 中。

- [ ] **Step 4: 运行,确认通过 + 全量**

Run: `source .venv/bin/activate && python -m pytest tests/ -q`
Expected: PASS（全绿）

- [ ] **Step 5: 浏览器实拍验证(dev-browser)**

启动服务 `kairo serve ~/kairo -p 8000`,用 dev-browser 打开某会议 ref 详情,点「附加素材」→ 弹框 → 传一张图 → 右栏 forms 出现 `附件`。截图确认。

- [ ] **Step 6: 提交**

```bash
git add src/kairo/web/templates/_ref_meta.html src/kairo/web/i18n.py tests/test_web_api.py
git commit -m "feat: #44 ref 详情加附加素材弹框入口 + attachment i18n"
```

---

## Task 8: 端到端冒烟(stub)+ PR

**Files:**
- Test: `tests/test_meeting_multiform_e2e.py`(新建)

- [ ] **Step 1: 写端到端测试(stub provider,验证合并 digest 链路)**

```python
# tests/test_meeting_multiform_e2e.py
from kairo.workspace import Workspace

def test_meeting_audio_plus_doc_one_digest(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.engine import step
    from kairo.provider import select_provider
    ws = Workspace.init(tmp_path / "ws", topic="t")
    audio = tmp_path / "talk.m4a"; audio.write_bytes(b"\x00\x01")
    rid = ws.add([audio])
    pdf = tmp_path / "deck.pdf"; pdf.write_bytes(b"%PDF-1.4")
    ws.add([pdf], ref_id=rid)
    img = tmp_path / "board.png"; img.write_bytes(b"\x89PNG\r\n")
    ws.add([img], ref_id=rid)
    step(ws, select_provider())
    # 一条 ref 一份 digest;transcript + source_text 都派生
    man = ws.read_manifest(rid)
    roles = sorted(f.role for f in man.forms)
    assert "transcript" in roles and "source_text" in roles and "attachment" in roles
    assert (tmp_path / "ws" / "references" / rid / "digest.md").is_file()
```

- [ ] **Step 2: 运行,确认通过**

Run: `source .venv/bin/activate && python -m pytest tests/test_meeting_multiform_e2e.py -v`
Expected: PASS

- [ ] **Step 3: 全量 + 提交**

```bash
source .venv/bin/activate && python -m pytest tests/ -q
git add tests/test_meeting_multiform_e2e.py
git commit -m "test: #44 会议多形态合并 digest 端到端冒烟"
```

- [ ] **Step 4: 推送 + 开 PR**

```bash
git push -u origin feat/44-meeting-multiform-digest
gh pr create --base main --head feat/44-meeting-multiform-digest \
  --title "feat: #44 会议=多形态 reference,合并 digest 并支持增量维护" \
  --body "Closes #44。详见 docs/design/44-meeting-multiform-digest.md。"
```

---

## Self-Review 记录

- **Spec 覆盖**:3.2 role→Task1;3.3 add 追加→Task2;3.4 TransformRule 多源→Task3、_read_body 拼接→Task4、看图→Task5;3.5 指纹→Task5;3.6 attach 路由→Task6、UI/i18n→Task7;端到端→Task8。✓
- **Placeholder**:无 TBD;每步含真实代码/命令/期望。
- **类型一致**:`_make(ref_id, src, key)`(Task3)、`_make(ref_id, key, man, body)`(Task5)签名各自闭合;`_read_body(man)`(Task4)与 Task5 discover 调用一致;`attach_to_ref` 复用 `ref_view`/`AddError`。
- **风险**:多源命名兼容旧 `{produces}.md`(Task3 legacy 分支);多模态降级在 stub 下不报错(Task5 read_dirs 仅 body 时为 None)。
