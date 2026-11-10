"""An mpv player drawn into a Qt OpenGL widget through libmpv's render API.

The render API works on Wayland and X11 alike, unlike embedding mpv's own
window by id, which only works on X11. mpv decodes on its own threads and asks
for a repaint through the update callback; Qt then calls `paintGL`, and mpv
draws the current frame into the widget's framebuffer.
"""

import locale

import mpv
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication, QNativeInterface, QOpenGLContext
from PySide6.QtOpenGLWidgets import QOpenGLWidget


def _get_proc_address(_ctx, name: bytes) -> int:
    context = QOpenGLContext.currentContext()
    return context.getProcAddress(name) if context else 0


def _native_display() -> dict:
    """The window system's display, which mpv needs to share VA-API frames with OpenGL."""
    native = QGuiApplication.instance().nativeInterface()
    if isinstance(native, QNativeInterface.QWaylandApplication):
        return {"wl_display": native.display()}
    if isinstance(native, QNativeInterface.QX11Application):
        return {"x11_display": native.display()}
    return {}


# Module-level, so ctypes never frees the callback while mpv still holds it.
_GET_PROC_ADDRESS = mpv.MpvGlGetProcAddressFn(_get_proc_address)


class MpvWidget(QOpenGLWidget):
    # Emitted from mpv's render thread; Qt queues it onto the GUI thread.
    _frame_ready = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # libmpv refuses to start unless numbers are formatted the C way, and Qt
        # sets the locale from the environment when the application starts.
        locale.setlocale(locale.LC_NUMERIC, "C")
        self.mpv = mpv.MPV(
            vo="libmpv",
            hwdec="auto-safe",  # VA-API on this machine
            keep_open="always",  # stay on the last frame at the end
            hr_seek="yes",  # exact seeks unless asked for keyframes
            pause=True,
            input_default_bindings=False,
            input_vo_keyboard=False,
            osc=False,
            config=False,  # don't pick up a personal mpv.conf
        )
        self._render = None
        self._pending: tuple[str, float] | None = None
        self._frame_ready.connect(self.update, Qt.ConnectionType.QueuedConnection)

    def load(self, url: str, start: float = 0.0) -> None:
        """Open `url` paused or playing as before, at `start` seconds.

        mpv can't open video until it has somewhere to draw, so a load asked
        for before the widget is first shown waits for `initializeGL`.
        """
        if self._render is None:
            self._pending = (url, start)
        else:
            self.mpv.loadfile(url, start=f"{start:.3f}")

    def stop(self) -> None:
        self._pending = None
        self.mpv.command_async("stop")

    def initializeGL(self) -> None:
        self._render = mpv.MpvRenderContext(
            self.mpv,
            "opengl",
            opengl_init_params={"get_proc_address": _GET_PROC_ADDRESS},
            **_native_display(),
        )
        self._render.update_cb = self._frame_ready.emit
        self.context().aboutToBeDestroyed.connect(self._free_render)
        if self._pending:
            self.load(*self._pending)
            self._pending = None

    def paintGL(self) -> None:
        if self._render is None:
            return
        ratio = self.devicePixelRatioF()
        self._render.render(
            flip_y=True,
            opengl_fbo={
                "w": round(self.width() * ratio),
                "h": round(self.height() * ratio),
                "fbo": self.defaultFramebufferObject(),
            },
        )

    def _free_render(self) -> None:
        if self._render is not None:
            self.makeCurrent()
            self._render.update_cb = None
            self._render.free()
            self._render = None
            self.doneCurrent()

    def shutdown(self) -> None:
        """Stop playback and release mpv. Call before the window closes."""
        self._free_render()
        self.mpv.terminate()
