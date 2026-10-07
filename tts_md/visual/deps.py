from __future__ import annotations


class VideoDepsError(RuntimeError):
    pass


def require_video_deps() -> None:
    missing = []
    try:
        import PIL  # noqa: F401
    except ImportError:
        missing.append("pillow")
    try:
        import pygments  # noqa: F401
    except ImportError:
        missing.append("pygments")
    if missing:
        raise VideoDepsError(
            f"--video requires {', '.join(missing)}. "
            "Install the video extra: pip install 'tts-md[video]'"
        )
