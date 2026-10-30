# Local Live Translator

[English](README.md)

화상 통화(Zoom, Google Meet, Teams 등)에서 상대방이 하는 말을 **실시간 자막과 번역**으로 보여 주는 맥용 프로그램이에요.
외국어로 면접이나 회의를 해야 하는데 아직 듣기가 익숙하지 않은 사람을 위해 만들었어요.

**모든 처리가 내 맥 안에서만** 이뤄져요. 클라우드 API나 계정이 필요 없고, 들은 내용이 컴퓨터 밖으로 나가지 않아요.

```
Zoom / Meet / Teams 소리 ─▶ audiotap (ScreenCaptureKit, 가상 오디오 장치 불필요)
                        ─▶ 문장 단위 분할 (에너지 VAD)
                        ─▶ parakeet-mlx        음성 → 텍스트  (~0.1초)
                        ─▶ Ollama gemma3:4b    텍스트 → 내 언어 (~1초)
                        ─▶ 플로팅 자막창 (화면 공유에 안 잡힘)
```

- BlackHole 같은 가상 오디오 장치가 **필요 없어요.** 시스템 오디오를 직접 캡처해요.
- **마이크는 녹음하지 않기** 때문에 상대방 목소리만 자막에 나와요.
- 원문은 **상대가 말하는 도중에 바로** 나오고(회색), 번역은 문장이 끝나면 약 1초 뒤에 이어서 나와요(흰색).
- 자막창은 **화면 공유나 녹화에 잡히지 않아서** 화면을 공유해도 괜찮아요.
- 통화 기록이 `transcripts/` 폴더에 저장돼서 나중에 복기할 수 있어요.

## 필요한 것

