"""
Local Live Translator — real-time subtitles + translation for video calls, 100% on-device.

  system audio (audiotap) → energy VAD sentence splitting → parakeet-mlx (speech-to-text)
  → Ollama local LLM (translation) → floating subtitle window hidden from screen sharing

Run:  ./run.sh            (all options: ./run.sh --help)
"""

import argparse
import datetime
import json
import os
import queue
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parent
SR = 16000
FRAME = 512  # 32ms
FRAME_SEC = FRAME / SR


# ───────────────────────── languages ─────────────────────────

LANGUAGES = {
    "en": "English", "ko": "Korean", "ja": "Japanese", "zh": "Simplified Chinese",
    "zh-TW": "Traditional Chinese", "es": "Spanish", "fr": "French", "de": "German",
    "pt": "Portuguese", "it": "Italian", "nl": "Dutch", "pl": "Polish", "ru": "Russian",
    "uk": "Ukrainian", "tr": "Turkish", "vi": "Vietnamese", "th": "Thai",
    "id": "Indonesian", "hi": "Hindi", "ar": "Arabic",
}

# Speech-to-text models. v2 is English-only and the most accurate for English;
# v3 covers 25 European languages and auto-detects which one is spoken.
STT_EN = "mlx-community/parakeet-tdt-0.6b-v2"
STT_MULTI = "mlx-community/parakeet-tdt-0.6b-v3"
STT_MULTI_LANGS = {
    "bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu", "it",
    "lv", "lt", "mt", "pl", "pt", "ro", "sk", "sl", "es", "sv", "ru", "uk",
}

# Extra translation rules for specific target languages.
STYLE = {
    "ko": """Use polite 해요체 and never use "당신". Keep common software terms the way Korean engineers say them (프로덕션, 배포, 온콜, 트레이드오프).""",
    "ja": """Use polite です/ます form and avoid あなた.""",
    "zh": """Use Simplified Chinese (简体中文).""",
    "zh-TW": """Use Traditional Chinese (繁體中文) as used in Taiwan.""",
}

# Status messages shown in the window title. Korean when translating into Korean, English otherwise.
MESSAGES = {
    "en": {"stt": "Loading speech model…", "llm": "Loading translation model ({})…", "listen": "Listening",
           "done": "File finished", "error": "Error: {}", "fail": "(translation failed: {})"},
    "ko": {"stt": "음성인식 모델 로딩 중…", "llm": "번역 모델({}) 로딩 중…", "listen": "듣는 중",
           "done": "파일 재생 완료", "error": "오류: {}", "fail": "(번역 실패: {})"},
}


def lang_name(code):
    # Unknown codes are passed through, so e.g. --to Tagalog also works.
    return LANGUAGES.get(code, code)


# ───────────────────────── audio sources ─────────────────────────

def audiotap_frames(apps):
    """Read 32ms float32 frames from the audiotap helper, forever."""
    cmd = [str(ROOT / "bin" / "audiotap")]
    for a in apps:
        cmd += ["--app", a]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    nbytes = FRAME * 4
    buf = b""
    try:
        while True:
            chunk = proc.stdout.read(nbytes - len(buf))
            if not chunk:
                code = proc.wait()
                raise RuntimeError(f"audiotap exited (code {code}) — see the terminal output above")
            buf += chunk
            if len(buf) == nbytes:
                yield np.frombuffer(buf, dtype="<f4")
                buf = b""
    finally:
        proc.kill()


def file_frames(path, realtime=True):
    """For testing: convert any audio file to 16kHz mono and stream it at real-time speed."""
    tmp = ROOT / ".cache_input.wav"
    subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{SR}", "-c", "1", str(path), str(tmp)], check=True)
    with wave.open(str(tmp)) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    tmp.unlink()
    # Append 1.5s of silence so the last sentence gets finalized
    data = np.concatenate([data, np.zeros(int(SR * 1.5), np.float32)])
    t0 = time.monotonic()
    for i in range(0, len(data) - FRAME + 1, FRAME):
        if realtime:
            delay = t0 + i / SR - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        yield data[i : i + FRAME]


