"""Offline tests for Camfrog 8.x control detection and the CEF chat reader."""
import camfrog_auto as ca


def row(cls, ctype="Pane", visible=True, left=0, top=0, w=100, h=20, parent="", enabled=True, name=""):
    return {"class_name": cls, "control_type": ctype, "name": name, "auto_id": "", "visible": visible,
            "enabled": enabled, "left": left, "top": top, "w": w, "h": h, "parent_class": parent}


def room_rows():
    return [
        row("Static", "Text", top=5),
        row("ComboBox", "ComboBox", top=10, w=200),
        row("Edit", "Edit", top=12, w=180, parent="ComboBox"),
        row("RICHEDIT50W", "Edit", top=300, w=500),               # idx 0: hidden other room's input
        row("RICHEDIT50W", "Edit", top=600, w=500, visible=False),
        row("Chrome_RenderWidgetHostHWND", "Document", top=50, w=600, h=400),
        row("Chrome_RenderWidgetHostHWND", "Document", top=50, w=40, h=40),
        row("RICHEDIT50W", "Edit", top=560, w=520),               # bottom-most visible = input
    ]


def test_detect_picks_expected_controls():
    rows = room_rows()
    got = {k: v[0] for k, v in ca.detect_camfrog(rows).items()}
    assert got["autoreply.input"] == {"class_name": "RICHEDIT50W", "index": 2}
    assert got["autoreply.history"] == {"control_type": "Document", "index": 0}
    assert got["status.edit"] == {"class_name": "Edit", "index": 0}


def test_detect_index_matches_find_order():
    rows = room_rows()
    sel = ca.detect_camfrog(rows)["autoreply.input"][0]
    same = [r for r in rows if r["class_name"] == sel["class_name"]]
    assert same[sel["index"]]["top"] == 560


def test_detect_falls_back_without_document_or_inner_edit():
    rows = [row("ComboBox", "ComboBox", top=5), row("RICHEDIT50W", top=500), row("RICHEDIT50W", top=100, w=600, h=300)]
    got = ca.detect_camfrog(rows)
    assert got["autoreply.history"][0] == {"class_name": "RICHEDIT50W", "index": 1}
    assert got["status.edit"][0]["class_name"] == "ComboBox"


def test_detect_nothing():
    assert ca.detect_camfrog([row("Static", "Text")]) == {}


def test_report_has_no_text_rows(tmp_path):
    rows = room_rows() + [row("Chrome_Text", "Text", name="secret chat line")]
    p = tmp_path / "r.txt"
    ca.write_detect_report(rows, p)
    assert "secret chat line" not in p.read_text(encoding="utf-8")


def test_defaults_validate_and_dont_clash():
    cfg = ca.deep_merge(ca.DEFAULTS, {"status": {"messages": ["hi"]}})
    errs, _ = ca.validate(cfg)
    assert not errs
    assert ca.selector_clashes(cfg) == []


def test_cli_has_detect():
    assert ca.build_parser().parse_args(["detect", "--apply"]).apply is True


def test_dump_controls_without_print_control_identifiers(tmp_path):
    class R:
        left, top = 1, 2
        def width(self): return 30
        def height(self): return 10
    class C:
        def __init__(self, cls, kids=()):
            self.element_info = type("E", (), {"class_name": cls, "control_type": "Pane", "name": "n", "automation_id": "7"})()
            self._k = list(kids)
        def rectangle(self): return R()
        def children(self): return self._k
    root = C("Root", [C("Kid", [C("Leaf")])])
    p = tmp_path / "c.txt"
    with p.open("w", encoding="utf-8") as f:
        ca.dump_controls(root, f)
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3 and lines[2].startswith("    Leaf |")


def test_detect_real_camfrog_main_window():
    """Rows taken from a real Camfrog 8.5 buddy-list dump (user-supplied controls.txt)."""
    rows = [row("CButtonTS", "Button", left=681, top=116, w=32, h=32, parent="#32770"),
            row("CComboBoxTS", "ComboBox", left=692, top=225, w=276, h=19, parent="#32770"),
            row("Edit", "Edit", left=695, top=228, w=253, h=13, parent="CComboBoxTS")]
    rows[1]["auto_id"] = "1436"
    rows[2]["auto_id"] = "1001"
    got = ca.detect_camfrog(rows)
    assert got["status.edit"][0] == {"class_name": "Edit", "auto_id": "1001", "index": 0}
    assert "autoreply.input" not in got  # no chat room in this window