- Apple Silicon 맥(M1 이상), macOS 13 이상
- 메모리 16GB 이상 권장
- [Ollama](https://ollama.com) (`brew install ollama`)
- Xcode Command Line Tools (`xcode-select --install`)

## 설치

```bash
git clone https://github.com/handue/local-live-translator.git
cd local-live-translator
./setup.sh
```

모델(음성인식 + 번역)을 합쳐 약 6GB를 내려받아요.

### 시스템 오디오 캡처 허용 (최초 1회)

**시스템 설정 → 개인정보 보호 및 보안 → 화면 및 시스템 오디오 기록**에서 이 프로그램을 실행하는 터미널 앱(Terminal, iTerm 등)을 켜 주세요.
그다음 **터미널을 완전히 종료했다가 다시 실행**해야 적용돼요.

## 사용법

```bash
./run.sh                    # 영어 → 한국어, 모든 앱 소리
./run.sh --app zoom         # Zoom 소리만 (알림 소리 등은 무시)
./run.sh --app chrome       # 크롬에서 Google Meet를 쓸 때
./run.sh --to ja            # 영어 → 일본어
./run.sh --to zh --app teams
./run.sh --from es --to ko  # 스페인어 → 한국어
./run.sh --from auto        # 말하는 언어 자동 감지 (유럽 언어)
```

자막창은 드래그해서 옮기고, 모서리를 끌어서 크기를 바꿀 수 있어요. 창을 닫으면 프로그램도 종료돼요.

### 지원 언어

| | |
|---|---|
| **번역할 언어** (`--to`) | `ko` `en` `ja` `zh` `zh-TW` `es` `fr` `de` `pt` `it` `nl` `pl` `ru` `uk` `tr` `vi` `th` `id` `hi` `ar`. 목록에 없는 언어도 이름으로 쓸 수 있어요 (`--to Tagalog`) |
| **상대가 말하는 언어** (`--from`) | `en`(기본값, 가장 정확해요) 또는 유럽 언어 25개: `bg` `cs` `da` `de` `el` `es` `et` `fi` `fr` `hr` `hu` `it` `lt` `lv` `mt` `nl` `pl` `pt` `ro` `ru` `sk` `sl` `sv` `uk`, 또는 `auto` |

기본값은 환경 변수로 바꿀 수 있어요: `export SUBS_TO=ja SUBS_FROM=en SUBS_LLM=gemma3:4b`

`--from`을 `en`이 아닌 언어로 지정하면 다국어 음성인식 모델(parakeet v3, 약 2.5GB)을 써요. 이 모델은 처음 쓸 때 자동으로 내려받아요.

### 통화 전 체크리스트

1. `context.txt`에 회사, 직무, 사람 이름, 주요 용어를 적어 두세요. 전문 용어 번역이 확실히 좋아져요.
2. 통화 **5분 전에** 실행해 두세요. 모델을 메모리에 올리는 데 시간이 조금 걸려요.
3. 유튜브 인터뷰 영상 같은 걸 틀어서 한 번 테스트해 보세요.

### 옵션

| 옵션 | 설명 |
|---|---|
| `--font-size 26` | 글자 크기 |
| `--opacity 0.8` | 창 투명도 |
| `--llm gemma3:12b` | 더 자연스러운 번역, 대신 약 3배 느려요 (먼저 `ollama pull gemma3:12b`) |
| `--silence 0.5` | 문장을 더 빨리 확정 (대신 문장이 잘게 끊겨요) |
| `--vad-threshold 0.004` | 상대 목소리가 작아서 자막이 잘 안 나올 때 |
| `--no-translate` | 번역 없이 원문 자막만 |
| `--show-in-share` | 화면 공유에도 자막창이 보이게 |
| `--console` | 창 없이 터미널에만 출력 |
| `-v` | 단계별 지연시간 표시 |
| `--file 영상.mp4` | 파일로 테스트 (권한 불필요) |

## 성능

MacBook Air M5(24GB)에서 측정한 값이에요.

| 단계 | 모델 | 지연 |
|---|---|---|
| 음성 → 텍스트 | parakeet-tdt-0.6b-v2 | 문장당 0.04~0.15초 |
| 번역 | gemma3:4b | 문장당 0.5~1.8초 (초당 약 42토큰) |
| 번역 | gemma3:12b | 문장당 2~6초 (초당 약 14토큰) |

번역 속도는 메모리 대역폭에 묶여 있어서 모델이 클수록 그만큼 느려져요.
맥북 에어처럼 팬이 없는 맥은 무거운 작업이 계속되면 더 느려지니, 통화 중에는 빌드처럼 무거운 작업을 꺼 두세요.

## 문제 해결

- **"Permission needed"가 떠요**: 화면 및 시스템 오디오 기록에서 터미널을 허용한 뒤 터미널을 재시작하세요.
- **자막이 안 나와요**: `-v`를 붙여 실행해 보세요. `--app` 이름이 맞는지(`./bin/audiotap --list`로 확인), 통화 음량이 0이 아닌지도 확인하세요.
- **번역이 느려요**: 더 작은 모델을 쓰고, 무거운 앱을 꺼 주세요.

## 동작 방식

- `audiotap/main.swift`: 시스템 오디오(16kHz mono float32)를 stdout으로 보내 주는 작은 ScreenCaptureKit 헬퍼예요. 앱별로 걸러서 캡처할 수 있어요.
- `app.py`:
  - VAD로 말을 문장 단위로 나눠요.
  - parakeet-mlx가 GPU에서 받아 적어요.
  - Ollama가 앞의 몇 문장을 문맥으로 참고해서 번역해요.
  - PyObjC 플로팅 패널에 결과를 보여 줘요. 이 패널은 `NSWindowSharingNone` 설정이라 화면 공유에 잡히지 않아요.

## 사용 시 주의

회사에 따라 면접 중 AI 도구 사용을 허용하지 않기도 해요. 면접 규정을 미리 확인하세요. 이 프로그램은 들리는 말을 번역할 뿐, 답변을 만들어 주지는 않아요.

## 라이선스

MIT
