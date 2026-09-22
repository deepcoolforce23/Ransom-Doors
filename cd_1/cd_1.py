"""
Fullscreen video player with a chase-sprite event and remembered-drive trigger.

Flow:
    1. Video plays fullscreen borderless.
    2. When it ends, the chase event starts.
    3. For the first 3 seconds, the sprite appears as a red-tinted
       A-90_JUMPSCARE.png that flickers and shakes in place.
    4. For the next 4 seconds, it becomes a red square that blinks in place.
    5. Then it swaps to the gif and begins chasing the mouse, alternating
       between gif and stop-sign states at random intervals.
    6. If it touches the mouse: fullscreen VHS static + noise sting, then
       black screen with "ENC_R_PTE_D ||" in the corner. Quits 3s later.
    7. If the timeout expires first, the app quits cleanly.

Flow (remembered-drive mode):
    - Loads the drive snapshot ransom.py wrote on the losing session.
    - Watches removable media for any drive whose fingerprint appears
      in that snapshot. No marker file is required — the drive's own
      identity is the trigger. No Startup folder involvement at all.
    - On a match: consumes the snapshot and runs the chase event once.
    - If the snapshot is missing or empty at launch, cd_1 quits
      immediately — there is nothing to watch for.

The red aura with VHS-style pixel static scales with the sprite's
proximity to the cursor via three independent knobs:

    - AURA_STATIC_BASE       global amount of static
    - AURA_STATIC_DENSITY_*  how much the AMOUNT scales with proximity
    - AURA_STATIC_ALPHA_*    how much the SHADE/Brightness scales

Every window in the chain is click-through.

Run:
    python cd_1.py

Requires:
    pip install PyQt6 playsound3
"""

import sys
import os
import random
import ctypes
import json
from ctypes import wintypes as _wintypes
from ctypes import c_ulonglong as _c_ulonglong
from ctypes import c_wchar_p as _c_wchar_p
from ctypes import byref as _byref

from PyQt6.QtWidgets import (QApplication, QWidget, QLabel,
                             QGraphicsColorizeEffect)
from PyQt6.QtCore import (Qt, QUrl, QTimer, QSize, pyqtSignal)
from PyQt6.QtGui import (QPixmap, QMovie, QCursor, QColor, QImage,
                         QPainter)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

try:
    from playsound3 import playsound
except Exception as e:
    print(f'[sound] playsound3 not available: {e}')

    def playsound(*args, **kwargs):
        return None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

VIDEO_FILENAME         = 'videoplayback.mp4'
SPRITE_FILENAME        = 'fake_mouse.gif'
JUMPSCARE_FILENAME     = 'A-90_JUMPSCARE.png'
STOP_SIGN_FILENAME     = 'stop_sign.png'
CHASE_START_SOUND      = 'unrecoverable.mp3'
BLIP_SOUND             = 'blip.mp3'

SPRITE_SIZE        = 32
CHASE_SPEED        = 10
SHAKE_JITTER       = 8
TICK_MS            = 16

SHAKE_DURATION_MS_RANGE = (2000, 4000)
STOP_DURATION_MS_RANGE  = (500, 1500)

INTRO_DURATION_MS     = 3000
INTRO_SIZE            = 96
INTRO_FLICKER_MS      = 50
INTRO_FLICKER_CHANCE  = 0.6
INTRO_SHAKE_STRENGTH  = 8

BLINK_DURATION_MS     = 4000
BLINK_INTERVAL_MS     = 110
BLINK_COLOR           = '#b40000'

EVENT_TIMEOUT_MS = 140000

CAUGHT_QUIT_DELAY_MS = 3000

CAUGHT_TEXT  = 'ENC_R_PTE_D ||'
CAUGHT_COLOR = '#b40000'

# ---- Aura / static ---------------------------------------------------------
AURA_CANVAS_W          = 160
AURA_CANVAS_H          = 90
AURA_REGEN_MS          = 45
AURA_OPACITY_NEAR      = 1.00
AURA_OPACITY_FAR       = 0.04
AURA_FALLOFF_EXPONENT  = 1.7

AURA_STATIC_BASE         = 0.50
AURA_STATIC_DENSITY_NEAR = 1.50
AURA_STATIC_DENSITY_FAR  = 0.08
AURA_STATIC_ALPHA_NEAR   = 1.00
AURA_STATIC_ALPHA_FAR    = 0.30
AURA_VHS_STREAK_SCALE    = 14.0

AURA_DEBUG = False

# ---- VHS static jumpscare --------------------------------------------------
VHS_NOISE_SOUND        = 'vhs-noise.mp3'
VHS_STATIC_DURATION_MS = 1800
VHS_STATIC_REGEN_MS    = 45
VHS_CANVAS_W           = 160
VHS_CANVAS_H           = 90
VHS_COVERAGE           = 0.80
VHS_STREAK_SCALE       = 0.003

