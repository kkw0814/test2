import html
import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st

# 데이터 파일 경로 (app.py와 같은 폴더)
DATA_FILE = Path(__file__).parent / "data.json"

FILTER_OPTIONS = ["전체", "미완료", "완료"]
MAX_NAME_LENGTH = 100
DEFAULT_NAME = "(이름 없음)"


class DataReadError(Exception):
    """데이터 파일을 안전하게 읽을 수 없을 때 발생한다. 이때는 파일을 덮어쓰지 않는다."""


@st.cache_resource(show_spinner=False)
def get_lock() -> threading.RLock:
    """여러 세션(탭/사용자)이 파일을 동시에 읽고 쓰지 않도록 프로세스 전체에서 공유하는 잠금."""
    return threading.RLock()


# ---------- 데이터 파일 입출력 ----------


def _notify(message: str) -> None:
    """데이터 복구/정리 같은 중요한 알림을 다음 렌더링에 보여준다."""
    st.session_state["notice"] = message


def _backup_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return DATA_FILE.with_name(f"{DATA_FILE.name}.bak-{stamp}-{uuid.uuid4().hex[:4]}")


def _quarantine(reason: str) -> None:
    """읽을 수 없는 파일을 지우지 않고 백업 이름으로 옮긴다. 실패하면 덮어쓰지 않도록 중단한다."""
    backup = _backup_path()
    try:
        os.replace(DATA_FILE, backup)
    except OSError as e:
        raise DataReadError(f"{reason} 백업에도 실패했습니다({e}).") from e
    _notify(f"{reason} 원본은 '{backup.name}'로 보관하고 빈 목록으로 시작합니다.")


def _to_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y")
    return bool(value)


def _normalize(raw: list) -> tuple[list[dict], bool]:
    """파일에서 읽은 목록을 앱이 다루는 형태로 정리한다. 반환: (항목 목록, 정리 여부)"""
    items: list[dict] = []
    seen: set[str] = set()
    changed = False
    for entry in raw:
        if not isinstance(entry, dict):
            changed = True  # 항목이 아닌 값은 버린다(원본은 백업으로 보관)
            continue

        raw_id = entry.get("id")
        item_id = str(raw_id).strip() if raw_id is not None else ""
        if not item_id or item_id in seen:
            item_id = uuid.uuid4().hex  # id가 없거나 중복이면 새로 발급
            changed = True
        elif not isinstance(raw_id, str):
            changed = True
        seen.add(item_id)

        raw_name = entry.get("name")
        name = "" if raw_name is None else str(raw_name).strip()
        if not name or not isinstance(raw_name, str):
            changed = True
        name = name or DEFAULT_NAME

        raw_checked = entry.get("checked", False)
        if not isinstance(raw_checked, bool):
            changed = True

        items.append(
            {
                "id": item_id,
                "name": name,
                "checked": _to_bool(raw_checked),
                "created_at": str(entry.get("created_at") or ""),
            }
        )
    return items, changed


def save_items(items: list[dict]) -> None:
    """임시 파일에 쓴 뒤 교체한다. 쓰는 도중 종료되어도 기존 파일이 깨지지 않는다."""
    tmp = DATA_FILE.with_name(DATA_FILE.name + ".tmp")
    with get_lock():
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, DATA_FILE)


