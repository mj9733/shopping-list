# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트

Streamlit으로 만드는 단일 파일 쇼핑 리스트 앱(추가·수정·삭제·체크). 요구사항의 기준은 `PRD.md`(v1.1)이다. `PROMPTS.md`의 4단계(뼈대 → 기능 → UI → 검토)를 모두 마쳤고, 4단계 검토 결과와 조치 내역은 `REVIEW.md`에 있다.

## 명령어

```bash
python -m pip install -r requirements.txt   # streamlit 설치
python -m streamlit run app.py              # 앱 실행 (streamlit.exe가 PATH에 없으므로 python -m 사용)
python -m pytest                            # 전체 테스트 (pytest는 requirements.txt에 없어 별도 설치)
python -m pytest test_app.py::test_add_item # 단일 테스트
```

`test_app.py`는 순수 함수 테스트와 `streamlit.testing.v1.AppTest`로 화면 조작을 흉내 내는 흐름 테스트로 나뉜다. 흐름 테스트는 `data_file` 픽스처가 `SHOPPING_LIST_FILE` 환경 변수를 임시 경로로 바꾸기 때문에 실제 `shopping_list.json`을 건드리지 않는다. 새 AppTest 테스트에도 이 픽스처를 쓴다. 탭 두 개 상황은 `AppTest`를 두 개 만들어 흉내 낸다.

AppTest로 확인할 수 없는 것들이 있다: `clear_on_submit`으로 입력창이 비는지, Enter 제출, 실제 렌더링(`$` 수식 해석, CSS), 브라우저 성능. 이런 것은 `SHOPPING_LIST_FILE`을 임시 파일로 지정한 서버를 다른 포트(예: 8502)에 띄워 브라우저로 확인한다.

## 아키텍처

모든 코드는 `app.py` 하나에 있고, 섹션 주석으로 나눈다.

- **데이터 계층 (Streamlit 의존 없음):** `new_item`, `clamp_quantity`, `normalize_items`, `load_items`, `save_items`, `find_item`
- **상태:** `init_state`, `sync_items`, `_commit`, 그리고 PRD 함수(`add_item`/`update_item`/`delete_item`/`toggle_item`/`clear_checked`)
- **콜백:** `_on_add`, `_on_toggle`, `_on_delete`, `_start_edit`, `_on_save_edit` 등. 토스트 알림은 여기서만 띄운다.
- **UI 계층:** `render_*`, `main`

데이터 흐름. **원본은 파일(`shopping_list.json`)이고, `st.session_state["items"]`는 화면 표시용 사본이다.**
- `main`은 매번 `sync_items()`로 파일을 다시 읽는다.
- 모든 변경은 `_commit(change)`를 거친다: 파일을 다시 읽기 → `change(items)` 적용 → 저장. 저장에 실패하면 세션을 바꾸지 않고 오류를 알린다.
- 이렇게 해서 여러 탭이 서로의 변경을 지우지 않는다. 변경 함수를 새로 만들 때도 `_commit`을 쓰고, 세션의 리스트를 직접 고쳐 저장하지 않는다.

설계상 지켜야 할 점:
- `load_items`는 예외를 던지지 않는다. 손상된 파일은 `*.bak.json`으로 백업하고 빈 목록으로 초기화한다. 보정한 항목은 파일에 다시 쓴다. 알릴 내용은 `errors` 리스트에 담는다.
- `save_items`는 임시 파일에 쓴 뒤 `os.replace`로 교체한다(Windows 잠금에 대비해 재시도).
- 사용자에게 보여줄 경고·오류는 `_notify()`로 쌓고, `render_notices()`가 한 번 보여준 뒤 비운다.
- 체크박스는 `value` 인자 대신 렌더링 직전에 `st.session_state[f"chk_{id}"]`를 파일 값으로 맞춘다. 그래야 다른 탭의 변경이 화면에 반영된다. 체크 콜백은 반전하지 않고 위젯 값을 그대로 저장한다.
- 위젯 key는 리스트 인덱스가 아니라 아이템 id로 만든다(`chk_{id}`, `edit_{id}`, `del_{id}`, `name_{id}`, `qty_{id}`, `save_{id}`, `cancel_{id}`). 인덱스를 쓰면 삭제나 정렬 후 위젯 상태가 다른 행으로 밀린다.
- 아이템 이름은 마크다운으로 렌더링하지 않는다. 행 이름은 `st.html` + `html.escape`로 그린다. `st.toast`처럼 마크다운으로 해석되는 곳에는 `_escape_markdown`을 거친다(`$`가 수식으로 해석된다).
- 목록은 화면에서만 미구매 → 구매 완료 순으로 정렬하고 `PAGE_SIZE`(50)개씩 나눈다. 저장 순서는 추가한 순서 그대로다.
- 입력 제한은 `MAX_NAME_LEN`(50), `MAX_QUANTITY`(999) 상수로 관리하며, 입력 위젯과 `normalize_items` 둘 다 이 값을 쓴다.
- 스타일은 `CSS` 상수 하나에 모여 있다. 행 스타일은 `st.container(key=...)`가 만드는 `st-key-row_{id}`/`st-key-editrow_{id}`/`st-key-item_list` 클래스를 대상으로 한다. Streamlit 컬럼의 기본 최소 너비(128px)를 CSS로 해제했기 때문에 모바일에서도 `st.columns(wrap=False)` 비율이 유지된다. 레이아웃을 바꾸면 400px 폭에서 꼭 확인한다.
- 외부 의존성은 streamlit만 쓴다.

## 환경 주의사항 (Windows)

- 프로젝트 경로에 한글이 있다(`바탕화면\쇼핑리스트`). 파일 입출력은 항상 `encoding="utf-8"`로 하고, JSON은 `ensure_ascii=False`로 저장한다.
- 콘솔 기본 인코딩이 cp949라서 이모지나 일부 문자를 print하면 `UnicodeEncodeError`가 난다. 스크립트 출력을 확인할 때는 `PYTHONIOENCODING=utf-8`을 설정한다.
- PowerShell 5.1의 `Set-Content -Encoding utf8`은 BOM을 붙인다. BOM이 붙은 `.streamlit/config.toml`은 Streamlit이 읽지 못하고 조용히 무시한다. 설정 파일은 Write 도구로 쓴다.
- `.streamlit/config.toml`의 `server.address = "localhost"` 때문에 로컬 서버는 이 PC에서만 접속된다.
