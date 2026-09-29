"""쇼핑 리스트 앱 테스트.

실행: python -m pytest
"""

import json
import os
import stat
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import app

APP_FILE = str(Path(__file__).parent / "app.py")


# ---------------------------------------------------------------------------
# 데이터 계층 (순수 함수)
# ---------------------------------------------------------------------------

def test_new_item_schema():
    item = app.new_item("우유", 2)
    assert set(item) == {"id", "name", "quantity", "checked", "created_at"}
    assert item["name"] == "우유"
    assert item["quantity"] == 2
    assert item["checked"] is False
    assert app.new_item("우유")["id"] != item["id"]


def test_save_and_load_roundtrip_keeps_korean(tmp_path):
    path = tmp_path / "list.json"
    items = [app.new_item("우유", 2), app.new_item("🍎 사과")]
    app.save_items(items, path)
    assert app.load_items(path) == items
    assert "우유" in path.read_text(encoding="utf-8")


def test_save_leaves_no_temp_files(tmp_path):
    path = tmp_path / "list.json"
    app.save_items([app.new_item("우유")], path)
    app.save_items([app.new_item("빵")], path)
    assert [p.name for p in tmp_path.iterdir()] == ["list.json"]


def test_load_missing_file_returns_empty(tmp_path):
    errors = []
    assert app.load_items(tmp_path / "none.json", errors) == []
    assert errors == []