def load_items() -> list[dict]:
    """data.json에서 목록을 불러온다.

    - 파일이 없으면 빈 목록
    - 읽을 수 없는 파일(깨진 JSON, 잘못된 형식, 인코딩 오류)은 백업으로 옮기고 알림 표시
    - 손으로 편집한 파일(BOM, id 누락/중복, 타입 오류)은 정리하고, 원본은 백업으로 보관
    - 파일에 접근할 수 없으면 DataReadError (이 경우 덮어쓰지 않는다)
    """
    with get_lock():
        try:
            raw_bytes = DATA_FILE.read_bytes()
        except FileNotFoundError:
            return []
        except OSError as e:
            raise DataReadError(str(e)) from e

        try:
            data = json.loads(raw_bytes.decode("utf-8-sig"))  # 메모장 BOM 허용
        except (UnicodeDecodeError, json.JSONDecodeError):
            if not raw_bytes.strip():
                return []  # 빈 파일은 잃을 것이 없다
            _quarantine("데이터 파일을 해석하지 못했습니다.")
            return []

        if not isinstance(data, list):
            _quarantine("데이터 파일 형식이 올바르지 않습니다.")
            return []

        items, changed = _normalize(data)
        if changed:
            backup = _backup_path()
            try:
                shutil.copy2(DATA_FILE, backup)
                save_items(items)
            except OSError as e:
                raise DataReadError(f"데이터 정리 중 오류가 발생했습니다({e}).") from e
            _notify(f"데이터 파일의 일부 항목을 정리했습니다. 원본은 '{backup.name}'로 보관했습니다.")
        return items


