"""쇼핑 리스트 앱 (Streamlit).

실행: streamlit run app.py
"""

import html
import json
import math
import os
import re
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

import streamlit as st

# 테스트에서는 SHOPPING_LIST_FILE 환경 변수로 임시 파일 경로를 지정한다.
DATA_FILE = Path(
    os.environ.get("SHOPPING_LIST_FILE", Path(__file__).parent / "shopping_list.json")
)
MAX_NAME_LEN = 50
MAX_QUANTITY = 999
PAGE_SIZE = 50


# ---------------------------------------------------------------------------
# 데이터 계층 (Streamlit에 의존하지 않는 순수 함수)
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_item(name: str, quantity: int = 1) -> dict:
    """PRD 6장 스키마에 맞는 새 아이템을 만든다."""
    return {
        "id": uuid.uuid4().hex,
        "name": name,
        "quantity": quantity,
        "checked": False,
        "created_at": _now(),
    }


def clamp_quantity(value: object) -> int:
    """수량을 1~MAX_QUANTITY 범위의 정수로 바꾼다. 숫자가 아니면 1."""
    if isinstance(value, bool):
        return 1
    try:
        quantity = int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return 1
    return min(max(quantity, 1), MAX_QUANTITY)


def normalize_items(data: list, errors: list[str] | None = None) -> list[dict]:
    """불러온 데이터를 PRD 6장 스키마에 맞게 검사하고 보정한다.

    이름이 없는 항목과 dict가 아닌 항목은 건너뛰고, 나머지 필드는 기본값으로
    채우거나 범위에 맞춘다. 건너뛰거나 고친 항목이 있으면 errors에 알린다.
    """
    items: list[dict] = []
    seen_ids: set[str] = set()
    skipped = fixed = 0
    for raw in data:
        name = raw.get("name") if isinstance(raw, dict) else None
        if not isinstance(name, str) or not name.strip():
            skipped += 1
            continue
        item_id = raw.get("id")
        created_at = raw.get("created_at")
        item = {
            "id": item_id
            if isinstance(item_id, str) and item_id and item_id not in seen_ids
            else uuid.uuid4().hex,
            "name": name.strip()[:MAX_NAME_LEN],
            "quantity": clamp_quantity(raw.get("quantity")),
            "checked": raw["checked"] if isinstance(raw.get("checked"), bool) else False,
            "created_at": created_at if isinstance(created_at, str) else _now(),
        }
        if item != raw:
            fixed += 1
        seen_ids.add(item["id"])
        items.append(item)
    if errors is not None:
        if skipped:
            errors.append(f"형식이 잘못된 항목 {skipped}개를 건너뛰었습니다.")
        if fixed:
            errors.append(f"항목 {fixed}개의 잘못된 값을 고쳤습니다.")
    return items


def _backup_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.{datetime.now():%Y%m%d-%H%M%S}.bak{path.suffix}")


def load_items(path: Path = DATA_FILE, errors: list[str] | None = None) -> list[dict]:
    """JSON 파일에서 아이템 목록을 읽는다.

    - 파일이 없으면 빈 리스트를 반환한다.
    - 파일이 손상됐으면 원본을 백업 파일로 복사하고 빈 목록으로 초기화한다.
    - 항목을 건너뛰거나 고쳤으면 고친 내용을 파일에 다시 쓴다.
    알릴 내용은 errors 리스트에 추가한다(주어진 경우).
    """
    errors = errors if errors is not None else []
    if not path.exists():
        return []
    try:
        raw = path.read_bytes()
    except OSError as e:
        errors.append(f"데이터 파일을 읽지 못했습니다: {e}")
        return []

    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, list):
            raise ValueError("최상위 값이 리스트가 아님")
    except ValueError:  # JSONDecodeError, UnicodeDecodeError 포함
        backup = _backup_path(path)
        try:
            backup.write_bytes(raw)
            save_items([], path)
        except OSError as e:
            errors.append(f"데이터 파일이 손상됐는데 백업하지 못했습니다: {e}")
            return []
        errors.append(f"데이터 파일이 손상돼 {backup.name}에 백업하고 빈 목록으로 시작합니다.")
        return []

    notes: list[str] = []
    items = normalize_items(data, notes)
    if notes:
        errors.extend(notes)
        try:
            save_items(items, path)
        except OSError:
            pass  # 다음 저장 때 다시 시도된다
    return items