# ───────────────────────── sentence splitting (VAD) ─────────────────────────

class Segmenter:
    """
    RMS energy VAD. Video-call audio is already noise-suppressed, so silence is
    close to zero and a simple threshold works well.
      - speech start → begin buffering (including the previous ~300ms)
      - while speaking → emit a 'partial' event every partial_every seconds
      - silence_sec of quiet → finalize the sentence
      - longer than max_sec → cut at the quietest point of the last 2 seconds
    """

    def __init__(self, emit, threshold=0.008, silence_sec=0.7, max_sec=12.0, partial_every=0.8):
        self.emit = emit
        self.th = threshold
        self.silence_frames = int(silence_sec / FRAME_SEC)
        self.max_frames = int(max_sec / FRAME_SEC)
        self.partial_frames = int(partial_every / FRAME_SEC)
        self.preroll = []
        self.frames, self.rms = [], []
        self.active = False
        self.loud_run = 0
        self.quiet_run = 0
        self.since_partial = 0
        self.seg_id = 0

    def push(self, f):
        r = float(np.sqrt(np.mean(f * f)))
        if not self.active:
            self.preroll = (self.preroll + [f])[-10:]
            self.loud_run = self.loud_run + 1 if r > self.th else 0
            if self.loud_run >= 2:
                self.active = True
                self.seg_id += 1
                self.frames = list(self.preroll)
                self.rms = [self.th] * len(self.frames)
                self.quiet_run = 0
                self.since_partial = 0
            return

        self.frames.append(f)
        self.rms.append(r)
        self.since_partial += 1
        self.quiet_run = self.quiet_run + 1 if r < self.th * 0.6 else 0

        if self.quiet_run >= self.silence_frames:
            self._finish(len(self.frames) - self.quiet_run + 3)
        elif len(self.frames) >= self.max_frames:
            tail = self.rms[-60:]
            cut = len(self.frames) - len(tail) + int(np.argmin(tail)) + 1
            rest = self.frames[cut:]
            self._finish(cut)
            # The remainder continues as a new sentence
            self.active = True
            self.seg_id += 1
            self.frames, self.rms = rest, [self.th] * len(rest)
            self.quiet_run = self.since_partial = 0
        elif self.since_partial >= self.partial_frames:
            self.since_partial = 0
            self.emit("partial", self.seg_id, np.concatenate(self.frames))

    def _finish(self, end):
        audio = np.concatenate(self.frames[:end]) if end > 0 else np.zeros(0, np.float32)
        self.active = False
        self.frames, self.rms, self.preroll = [], [], []
        self.loud_run = 0
        if len(audio) >= SR * 0.4:
            self.emit("final", self.seg_id, audio)
        else:
            self.emit("drop", self.seg_id, None)


# ───────────────────────── speech-to-text (parakeet-mlx) ─────────────────────────

class Transcriber:
    def __init__(self, model_id):
        import mlx.core as mx
        from parakeet_mlx import from_pretrained
        from parakeet_mlx.audio import get_logmel

        self.mx, self.get_logmel = mx, get_logmel
        self.model = from_pretrained(model_id)
        self(np.zeros(SR, np.float32))  # warm-up (compiles Metal kernels)

    def __call__(self, audio):
        mel = self.get_logmel(self.mx.array(audio, dtype=self.mx.float32), self.model.preprocessor_config)
        return self.model.generate(mel.astype(self.mx.bfloat16))[0].text.strip()


# ───────────────────────── translation (Ollama) ─────────────────────────

# Keep this short: small models (gemma3:4b) tend to copy words from long rule lists and examples
# into the translation. The original text is shown above each translation anyway.
SYSTEM_PROMPT = """You are a live interpreter. Translate what the speaker says from {src} into natural, easy-to-read {dst}.
Output only the {dst} translation of the given text — nothing else. Translate everything and add nothing.
The text comes from speech recognition, so fix obvious recognition errors silently.
{style}"""