# ---- Remembered-drive trigger ----------------------------------------------
# cd_1 fires when a removable drive is present whose fingerprint matches
# an entry in the snapshot that ransom.py wrote on the losing session.
# No marker file is required — the drive's own identity is the trigger.
# No Startup folder involvement; cd_1 watches from wherever it's run.
#
# How often cd_1 re-enumerates removable drives. Lower = snappier
# detection of a plugged-in drive, at the cost of one cheap syscall
# per tick. Fingerprints are cached across ticks so a drive that
# stays plugged in is only fingerprinted once.
TRIGGER_WATCH_INTERVAL_MS = 250
TRIGGER_WATCH_TIMEOUT_MS  = 0
TRIGGER_SKIP_VIDEO        = False

DRIVE_SNAPSHOT_FILENAME = 'drive_snapshot.json'

DRIVE_TYPE_REMOVABLE = 2
DRIVE_TYPE_CDROM     = 5
TRIGGER_DRIVE_TYPES  = {DRIVE_TYPE_REMOVABLE, DRIVE_TYPE_CDROM}


def resource_path(filename):
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, filename)


# ---------------------------------------------------------------------------
# Removable-media detection (read-only)
# ---------------------------------------------------------------------------

def _enumerate_removable_drives():
    try:
        buf = ctypes.create_unicode_buffer(512)
        length = ctypes.windll.kernel32.GetLogicalDriveStringsW(512, buf)
    except Exception:
        return []
    out = []
    for part in buf[:length].split('\x00'):
        if not part:
            continue
        try:
            t = ctypes.windll.kernel32.GetDriveTypeW(_c_wchar_p(part))
        except Exception:
            continue
        if t in TRIGGER_DRIVE_TYPES:
            out.append((part, t))
    return out


# ---------------------------------------------------------------------------
# Drive snapshot matching
# ---------------------------------------------------------------------------

def _snapshot_path():
    d = os.path.join(
        os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
        'RansomIconState',
    )
    return os.path.join(d, DRIVE_SNAPSHOT_FILENAME)


def _load_drive_snapshot():
    """Return a set of fingerprint keys, or None if no snapshot exists."""
    p = _snapshot_path()
    if not os.path.isfile(p):
        return None
    try:
        with open(p, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f'[snapshot] read failed: {e}')
        return None

    entries = data.get('drives', [])
    keys = set()
    for e in entries:
        try:
            keys.add(
                f"{e['serial']}|{e['label']}|{e['fs']}|{e['total']}"
            )
        except Exception:
            continue
    return keys


def _drive_fingerprint_key(root):
    """Compute the same fingerprint key ransom.py would have."""
    try:
        name_buf = ctypes.create_unicode_buffer(261)
        fs_buf   = ctypes.create_unicode_buffer(261)
        serial   = _wintypes.DWORD(0)
        maxlen   = _wintypes.DWORD(0)
        flags    = _wintypes.DWORD(0)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            _c_wchar_p(root), name_buf, 261,
            _byref(serial), _byref(maxlen), _byref(flags),
            fs_buf, 261,
        )
        if not ok:
            return None
        total = _c_ulonglong(0)
        free  = _c_ulonglong(0)
        try:
            ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                _c_wchar_p(root), None, _byref(total), _byref(free)
            )
        except Exception:
            pass
        return (
            f"{serial.value:08X}|{name_buf.value or ''}|"
            f"{fs_buf.value or ''}|{total.value}"
        )
    except Exception:
        return None


def _find_remembered_drive(snapshot_keys, fingerprint_cache, prev_roots):
    """Return (matching_root_or_None, current_roots_set).

    Fingerprints each drive only on first sighting — i.e. when its
    root appears that wasn't present at the previous poll. Drives that
    stayed mounted between polls reuse their cached fingerprint, so
    the steady-state cost of a tick is just drive enumeration.

    No file is read from any drive. The drive's identity alone is
    the trigger.
    """
    if not snapshot_keys:
        return None, set()

    current = set()
    for root, _t in _enumerate_removable_drives():
        current.add(root)
        if root not in prev_roots:
            # Newly inserted at this root — fingerprint it now.
            fingerprint_cache[root] = _drive_fingerprint_key(root)

    # Drop vanished roots so their next insertion re-fingerprints
    # (a different physical drive may show up at the same letter).
    for root in list(fingerprint_cache.keys()):
        if root not in current:
            del fingerprint_cache[root]

    for root in current:
        key = fingerprint_cache.get(root)
        if key and key in snapshot_keys:
            return root, current

    return None, current


# ---------------------------------------------------------------------------
# Video player
# ---------------------------------------------------------------------------