def save_items(items: list[dict], path: Path = DATA_FILE, retries: int = 3) -> None:
    """아이템 목록을 JSON 파일에 저장한다 (UTF-8, 한글 그대로).

    임시 파일에 다 쓴 뒤 원본과 교체하므로, 쓰는 도중 중단돼도 원본이 깨지지 않는다.
    Windows에서 다른 스레드가 파일을 읽는 중이면 교체가 잠깐 실패할 수 있어 몇 번 재시도한다.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        for attempt in range(retries):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == retries - 1:
                    raise
                time.sleep(0.05)
    finally:
        tmp.unlink(missing_ok=True)


def find_item(items: list[dict], item_id: str) -> dict | None:
    return next((i for i in items if i["id"] == item_id), None)


# ---------------------------------------------------------------------------
# 상태: 파일이 원본이다. 조작할 때마다 파일을 다시 읽고 → 바꾸고 → 저장한다.
# ---------------------------------------------------------------------------

def init_state() -> None:
    """세션 상태를 처음 한 번만 초기화한다."""
    defaults = {"items": [], "editing_id": None, "edit_error": None, "notices": [], "page": 0}
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _notify(level: str, message: str) -> None:
    """다음 화면에 한 번만 보여줄 알림(warning/error)을 쌓는다."""
    st.session_state["notices"].append((level, message))


def sync_items() -> None:
    """파일에서 최신 목록을 읽어 세션에 반영한다. 다른 탭에서 바꾼 내용도 여기서 반영된다."""
    errors: list[str] = []
    st.session_state["items"] = load_items(errors=errors)
    for message in errors:
        _notify("warning", message)
    if find_item(st.session_state["items"], st.session_state["editing_id"] or "") is None:
        st.session_state["editing_id"] = None


def _commit(change: Callable[[list[dict]], bool]) -> bool:
    """최신 파일 내용에 change를 적용하고 저장한다. 실패하면 알리고 False를 반환한다."""
    errors: list[str] = []
    items = load_items(errors=errors)
    for message in errors:
        _notify("warning", message)
    if not change(items):
        st.session_state["items"] = items
        return False
    try:
        save_items(items)
    except OSError as e:
        _notify("error", f"저장하지 못해 변경 내용이 반영되지 않았습니다: {e}")
        return False
    st.session_state["items"] = items
    return True


def _require(item: dict | None) -> bool:
    if item is None:
        _notify("warning", "다른 곳에서 이미 삭제된 항목입니다.")
        return False
    return True


def add_item(name: str, quantity: int = 1) -> bool:
    """목록 맨 끝에 아이템을 추가하고 저장한다."""
    def change(items: list[dict]) -> bool:
        items.append(new_item(name[:MAX_NAME_LEN], clamp_quantity(quantity)))
        return True
    return _commit(change)


def update_item(item_id: str, name: str, quantity: int) -> bool:
    """아이템의 이름과 수량을 수정하고 저장한다."""
    def change(items: list[dict]) -> bool:
        item = find_item(items, item_id)
        if not _require(item):
            return False
        item.update(name=name[:MAX_NAME_LEN], quantity=clamp_quantity(quantity))
        return True
    return _commit(change)


def delete_item(item_id: str) -> bool:
    """아이템을 삭제하고 저장한다."""
    def change(items: list[dict]) -> bool:
        item = find_item(items, item_id)
        if not _require(item):
            return False
        items.remove(item)
        return True
    ok = _commit(change)
    if st.session_state["editing_id"] == item_id:
        st.session_state["editing_id"] = None
    return ok


def toggle_item(item_id: str, checked: bool) -> bool:
    """아이템의 구매 완료 여부를 checked로 설정하고 저장한다.

    반전이 아니라 화면에서 선택한 값으로 설정해야, 다른 탭에서 먼저 바뀐 경우에도
    사용자가 본 대로 저장된다.
    """
    def change(items: list[dict]) -> bool:
        item = find_item(items, item_id)
        if not _require(item):
            return False
        item["checked"] = checked
        return True
    return _commit(change)


def clear_checked() -> int:
    """구매 완료된 아이템을 모두 삭제하고 저장한다. 삭제한 개수를 반환한다."""
    removed = 0

    def change(items: list[dict]) -> bool:
        nonlocal removed
        remaining = [i for i in items if not i["checked"]]
        removed = len(items) - len(remaining)
        items[:] = remaining
        return removed > 0

    if not _commit(change):
        return 0
    if find_item(st.session_state["items"], st.session_state["editing_id"] or "") is None:
        st.session_state["editing_id"] = None
    return removed


# ---------------------------------------------------------------------------
# 콜백
# ---------------------------------------------------------------------------

def _escape_markdown(text: str) -> str:
    """st.toast 등 마크다운으로 해석되는 곳에 이름을 넣을 때 쓴다 ($는 수식으로 해석됨)."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|~<>$])", r"\\\1", text)