class Translator:
    def __init__(self, model, host, src, dst, context_text):
        self.model = model
        self.url = host.rstrip("/") + "/api/chat"
        self.src = "the speaker's language" if src == "auto" else lang_name(src)
        self.dst = lang_name(dst)
        self.system = SYSTEM_PROMPT.format(src=self.src, dst=self.dst, style=STYLE.get(dst, ""))
        if context_text.strip():
            self.system += "\nContext about this call (use it to choose the right terminology):\n" + context_text.strip() + "\n"
        self.history = []  # (source, translation)
        self.extra = {}

    def warmup(self):
        r = requests.post(self.url.replace("/api/chat", "/api/show"), json={"model": self.model}, timeout=10)
        if r.status_code == 404:
            raise RuntimeError(f"Ollama model '{self.model}' not found. Run: ollama pull {self.model}")
        r.raise_for_status()
        # Thinking models (e.g. qwen3) are much faster with thinking turned off
        if "thinking" in r.json().get("capabilities", []):
            self.extra["think"] = False
        requests.post(
            self.url,
            json={"model": self.model, "messages": [{"role": "user", "content": "hi"}],
                  "stream": False, "keep_alive": "2h", "options": {"num_predict": 1}, **self.extra},
            timeout=300,
        ).raise_for_status()

    def translate(self, text, on_token):
        src_label = "Original" if self.src.startswith("the ") else self.src
        prev = "\n".join(f"{src_label}: {s}\n{self.dst}: {t}" for s, t in self.history[-4:])
        user = (f"Previous conversation (context only, do not translate):\n{prev}\n\n" if prev else "")
        user += f"Translate this into {self.dst}:\n{text}"
        r = requests.post(
            self.url,
            json={
                "model": self.model,
                "messages": [{"role": "system", "content": self.system}, {"role": "user", "content": user}],
                "stream": True,
                "keep_alive": "2h",
                "options": {"temperature": 0, "num_ctx": 4096},
                **self.extra,
            },
            stream=True,
            timeout=120,
        )
        r.raise_for_status()
        out = ""
        for line in r.iter_lines():
            if not line:
                continue
            msg = json.loads(line)
            out += msg.get("message", {}).get("content", "")
            on_token(out.strip())
            if msg.get("done"):
                break
        out = out.strip()
        self.history.append((text, out))
        return out


# ───────────────────────── pipeline ─────────────────────────