class VideoPlayer(QWidget):
    finished = pyqtSignal()

    def __init__(self, video_path):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet('background-color: black;')

        self.video_widget = QVideoWidget(self)
        self.video_widget.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self.video_widget.setStyleSheet('background-color: black;')

        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(1.0)

        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.video_widget)
        self.player.setAudioOutput(self.audio_output)
        self.player.setSource(QUrl.fromLocalFile(video_path))
        self.player.mediaStatusChanged.connect(self._on_status)

    def _on_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            QTimer.singleShot(100, self.finished.emit)

    def resizeEvent(self, event):
        self.video_widget.setGeometry(0, 0, self.width(), self.height())
        event.accept()

    def start(self):
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(0, self.player.play)


# ---------------------------------------------------------------------------
# Sprite event
# ---------------------------------------------------------------------------

class SpriteEvent(QWidget):
    caught = pyqtSignal()
    timed_out = pyqtSignal()

    def __init__(self, gif_path, jumpscare_path, stop_sign_path):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet('background: transparent;')

        # ---- aura border ------------------------------------------
        self.border = QLabel(self)
        self.border.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self.border.setStyleSheet('background: transparent; border: none;')
        self.border.setGeometry(0, 0, 1, 1)
        self.border.show()
        self._init_aura_cache()

        self._aura_full = None
        self._aura_out = None
        self._aura_opacity = 0.0
        self._visibility = 0.0

        # ---- chase sprite (gif) -----------------------------------
        self.sprite = QLabel(self)
        self.sprite.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        self.sprite.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self.sprite.setStyleSheet('background: transparent; border: none;')
        self.sprite.setFixedSize(SPRITE_SIZE, SPRITE_SIZE)

        self.movie = None
        if os.path.isfile(gif_path):
            self.movie = QMovie(gif_path)
            self.movie.setScaledSize(QSize(SPRITE_SIZE, SPRITE_SIZE))
            self.sprite.setMovie(self.movie)
            self.movie.start()
        else:
            print(f'[sprite] gif not found: {gif_path}')
            self.sprite.setStyleSheet(
                'background-color: #b40000; border: none;'
            )

        # ---- blink square -----------------------------------------
        self.blink_label = QLabel(self)
        self.blink_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self.blink_label.setStyleSheet(
            f'background-color: {BLINK_COLOR}; border: none;'
        )
        self.blink_label.setFixedSize(SPRITE_SIZE, SPRITE_SIZE)
        self.blink_label.hide()

        # ---- stop-sign overlay ------------------------------------
        self.stop_label = QLabel(self)
        self.stop_label.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        self.stop_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self.stop_label.setStyleSheet('background: transparent; border: none;')
        self.stop_label.setFixedSize(SPRITE_SIZE, SPRITE_SIZE)
        self.stop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(stop_sign_path)
        if not pixmap.isNull():
            pixmap = pixmap.scaled(
                SPRITE_SIZE, SPRITE_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.stop_label.setPixmap(pixmap)
        else:
            print(f'[sprite] stop sign not found: {stop_sign_path}')
            self.stop_label.setText('X')
            self.stop_label.setStyleSheet(
                'color: #ff2020; font-size: 48px; font-weight: bold;'
                'background: transparent;'
            )
        self.stop_label.hide()

        # ---- intro sprite (red A-90) ------------------------------
        self.intro_label = QLabel(self)
        self.intro_label.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        self.intro_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self.intro_label.setStyleSheet('background: transparent; border: none;')
        self.intro_label.setFixedSize(INTRO_SIZE, INTRO_SIZE)
        self.intro_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        intro_pixmap = QPixmap(jumpscare_path)
        if not intro_pixmap.isNull():
            intro_pixmap = intro_pixmap.scaled(
                INTRO_SIZE, INTRO_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.intro_label.setPixmap(intro_pixmap)
            colorize = QGraphicsColorizeEffect(self.intro_label)
            colorize.setColor(QColor(255, 0, 0))
            colorize.setStrength(0.85)
            self.intro_label.setGraphicsEffect(colorize)
        else:
            print(f'[sprite] jumpscare not found: {jumpscare_path}')
            self.intro_label.setStyleSheet(
                'background-color: #b40000; border: none;'
            )
        self.intro_label.hide()

        self._sounds = []

        self.pos_x = 0
        self.pos_y = 0
        self.state = 'intro'
        self._finished = False
        self._blink_on = True

        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self._tick)
        self.tick_timer.start(TICK_MS)

        self.state_timer = QTimer(self)
        self.state_timer.setSingleShot(True)
        self.state_timer.timeout.connect(self._next_state)

        self.timeout_timer = QTimer(self)
        self.timeout_timer.setSingleShot(True)
        self.timeout_timer.timeout.connect(self._on_timeout)

        self.intro_flicker_timer = QTimer(self)
        self.intro_flicker_timer.setInterval(INTRO_FLICKER_MS)
        self.intro_flicker_timer.timeout.connect(self._intro_flicker)

        self.intro_end_timer = QTimer(self)
        self.intro_end_timer.setSingleShot(True)
        self.intro_end_timer.timeout.connect(self._end_intro)

        self.blink_timer = QTimer(self)
        self.blink_timer.setInterval(BLINK_INTERVAL_MS)
        self.blink_timer.timeout.connect(self._blink_flicker)

        self.blink_end_timer = QTimer(self)
        self.blink_end_timer.setSingleShot(True)
        self.blink_end_timer.timeout.connect(self._end_blink)

        self.aura_timer = QTimer(self)
        self.aura_timer.timeout.connect(self._refresh_aura)

    # ------------------------------------------------------------------
    # Sound
    # ------------------------------------------------------------------

    def _play(self, filename, block=False):
        path = resource_path(filename)
        if not os.path.isfile(path):
            print(f'[sound] not found: {path}')
            return
        try:
            sound = playsound(path, block=block)
        except Exception as e:
            print(f'[sound] {filename}: {e}')
            return
        if sound is not None:
            self._sounds.append(sound)

    def _stop_all_sounds(self):
        for s in self._sounds:
            try:
                s.stop()
            except Exception:
                pass
        self._sounds.clear()

    # ------------------------------------------------------------------
    # Aura cache
    # ------------------------------------------------------------------

    def _init_aura_cache(self):
        w, h = AURA_CANVAS_W, AURA_CANVAS_H
        self._aura_w = w
        self._aura_h = h

        self._aura_base = QImage(w, h, QImage.Format.Format_ARGB32)
        self._aura_base.fill(Qt.GlobalColor.transparent)

        self._static_prob = [0.0] * (w * h)

        cx = (w - 1) / 2.0
        cy = (h - 1) / 2.0
        max_d = (cx * cx + cy * cy) ** 0.5

        for y in range(h):
            dy = y - cy
            ny = abs(dy) / cy if cy > 0 else 0.0
            row_base = y * w
            for x in range(w):
                dx = x - cx
                nx = abs(dx) / cx if cx > 0 else 0.0

                d = ((dx * dx + dy * dy) ** 0.5) / max_d

                aura = (d - 0.30) / 0.70
                if aura < 0.0:
                    aura = 0.0
                elif aura > 1.0:
                    aura = 1.0

                corner = nx * ny

                alpha_f = aura * 0.55 + corner * 0.25
                alpha = int(alpha_f * 180)
                if alpha > 255:
                    alpha = 255
                if alpha > 0:
                    r = 200 + int(corner * 55)
                    if r > 255:
                        r = 255
                    self._aura_base.setPixelColor(
                        x, y, QColor(r, 0, 0, alpha)
                    )

                self._static_prob[row_base + x] = (
                    0.004 + corner * 0.32 + aura * 0.04
                )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        self.showFullScreen()
        self.raise_()
        self.activateWindow()

        QTimer.singleShot(0, self._first_refresh)

        self.pos_x = self.width() // 2 - SPRITE_SIZE // 2
        self.pos_y = self.height() // 2 - SPRITE_SIZE // 2
        self._apply_position()

        self._enter_intro()

        self.timeout_timer.start(EVENT_TIMEOUT_MS)
        self.aura_timer.start(AURA_REGEN_MS)

        self._play(CHASE_START_SOUND, block=False)

    def _first_refresh(self):
        self.border.setGeometry(0, 0, self.width(), self.height())
        self._visibility = self._compute_visibility()
        self._refresh_aura()
        self._update_aura_opacity()

    def _apply_position(self):
        self.sprite.move(self.pos_x, self.pos_y)
        self.blink_label.move(self.pos_x, self.pos_y)
        self.stop_label.move(self.pos_x, self.pos_y)
        cx = self.pos_x + SPRITE_SIZE // 2
        cy = self.pos_y + SPRITE_SIZE // 2
        self.intro_label.move(cx - INTRO_SIZE // 2, cy - INTRO_SIZE // 2)

    def resizeEvent(self, event):
        self.border.setGeometry(0, 0, self.width(), self.height())
        if not self._finished:
            self._visibility = self._compute_visibility()
            self._refresh_aura()
            self._update_aura_opacity()
        event.accept()

    # ------------------------------------------------------------------
    # Aura
    # ------------------------------------------------------------------

    def _compute_visibility(self):
        if self._finished:
            return 0.0
        if self.width() <= 1 or self.height() <= 1:
            return 0.0

        gpos = QCursor.pos()
        local_x = gpos.x() - self.x()
        local_y = gpos.y() - self.y()

        sx = self.pos_x + SPRITE_SIZE // 2
        sy = self.pos_y + SPRITE_SIZE // 2
        dx = local_x - sx
        dy = local_y - sy
        sprite_dist = (dx * dx + dy * dy) ** 0.5

        max_dist = max(
            1.0,
            (self.width() ** 2 + self.height() ** 2) ** 0.5,
        )
        ratio = min(1.0, sprite_dist / max_dist)
        return 1.0 - (ratio ** AURA_FALLOFF_EXPONENT)

    def _refresh_aura(self):
        if self._finished:
            return
        if self.width() <= 1 or self.height() <= 1:
            return

        if (self.border.width() != self.width()
                or self.border.height() != self.height()):
            self.border.setGeometry(0, 0, self.width(), self.height())

        vis = self._visibility

        density = AURA_STATIC_BASE * (
            AURA_STATIC_DENSITY_FAR
            + (AURA_STATIC_DENSITY_NEAR - AURA_STATIC_DENSITY_FAR) * vis
        )
        alpha_mult = (
            AURA_STATIC_ALPHA_FAR
            + (AURA_STATIC_ALPHA_NEAR - AURA_STATIC_ALPHA_FAR) * vis
        )

        if AURA_DEBUG:
            print(
                f'[aura] vis={vis:.3f} '
                f'base={AURA_STATIC_BASE:.2f} '
                f'density={density:.3f} '
                f'alpha={alpha_mult:.3f}'
            )

        w, h = self._aura_w, self._aura_h
        image = self._aura_base.copy()
        prob = self._static_prob

        # ---- 1. base snow ------------------------------------------
        for y in range(h):
            row_base = y * w
            scan = 0.55 if (y & 1) else 1.0
            for x in range(w):
                if random.random() >= prob[row_base + x] * density:
                    continue

                roll = random.random()
                if roll < 0.12:
                    v = random.randint(0, 55)
                    r = g = b = v
                elif roll < 0.32:
                    r = random.randint(180, 255)
                    g = random.randint(0, 60)
                    b = random.randint(0, 60)
                else:
                    v = random.randint(150, 255)
                    r = v
                    g = int(v * random.uniform(0.82, 1.0))
                    b = int(v * random.uniform(0.78, 1.0))

                a = int(random.randint(140, 240) * alpha_mult * scan)
                if a < 4:
                    a = 4
                elif a > 255:
                    a = 255
                image.setPixelColor(x, y, QColor(r, g, b, a))

        # ---- 2. horizontal dropout streaks -------------------------
        n_streaks = int(density * AURA_VHS_STREAK_SCALE)
        for _ in range(n_streaks):
            sy = random.randrange(h)
            sx = random.randrange(w)
            length = random.randint(2, max(3, w // 2))
            scan = 0.60 if (sy & 1) else 1.0
            kind = random.random()

            for i in range(length):
                px = sx + i
                if px >= w:
                    break
                if random.random() < 0.25:
                    continue

                if kind < 0.30:
                    v = random.randint(0, 45)
                    r = g = b = v
                elif kind < 0.55:
                    r = random.randint(200, 255)
                    g = random.randint(0, 50)
                    b = random.randint(0, 50)
                else:
                    v = random.randint(170, 255)
                    r = v
                    g = int(v * random.uniform(0.82, 1.0))
                    b = int(v * random.uniform(0.78, 1.0))

                a = int(random.randint(150, 240) * alpha_mult * scan)
                if a < 4:
                    a = 4
                elif a > 255:
                    a = 255
                image.setPixelColor(px, sy, QColor(r, g, b, a))

        self._aura_full = QPixmap.fromImage(image).scaled(
            self.width(), self.height(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self._paint_aura()

    def _update_aura_opacity(self):
        if self._finished:
            return

        vis = self._visibility
        opacity = (
            AURA_OPACITY_FAR
            + (AURA_OPACITY_NEAR - AURA_OPACITY_FAR) * vis
        )
        self._aura_opacity = opacity
        self._paint_aura()

    def _paint_aura(self):
        if self._aura_full is None:
            return

        opacity = self._aura_opacity

        if opacity <= 0.001:
            self.border.clear()
            return

        if opacity >= 0.999:
            self.border.setPixmap(self._aura_full)
            return

        w, h = self._aura_full.width(), self._aura_full.height()
        if (self._aura_out is None
                or self._aura_out.width() != w
                or self._aura_out.height() != h):
            self._aura_out = QPixmap(w, h)

        self._aura_out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(self._aura_out)
        painter.setOpacity(opacity)
        painter.drawPixmap(0, 0, self._aura_full)
        painter.end()
        self.border.setPixmap(self._aura_out)

    # ------------------------------------------------------------------
    # Intro phase
    # ------------------------------------------------------------------

    def _enter_intro(self):
        self.state = 'intro'
        if self.movie is not None:
            self.movie.stop()
        self.sprite.hide()
        self.blink_label.hide()
        self.stop_label.hide()
        self.intro_label.show()
        self.intro_flicker_timer.start()
        self.intro_end_timer.start(INTRO_DURATION_MS)

    def _intro_flicker(self):
        if self._finished or self.state != 'intro':
            return
        self.intro_label.setVisible(
            random.random() < INTRO_FLICKER_CHANCE
        )

    def _end_intro(self):
        if self._finished:
            return
        self.intro_flicker_timer.stop()
        self.intro_label.hide()
        self._enter_blink()

    # ------------------------------------------------------------------
    # Blink phase
    # ------------------------------------------------------------------

    def _enter_blink(self):
        self.state = 'blink'
        if self.movie is not None:
            self.movie.stop()
        self.sprite.hide()
        self.stop_label.hide()
        self.intro_label.hide()
        self._blink_on = True
        self.blink_label.setVisible(True)
        self.blink_timer.start()
        self.blink_end_timer.start(BLINK_DURATION_MS)

    def _blink_flicker(self):
        if self._finished or self.state != 'blink':
            return
        self._blink_on = not self._blink_on
        self.blink_label.setVisible(self._blink_on)

    def _end_blink(self):
        if self._finished:
            return
        self.blink_timer.stop()
        self.blink_label.hide()
        if self.movie is not None:
            self.movie.start()
        self._enter_shake()

    # ------------------------------------------------------------------
    # Chase phases
    # ------------------------------------------------------------------

    def _enter_shake(self):
        self._play(BLIP_SOUND, block=False)
        self.state = 'shake'
        if self.movie is not None:
            self.sprite.show()
        self.blink_label.hide()
        self.stop_label.hide()
        self.state_timer.start(random.randint(*SHAKE_DURATION_MS_RANGE))

    def _enter_stop(self):
        self._play(BLIP_SOUND, block=False)
        self.state = 'stop'
        self.sprite.hide()
        self.blink_label.hide()
        self.stop_label.show()
        self.state_timer.start(random.randint(*STOP_DURATION_MS_RANGE))

    def _next_state(self):
        if self._finished:
            return
        if self.state == 'shake':
            self._enter_stop()
        else:
            self._enter_shake()

    # ------------------------------------------------------------------
    # Per-frame update
    # ------------------------------------------------------------------

    def _tick(self):
        if self._finished:
            return

        gpos = QCursor.pos()
        local_x = gpos.x() - self.x()
        local_y = gpos.y() - self.y()

        if self.state == 'intro':
            self.pos_x += random.randint(
                -INTRO_SHAKE_STRENGTH, INTRO_SHAKE_STRENGTH
            )
            self.pos_y += random.randint(
                -INTRO_SHAKE_STRENGTH, INTRO_SHAKE_STRENGTH
            )
            max_x = max(0, self.width() - SPRITE_SIZE)
            max_y = max(0, self.height() - SPRITE_SIZE)
            self.pos_x = max(0, min(max_x, self.pos_x))
            self.pos_y = max(0, min(max_y, self.pos_y))
            self._apply_position()
            self._visibility = self._compute_visibility()
            self._update_aura_opacity()
            return

        if self.state == 'blink':
            self._visibility = self._compute_visibility()
            self._update_aura_opacity()
            return

        if self.state == 'shake':
            cx = self.pos_x + SPRITE_SIZE // 2
            cy = self.pos_y + SPRITE_SIZE // 2
            dx = local_x - cx
            dy = local_y - cy
            dist = (dx * dx + dy * dy) ** 0.5
            if dist > 1:
                self.pos_x += int(dx / dist * CHASE_SPEED)
                self.pos_y += int(dy / dist * CHASE_SPEED)

            self.pos_x += random.randint(-SHAKE_JITTER, SHAKE_JITTER)
            self.pos_y += random.randint(-SHAKE_JITTER, SHAKE_JITTER)

            max_x = max(0, self.width() - SPRITE_SIZE)
            max_y = max(0, self.height() - SPRITE_SIZE)
            self.pos_x = max(0, min(max_x, self.pos_x))
            self.pos_y = max(0, min(max_y, self.pos_y))

            self._apply_position()

        self._visibility = self._compute_visibility()
        self._update_aura_opacity()

        cx = self.pos_x + SPRITE_SIZE // 2
        cy = self.pos_y + SPRITE_SIZE // 2
        ddx = local_x - cx
        ddy = local_y - cy
        radius = SPRITE_SIZE // 2
        if ddx * ddx + ddy * ddy < radius * radius:
            self._on_caught()

    # ------------------------------------------------------------------
    # End conditions
    # ------------------------------------------------------------------

    def _stop_everything(self):
        self.tick_timer.stop()
        self.state_timer.stop()
        self.timeout_timer.stop()
        self.intro_flicker_timer.stop()
        self.intro_end_timer.stop()
        self.blink_timer.stop()
        self.blink_end_timer.stop()
        self.aura_timer.stop()
        if self.movie is not None:
            self.movie.stop()
        self._stop_all_sounds()

    def _on_caught(self):
        if self._finished:
            return
        self._finished = True
        self._stop_everything()
        self.caught.emit()

    def _on_timeout(self):
        if self._finished:
            return
        self._finished = True
        self._stop_everything()
        self.timed_out.emit()


# ---------------------------------------------------------------------------
# VHS static jumpscare
# ---------------------------------------------------------------------------

class StaticJumpscare(QWidget):
    finished = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet('background-color: black;')

        self.canvas = QLabel(self)
        self.canvas.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self.canvas.setStyleSheet('background-color: black; border: none;')
        self.canvas.setGeometry(0, 0, 1, 1)

        self._sounds = []
        self._finished = False

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)

    def start(self):
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.canvas.setGeometry(0, 0, self.width(), self.height())

        self._play(VHS_NOISE_SOUND, block=False)
        self._refresh()
        self.timer.start(VHS_STATIC_REGEN_MS)
        QTimer.singleShot(VHS_STATIC_DURATION_MS, self._finish)

    def resizeEvent(self, event):
        self.canvas.setGeometry(0, 0, self.width(), self.height())
        event.accept()

    def _play(self, filename, block=False):
        path = resource_path(filename)
        if not os.path.isfile(path):
            print(f'[sound] not found: {path}')
            return
        try:
            sound = playsound(path, block=block)
        except Exception as e:
            print(f'[sound] {filename}: {e}')
            return
        if sound is not None:
            self._sounds.append(sound)

    def _stop_all_sounds(self):
        for s in self._sounds:
            try:
                s.stop()
            except Exception:
                pass
        self._sounds.clear()

    def _refresh(self):
        if self._finished:
            return
        w, h = self.width(), self.height()
        if w <= 1 or h <= 1:
            return

        cw, ch = VHS_CANVAS_W, VHS_CANVAS_H
        image = QImage(cw, ch, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 255))

        coverage = VHS_COVERAGE

        for y in range(ch):
            scan = 0.60 if (y & 1) else 1.0
            for x in range(cw):
                if random.random() >= coverage:
                    continue

                roll = random.random()
                if roll < 0.15:
                    v = random.randint(0, 55)
                    r = g = b = v
                elif roll < 0.30:
                    r = random.randint(180, 255)
                    g = random.randint(0, 60)
                    b = random.randint(0, 60)
                else:
                    v = random.randint(140, 255)
                    r = v
                    g = int(v * random.uniform(0.85, 1.0))
                    b = int(v * random.uniform(0.80, 1.0))

                a = int(random.randint(180, 255) * scan)
                if a < 4:
                    a = 4
                elif a > 255:
                    a = 255
                image.setPixelColor(x, y, QColor(r, g, b, a))

        n_streaks = max(8, int(cw * ch * VHS_STREAK_SCALE))
        for _ in range(n_streaks):
            sy = random.randrange(ch)
            sx = random.randrange(cw)
            length = random.randint(4, max(5, cw // 2))
            scan = 0.65 if (sy & 1) else 1.0
            kind = random.random()

            for i in range(length):
                px = sx + i
                if px >= cw:
                    break
                if random.random() < 0.20:
                    continue

                if kind < 0.30:
                    v = random.randint(0, 45)
                    r = g = b = v
                elif kind < 0.55:
                    r = random.randint(200, 255)
                    g = random.randint(0, 50)
                    b = random.randint(0, 50)
                else:
                    v = random.randint(170, 255)
                    r = v
                    g = int(v * random.uniform(0.85, 1.0))
                    b = int(v * random.uniform(0.80, 1.0))

                a = int(random.randint(180, 255) * scan)
                if a < 4:
                    a = 4
                elif a > 255:
                    a = 255
                image.setPixelColor(px, sy, QColor(r, g, b, a))

        pixmap = QPixmap.fromImage(image).scaled(
            w, h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self.canvas.setPixmap(pixmap)

    def _finish(self):
        if self._finished:
            return
        self._finished = True
        self.timer.stop()
        self._stop_all_sounds()
        self.finished.emit()


# ---------------------------------------------------------------------------
# Caught screen
# ---------------------------------------------------------------------------

class CaughtScreen(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet('background-color: black;')

        self.label = QLabel(CAUGHT_TEXT, self)
        self.label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self.label.setStyleSheet(
            f'color: {CAUGHT_COLOR};'
            f'background: transparent;'
            f'font-family: "Consolas", "Courier New", monospace;'
            f'font-size: 22px;'
            f'font-weight: bold;'
            f'letter-spacing: 2px;'
        )
        self.label.adjustSize()

    def showEvent(self, event):
        self._place_label()
        super().showEvent(event)

    def resizeEvent(self, event):
        self._place_label()
        event.accept()

    def _place_label(self):
        margin = 24
        self.label.adjustSize()
        x = self.width() - self.label.width() - margin
        y = self.height() - self.label.height() - margin
        self.label.move(x, y)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def main():
    video_path = resource_path(VIDEO_FILENAME)
    if not os.path.isfile(video_path):
        print(f'ERROR: video not found: {video_path}')
        return 1

    gif_path = resource_path(SPRITE_FILENAME)
    jumpscare_path = resource_path(JUMPSCARE_FILENAME)
    stop_path = resource_path(STOP_SIGN_FILENAME)

    app = QApplication(sys.argv)

    state = {
        'video': None,
        'sprite': None,
        'jumpscare': None,
        'caught': None,
        'watcher_timer': None,
        'watcher_elapsed': 0,
        'snapshot_keys': None,
        'fingerprint_cache': {},
        'prev_roots': set(),
    }

    # ---------- end-of-chain handlers ----------

    def on_jumpscare_finished():
        if state['jumpscare'] is not None:
            state['jumpscare'].close()
            state['jumpscare'] = None
        caught = CaughtScreen()
        state['caught'] = caught
        caught.showFullScreen()
        caught.raise_()
        caught.activateWindow()
        QTimer.singleShot(CAUGHT_QUIT_DELAY_MS, QApplication.quit)

    def on_sprite_timeout():
        if state['sprite'] is not None:
            state['sprite'].close()
            state['sprite'] = None
        QApplication.quit()

    def on_sprite_caught():
        if state['sprite'] is not None:
            state['sprite'].close()
            state['sprite'] = None
        jumpscare = StaticJumpscare()
        jumpscare.finished.connect(on_jumpscare_finished)
        state['jumpscare'] = jumpscare
        jumpscare.start()

    # ---------- launchers ----------

    def start_chase():
        sprite = SpriteEvent(gif_path, jumpscare_path, stop_path)
        sprite.caught.connect(on_sprite_caught)
        sprite.timed_out.connect(on_sprite_timeout)
        state['sprite'] = sprite
        sprite.start()

    def on_video_end():
        if state['video'] is not None:
            state['video'].close()
            state['video'] = None
        start_chase()

    def start_video():
        video = VideoPlayer(video_path)
        video.finished.connect(on_video_end)
        state['video'] = video
        video.start()

    def launch_full_sequence(skip_video=False):
        if skip_video:
            start_chase()
        else:
            start_video()

    # ---------- remembered-drive trigger ----------

    def on_remembered_drive_found(drive):
        print(f'[trigger] remembered drive present: {drive}')
        if state['watcher_timer'] is not None:
            state['watcher_timer'].stop()
            state['watcher_timer'] = None
        # Consume the snapshot so a leftover file can't re-trigger later.
        try:
            p = _snapshot_path()
            if os.path.isfile(p):
                os.remove(p)
                print(f'[snapshot] consumed {p}')
        except Exception as e:
            print(f'[snapshot] cleanup failed: {e}')
        launch_full_sequence(skip_video=TRIGGER_SKIP_VIDEO)

    def on_watcher_tick():
        state['watcher_elapsed'] += TRIGGER_WATCH_INTERVAL_MS

        if (TRIGGER_WATCH_TIMEOUT_MS > 0
                and state['watcher_elapsed'] >= TRIGGER_WATCH_TIMEOUT_MS):
            print('[watcher] watch window expired, no matching drive seen')
            if state['watcher_timer'] is not None:
                state['watcher_timer'].stop()
                state['watcher_timer'] = None
            QApplication.quit()
            return

        drive, prev = _find_remembered_drive(
            state['snapshot_keys'],
            state['fingerprint_cache'],
            state['prev_roots'],
        )
        state['prev_roots'] = prev
        if drive:
            on_remembered_drive_found(drive)

    # ---------- entry ----------

    snapshot_keys = _load_drive_snapshot()

    if snapshot_keys is None:
        print('[watch] no drive snapshot found; nothing to watch for')
        return 0

    if not snapshot_keys:
        print('[watch] drive snapshot is empty; nothing to watch for')
        return 0

    print(f'[watch] watching for {len(snapshot_keys)} remembered drive(s)')
    state['snapshot_keys'] = snapshot_keys

    drive, prev = _find_remembered_drive(
        snapshot_keys,
        state['fingerprint_cache'],
        state['prev_roots'],
    )
    state['prev_roots'] = prev
    if drive:
        on_remembered_drive_found(drive)
    else:
        timer = QTimer()
        timer.timeout.connect(on_watcher_tick)
        timer.start(TRIGGER_WATCH_INTERVAL_MS)
        state['watcher_timer'] = timer

    return app.exec()


if __name__ == '__main__':
    sys.exit(main())