def new_item(name: str) -> dict:
    """새 항목 딕셔너리를 생성한다."""
    return {
        "id": uuid.uuid4().hex,
        "name": name.strip(),
        "checked": False,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def mutate(change) -> bool:
    """파일을 기준으로 '읽기 → 변경 → 저장'을 잠금 안에서 한 번에 수행한다.

    세션 메모리의 오래된 목록으로 파일 전체를 덮어쓰지 않으므로,
    다른 탭/사용자가 추가한 항목이 사라지지 않는다.
    change(items)는 전달받은 목록을 제자리에서 수정해야 한다.
    """
    try:
        with get_lock():
            items = load_items()
            change(items)
            save_items(items)
    except (OSError, DataReadError) as e:
        st.session_state["flash"] = f"저장하지 못했습니다: {e}"
        return False
    st.session_state["shopping_items"] = items
    return True


# ---------- 상태 ----------


def init_state() -> None:
    """session_state를 초기화하고, 매 실행마다 파일의 최신 목록을 불러온다."""
    # 주의: "items"는 session_state의 dict 메서드 이름과 겹치므로 사용하지 않는다
    for key, default in (
        ("shopping_items", []),
        ("editing_id", None),  # 수정 중인 항목 id
        ("flash", None),  # 다음 렌더링에 보여줄 경고 메시지
        ("notice", None),  # 데이터 복구/정리 알림
        ("undo", None),  # 마지막 삭제를 되돌리기 위한 정보
    ):
        if key not in st.session_state:
            st.session_state[key] = default

    try:
        st.session_state["shopping_items"] = load_items()
    except DataReadError as e:
        st.session_state["flash"] = f"데이터 파일을 읽지 못했습니다: {e} 마지막으로 불러온 목록을 표시합니다."

    # 다른 탭에서 삭제된 항목을 수정 중이었다면 수정 모드를 해제한다
    if st.session_state["editing_id"] not in {i["id"] for i in get_items()}:
        st.session_state["editing_id"] = None


def get_items() -> list[dict]:
    return st.session_state["shopping_items"]


def md_code(text: str) -> str:
    """텍스트를 마크다운 인라인 코드로 감싸 입력한 그대로 보이게 한다."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def short(text: str, limit: int = 30) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


# ---------- 상태 변경 로직 (콜백으로 호출되며, 변경 직후 항상 저장) ----------


def add_item() -> None:
    """F1: 추가 폼 제출 시 항목을 추가한다. 빈 입력은 거부한다."""
    name = st.session_state.get("add_input", "").strip()[:MAX_NAME_LENGTH]
    if not name:
        st.session_state["flash"] = "아이템 이름을 입력해주세요."
        return
    if mutate(lambda items: items.append(new_item(name))):
        st.session_state["undo"] = None


def start_edit(item_id: str) -> None:
    """F2: 수정 모드로 전환한다. 한 번에 한 항목만 수정할 수 있다."""
    st.session_state["editing_id"] = item_id


def cancel_edit() -> None:
    st.session_state["editing_id"] = None


def update_item(item_id: str) -> None:
    """F2: 수정한 이름을 저장한다. 빈 값은 거부하고 수정 모드를 유지한다."""
    name = st.session_state.get(f"edit_input_{item_id}", "").strip()[:MAX_NAME_LENGTH]
    if not name:
        st.session_state["flash"] = "아이템 이름은 비워둘 수 없습니다."
        return

    found = []

    def change(items: list[dict]) -> None:
        for item in items:
            if item["id"] == item_id:
                item["name"] = name
                found.append(True)
                break

    if mutate(change):
        if not found:
            st.session_state["flash"] = "다른 곳에서 이미 삭제된 항목입니다."
        st.session_state["editing_id"] = None
        st.session_state["undo"] = None


def delete_item(item_id: str) -> None:
    """F3: 항목을 삭제한다. 직후 '실행 취소'로 되돌릴 수 있다."""
    removed: list[tuple[int, dict]] = []

    def change(items: list[dict]) -> None:
        for index, item in enumerate(items):
            if item["id"] == item_id:
                removed.append((index, items.pop(index)))
                break

    if mutate(change):
        if removed:
            st.session_state["undo"] = {
                "message": f"{md_code(short(removed[0][1]['name']))} 항목을 삭제했습니다.",
                "removed": removed,
            }
        if st.session_state["editing_id"] == item_id:
            st.session_state["editing_id"] = None


def toggle_item(item_id: str) -> None:
    """F4: 체크박스 상태를 항목에 반영한다."""
    checked = bool(st.session_state.get(f"check_{item_id}", False))

    def change(items: list[dict]) -> None:
        for item in items:
            if item["id"] == item_id:
                item["checked"] = checked
                break

    mutate(change)


def clear_checked() -> None:
    """F6: 완료된 항목을 모두 삭제한다. 직후 '실행 취소'로 되돌릴 수 있다."""
    removed: list[tuple[int, dict]] = []

    def change(items: list[dict]) -> None:
        removed.extend((i, item) for i, item in enumerate(items) if item["checked"])
        items[:] = [item for item in items if not item["checked"]]

    if mutate(change):
        if removed:
            st.session_state["undo"] = {
                "message": f"완료 항목 {len(removed)}개를 삭제했습니다.",
                "removed": removed,
            }


def undo_delete() -> None:
    """마지막 삭제를 되돌린다. 원래 위치에 다시 넣고, 이미 존재하는 id는 건너뛴다."""
    undo = st.session_state.get("undo")
    if not undo:
        return

    def change(items: list[dict]) -> None:
        existing = {item["id"] for item in items}
        for index, item in sorted(undo["removed"], key=lambda pair: pair[0]):
            if item["id"] not in existing:
                items.insert(min(index, len(items)), item)

    if mutate(change):
        st.session_state["undo"] = None


# ---------- 화면 ----------

# 고정 CSS (사용자 입력이 들어가지 않는 정적 문자열이므로 HTML 주입 위험 없음).
# 색상은 지정하지 않고 테마 변수/투명도만 써서 라이트·다크 모두에서 읽히게 한다.
APP_CSS = """
<style>
/* 좁은 화면에서도 체크박스/버튼이 한 줄로 유지되도록 컬럼 줄바꿈을 막는다 */
[data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap !important;
    gap: 0.5rem !important;
}
[data-testid="stColumn"] {
    min-width: 0 !important;
}
/* 터치하기 쉬운 버튼 크기 (권장 44px) */
.stButton button, [data-testid="stFormSubmitButton"] button {
    min-height: 2.75rem;
    padding: 0.25rem 0.6rem;
}
h1 {
    padding-bottom: 0.25rem;
}

/* 항목 한 줄: [체크박스] [이름] [수정] [삭제] */
[class*="st-key-line_"] {
    position: relative;
}
[class*="st-key-line_"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(1) {
    flex: 0 0 2rem !important;
}
[class*="st-key-line_"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(2) {
    flex: 1 1 0 !important;
}
[class*="st-key-line_"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(n+3) {
    flex: 0 0 2.75rem !important;
}
/* 이름을 눌러도 체크가 토글되도록, 체크박스 라벨 영역을 한 줄 전체로 확장한다.
   체크박스를 감싼 요소가 position: relative이면 확장이 그 칸 안에 갇히므로 static으로 푼다 */
[class*="st-key-line_"] [data-testid="stElementContainer"]:has([data-testid="stCheckbox"]) {
    position: static !important;
}
[class*="st-key-line_"] [data-testid="stCheckbox"] label {
    position: static;
    cursor: pointer;
}
[class*="st-key-line_"] [data-testid="stCheckbox"] label::after {
    content: "";
    position: absolute;
    inset: -0.4rem 0;
    z-index: 1;
}
[class*="st-key-line_"] button {
    position: relative;
    z-index: 2;
}
.item-name {
    overflow-wrap: anywhere;
    line-height: 1.4;
}
.item-name.done {
    text-decoration: line-through;
    opacity: 0.55;
}

/* 아이콘 버튼: 접근성 이름("수정", "삭제")은 유지하고 화면에는 아이콘만 보여준다 */
[class*="st-key-edit_"]:not([class*="st-key-edit_form_"]) button p,
[class*="st-key-delete_"] button p {
    font-size: 0;
    line-height: 0;
}
[class*="st-key-edit_"]:not([class*="st-key-edit_form_"]) button p::before,
[class*="st-key-delete_"] button p::before {
    font-size: 1rem;
    line-height: 1.5;
}
[class*="st-key-edit_"]:not([class*="st-key-edit_form_"]) button p::before {
    content: "✏️";
}
[class*="st-key-delete_"] button p::before {
    content: "🗑️";
}
</style>
"""


def inject_css() -> None:
    st.markdown(APP_CSS, unsafe_allow_html=True)


def render_header() -> None:
    st.title("🛒 쇼핑 리스트")
    st.caption("살 물건을 적고, 샀으면 체크하세요.")


def render_input() -> None:
    """F1: 추가 폼 (Enter 제출 지원, 제출 후 입력창 비움)"""
    with st.form("add_form", clear_on_submit=True, border=False):
        col_input, col_btn = st.columns([5, 2], vertical_alignment="bottom")
        col_input.text_input(
            "새 아이템",
            key="add_input",
            placeholder="사야 할 물건을 입력하세요",
            max_chars=MAX_NAME_LENGTH,
        )
        col_btn.form_submit_button(
            "추가", on_click=add_item, type="primary", use_container_width=True
        )


def render_flash() -> None:
    """콜백/불러오기에서 저장한 메시지를 한 번만 보여준다."""
    notice = st.session_state["notice"]
    if notice:
        st.warning(notice, icon="⚠️")
        st.session_state["notice"] = None
    message = st.session_state["flash"]
    if message:
        st.warning(message)
        st.session_state["flash"] = None


def render_undo() -> None:
    """삭제 직후 '실행 취소' 안내"""
    undo = st.session_state["undo"]
    if not undo:
        return
    col_msg, col_btn = st.columns([5, 2], vertical_alignment="center")
    col_msg.markdown(undo["message"])
    col_btn.button(
        "실행 취소", key="undo_btn", on_click=undo_delete, use_container_width=True
    )


def render_summary() -> None:
    """F5: 남은 개수 / 전체 개수 + 완료 비율 진행바"""
    items = get_items()
    total = len(items)
    remaining = sum(1 for i in items if not i["checked"])
    done_ratio = (total - remaining) / total if total else 0.0
    st.progress(done_ratio, text=f"남은 항목 {remaining} / 전체 {total}")


def render_filter() -> str:
    """F7: 전체 / 미완료 / 완료 필터. 선택 해제 시 '전체'로 간주한다."""
    selected = st.segmented_control(
        "보기",
        FILTER_OPTIONS,
        default="전체",
        key="filter",
        label_visibility="collapsed",
    )
    return selected or "전체"


def render_item_row(item: dict) -> None:
    item_id = item["id"]
    editing_id = st.session_state["editing_id"]

    # 항목마다 카드(테두리 컨테이너)로 감싸 구분한다
    with st.container(border=True, key=f"row_{item_id}"):
        if editing_id == item_id:
            # 수정 모드: 입력창 + [저장][취소]
            with st.form(f"edit_form_{item_id}", border=False):
                st.text_input(
                    "이름 수정",
                    value=item["name"],
                    key=f"edit_input_{item_id}",
                    max_chars=MAX_NAME_LENGTH,
                    label_visibility="collapsed",
                )
                col_save, col_cancel = st.columns(2)
                col_save.form_submit_button(
                    "저장",
                    on_click=update_item,
                    args=(item_id,),
                    type="primary",
                    use_container_width=True,
                )
                col_cancel.form_submit_button(
                    "취소", on_click=cancel_edit, use_container_width=True
                )
            return

        # 일반 모드: 체크박스 / 이름 / 수정 / 삭제
        with st.container(key=f"line_{item_id}"):
            col_check, col_name, col_edit, col_delete = st.columns(
                [1, 8, 1, 1], vertical_alignment="center"
            )
            # 라벨은 화면에 보이지 않고 접근성 이름으로만 쓰이므로 이름을 그대로 넣는다
            col_check.checkbox(
                item["name"],
                value=item["checked"],
                key=f"check_{item_id}",
                on_change=toggle_item,
                args=(item_id,),
                label_visibility="collapsed",
            )
            # 이름은 html.escape로 이스케이프해 입력한 그대로(태그·이모지 코드·링크 변환 없이) 표시한다
            css_class = "item-name done" if item["checked"] else "item-name"
            col_name.html(f'<span class="{css_class}">{html.escape(item["name"])}</span>')
            col_edit.button(
                "수정",
                key=f"edit_{item_id}",
                on_click=start_edit,
                args=(item_id,),
                help="수정",
                disabled=editing_id is not None,  # 다른 항목을 수정 중이면 입력 내용 유실 방지
                use_container_width=True,
            )
            col_delete.button(
                "삭제",
                key=f"delete_{item_id}",
                on_click=delete_item,
                args=(item_id,),
                help="삭제",
                use_container_width=True,
            )


def render_list(filter_value: str) -> None:
    items = get_items()
    if not items:
        st.markdown(
            "<div style='text-align:center; padding:2rem 0; opacity:0.7;'>"
            "<div style='font-size:3rem;'>🧺</div>"
            "<div>목록이 비어 있습니다.<br>위에서 첫 아이템을 추가해보세요!</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        return

    if filter_value == "미완료":
        visible = [i for i in items if not i["checked"]]
    elif filter_value == "완료":
        visible = [i for i in items if i["checked"]]
    else:
        visible = items

    if not visible:
        # 필터 이름은 고정 선택지 중 하나이므로 사용자 입력이 아니다
        st.caption(f"'{filter_value}' 항목이 없습니다.")
    for item in visible:
        render_item_row(item)


def render_footer() -> None:
    """F6: 완료 항목 일괄 삭제 (완료 항목이 없으면 비활성화)"""
    has_checked = any(i["checked"] for i in get_items())
    st.button(
        "🧹 완료 항목 삭제",
        on_click=clear_checked,
        disabled=not has_checked,
        use_container_width=True,
    )


def main() -> None:
    st.set_page_config(page_title="쇼핑 리스트", page_icon="🛒", layout="centered")
    init_state()
    inject_css()
    render_header()
    render_input()
    render_flash()
    render_summary()
    render_undo()
    filter_value = render_filter()
    render_list(filter_value)
    render_footer()


main()