class Pipeline:
    """
    Three threads:
      audio : read frames + VAD → stt_q
      stt   : parakeet (MLX must only be used from this thread) → tr_q
      trans : streaming translation via Ollama
    The ui object has status(text), source(id, text, final), target(id, text, final).
    """

    def __init__(self, args, ui):
        self.args, self.ui = args, ui
        self.msg = MESSAGES["ko" if args.to == "ko" else "en"]
        self.stt_q = queue.Queue()
        self.tr_q = queue.Queue()
        self.log_path = None

    def start(self):
        threading.Thread(target=self._boot, daemon=True).start()

    def _boot(self):
        try:
            a, m = self.args, self.msg
            self.ui.status(m["stt"])
            # MLX can only be used from the thread that loaded the model, so load it inside the STT thread
            ready, err = threading.Event(), []
            threading.Thread(target=self._stt_loop, args=(ready, err), daemon=True).start()
            ready.wait()
            if err:
                raise err[0]
            ctx_file = ROOT / "context.txt"
            ctx = ctx_file.read_text(encoding="utf-8") if ctx_file.exists() else ""
            self.tr = None if a.no_translate else Translator(a.llm, a.ollama, a.source, a.to, ctx)
            if self.tr:
                self.ui.status(m["llm"].format(a.llm))
                self.tr.warmup()

            if not a.no_save:
                d = ROOT / "transcripts"
                d.mkdir(exist_ok=True)
                now = datetime.datetime.now()
                self.log_path = d / (now.strftime("%Y-%m-%d_%H%M") + ".md")
                pair = f"{a.source} → {a.to}" if self.tr else a.source
                self.log_path.write_text(f"# {now:%Y-%m-%d %H:%M} ({pair})\n\n", encoding="utf-8")

            if self.tr:
                threading.Thread(target=self._tr_loop, daemon=True).start()

            frames = file_frames(a.file, realtime=not a.fast) if a.file else audiotap_frames(a.app)
            seg = Segmenter(lambda *e: self.stt_q.put(e), threshold=a.vad_threshold,
                            silence_sec=a.silence, max_sec=a.max_segment)
            self.ui.status(m["listen"] + (f" · {', '.join(a.app)}" if a.app else ""))
            for f in frames:
                seg.push(f)
            self.stt_q.put(("eof", None, None))
        except Exception as e:
            self.ui.status(self.msg["error"].format(e))
            print(f"[error] {e}", file=sys.stderr)

    def _stt_loop(self, ready, err):
        try:
            self.stt = Transcriber(self.args.stt_model)
        except Exception as e:
            err.append(e)
            return
        finally:
            ready.set()
        while True:
            kind, sid, audio = self.stt_q.get()
            if kind == "eof":
                self.tr_q.put(None)
                if not self.tr and self.args.file:
                    self.ui.status(self.msg["done"])
                return
            if kind == "drop":
                self.ui.source(sid, "", True)
                continue
            # Skip stale partials when newer audio is already waiting
            if kind == "partial" and not self.stt_q.empty():
                continue
            t = time.perf_counter()
            text = self.stt(audio)
            if self.args.verbose:
                print(f"[stt {kind} {len(audio)/SR:.1f}s → {(time.perf_counter()-t)*1000:.0f}ms] {text}", file=sys.stderr)
            if kind == "final":
                self.ui.source(sid, text, True)
                if text and self.tr:
                    self.tr_q.put((sid, text, time.perf_counter()))
                elif text:
                    self._save(text, "")
            elif text:
                self.ui.source(sid, text, False)

    def _tr_loop(self):
        while True:
            item = self.tr_q.get()
            if item is None:
                if self.args.file:
                    self.ui.status(self.msg["done"])
                return
            sid, text, t0 = item
            # If translation can't keep up and sentences are piling up, translate them together
            while not self.tr_q.empty():
                nxt = self.tr_q.get()
                if nxt is None:
                    self.tr_q.put(None)
                    break
                self.ui.target(sid, "", True)  # shown together with the next sentence
                sid, text = nxt[0], text + " " + nxt[1]
            try:
                out = self.tr.translate(text, lambda partial: self.ui.target(sid, partial, False))
                self.ui.target(sid, out, True)
                if self.args.verbose:
                    print(f"[trans {(time.perf_counter()-t0)*1000:.0f}ms] {out}", file=sys.stderr)
                self._save(text, out)
            except Exception as e:
                self.ui.target(sid, self.msg["fail"].format(e), True)

    def _save(self, src, dst):
        if self.log_path:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"- {src}\n" + (f"  - {dst}\n" if dst else "") + "\n")


# ───────────────────────── UI: console ─────────────────────────

class ConsoleUI:
    def __init__(self, args):
        self.src_tag = args.source.upper()
        self.dst_tag = args.to.upper()

    def status(self, text):
        print(f"· {text}", flush=True)

    def source(self, sid, text, final):
        if final and text:
            print(f"\n[{self.src_tag} {sid}] {text}", flush=True)

    def target(self, sid, text, final):
        if final and text:
            print(f"[{self.dst_tag} {sid}] {text}", flush=True)


# ───────────────────────── UI: floating subtitle window ─────────────────────────

