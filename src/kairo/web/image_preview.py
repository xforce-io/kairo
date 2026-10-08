"""图片 form 预览的缩放、平移与适应。

Topic 阅读区与全局 Ref 的 form 预览共用这里的尺寸结果。
显示宽度以像素计；偏移为图片左上角相对预览区左上角的平移，负值表示图片往左上移、露出右下。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape

ZOOM_RATIO = 1.5
MAX_ZOOM = 8.0


@dataclass(frozen=True)
class ImageFrame:
    image_width: float
    image_height: float
    viewport_width: float
    viewport_height: float
    opened_width: float
    display_width: float
    display_height: float
    offset_x: float
    offset_y: float


def open_frame(
    image_width: float,
    image_height: float,
    viewport_width: float,
    viewport_height: float,
) -> ImageFrame:
    """刚打开：显示宽度不超过预览区内容宽度，小图不放大，平移为零。"""
    if min(image_width, image_height, viewport_width, viewport_height) <= 0:
        raise ValueError("image and viewport sizes must be positive")
    opened = min(image_width, viewport_width)
    return ImageFrame(
        image_width=image_width,
        image_height=image_height,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        opened_width=opened,
        display_width=opened,
        display_height=image_height * opened / image_width,
        offset_x=0.0,
        offset_y=0.0,
    )


def clamp_offset(
    display_width: float,
    display_height: float,
    viewport_width: float,
    viewport_height: float,
    offset_x: float,
    offset_y: float,
) -> tuple[float, float]:
    """把平移限制在不露出空白的范围。未超出的轴保持 0。"""
    min_x = min(0.0, viewport_width - display_width)
    min_y = min(0.0, viewport_height - display_height)
    return (min(0.0, max(min_x, offset_x)), min(0.0, max(min_y, offset_y)))


def _resized(frame: ImageFrame, display_width: float, offset_x: float, offset_y: float) -> ImageFrame:
    display_height = frame.image_height * display_width / frame.image_width
    ox, oy = clamp_offset(
        display_width,
        display_height,
        frame.viewport_width,
        frame.viewport_height,
        offset_x,
        offset_y,
    )
    return replace(
        frame,
        display_width=display_width,
        display_height=display_height,
        offset_x=ox,
        offset_y=oy,
    )


def zoom_in(frame: ImageFrame) -> ImageFrame:
    """放大一步。从刚打开的宽度起，结果至少为 1.5 倍，且不超过 8 倍。"""
    cap = frame.opened_width * MAX_ZOOM
    next_width = min(frame.display_width * ZOOM_RATIO, cap)
    if frame.display_width <= frame.opened_width:
        next_width = min(max(next_width, frame.opened_width * ZOOM_RATIO), cap)
    return _resized(frame, next_width, frame.offset_x, frame.offset_y)


def zoom_out(frame: ImageFrame) -> ImageFrame:
    """缩小一步，不低于刚打开的宽度。回到该宽度时平移归零。"""
    next_width = max(frame.opened_width, frame.display_width / ZOOM_RATIO)
    if next_width <= frame.opened_width:
        return _resized(frame, frame.opened_width, 0.0, 0.0)
    return _resized(frame, next_width, frame.offset_x, frame.offset_y)


def fit(frame: ImageFrame) -> ImageFrame:
    """回到刚打开的适应宽度，平移归零。"""
    return _resized(frame, frame.opened_width, 0.0, 0.0)


def pan_to_hidden(frame: ImageFrame) -> ImageFrame:
    """把视口移到当前显示尺寸下被裁掉的远端。没有超出时不变。"""
    offset_x = min(0.0, frame.viewport_width - frame.display_width)
    offset_y = min(0.0, frame.viewport_height - frame.display_height)
    return _resized(frame, frame.display_width, offset_x, offset_y)


def image_point_visible(frame: ImageFrame, image_x: float, image_y: float) -> bool:
    """图片像素是否落在预览区内部（不含贴在右缘或下缘之外的点）。"""
    if image_x < 0 or image_y < 0 or image_x >= frame.image_width or image_y >= frame.image_height:
        return False
    scale_x = frame.display_width / frame.image_width
    scale_y = frame.display_height / frame.image_height
    screen_x = frame.offset_x + image_x * scale_x
    screen_y = frame.offset_y + image_y * scale_y
    return 0 <= screen_x < frame.viewport_width and 0 <= screen_y < frame.viewport_height


def render_image_preview(src: str, alt: str, labels: dict[str, str]) -> str:
    """两处预览入口共用的图片预览片段。尺寸由客户端按本模块的比例测量后套用。"""
    ratio = format(ZOOM_RATIO, "g")
    cap = format(MAX_ZOOM, "g")
    return (
        f'<div class="img-preview" data-zoom-ratio="{ratio}" data-max-zoom="{cap}">'
        f'<div class="img-preview-bar" role="toolbar" aria-label="{escape(labels["toolbar"])}">'
        f'<button type="button" data-img-zoom-in>{escape(labels["zoom_in"])}</button>'
        f'<button type="button" data-img-zoom-out>{escape(labels["zoom_out"])}</button>'
        f'<button type="button" data-img-fit>{escape(labels["fit"])}</button>'
        f"</div>"
        f'<div class="img-preview-viewport">'
        f'<img class="doc-img" src="{escape(src, quote=True)}" '
        f'alt="{escape(alt)}" draggable="false">'
        f"</div></div>"
    )
