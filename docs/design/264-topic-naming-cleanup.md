# 264 — 统一 Topic 命名：workspace→Topic，kind→preset

**Owner**: xforce-io/kairo  
**Status**: Implementing  
**Created**: 2026-09-04

## 背景

当前代码中混用 workspace/Topic 的概念，需要统一命名并明确边界，提升用户体验的一致性。

## 目标

1. **用户可见层面**统一使用 Topic，保留 workspace 作为内部实现名
2. **配置字段**重命名 `kind` → `preset`，明确这是预设而非分类
3. **Project 关联**使用 `topics` 而非 `workspace_slugs`
4. 保持完整的向后兼容性

## 设计

### 1. 概念定义

- **Topic**：知识加工对象，包含研究问题、constitution、结论与 agent 上下文
- **workspace**：Topic 的磁盘目录实现名，仅在代码内部和兼容字段中使用
- **preset**：constitution 填法预设，替代 `kind`

### 2. 命名映射

#### Constitution 字段

```yaml
# 新建 Topic (新写法)
preset: standard  # 或 journal
kind: null        # 不再写入

# 旧 Topic (兼容读取)
kind: topic       # 读取时映射为 preset: standard
kind: journal     # 读取时映射为 preset: journal
```

运行时优先读取 `preset`，回退到 `kind` 映射，最终回退到 topic 名称判断（如「总结」）。

#### Project 关联字段

```json
{
  "topics": ["topic-a", "topic-b"],      // 新字段
  "workspace_slugs": null                 // 废弃，仅读取兼容
}
```

读取时自动迁移 `workspace_slugs` → `topics`，写入时只写 `topics`。

### 3. CLI 变更

- **新增命令**: `kairo rm` 作为删除 Topic 的主命令
- **废弃命令**: `kairo rm-ws` 保留作为别名，标注废弃
- **Help 文本**: 所有 "workspace" → "Topic"
- **Epilog**: "多 workspace" → "多 Topic"

### 4. Web 界面

- i18n 目录表已使用 "Topics" 等合适术语
- 更新少量遗留的 "workspace" 为 "Topic"
- 导航、标签、帮助文本统一使用 Topic

### 5. 向后兼容策略

#### Constitution 读取
```python
def resolve_preset(preset, kind, topic):
    # 1. 优先 preset 字段
    if preset in (PRESET_STANDARD, PRESET_JOURNAL):
        return preset
    # 2. 兼容 kind 映射
    if kind == KIND_JOURNAL:
        return PRESET_JOURNAL
    if kind == KIND_TOPIC:
        return PRESET_STANDARD
    # 3. Topic 名称推断（如「总结」）
    if topic == "总结":
        return PRESET_JOURNAL
    # 4. 默认
    return PRESET_STANDARD
```

#### Project 读取
```python
class Project(BaseModel):
    topics: list[str] = Field(default_factory=list)
    workspace_slugs: list[str] | None = None  # 兼容
    
    def model_post_init(self, __context):
        # 自动迁移
        if self.workspace_slugs and not self.topics:
            self.topics = list(self.workspace_slugs)
```

### 6. 测试要求

- 旧 constitution 能正确读取（`kind: topic` / `kind: journal`）
- 新 constitution 正确写入（`preset: standard` / `preset: journal`）
- 旧 Project JSON 能正确迁移 `workspace_slugs`
- CLI 命令向后兼容（`rm-ws` 仍可用但标注废弃）

## 实现清单

- [x] 更新 `models.py`: 添加 `preset` 字段，保持 `kind` 兼容
- [x] 更新 `kind.py`: 新增 `PRESET_*` 常量和 `resolve_preset()`
- [x] 更新 `projects.py`: `workspace_slugs` → `topics`
- [x] 更新 `cli.py`: 用户文本和 `rm` 命令
- [x] 更新 `web/i18n.py`: i18n 字符串
- [x] 更新文档: `README.md`, `README.zh-CN.md`, `docs/glossary.md`
- [x] 创建本设计文档
- [ ] 测试通过
- [ ] 提交和 PR

## 不变内容

以下**不在**本次重命名范围：

- `stream` / `corpus` 保持为 Topic 内的引用类
- Data Source internal kind
- Web JobKind
- Engine step / fold / backup 语义

## 风险与缓解

**风险**: 用户脚本中硬编码 `rm-ws` 或读取 `kind` 字段  
**缓解**: 
- `rm-ws` 保留作为别名，标注废弃
- `kind` 字段读取时兼容映射

**风险**: 旧 Project JSON 包含 `workspace_slugs`  
**缓解**: `model_post_init` 自动迁移

## 验收标准

- [x] Issue #264 存在
- [ ] PR 关联并关闭 issue
- [ ] 涉及的测试通过
- [ ] `kairo --help` 使用 Topic 术语
- [ ] 旧 `kind:` 配置仍能加载
- [ ] PR 描述包含重命名映射和兼容性说明

## 参考

- Issue: https://github.com/xforce-io/kairo/issues/264
- PR: (待创建)