def run_overlay(args):
    from AppKit import (
        NSApplication, NSApplicationActivationPolicyAccessory, NSBackingStoreBuffered, NSColor,
        NSFont, NSFontWeightRegular, NSFontWeightSemibold, NSMakeRect, NSMutableAttributedString,
        NSAttributedString, NSMutableParagraphStyle, NSPanel, NSScreen, NSScrollView, NSTextView,
        NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowCollectionBehaviorFullScreenAuxiliary,
        NSWindowStyleMaskClosable, NSWindowStyleMaskHUDWindow, NSWindowStyleMaskNonactivatingPanel,
        NSWindowStyleMaskResizable, NSWindowStyleMaskTitled, NSWindowStyleMaskUtilityWindow,
        NSViewWidthSizable, NSViewHeightSizable, NSStatusWindowLevel,
    )
    from Foundation import NSObject
    from PyObjCTools import AppHelper

    NSFontAttributeName = "NSFont"
    NSForegroundColorAttributeName = "NSColor"
    NSParagraphStyleAttributeName = "NSParagraphStyle"
    NSWindowSharingNone = 0
    TITLE = "Live Translator"

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    vf = NSScreen.mainScreen().visibleFrame()
    w, h = 760, 260
    rect = NSMakeRect(vf.origin.x + (vf.size.width - w) / 2, vf.origin.y + 40, w, h)
    style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable
             | NSWindowStyleMaskUtilityWindow | NSWindowStyleMaskHUDWindow | NSWindowStyleMaskNonactivatingPanel)
    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(rect, style, NSBackingStoreBuffered, False)
    panel.setTitle_(TITLE)
    panel.setLevel_(NSStatusWindowLevel)  # stays above full-screen Zoom too
    panel.setFloatingPanel_(True)
    panel.setHidesOnDeactivate_(False)
    panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary)
    panel.setAlphaValue_(args.opacity)
    panel.setMovableByWindowBackground_(True)
    if not args.show_in_share:
        panel.setSharingType_(NSWindowSharingNone)  # invisible to screen sharing / recording

    content = panel.contentView()
    scroll = NSScrollView.alloc().initWithFrame_(content.bounds())
    scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
    scroll.setHasVerticalScroller_(True)
    scroll.setDrawsBackground_(False)
    tv = NSTextView.alloc().initWithFrame_(content.bounds())
    tv.setEditable_(False)
    tv.setSelectable_(True)
    tv.setDrawsBackground_(False)
    tv.setTextContainerInset_((10, 8))
    tv.setAutoresizingMask_(NSViewWidthSizable)
    scroll.setDocumentView_(tv)
    content.addSubview_(scroll)

    fs = args.font_size
    para = NSMutableParagraphStyle.alloc().init()
    para.setParagraphSpacing_(4)
    gap = NSMutableParagraphStyle.alloc().init()
    gap.setParagraphSpacing_(14)
    st = {
        "src": {NSFontAttributeName: NSFont.systemFontOfSize_weight_(fs * 0.72, NSFontWeightRegular),
                NSForegroundColorAttributeName: NSColor.colorWithWhite_alpha_(0.72, 1), NSParagraphStyleAttributeName: para},
        "src_p": {NSFontAttributeName: NSFont.systemFontOfSize_weight_(fs * 0.72, NSFontWeightRegular),
                  NSForegroundColorAttributeName: NSColor.colorWithWhite_alpha_(0.5, 1), NSParagraphStyleAttributeName: para},
        "dst": {NSFontAttributeName: NSFont.systemFontOfSize_weight_(fs, NSFontWeightSemibold),
                NSForegroundColorAttributeName: NSColor.whiteColor(), NSParagraphStyleAttributeName: gap},
        "dst_p": {NSFontAttributeName: NSFont.systemFontOfSize_weight_(fs, NSFontWeightSemibold),
                  NSForegroundColorAttributeName: NSColor.colorWithWhite_alpha_(0.8, 1), NSParagraphStyleAttributeName: gap},
    }

    entries = {}  # sid -> dict(src, src_final, dst, dst_final)

    def render():
        s = NSMutableAttributedString.alloc().init()
        for sid in sorted(entries)[-args.keep:]:
            e = entries[sid]
            if not e["src"]:
                continue
            s.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
                e["src"] + "\n", st["src"] if e["src_final"] else st["src_p"]))
            if args.no_translate:
                continue
            if e["dst"] or (e["src_final"] and not e["dst_final"]):
                s.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
                    (e["dst"] or "…") + "\n", st["dst"] if e["dst_final"] else st["dst_p"]))
        tv.textStorage().setAttributedString_(s)
        tv.scrollRangeToVisible_((s.length(), 0))

    def entry(sid):
        return entries.setdefault(sid, {"src": "", "src_final": False, "dst": "", "dst_final": False})

    class UI:
        def status(self, text):
            AppHelper.callAfter(lambda: panel.setTitle_(f"{TITLE} · {text}"))
            print(f"· {text}", flush=True)

        def source(self, sid, text, final):
            def f():
                if final and not text:
                    entries.pop(sid, None)
                else:
                    e = entry(sid)
                    e["src"], e["src_final"] = text, final
                render()
            AppHelper.callAfter(f)

        def target(self, sid, text, final):
            def f():
                e = entry(sid)
                e["dst"], e["dst_final"] = text, final
                render()
            AppHelper.callAfter(f)

    class Delegate(NSObject):
        def windowWillClose_(self, n):
            AppHelper.stopEventLoop()

    delegate = Delegate.alloc().init()
    panel.setDelegate_(delegate)
    panel.orderFrontRegardless()

    Pipeline(args, UI()).start()
    AppHelper.runEventLoop(installInterrupt=True)
    os._exit(0)