def _with_object_particle(name: str) -> str:
    """이름 뒤에 받침에 맞는 목적격 조사(을/를)를 붙인다."""
    last = name[-1]
    if "가" <= last <= "힣":
        return name + ("을" if (ord(last) - ord("가")) % 28 else "를")
    return name + "을(를)"


def _toast(name: str, verb: str) -> None:
    st.toast(f"{_escape_markdown(_with_object_particle(name))} {verb}")


def _on_add() -> None:
    name = st.session_state["add_name"].strip()
    if not name:
        _notify("warning", "아이템 이름을 입력해 주세요.")
        return
    if add_item(name, int(st.session_state["add_qty"])):
        _toast(name, "추가했어요")


def _on_toggle(item_id: str) -> None:
    toggle_item(item_id, st.session_state[f"chk_{item_id}"])


def _on_delete(item_id: str) -> None:
    item = find_item(st.session_state["items"], item_id)
    if delete_item(item_id) and item is not None:
        _toast(item["name"], "삭제했어요")


def _on_clear_checked() -> None:
    removed = clear_checked()
    if removed:
        st.toast(f"구매 완료 항목 {removed}개를 삭제했어요")


def _start_edit(item_id: str) -> None:
    previous = find_item(st.session_state["items"], st.session_state["editing_id"] or "")
    if previous is not None and previous["id"] != item_id:
        st.toast(f"{_escape_markdown(previous['name'])} 수정은 저장하지 않고 닫았어요")
    st.session_state["editing_id"] = item_id
    st.session_state["edit_error"] = None


def _on_save_edit(item_id: str) -> None:
    name = st.session_state[f"name_{item_id}"].strip()
    if not name:
        st.session_state["edit_error"] = "아이템 이름을 입력해 주세요."
        return
    update_item(item_id, name, int(st.session_state[f"qty_{item_id}"]))
    st.session_state["editing_id"] = None
    st.session_state["edit_error"] = None


def _cancel_edit() -> None:
    st.session_state["editing_id"] = None
    st.session_state["edit_error"] = None


def _change_page(delta: int) -> None:
    st.session_state["page"] += delta


# ---------------------------------------------------------------------------
# UI 계층
# ---------------------------------------------------------------------------

CSS = """
<style>
.stMainBlockContainer, .block-container { max-width: 720px; }
.item-name { font-size: 1.05rem; word-break: break-all; }
.item-name.checked { text-decoration: line-through; opacity: 0.5; }
.qty-badge {
    display: inline-block; margin-left: 0.4rem; padding: 0 0.5rem;
    border-radius: 999px; font-size: 0.8rem; white-space: nowrap;
    background: rgba(128, 128, 128, 0.18);
}
.item-name.checked + .qty-badge { opacity: 0.5; }
/* 좁은 화면에서도 컬럼 비율을 지키도록 Streamlit 기본 최소 너비(128px)를 해제 */
[data-testid="stColumn"] { min-width: 0 !important; }
/* 행마다 stLayoutWrapper로 감싸져 있으므로 두 번째 행부터 구분선을 긋는다 */
.st-key-item_list [data-testid="stLayoutWrapper"] + [data-testid="stLayoutWrapper"] {
    border-top: 1px solid rgba(128, 128, 128, 0.2); padding-top: 0.5rem;
}
[class*="st-key-editrow_"] {
    background: rgba(255, 75, 75, 0.08); border-radius: 0.5rem;
    border-left: 3px solid rgba(255, 75, 75, 0.7); padding: 0.5rem;
}
</style>
"""


def item_label_html(item: dict) -> str:
    """이름과 수량 배지 HTML. 마크다운을 거치지 않도록 st.html로 그린다."""
    cls = "item-name checked" if item["checked"] else "item-name"
    return (
        f'<span class="{cls}">{html.escape(item["name"])}</span>'
        f'<span class="qty-badge">x {item["quantity"]}</span>'
    )


def render_add_form() -> None:
    """아이템 추가 폼을 그린다."""
    with st.form("add_form", clear_on_submit=True, border=False):
        col_name, col_qty, col_btn = st.columns(
            [0.62, 0.2, 0.18], vertical_alignment="bottom", wrap=False
        )
        col_name.text_input(
            "아이템 이름", placeholder="예: 우유", max_chars=MAX_NAME_LEN, key="add_name"
        )
        col_qty.number_input(
            "수량", min_value=1, max_value=MAX_QUANTITY, value=1, step=1, key="add_qty"
        )
        col_btn.form_submit_button("추가", type="primary", width="stretch", on_click=_on_add)


def render_notices() -> None:
    """쌓인 알림을 한 번 보여주고 비운다."""
    for level, message in st.session_state["notices"]:
        (st.error if level == "error" else st.warning)(message)
    st.session_state["notices"] = []