@pytest.mark.parametrize("content", ["{broken", '{"a": 1}', ""])
def test_corrupted_file_is_backed_up_and_reset(tmp_path, content):
    path = tmp_path / "list.json"
    path.write_text(content, encoding="utf-8")
    errors = []
    assert app.load_items(path, errors) == []
    assert len(errors) == 1 and "백업" in errors[0]
    backups = list(tmp_path.glob("list.*.bak.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == content
    # 원본은 빈 목록으로 초기화돼, 다시 읽어도 백업이 또 생기지 않는다
    assert app.load_items(path, errors) == []
    assert len(list(tmp_path.glob("list.*.bak.json"))) == 1


@pytest.mark.parametrize(
    "value, expected",
    [(2, 2), ("3", 3), (2.7, 2), (0, 1), (-5, 1), (10**15, 999), ("abc", 1),
     (None, 1), (True, 1), (float("inf"), 1), (float("nan"), 1)],
)
def test_clamp_quantity(value, expected):
    assert app.clamp_quantity(value) == expected


def test_normalize_fixes_and_skips_bad_items():
    data = [
        {"id": "a", "name": "우유", "quantity": 2, "checked": True, "created_at": "t"},  # 정상
        {"id": "b", "name": "  빵  "},                                                  # 필드 누락
        {"id": "a", "name": "계란", "quantity": "abc", "checked": "yes"},              # id 중복, 타입 오류
        {"name": "가" * 80, "quantity": 0},                                             # 긴 이름, 수량 0
        {"id": "c", "quantity": 1},                                                     # 이름 없음 → 건너뜀
        {"id": "d", "name": "   "},                                                     # 빈 이름 → 건너뜀
        "우유",                                                                          # dict 아님 → 건너뜀
    ]
    errors = []
    items = app.normalize_items(data, errors)
    assert [i["name"] for i in items] == ["우유", "빵", "계란", "가" * app.MAX_NAME_LEN]
    assert items[0] == data[0]
    assert items[1]["quantity"] == 1 and items[1]["checked"] is False
    assert items[2]["id"] != "a" and items[2]["quantity"] == 1 and items[2]["checked"] is False
    assert items[3]["quantity"] == 1
    assert len({i["id"] for i in items}) == 4
    assert errors == ["형식이 잘못된 항목 3개를 건너뛰었습니다.", "항목 3개의 잘못된 값을 고쳤습니다."]


def test_load_writes_back_normalized_items(tmp_path):
    path = tmp_path / "list.json"
    path.write_text(json.dumps([{"name": "우유"}]), encoding="utf-8")
    errors = []
    items = app.load_items(path, errors)
    assert errors
    assert json.loads(path.read_text(encoding="utf-8")) == items
    errors = []
    app.load_items(path, errors)
    assert errors == []  # 한 번 고친 뒤에는 다시 알리지 않는다


# ---------------------------------------------------------------------------
# 앱 흐름 (AppTest)
# ---------------------------------------------------------------------------

@pytest.fixture
def data_file(tmp_path, monkeypatch):
    path = tmp_path / "shopping_list.json"
    monkeypatch.setenv("SHOPPING_LIST_FILE", str(path))
    return path


def run_app() -> AppTest:
    return AppTest.from_file(APP_FILE).run()


def add(at: AppTest, name: str, quantity: int = 1) -> AppTest:
    at.text_input(key="add_name").input(name)
    at.number_input(key="add_qty").set_value(quantity)
    next(b for b in at.button if b.label == "추가").click()
    return at.run()


def summary(at: AppTest) -> dict[str, str]:
    return {m.label: m.value for m in at.metric}


def labels(at: AppTest) -> list[str]:
    return [e.proto.body for e in at.get("html")]


def saved(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, items: list) -> None:
    path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def test_empty_list_shows_info(data_file):
    at = run_app()
    assert not at.exception
    assert at.info[0].value == "아직 담은 물건이 없어요. 위에서 추가해 보세요."
    assert len(at.metric) == 0


def test_add_item(data_file):
    at = add(run_app(), "우유", 2)
    items = at.session_state["items"]
    assert [(i["name"], i["quantity"], i["checked"]) for i in items] == [("우유", 2, False)]
    assert saved(data_file) == items
    assert labels(at) == ['<span class="item-name">우유</span><span class="qty-badge">x 2</span>']
    assert summary(at) == {"전체": "1", "남은 항목": "1", "구매 완료": "0"}
    assert at.toast[0].value == "우유를 추가했어요"


def test_add_appends_to_end_and_strips_name(data_file):
    at = add(add(run_app(), "우유"), "  빵  ")
    assert [i["name"] for i in at.session_state["items"]] == ["우유", "빵"]


def test_add_blank_name_warns(data_file):
    at = add(run_app(), "   ")
    assert at.session_state["items"] == []
    assert at.warning[0].value == "아이템 이름을 입력해 주세요."
    assert not data_file.exists()
    at.run()
    assert len(at.warning) == 0  # 알림은 한 번만 보인다


def test_input_limits(data_file):
    at = run_app()
    assert at.text_input(key="add_name").max_chars == app.MAX_NAME_LEN
    assert at.number_input(key="add_qty").proto.max == app.MAX_QUANTITY


def test_edit_save(data_file):
    at = add(run_app(), "우유")
    item_id = at.session_state["items"][0]["id"]
    at.button(key=f"edit_{item_id}").click().run()
    at.text_input(key=f"name_{item_id}").input("두유")
    at.number_input(key=f"qty_{item_id}").set_value(3)
    at.button(key=f"save_{item_id}").click().run()
    assert at.session_state["editing_id"] is None
    assert saved(data_file)[0]["name"] == "두유"
    assert saved(data_file)[0]["quantity"] == 3


def test_edit_cancel_keeps_original(data_file):
    at = add(run_app(), "우유")
    item_id = at.session_state["items"][0]["id"]
    at.button(key=f"edit_{item_id}").click().run()
    at.text_input(key=f"name_{item_id}").input("두유")
    at.button(key=f"cancel_{item_id}").click().run()
    assert at.session_state["editing_id"] is None
    assert at.session_state["items"][0]["name"] == "우유"


def test_edit_blank_name_warns_and_stays_in_edit_mode(data_file):
    at = add(run_app(), "우유")
    item_id = at.session_state["items"][0]["id"]
    at.button(key=f"edit_{item_id}").click().run()
    at.text_input(key=f"name_{item_id}").input("  ")
    at.button(key=f"save_{item_id}").click().run()
    assert at.session_state["editing_id"] == item_id
    assert at.session_state["items"][0]["name"] == "우유"
    assert at.warning[0].value == "아이템 이름을 입력해 주세요."


def test_switching_edit_row_tells_user(data_file):
    at = add(add(run_app(), "우유"), "빵")
    a, b = (i["id"] for i in at.session_state["items"])
    at.button(key=f"edit_{a}").click().run()
    at.button(key=f"edit_{b}").click().run()
    assert at.session_state["editing_id"] == b
    assert len(at.text_input) == 2  # 추가 폼 + 편집 중인 한 행
    assert at.toast[0].value == "우유 수정은 저장하지 않고 닫았어요"


def test_delete_item(data_file):
    at = add(add(add(run_app(), "우유"), "빵"), "계란")
    milk, bread, egg = (i["id"] for i in at.session_state["items"])
    at.checkbox(key=f"chk_{egg}").check().run()
    at.button(key=f"del_{bread}").click().run()
    assert [i["name"] for i in saved(data_file)] == ["우유", "계란"]
    # 가운데 아이템을 지워도 다른 행의 체크 상태가 밀리지 않는다
    assert at.checkbox(key=f"chk_{milk}").value is False
    assert at.checkbox(key=f"chk_{egg}").value is True
    assert at.toast[0].value == "빵을 삭제했어요"


def test_deleting_other_item_keeps_edit_mode(data_file):
    at = add(add(run_app(), "우유"), "빵")
    milk, bread = (i["id"] for i in at.session_state["items"])
    at.button(key=f"edit_{milk}").click().run()
    at.button(key=f"del_{bread}").click().run()
    assert at.session_state["editing_id"] == milk


def test_clear_checked_removing_edited_item_exits_edit_mode(data_file):
    at = add(add(run_app(), "우유"), "빵")
    milk = at.session_state["items"][0]["id"]
    at.checkbox(key=f"chk_{milk}").check().run()
    at.button(key=f"edit_{milk}").click().run()
    at.button(key="clear_checked").click().run()
    assert at.session_state["editing_id"] is None
    assert [i["name"] for i in at.session_state["items"]] == ["빵"]


def test_toggle_check(data_file):
    at = add(run_app(), "우유")
    item_id = at.session_state["items"][0]["id"]
    at.checkbox(key=f"chk_{item_id}").check().run()
    assert saved(data_file)[0]["checked"] is True
    assert '<span class="item-name checked">우유</span>' in labels(at)[0]
    assert summary(at) == {"전체": "1", "남은 항목": "0", "구매 완료": "1"}
    at.checkbox(key=f"chk_{item_id}").uncheck().run()
    assert saved(data_file)[0]["checked"] is False


def test_clear_checked(data_file):
    at = add(add(run_app(), "우유"), "빵")
    assert at.button(key="clear_checked").disabled
    milk = at.session_state["items"][0]["id"]
    at.checkbox(key=f"chk_{milk}").check().run()
    assert not at.button(key="clear_checked").disabled
    at.button(key="clear_checked").click().run()
    assert [i["name"] for i in saved(data_file)] == ["빵"]
    assert at.toast[0].value == "구매 완료 항목 1개를 삭제했어요"


def test_name_is_not_interpreted_as_markdown_or_html(data_file):
    at = add(run_app(), "사과 $3 배 $5 <b>굵게</b> :tomato:")
    assert "사과 $3 배 $5 &lt;b&gt;굵게&lt;/b&gt; :tomato:" in labels(at)[0]
    assert at.toast[0].value == r"사과 \$3 배 \$5 \<b\>굵게\</b\> :tomato:을\(를\) 추가했어요"


def test_checked_items_sorted_to_bottom(data_file):
    at = add(add(add(run_app(), "우유"), "빵"), "계란")
    milk, bread, egg = (i["id"] for i in at.session_state["items"])
    at.checkbox(key=f"chk_{milk}").check().run()
    assert [c.key for c in at.checkbox] == [f"chk_{bread}", f"chk_{egg}", f"chk_{milk}"]
    # 저장 순서는 추가한 순서 그대로다
    assert [i["name"] for i in saved(data_file)] == ["우유", "빵", "계란"]
    assert at.checkbox(key=f"chk_{milk}").value is True
    assert at.checkbox(key=f"chk_{bread}").value is False


def test_progress_bar(data_file):
    at = add(add(run_app(), "우유"), "빵")
    at.checkbox(key=f"chk_{at.session_state['items'][0]['id']}").check().run()
    assert at.get("progress")[0].proto.text == "구매 진행률 50%"


def test_state_persists_across_sessions(data_file):
    at = add(run_app(), "우유")
    item_id = at.session_state["items"][0]["id"]
    at.checkbox(key=f"chk_{item_id}").check().run()
    reopened = run_app()
    assert reopened.session_state["items"] == at.session_state["items"]
    assert reopened.checkbox(key=f"chk_{item_id}").value is True


# --- 4단계 검토에서 찾은 문제들의 회귀 테스트 ---

def test_bad_fields_in_file_do_not_crash(data_file):  # 1
    write(data_file, [{"id": "x1", "name": "우유"}, {"name": "빵", "quantity": 0}, "계란"])
    at = run_app()
    assert not at.exception
    assert [i["name"] for i in at.session_state["items"]] == ["우유", "빵"]
    assert any("건너뛰었습니다" in w.value for w in at.warning)
    at.button(key="edit_x1").click().run()  # 수정 모드도 열린다
    assert not at.exception


def test_corrupted_file_is_backed_up_in_app(data_file):  # 2, 9
    data_file.write_text('[{"id":"x1","name":"우유"}, 깨짐', encoding="utf-8")
    at = run_app()
    assert not at.exception
    assert "백업" in at.warning[0].value
    at = add(at, "빵")
    assert [i["name"] for i in saved(data_file)] == ["빵"]
    backup = next(data_file.parent.glob("shopping_list.*.bak.json"))
    assert "우유" in backup.read_text(encoding="utf-8")
    assert len(at.warning) == 0  # 경고는 한 번만


def test_two_sessions_do_not_overwrite_each_other(data_file):  # 3
    tab_a, tab_b = run_app(), run_app()
    add(tab_a, "우유")
    tab_b = add(tab_b, "빵")
    assert [i["name"] for i in saved(data_file)] == ["우유", "빵"]
    assert [i["name"] for i in tab_b.session_state["items"]] == ["우유", "빵"]


def test_other_session_changes_show_up(data_file):  # 3
    tab_a = add(run_app(), "우유")
    milk = tab_a.session_state["items"][0]["id"]
    tab_b = run_app()
    tab_b.button(key=f"edit_{milk}").click().run()
    tab_a.checkbox(key=f"chk_{milk}").check().run()
    tab_a.button(key="clear_checked").click().run()
    tab_b.run()
    assert tab_b.session_state["items"] == []
    assert tab_b.session_state["editing_id"] is None


def test_toggle_uses_screen_value_not_flip(data_file):  # 3
    tab_a = add(run_app(), "우유")
    milk = tab_a.session_state["items"][0]["id"]
    tab_b = run_app()
    tab_a.checkbox(key=f"chk_{milk}").check().run()
    tab_b.checkbox(key=f"chk_{milk}").check().run()  # B 화면에서는 아직 체크 안 된 상태에서 체크
    assert saved(data_file)[0]["checked"] is True


def test_deleted_elsewhere_warns(data_file):  # 3
    tab_a = add(run_app(), "우유")
    milk = tab_a.session_state["items"][0]["id"]
    tab_b = run_app()
    tab_a.button(key=f"del_{milk}").click().run()
    tab_b.button(key=f"del_{milk}").click().run()
    assert not tab_b.exception
    assert tab_b.warning[0].value == "다른 곳에서 이미 삭제된 항목입니다."


@pytest.mark.skipif(os.name != "nt", reason="읽기 전용 속성으로 쓰기 실패를 만드는 방법은 Windows 기준")
def test_save_failure_shows_error_and_keeps_state(data_file):  # 4
    at = add(run_app(), "우유")
    os.chmod(data_file, stat.S_IREAD)
    try:
        at = add(at, "빵")
    finally:
        os.chmod(data_file, stat.S_IWRITE | stat.S_IREAD)
    assert not at.exception
    assert "저장하지 못해" in at.error[0].value
    assert [i["name"] for i in at.session_state["items"]] == ["우유"]
    assert [i["name"] for i in saved(data_file)] == ["우유"]
    assert len(at.toast) == 0


def test_pagination(data_file):  # 6
    write(data_file, [app.new_item(f"아이템{n}") for n in range(app.PAGE_SIZE + 10)])
    at = run_app()
    assert len(at.checkbox) == app.PAGE_SIZE
    assert at.button(key="prev_page").disabled
    at.button(key="next_page").click().run()
    assert len(at.checkbox) == 10
    assert at.button(key="next_page").disabled
    assert summary(at)["전체"] == str(app.PAGE_SIZE + 10)


def test_no_pagination_for_small_list(data_file):  # 6
    at = add(run_app(), "우유")
    with pytest.raises(KeyError):
        at.button(key="next_page")


LOCAL_CONFIG = Path(__file__).parent / ".streamlit" / "config.toml"


@pytest.mark.skipif(not LOCAL_CONFIG.exists(), reason="로컬 전용 설정 파일(.gitignore 대상)이 없음")
def test_server_listens_on_localhost_only():  # 7
    import tomllib

    raw = LOCAL_CONFIG.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")  # BOM이 있으면 Streamlit이 설정 파일을 읽지 못한다
    assert tomllib.loads(raw.decode("utf-8"))["server"]["address"] == "localhost"
