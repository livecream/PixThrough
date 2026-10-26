# 사진셀렉정리툴 (PixThrough)

사진 폴더를 스캔해 같은 장면(연속 촬영)끼리 자동으로 묶고, 선명도/노출/얼굴(눈뜸)
기준으로 장면별 베스트 컷을 골라 지정한 폴더로 복사(또는 이동)해주는 도구.

## 다운로드 (macOS)

터미널 없이 바로 쓰려면 [Releases](https://github.com/livecream/PixThrough/releases/latest)에서
`PixThrough-macOS.zip`을 받아 압축을 풀고 `PixThrough.app`을 실행하세요.

> 서명되지 않은 앱이라 **첫 실행은 반드시 우클릭 → 열기**로 해야 합니다
> (macOS가 처음 한 번은 더블클릭 실행을 막습니다). 이후에는 평소처럼 더블클릭하면 됩니다.

## 다운로드 (Windows)

[Releases](https://github.com/livecream/PixThrough/releases/latest)에서
`PixThrough-Windows.zip`을 받아 압축을 풀고, `PixThrough` 폴더 안의
`PixThrough.exe`를 실행하세요. 설치나 Python은 필요 없습니다.

> 압축을 **반드시 먼저 푼 뒤** 실행하세요(zip 안에서 바로 실행하면 동작하지 않습니다).
> `PixThrough.exe`는 같은 폴더의 `_internal` 폴더가 있어야 실행되므로 폴더째로 두세요.
> 서명되지 않은 앱이라 처음 실행할 때 "Windows의 PC 보호" 창이 뜨면
> **추가 정보 → 실행**을 누르면 됩니다.

## 업데이트

v1.3.0부터는 앱이 켜질 때 새 버전을 확인합니다. 새 버전이 있으면 알림이 뜨고,
**업데이트**를 누르면 받아서 앱을 바꾼 뒤 다시 시작합니다(메뉴 **도움말 > 업데이트 확인…**
으로 언제든 직접 확인할 수 있고, 시작할 때 확인은 같은 메뉴에서 끌 수 있습니다).
설정·마지막 폴더·저장된 점수는 그대로 남습니다.

- macOS: 다운로드 폴더에서 바로 실행하면 macOS가 앱을 임시 위치에서 실행해 교체할 수
  없습니다. `PixThrough.app`을 **응용 프로그램(Applications)** 폴더 등으로 옮겨 두세요.
- 앱이 있는 폴더에 쓸 권한이 없으면(예: Windows의 `Program Files`) 다운로드 페이지를
  열어 주니 직접 받아 바꾸면 됩니다.
- v1.2.0 이하에는 이 기능이 없어서, v1.3.0은 한 번 직접 받아 바꿔야 합니다.

## 개발자용 (소스에서 실행)

```bash
git clone https://github.com/livecream/PixThrough.git
cd PixThrough
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# GUI 실행
.venv/bin/python src/gui.py

# CLI로 직접 실행 (예: 미리보기)
.venv/bin/python src/cli.py report --input ~/Photos/2026-09 --recursive --output report.csv

# .app으로 재빌드
.venv/bin/pyinstaller packaging/PixThrough.spec --distpath dist --workpath build
```

## Windows에서 빌드하기

PyInstaller는 크로스 컴파일을 지원하지 않는다 — macOS에서 빌드하면 macOS용
바이너리만 나오므로, Windows용 실행 파일은 **반드시 Windows PC(또는 VM)에서**
빌드해야 한다. `packaging/PixThrough.windows.spec`이 Windows 전용 스펙이다
(macOS 전용인 `.app` 번들 단계만 빠지고 나머지는 동일).

Windows PowerShell(또는 cmd)에서:

```powershell
git clone https://github.com/livecream/PixThrough.git
cd PixThrough
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# GUI 실행
.venv\Scripts\python src\gui.py

# .exe로 빌드 (dist\PixThrough\PixThrough.exe 생성)
.venv\Scripts\pyinstaller packaging\PixThrough.windows.spec --distpath dist --workpath build
```

빌드 결과물은 `dist\PixThrough\` 폴더 전체(실행 파일 + 의존 DLL/데이터)이며,
이 폴더를 통째로 압축해서 배포한다 — `PixThrough.exe`만 따로 옮기면 실행되지
않는다. macOS와 마찬가지로 서명되지 않은 실행 파일이라 처음 실행할 때
Windows Defender SmartScreen이 "알 수 없는 게시자" 경고를 띄울 수 있는데,
"추가 정보" → "실행"으로 넘어가면 된다(코드 서명 인증서가 없으면 항상 뜨는
정상적인 경고).

## 릴리스 배포 (자동)

`.github/workflows/release.yml`이 태그 push를 감지해 GitHub Actions에서
Windows/macOS를 각각 빌드하고 릴리스에 두 zip을 올린다 — 로컬에서 OS별로
따로 빌드할 필요 없음.

1. `src/version.py`의 `__version__`을 올리고, 릴리스 노트를 `docs/release-notes/v<버전>.md`에
   한글로 써서 함께 커밋 (예: `1.3.0`, `docs/release-notes/v1.3.0.md` — 없으면 PR 목록으로 자동 생성)
2. 같은 버전으로 태그를 만들어 push — 태그와 `version.py`가 다르면 빌드가 실패한다
   ```bash
   git tag v1.2.0
   git push origin main v1.2.0
   ```
3. GitHub → Actions 탭에서 진행 상황 확인 (보통 10~20분), 끝나면 Releases에 zip 두 개가 올라온다

이미 있는 릴리스에 특정 OS 빌드만 추가/교체하려면 Actions → Release →
Run workflow에서 태그와 OS를 골라 수동 실행한다.
이미 나온 릴리스의 노트만 바꾸려면 노트 파일을 고쳐 main에 넣은 뒤 Actions →
Release notes → Run workflow에서 태그를 넣어 실행한다(빌드는 다시 하지 않음).

자세한 개발 가이드는 [CLAUDE.md](CLAUDE.md), 스펙은 [PROJECT_SPEC.md](PROJECT_SPEC.md) 참고.