# ───────────────────────── main ─────────────────────────

def main():
    p = argparse.ArgumentParser(description="Real-time local subtitles + translation for video calls")
    p.add_argument("--from", dest="source", default=os.environ.get("SUBS_FROM", "en"),
                   help="spoken language: en (default), a European language code, or auto")
    p.add_argument("--to", default=os.environ.get("SUBS_TO", "ko"),
                   help="translate into this language (default ko). See --list-languages")
    p.add_argument("--list-languages", action="store_true", help="show language codes and exit")
    p.add_argument("--app", action="append", default=[],
                   help="only capture this app's audio (e.g. zoom, teams, chrome). Repeatable. Default: all apps")
    p.add_argument("--llm", default=os.environ.get("SUBS_LLM", "gemma3:4b"), help="Ollama translation model (default gemma3:4b)")
    p.add_argument("--ollama", default="http://localhost:11434")
    p.add_argument("--stt-model", help="override the parakeet model (chosen automatically from --from)")
    p.add_argument("--no-translate", action="store_true", help="show the transcript only")
    p.add_argument("--font-size", type=float, default=22)
    p.add_argument("--opacity", type=float, default=0.92)
    p.add_argument("--keep", type=int, default=30, help="number of sentences kept in the window")
    p.add_argument("--show-in-share", action="store_true", help="let the window appear in screen sharing (hidden by default)")
    p.add_argument("--vad-threshold", type=float, default=0.008, help="speech detection threshold (lower = more sensitive)")
    p.add_argument("--silence", type=float, default=0.7, help="seconds of silence that end a sentence")
    p.add_argument("--max-segment", type=float, default=12.0, help="max sentence length in seconds")
    p.add_argument("--no-save", action="store_true", help="don't save a transcript to transcripts/")
    p.add_argument("--console", action="store_true", help="print to the terminal instead of opening a window")
    p.add_argument("--file", help="testing: use an audio/video file as input")
    p.add_argument("--fast", action="store_true", help="process --file faster than real time")
    p.add_argument("-v", "--verbose", action="store_true", help="print per-step latency")
    args = p.parse_args()

    if args.list_languages:
        print("Translate into (--to):")
        for code, name in LANGUAGES.items():
            print(f"  {code:6} {name}")
        print("  (any other language name also works, e.g. --to Tagalog)\n")
        print("Spoken language (--from):")
        print("  en     English (most accurate, default)")
        print("  auto   auto-detect among: " + ", ".join(sorted(STT_MULTI_LANGS)))
        print("  or one of the codes above, e.g. --from es")
        return

    if args.source != "auto" and args.source not in STT_MULTI_LANGS:
        p.error(f"--from {args.source}: speech recognition supports en, auto, or: {', '.join(sorted(STT_MULTI_LANGS))}")
    if not args.stt_model:
        args.stt_model = STT_EN if args.source == "en" else STT_MULTI
    if args.source == args.to:
        args.no_translate = True

    if args.console:
        Pipeline(args, ConsoleUI(args)).start()
        try:
            while threading.active_count() > 1:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
    else:
        run_overlay(args)


if __name__ == "__main__":
    main()