def test_dump_redacts_tree_item_names(tmp_path):
    class R:
        left = top = 0
        def width(self): return 1
        def height(self): return 1
    class C:
        def __init__(self, ct, nm, kids=()):
            self.element_info = type("E", (), {"class_name": "x", "control_type": ct, "name": nm, "automation_id": ""})()
            self._k = list(kids)
        def rectangle(self): return R()
        def children(self): return self._k
    p = tmp_path / "d.txt"
    with p.open("w", encoding="utf-8") as f:
        ca.dump_controls(C("Tree", "", [C("TreeItem", "SecretNick")]), f)
    assert "SecretNick" not in p.read_text(encoding="utf-8")


def test_parse_web_log_real_layout():
    items = [
        {"type": "Text", "name": "Today"},
        {"type": "Image"}, {"type": "Hyperlink", "name": "Bob"}, {"type": "Text", "name": "Bob"},
        {"type": "Text", "name": "21:04"}, {"type": "Text", "name": "hello Seaza"},
        {"type": "Image"}, {"type": "Hyperlink", "name": ""}, {"type": "Text", "name": "Ann"},
        {"type": "Text", "name": "9:05 PM"}, {"type": "Text", "name": "สวัสดี"}, {"type": "Text", "name": "ทุกคน"},
        {"type": "Image"}, {"type": "Text", "name": "Joe"}, {"type": "Text", "name": "has joined"},  # no link: notice
    ]
    assert ca.parse_web_log(items) == ["Bob: hello Seaza", "Ann: สวัสดี ทุกคน"]


def test_parse_web_log_partial_head_is_ignored():
    assert ca.parse_web_log([{"type": "Text", "name": "tail of old msg"}]) == []


def test_parse_web_dom_rows_current_camfrog_im_shape():
    rows = [
        {"class_name": "msg-user animated-message", "children": [
            {"class_name": "bubble-arrow", "text": ""},
            {"class_name": "nickname-field", "text": "zdevz"},
            {"class_name": "msg-time", "text": "10/11/2026 08:24"},
            {"class_name": "msgid", "text": "hello\nSeaza"},
        ]},
        {"class_name": "msg-user animated-message", "children": [
            {"class_name": "nickname-field", "text": "Seaza"},
            {"class_name": "msg-time", "text": "10/11/2026 08:25"},
            {"class_name": "msgid", "text": "Thanks"},
        ]},
        {"class_name": "separator-new-text", "children": [
            {"class_name": "nickname-field", "text": "zdevz"},
            {"class_name": "msgid", "text": "not a message row"},
        ]},
        {"class_name": "msg-user animated-message", "children": [
            {"class_name": "nickname-field", "text": ""},
            {"class_name": "msgid", "text": "missing sender"},
        ]},
    ]

    assert ca.parse_web_dom_rows(rows) == ["zdevz: hello Seaza", "Seaza: Thanks"]
    assert ca.parse_web_dom_rows(rows, tail=1) == ["Seaza: Thanks"]


def real_room_rows():
    host = lambda top, h, left=489, w=582: row("AtlAxWinLic140", "Pane", left=left, top=top, w=w, h=h, parent="CSplitterCtrlsTS")
    ie = lambda nm, top, h, left=489, w=582: row("Internet Explorer_Server", "Pane", name=nm, left=left, top=top, w=w, h=h, parent="Shell DocObject View")
    rows = [host(181, 413), ie("camfrog://wb-log-room-data.inside/", 181, 413),
            host(640, 35, 604, 609), ie("camfrog://wb-edit-data.inside/", 640, 35, 604, 609),
            row("CEditTS", "Edit", top=90, parent="CSplitterCtrlsTS")]
    for r in rows:
        if r["class_name"] == "AtlAxWinLic140":
            r["auto_id"] = "1002"
    return rows


def test_detect_real_room_window():
    got = {k: v[0] for k, v in ca.detect_camfrog(real_room_rows()).items()}
    assert got["autoreply.history"] == {"class_name": "AtlAxWinLic140", "auto_id": "1002", "index": 0}
    assert got["autoreply.input"] == {"class_name": "AtlAxWinLic140", "auto_id": "1002", "index": 1}


def test_detect_ignores_im_window():
    rows = real_room_rows()
    for r in rows:
        r["name"] = r["name"].replace("wb-log-room-data", "wb-log-im-data")
    assert "autoreply.history" not in ca.detect_camfrog(rows)


def test_escape_keys():
    assert ca._escape_keys("a+b (x)") == "a{+}b {(}x{)}"


def test_log_kind_validation():
    cfg = ca.deep_merge(ca.DEFAULTS, {"status": {"messages": ["hi"]}, "autoreply": {"log_kind": "x"}})
    assert any("log_kind" in e for e in ca.validate(cfg)[0])


def test_press_button_falls_back_to_click():
    calls = []
    class B:
        def invoke(self): raise RuntimeError("NoPatternInterfaceError")
        def click_input(self): calls.append("click")
    ca.press_button(B())
    assert calls == ["click"]
