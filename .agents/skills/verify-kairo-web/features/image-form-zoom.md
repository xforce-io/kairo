# 图片 form 预览缩放（#442）

## 入口

- Topic 阅读区：打开 `/w/<slug>`，点这条参考（地址变为 `/w/<slug>?ref=<ref_id>`）。`/w/<slug>/ref/<ref_id>` 是换入阅读区的片段，不是带脚本的整页。
- 全局 Ref 抽屉：`/refs/<ref_id>` 点图片 form，预览来自 `/refs/<ref_id>/form/0`

不触发 Run，不碰现网 `~/kairo`。

## Fixture

```python
# uv run python <scratch>/kairo-verify-fixture-image.py "$ROOT"
import struct
import sys
import zlib
from pathlib import Path

from kairo.refs import add_global_ref
from kairo.workspace import Workspace


def write_png(path: Path, width: int, height: int) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + bytes((30, 90, 160)) * width for _ in range(height))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


root = Path(sys.argv[1])
ws = Workspace.init(root / "demo", topic="demo")
board = root / "board.png"
write_png(board, 800, 1600)
rid = ws.add([board], copy=True, title="白板")
add_global_ref(root, [board], ref_id="img-ref", title="白板", copy=True)
print(rid)
```

记下脚本打印的 Topic ref id。全局 Ref id 固定为 `img-ref`。图片高 1600、宽 800，预览区套不住整张高，刚打开时下沿在视口外。

## 路径 → 可判定结果

| # | 路径 | 可判定结果 |
|---|---|---|
| A / S1.A1 | 打开 `/w/demo`，点这条参考，阅读区出现这张图 | 阅读区有图片和「放大 / Zoom in」「缩小 / Zoom out」。点放大后图片显示宽度 ≥ 刚打开的 1.5 倍。点缩小后 ≤ 刚打开宽度。仍在 `/w/demo?ref=<rid>`，页面没有听读播放控件 |
| B / S2.A1 | 仍在 A 的阅读区 | 放大后图片超出预览视口。在视口内把图片向左上拖，直到下沿进入视口（该区域刚打开时不在视口内）。点「适应预览区 / Fit to preview」后显示宽度 ≤ 视口内容宽度，且不整页刷新 |
| C / S3.A1 | GET `/refs/img-ref`，点图片 form 的预览 | 抽屉里是同一张图，有放大和缩小。放大后显示宽度 ≥ 刚打开的 1.5 倍，缩小后 ≤ 刚打开宽度，不是听读 |
