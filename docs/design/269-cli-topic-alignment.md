# #269 CLI 与 Topic/Ref/Project 架构契合

- Issue: [#269](https://github.com/xforce-io/kairo/issues/269)
- 分支: `cursor/cli-topic-alignment-f499`
- 状态: Implementation
- 日期: 2026-09-04

本文件是 #269 的详细设计唯一事实源。

## 1. 背景

当前 CLI 使用 `--workspace` 作为用户可见参数，但架构已演进为 Ref → Tag → Topic membership via include_tags → Project links Topics（#242/#249/#252/#264）。需要将 CLI 用户心智模型与 Console 对齐。

## 2. 目标

1. **与 Console 相同心智模型**：Timeline/Ref 发现 → Topic 处理 → Project 执行
2. **复用既有领域 API**：`kairo.refs`（add_global_ref、tag_*、set_include_tags、topic_members）、`engine.step` / `run_workspace`、`projects.*`
3. **弃用用户可见 `--workspace`**。使用 `--topic`（或 `-t`）用于所有用户命名 Topic 的地方
4. **处理命令支持 `--topic`**：`step`、`run`、`status`、`re-step`、`retry-ref`、`prose`、`accept`、`index`、`history`、`rollback`、`diff`
5. **`kairo add --topic SLUG`**：显式将 Ref 标记为 Topic 成员（用 Topic 名称 Tag）
6. **Topic create 默认 include_tags**：`kairo new` 和 `kairo init` 默认设置 `include_tags=[topic_name]`
7. **`kairo list --json` 暴露成员信息**：显示 `include_tags` 和 `member_count`
8. **重写 CLI epilog**：三条路径（Ref/Timeline、Topic 处理、Project 执行）

## 3. 实现

### 3.1 CLI 参数变更

| 命令 | 变更 | 兼容性 |
|---|---|---|
| `add` | 新增 `--topic SLUG` 参数，用于显式标记 Ref | 完全向后兼容 |
| `init` | 默认设置 `include_tags=[topic]`，需要 Tag 预先存在 | 行为变更 |
| `new` | 默认设置 `include_tags=[topic]`，需要 Tag 预先存在 | 行为变更 |
| `list` | `--json` 输出增加 `include_tags` 和 `member_count` | 扩展兼容 |
| `archive` | `--workspace` → `--topic` | 参数名变更 |
| `review` | `--workspace` → `--topic` | 参数名变更 |
| `step`、`run`、`status` 等 | 新增 `--topic` / `-t` 参数 | 完全向后兼容 |

### 3.2 `kairo add --topic` 行为

```python
# 1. 正常登记 Ref（home 根据 cwd 判定）
rid = ws.add(...) if ws else add_global_ref(...)

# 2. 如果指定了 --topic
if topic:
    # 2.1 检查 Topic 名称 Tag 是否存在
    if topic not in list_tags(serve):
        raise Error("Tag 不在词表中，请先创建")
    
    # 2.2 用 Topic 名称 Tag 标记 Ref
    add_tag(serve, home=home_slug, ref_id=rid, tag=topic)
```

这是**显式**成员关系，不依赖 home 推断。

### 3.3 Topic 创建默认规则

```python
# kairo init / new
ws = Workspace.init(dest, topic=topic)
constitution = ws.constitution
constitution.include_tags = [topic]  # 默认包含同名 Tag
ws.write_constitution(constitution)
```

前提：Topic 名称 Tag 必须预先在词表中存在，否则拒绝创建。

### 3.4 处理命令 Topic 支持

所有处理命令增加统一的 `--topic` / `-t` 参数：

```python
def _open_ws(topic: str | None = None) -> Workspace:
    if topic:
        serve = _serve_root(None)
        return Workspace.open(serve / topic)
    return Workspace.open(Path.cwd())
```

默认行为（省略 `--topic`）保持不变：使用 cwd。

### 3.5 用户文案更新

- Epilog：三条路径明确列出
- 错误消息：将 "workspace" 改为 "Topic"
- 命令帮助文本：统一使用 "Topic" 术语
- `status` 输出：`topic` 字段保持，增加 `name` 字段

## 4. 复用映射

| 功能 | 复用的 API |
|---|---|
| 添加并标记 Ref | `kairo.refs.add_tag` |
| 检查 Tag 存在 | `kairo.refs.list_tags` |
| 设置包含规则 | `Constitution.include_tags` (Workspace.write_constitution) |
| 获取成员 | `kairo.refs.topic_members` |
| 获取包含规则 | `kairo.refs.include_tags_of` |
| 打开 Topic | `Workspace.open` |
| 运行处理 | `engine.step`、`engine_run_workspace` 等 |

不新增领域 API，仅在 CLI 层组合调用。

## 5. 测试计划

### 5.1 add --topic

```python
def test_add_with_topic(tmp_path):
    # 1. 创建 serve root 和 Tag
    serve = tmp_path / "root"
    create_tag(serve, "energy")
    
    # 2. 全局 add --topic energy
    kairo add note.txt --topic energy
    
    # 3. 验证：Ref 已标记 energy Tag
    refs = list_all_refs(serve)
    ref = next(r for r in refs if r.id == "...")
    assert "energy" in ref.tags
    
    # 4. 验证：Topic 设置 include_tags=[energy] 后可见该 Ref
    topic = Workspace.init(serve / "energy", topic="energy")
    topic.constitution.include_tags = ["energy"]
    members = topic_members(serve, "energy")
    assert ref.id in [m.id for m in members]
```

### 5.2 Topic create 默认规则

```python
def test_new_topic_default_include(tmp_path):
    serve = tmp_path / "root"
    create_tag(serve, "research")
    
    kairo new research --root serve
    
    ws = Workspace.open(serve / "research")
    assert ws.constitution.include_tags == ["research"]
```

### 5.3 list --json 暴露成员

```python
def test_list_includes_member_info(tmp_path):
    serve = tmp_path / "root"
    # ... 设置 Topic 和成员 ...
    
    result = kairo list --root serve --json
    data = json.loads(result.output)
    
    topic = next(t for t in data if t["slug"] == "energy")
    assert "include_tags" in topic
    assert "member_count" in topic
    assert topic["member_count"] == 2
```

### 5.4 处理命令 --topic

```python
def test_step_with_topic(tmp_path):
    serve = tmp_path / "root"
    topic = Workspace.init(serve / "research", topic="research")
    
    # 从其他目录执行
    monkeypatch.chdir(tmp_path)
    
    kairo step --topic research
    
    # 验证：research Topic 已处理
    assert topic.read_state().targets != {}
```

### 5.5 add --topic 失败情况

```python
def test_add_topic_tag_missing(tmp_path):
    serve = tmp_path / "root"
    
    result = kairo add note.txt --topic missing
    
    assert result.exit_code == 1
    assert "Tag 不在词表中" in result.output
```

## 6. 边界

- `--topic` 参数仅在 CLI 层，不改变内部 Workspace 类名
- home 仍定位源文件和 digest，不因 `--topic` 改变
- 兼容：省略 `--topic` 时所有命令保持现有行为（cwd 判定）
- Tag 必须预先在词表中存在，`add --topic` 和 Topic create 均 fail-closed
- 不在此 PR 中重命名内部 `Workspace` 类或 `workspace` 目录

## 7. 迁移/兼容

- **完全向后兼容**：不带 `--topic` 的现有命令继续工作
- **参数名变更**：`archive --workspace` 和 `review --workspace` 改为 `--topic`（接受相同值）
- **行为变更**：`init` 和 `new` 现在默认设置 `include_tags`，需要同名 Tag 预先存在
- **扩展兼容**：`list --json` 输出增加字段，但不破坏现有字段

## 8. 关联

- 父 #269
- 依赖 #242（全局 Ref）、#252（Tag 规则）、#264（Project 关联）
