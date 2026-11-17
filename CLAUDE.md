# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 언어 규칙
- 응답, 코드 주석, 커밋 메시지, 문서: 한국어 (상위 `C:\Users\User\CLAUDE.md` 규칙 승계)
- 변수명/함수명: 영어

## 문서
- `PRD.md`: 제품 요구사항 (기능 F1~F8, 데이터 모델, 배포 계획)
- `PROMPTS.md`: 개발 단계별 프롬프트 (1 뼈대 → 2 기능 → 3 UI → 4 Playwright 검토)
- `REVIEW.md`: 4단계 검토 결과와 이후 수정 내역

## 명령어
- 설치: `pip install -r requirements.txt`
- 실행: `streamlit run app.py` (기본 http://localhost:8501)
- 이 PC에서는 `python`이 PATH에 없고 Anaconda에 있다: `C:\Users\User\anaconda3\python.exe -m streamlit run app.py`
- 빌드/린트/테스트 체계는 없다. 검증은 Playwright MCP(브라우저 조작)와 `streamlit.testing.v1.AppTest`로 한다.

## 구조 (`app.py` 단일 파일)
- 저장소는 `app.py` 옆의 `data.json`. **파일이 기준(source of truth)** 이고 `session_state["shopping_items"]`는 매 실행마다 파일에서 새로 불러온 복사본이다.
- 모든 변경은 `mutate(change)`를 거친다: 잠금 안에서 파일을 다시 읽고 → 변경하고 → 임시 파일에 쓴 뒤 교체한다. 세션 메모리의 오래된 목록으로 파일 전체를 덮어쓰면 다른 탭/사용자의 변경이 사라지므로 그렇게 쓰지 않는다. 콜백(`add_item`, `update_item`, `delete_item`, `toggle_item`, `clear_checked`, `undo_delete`)이 `mutate`를 호출하고, 재실행은 콜백 종료 후 자동으로 일어난다(`st.rerun` 불필요).
- `load_items()`는 읽을 수 없는 파일을 지우거나 덮어쓰지 않는다. 깨진 JSON/잘못된 형식/인코딩 오류는 `data.json.bak-*`로 옮기고, id 누락·중복·타입 오류는 정리하되 원본을 백업한 뒤 알림(`notice`)을 띄운다. 파일 접근 자체가 실패하면 `DataReadError`로 중단한다.
- `st.session_state`는 항상 `st.session_state["키"]` 형태로 접근한다(`shopping_items`, `editing_id`, `flash`, `notice`, `undo`). `st.session_state.items`처럼 속성 접근을 쓰면 dict의 `.items()` 메서드와 충돌해 `TypeError`가 난다.
- 위젯 key는 항목 id 기반(`check_`, `edit_`, `delete_`, `edit_input_`, `edit_form_`, `row_`, `line_`)이고, CSS가 `st-key-*` 클래스에 의존한다. key 접두어를 바꾸면 CSS도 함께 고쳐야 한다.

## 화면/CSS 주의
- 항목 이름은 `html.escape`한 값을 `st.html`로 렌더링한다(마크다운 라벨로 쓰면 `:smile:` 변환, `&amp;` 해석, URL 자동 링크 문제가 생긴다). 체크박스 라벨은 `label_visibility="collapsed"`로 숨기고 접근성 이름으로만 쓴다.
- 이름을 눌러도 체크가 토글되도록 `APP_CSS`가 체크박스 `label::after`를 한 줄 전체로 확장한다. 이를 위해 체크박스를 감싼 `stElementContainer`의 `position: relative`를 `static`으로 푼다. Streamlit DOM 구조가 바뀌면 이 부분을 가장 먼저 확인한다.
- ✏️/🗑️ 버튼은 라벨을 "수정"/"삭제"로 두고 CSS(`font-size: 0` + `::before`)로 아이콘만 보여준다. 접근성 이름을 유지하기 위한 것이며, `edit_form_` 키의 폼 버튼에는 적용되지 않도록 `:not()`으로 제외한다.
- `APP_CSS`는 모바일에서 컬럼이 세로로 쌓이지 않도록 `stHorizontalBlock`의 줄바꿈을 막는다. 항목 줄의 아이콘 버튼 열은 44px 고정이다.
- 테마는 `.streamlit/config.toml`의 `[theme.light]`/`[theme.dark]`로 각각 정의한다. 단일 `[theme]`만 쓰면 커스텀 테마로 취급되어 시스템 다크 모드를 따르지 않는다. 테마 변경은 서버 재시작 후 반영된다.
- 수정 중에는 다른 항목의 수정 버튼이 비활성화된다(입력 내용 유실 방지). 삭제/일괄 삭제 직후에는 `undo`에 삭제 항목과 원래 위치를 보관해 "실행 취소"로 복원한다.

## 검증 시 주의
- 사용자가 8501 서버의 실제 `data.json`을 쓰고 있을 수 있다. 테스트는 `app.py`를 임시 폴더에 복사해 다른 포트와 별도 `data.json`으로 실행한다.
- 이름 영역이 오버레이로 덮여 있어 Playwright의 `locator.click()`이 "intercepts pointer events"로 실패한다. 이름 클릭은 좌표(`page.mouse.click`)로 한다.

## 배포 시 주의
- `.gitignore`가 `data.json`, `data.json.bak-*`, `__pycache__/`, `.playwright-mcp/`, `screenshots/`를 제외한다. 실제 쇼핑 목록이 저장소에 올라가지 않게 유지한다.
- Streamlit Community Cloud에서는 재시작 시 `data.json`이 초기화될 수 있고, 로그인이 없어 접속자 모두 같은 목록을 공유한다(PRD 13절). 동시 접속 시 서로의 변경을 덮어쓰지는 않지만 목록 자체는 공유된다.
- `requirements.txt`는 검증한 버전(1.58) 이상으로 고정되어 있다.