def render_summary() -> None:
    """전체/남은 항목/구매 완료 개수와 진행률을 그린다."""
    items = st.session_state["items"]
    if not items:
        st.info("아직 담은 물건이 없어요. 위에서 추가해 보세요.")
        return
    done = sum(1 for i in items if i["checked"])
    col_total, col_left, col_done = st.columns(3, wrap=False)
    col_total.metric("전체", len(items), border=True)
    col_left.metric("남은 항목", len(items) - done, border=True)
    col_done.metric("구매 완료", done, border=True)
    st.progress(done / len(items), text=f"구매 진행률 {done * 100 // len(items)}%")


def render_item_row(item: dict) -> None:
    """아이템 한 행(보기 모드 또는 편집 모드)을 그린다."""
    item_id = item["id"]

    if st.session_state["editing_id"] == item_id:
        with st.container(key=f"editrow_{item_id}"):
            with st.form(f"editform_{item_id}", border=False):
                col_name, col_qty = st.columns([0.72, 0.28], wrap=False)
                col_name.text_input(
                    "이름", value=item["name"], max_chars=MAX_NAME_LEN,
                    key=f"name_{item_id}", label_visibility="collapsed",
                )
                col_qty.number_input(
                    "수량", min_value=1, max_value=MAX_QUANTITY, value=item["quantity"],
                    step=1, key=f"qty_{item_id}", label_visibility="collapsed",
                )
                with st.container(horizontal=True, horizontal_alignment="right"):
                    st.form_submit_button(
                        "저장", key=f"save_{item_id}", type="primary",
                        on_click=_on_save_edit, args=(item_id,),
                    )
                    st.form_submit_button(
                        "취소", key=f"cancel_{item_id}", on_click=_cancel_edit
                    )
                if st.session_state["edit_error"]:
                    st.warning(st.session_state["edit_error"])
        return

    # 파일에서 다시 읽은 값이 화면에 반영되도록 위젯 상태를 먼저 맞춘다(value 인자 대신).
    st.session_state[f"chk_{item_id}"] = item["checked"]
    with st.container(key=f"row_{item_id}"):
        col_chk, col_name, col_edit, col_del = st.columns(
            [0.08, 0.72, 0.1, 0.1], vertical_alignment="center", wrap=False
        )
        col_chk.checkbox(
            "구매 완료", key=f"chk_{item_id}",
            on_change=_on_toggle, args=(item_id,), label_visibility="collapsed",
        )
        col_name.html(item_label_html(item))
        col_edit.button(
            "✏️", key=f"edit_{item_id}", help="수정", type="tertiary",
            on_click=_start_edit, args=(item_id,),
        )
        col_del.button(
            "🗑️", key=f"del_{item_id}", help="삭제", type="tertiary",
            on_click=_on_delete, args=(item_id,),
        )


def render_list() -> None:
    """아이템 목록을 미구매 → 구매 완료 순으로, PAGE_SIZE개씩 나눠 그린다."""
    items = st.session_state["items"]
    if not items:
        return
    ordered = sorted(items, key=lambda i: i["checked"])  # 안정 정렬이라 추가 순서 유지
    pages = math.ceil(len(ordered) / PAGE_SIZE)
    page = min(max(st.session_state["page"], 0), pages - 1)
    st.session_state["page"] = page

    with st.container(border=True, key="item_list"):
        for item in ordered[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
            render_item_row(item)

    if pages > 1:
        with st.container(
            horizontal=True, horizontal_alignment="center", vertical_alignment="center"
        ):
            st.button("이전", key="prev_page", disabled=page == 0,
                      on_click=_change_page, args=(-1,))
            st.caption(f"{page + 1} / {pages} 페이지")
            st.button("다음", key="next_page", disabled=page == pages - 1,
                      on_click=_change_page, args=(1,))

    with st.container(horizontal=True, horizontal_alignment="right"):
        st.button(
            "구매 완료 항목 모두 삭제",
            key="clear_checked",
            on_click=_on_clear_checked,
            disabled=not any(i["checked"] for i in items),
        )


def main() -> None:
    """페이지를 설정하고, 파일과 상태를 맞춘 뒤 화면을 그린다."""
    st.set_page_config(page_title="쇼핑 리스트", page_icon="🛒")
    init_state()
    sync_items()
    st.markdown(CSS, unsafe_allow_html=True)
    st.title("🛒 쇼핑 리스트")
    st.caption("살 물건을 적어 두고, 장을 보면서 하나씩 체크하세요.")
    render_add_form()
    render_notices()
    render_summary()
    render_list()


if __name__ == "__main__":
    main()
