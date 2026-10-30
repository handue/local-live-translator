# Local Live Translator

[한국어](README.ko.md)

Real-time subtitles and translation for video calls (Zoom, Google Meet, Teams…) on your Mac.
Built for people who join job interviews and meetings in a language they're still learning.

**Everything runs on your Mac.** No cloud APIs, no accounts, and nothing you hear leaves your computer.

```
Zoom / Meet / Teams audio ─▶ audiotap (ScreenCaptureKit, no virtual audio driver)
                          ─▶ sentence splitting (energy VAD)
                          ─▶ parakeet-mlx        speech → text   (~0.1s)
                          ─▶ Ollama gemma3:4b    text → your language (~1s)
                          ─▶ floating subtitle window, hidden from screen sharing
```

- **No BlackHole or virtual audio device.** System audio is captured directly.
- **Your microphone is never recorded**, so only the other side gets subtitles.
- The original text appears **while they're still speaking** (gray). The translation follows about 1 second after each sentence (white).
- **The window is invisible to screen sharing and recording**, so you can share your screen safely.
- A transcript of every call is saved to `transcripts/` for review afterwards.

## Requirements

- Apple Silicon Mac (M1 or later), macOS 13+
- 16GB RAM or more recommended
- [Ollama](https://ollama.com) (`brew install ollama`)
- Xcode Command Line Tools (`xcode-select --install`)

## Install

```bash
git clone https://github.com/handue/local-live-translator.git
cd local-live-translator
./setup.sh
```

This downloads about 6GB of models (speech + translation).

### Allow system audio capture (once)

Open **System Settings → Privacy & Security → Screen & System Audio Recording** and turn on the terminal
app you run this from (Terminal, iTerm, …). Then **quit and reopen the terminal**.

## Usage

```bash
./run.sh                    # English → Korean, all apps
./run.sh --app zoom         # only Zoom's audio (ignores notification sounds etc.)
./run.sh --app chrome       # Google Meet in Chrome
./run.sh --to ja            # English → Japanese
./run.sh --to zh --app teams
./run.sh --from es --to en  # Spanish → English
./run.sh --from auto        # auto-detect the spoken language (European languages)
```

Drag the window to move it, drag its edges to resize, and close it to quit.

### Languages

| | |
|---|---|
| **Translate into** (`--to`) | `en` `ko` `ja` `zh` `zh-TW` `es` `fr` `de` `pt` `it` `nl` `pl` `ru` `uk` `tr` `vi` `th` `id` `hi` `ar`. Any other language name also works (`--to Tagalog`) |
| **Spoken language** (`--from`) | `en` (default, most accurate) or 25 European languages: `bg` `cs` `da` `de` `el` `es` `et` `fi` `fr` `hr` `hu` `it` `lt` `lv` `mt` `nl` `pl` `pt` `ro` `ru` `sk` `sl` `sv` `uk`, or `auto` |

You can set defaults with environment variables: `export SUBS_TO=ja SUBS_FROM=en SUBS_LLM=gemma3:4b`.

`--from` anything other than `en` uses the multilingual speech model (parakeet v3, ~2.5GB), which is downloaded on first use.

### Before a call

1. Fill in `context.txt` with the company, role, names, and key terms. This noticeably improves translation of jargon.
2. Start it **5 minutes early**. Models take a moment to load into memory.
3. Do a quick test with a YouTube interview video.

### Options

| Option | |
|---|---|
| `--font-size 26` | text size |
| `--opacity 0.8` | window opacity |
| `--llm gemma3:12b` | more natural translation, about 3× slower (run `ollama pull gemma3:12b` first) |
| `--silence 0.5` | end sentences sooner (they get split into smaller pieces) |
| `--vad-threshold 0.004` | the speaker is quiet and subtitles don't appear |
| `--no-translate` | transcript only |
| `--show-in-share` | let the window appear in screen sharing |
| `--console` | print to the terminal, no window |
| `-v` | print per-step latency |
| `--file video.mp4` | test with a file (no permission needed) |

## Performance

Measured on a MacBook Air M5 (24GB):

| Step | Model | Latency |
|---|---|---|
| Speech → text | parakeet-tdt-0.6b-v2 | 0.04–0.15s per sentence |
| Translation | gemma3:4b | 0.5–1.8s per sentence (~42 tokens/s) |
| Translation | gemma3:12b | 2–6s per sentence (~14 tokens/s) |

Translation speed is limited by memory bandwidth, so a bigger model is proportionally slower.
Fanless Macs (MacBook Air) also slow down under sustained heavy load, so close builds and other heavy apps during calls.

## Troubleshooting

- **"Permission needed"**: allow your terminal in Screen & System Audio Recording, then restart the terminal.
- **No subtitles**: run with `-v`. Check the `--app` name (`./bin/audiotap --list`) and make sure the call volume isn't muted.
- **Translation is slow**: try a smaller model, and close heavy apps.

## How it works

- `audiotap/main.swift`: a tiny ScreenCaptureKit helper that streams system audio (16kHz mono float32) to stdout. It can filter by app.
- `app.py`:
  - VAD splits speech into sentences.
  - parakeet-mlx transcribes them on the GPU.
  - Ollama translates, using the last few sentences as context.
  - A PyObjC floating panel shows the result. It uses `NSWindowSharingNone`, so screen sharing can't capture it.

## A note on use

Some companies don't allow AI tools during interviews. Check the rules for your interview. This tool only translates what you hear. It doesn't generate answers.

## License

MIT
