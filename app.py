# -*- coding: utf-8 -*-
"""
n8n Workflow Viewer
--------------------
یک برنامه‌ی Streamlit که فایل JSON خروجی n8n را می‌خواند و آن را روی یک
بوم (canvas) سفارشی، با ظاهری شبیه به خود ویرایشگر n8n، نمایش می‌دهد.
فقط برای «دیدن» و «بررسی» workflow است — هیچ اجرایی روی workflow انجام نمی‌شود.

اجرا:
    streamlit run app.py
"""

import html
import json
import re
import uuid
from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components


# ----------------------------------------------------------------------------
# تنظیمات کلی صفحه و استایل پوسته‌ی Streamlit (نه بوم داخلی)
# ----------------------------------------------------------------------------
st.set_page_config(
    page_title="n8n Workflow Viewer",
    page_icon="🧩",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    @import url('https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css');
    html, body, [class*="css"] { font-family: 'Vazirmatn', sans-serif !important; }
    h1, h2, h3, h4, h5, h6, p, label, .stMarkdown, .stRadio, .stSelectbox, .stTextInput {
        direction: rtl; text-align: right;
    }
    .stCodeBlock, code, pre { direction: ltr !important; text-align: left !important; }
    section[data-testid="stSidebar"] { direction: rtl; }
    .metric-card {
        background: rgba(255,255,255,0.04);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 10px; padding: 14px 18px; text-align: center;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ----------------------------------------------------------------------------
# دسته‌بندی نوع نودها → رنگ / آیکون / برچسب فارسی
# ----------------------------------------------------------------------------
# (regex روی type نود, برچسب فارسی, رنگ, ایموجی‌آیکون)
CATEGORY_RULES = [
    (r"stickyNote", "یادداشت", "#f5d76e", "🗒️"),
    (r"webhook|trigger|cron|schedule|formTrigger|errorTrigger", "تریگر", "#3ba55d", "⚡"),
    (r"\bif\b|switch|filter|compare", "منطق/شرط", "#f0a020", "🔀"),
    (r"merge|splitInBatches|itemLists|aggregate|sort", "ترکیب/داده", "#9b59b6", "🔗"),
    (r"function|code\b|executeWorkflow", "کد/تابع", "#7c5cff", "💻"),
    (r"httpRequest|graphql|\brequest\b", "درخواست HTTP", "#2e86de", "🌐"),
    (r"\bset\b|edit", "تنظیم داده", "#17a2b8", "📝"),
    (r"noOp", "بدون‌عملیات", "#6c757d", "⏺️"),
    (r"email|slack|telegram|gmail|discord|whatsapp", "پیام‌رسانی", "#e84393", "✉️"),
    (r"postgres|mysql|mongodb|redis|database|sheet", "دیتابیس/شیت", "#c9a227", "🗄️"),
]
DEFAULT_CATEGORY = ("سایر", "#8898aa", "🧩")

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|secret|token|apikey|api_key|credential|auth|key$)", re.IGNORECASE
)
# نام‌های متداول هدر/فیلد که نشان‌دهنده‌ی محتوای حساس در الگوی {"name": ..., "value": ...} هستند
# (این الگو در پارامترهای HTTP Request نود n8n برای هدرها/کوئری‌پارامترها رایج است)
SENSITIVE_NAME_VALUE_PATTERN = re.compile(
    r"(authorization|api[-_]?key|x-api-key|bearer|access[-_]?token|secret|password|cookie)",
    re.IGNORECASE,
)
# مقادیری که خودشان شبیه توکن/بیرر هستند (حتی اگر نام فیلد چیز خاصی نباشد)
SENSITIVE_VALUE_LOOK_PATTERN = re.compile(r"^bearer\s+\S+|^[A-Za-z0-9\-_.]{24,}$")

TRIGGER_PATTERN = re.compile(r"trigger|webhook|cron|schedule", re.IGNORECASE)
STICKY_PATTERN = re.compile(r"stickyNote", re.IGNORECASE)


def categorize(node_type: str):
    for pattern, label, color, glyph in CATEGORY_RULES:
        if re.search(pattern, node_type, re.IGNORECASE):
            return label, color, glyph
    return DEFAULT_CATEGORY


def mask_sensitive(obj):
    """به‌صورت بازگشتی مقادیر حساس (پسورد، توکن، کلید، هدرهای Authorization و ...) را ماسک می‌کند."""
    if isinstance(obj, dict):
        # الگوی رایج n8n برای هدر/کوئری‌پارامتر: {"name": "Authorization", "value": "Bearer xyz"}
        name_val = obj.get("name")
        if (
            isinstance(name_val, str)
            and "value" in obj
            and SENSITIVE_NAME_VALUE_PATTERN.search(name_val)
        ):
            return {**obj, "value": "••••••••"}

        return {
            k: (
                "••••••••"
                if SENSITIVE_KEY_PATTERN.search(str(k))
                else (
                    "••••••••"
                    if k == "value" and isinstance(v, str) and SENSITIVE_VALUE_LOOK_PATTERN.match(v)
                    else mask_sensitive(v)
                )
            )
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [mask_sensitive(v) for v in obj]
    return obj


def parse_workflow(data: dict):
    """فایل JSON n8n را پارس کرده و لیست نودها + یال‌های اتصال را برمی‌گرداند."""
    raw_nodes = data.get("nodes", [])
    connections = data.get("connections", {})
    nodes_by_name = {n.get("name"): n for n in raw_nodes}
    nodes_by_id = {n.get("id"): n for n in raw_nodes if n.get("id")}

    edges = []
    for source_key, conn_types in connections.items():
        source_node = nodes_by_name.get(source_key) or nodes_by_id.get(source_key)
        source_name = source_node.get("name") if source_node else source_key
        if not isinstance(conn_types, dict):
            continue
        for conn_type, branches in conn_types.items():
            if not isinstance(branches, list):
                continue
            for branch_idx, branch in enumerate(branches):
                if not isinstance(branch, list):
                    continue
                for target in branch:
                    target_key = target.get("node")
                    target_node = nodes_by_name.get(target_key) or nodes_by_id.get(target_key)
                    target_name = target_node.get("name") if target_node else target_key
                    edges.append(
                        {
                            "source": source_name,
                            "target": target_name,
                            "type": conn_type,
                            "branch": branch_idx,
                        }
                    )
    return raw_nodes, edges


# ----------------------------------------------------------------------------
# نوار کناری: بارگذاری فایل
# ----------------------------------------------------------------------------
st.sidebar.title("🧩 n8n Workflow Viewer")
st.sidebar.caption("فقط نمایش — هیچ workflow ای اجرا نمی‌شود.")
uploaded = st.sidebar.file_uploader("فایل JSON ورک‌فلو n8n را انتخاب کنید", type=["json"])
st.sidebar.markdown("---")

if uploaded is None:
    st.title("🧩 n8n Workflow Viewer")
    st.info(
        "یک فایل JSON خروجی n8n (Export → Download) را از نوار کناری بارگذاری کنید "
        "تا نمایش گراف تعاملی شبیه به ویرایشگر n8n نمایش داده شود."
    )
    st.stop()

try:
    data = json.loads(uploaded.read().decode("utf-8"))
except Exception as e:
    st.error(f"خطا در خواندن فایل JSON: {e}")
    st.stop()

nodes_raw, edges_raw = parse_workflow(data)
if not nodes_raw:
    st.warning("در این فایل هیچ نودی پیدا نشد. مطمئن شوید فایل، خروجی صحیح یک workflow از n8n است.")
    st.stop()

workflow_name = data.get("name", uploaded.name)
is_active = data.get("active", None)

# ----------------------------------------------------------------------------
# هدر و خلاصه‌ی وضعیت workflow
# ----------------------------------------------------------------------------
st.title(f"🧩 {workflow_name}")
trigger_count = sum(1 for n in nodes_raw if TRIGGER_PATTERN.search(n.get("type", "")))

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown(f'<div class="metric-card"><h3>{len(nodes_raw)}</h3>تعداد نودها</div>', unsafe_allow_html=True)
with c2:
    st.markdown(f'<div class="metric-card"><h3>{len(edges_raw)}</h3>تعداد اتصالات</div>', unsafe_allow_html=True)
with c3:
    st.markdown(f'<div class="metric-card"><h3>{trigger_count}</h3>نود تریگر</div>', unsafe_allow_html=True)
with c4:
    status_label = "فعال ✅" if is_active else ("غیرفعال ⛔" if is_active is False else "نامشخص")
    st.markdown(f'<div class="metric-card"><h3>{status_label}</h3>وضعیت</div>', unsafe_allow_html=True)

st.markdown("")


# ----------------------------------------------------------------------------
# آماده‌سازی داده برای بوم (canvas) شبیه‌ساز n8n
# ----------------------------------------------------------------------------
NODE_SIZE = 76        # اندازه‌ی جعبه‌ی آیکون (مربع)
STICKY_DEFAULT_W = 240
STICKY_DEFAULT_H = 160

payload_nodes = []
name_to_id = {}

xs, ys = [], []
for n in nodes_raw:
    pos = n.get("position") or [0, 0]
    xs.append(pos[0])
    ys.append(pos[1])
min_x = min(xs) if xs else 0
min_y = min(ys) if ys else 0
PAD = 120

for n in nodes_raw:
    name = n.get("name", "بدون‌نام")
    ntype = n.get("type", "")
    label_fa, color, glyph = categorize(ntype)
    is_trigger = bool(TRIGGER_PATTERN.search(ntype)) and not STICKY_PATTERN.search(ntype)
    is_sticky = bool(STICKY_PATTERN.search(ntype))
    pos = n.get("position") or [0, 0]
    node_id = n.get("id") or str(uuid.uuid4())
    name_to_id[name] = node_id

    params = n.get("parameters", {}) or {}
    sticky_w = params.get("width", STICKY_DEFAULT_W)
    sticky_h = params.get("height", STICKY_DEFAULT_H)
    sticky_content = params.get("content", "")

    payload_nodes.append(
        {
            "id": node_id,
            "name": name,
            "type": ntype,
            "typeVersion": n.get("typeVersion", ""),
            "category": label_fa,
            "color": color,
            "glyph": glyph,
            "isTrigger": is_trigger,
            "isSticky": is_sticky,
            "disabled": bool(n.get("disabled")),
            "notes": n.get("notes", ""),
            "x": (pos[0] - min_x) + PAD,
            "y": (pos[1] - min_y) + PAD,
            "stickyW": sticky_w,
            "stickyH": sticky_h,
            "stickyContent": sticky_content,
            "parameters": mask_sensitive(params),
            "credentials": mask_sensitive(n.get("credentials", {})),
        }
    )

payload_edges = []
for e in edges_raw:
    sid = name_to_id.get(e["source"])
    tid = name_to_id.get(e["target"])
    if sid and tid:
        payload_edges.append({"source": sid, "target": tid, "type": e["type"], "branch": e["branch"]})

categories_present = sorted({n["category"] for n in payload_nodes})

max_x = max((n["x"] + (n["stickyW"] if n["isSticky"] else NODE_SIZE) for n in payload_nodes), default=800)
max_y = max((n["y"] + (n["stickyH"] if n["isSticky"] else NODE_SIZE) for n in payload_nodes), default=600)

data_json = json.dumps(
    {
        "nodes": payload_nodes,
        "edges": payload_edges,
        "categories": categories_present,
        "canvasW": max_x + PAD,
        "canvasH": max_y + PAD,
        "nodeSize": NODE_SIZE,
    },
    ensure_ascii=False,
).replace("</", "<\\/")

component_id = "n8nviewer_" + uuid.uuid4().hex[:8]


# ----------------------------------------------------------------------------
# HTML/CSS/JS بوم سفارشی شبیه‌ساز n8n
# ----------------------------------------------------------------------------
CANVAS_HEIGHT = 700

html_code = f"""
<div id="{component_id}" class="n8nv-root">
  <style>
    #{component_id} {{
      --bg: #f5f7fa;  /* تغییر از #1a1d23 به سفید/روشن */
      --grid-dot: #d0d7de;  /* تغییر از #2c3038 به خاکستری روشن */
      --panel-bg: #ffffff;  /* تغییر از #21252c به سفید */
      --panel-border: #d0d7de;  /* تغییر از #333844 به خاکستری روشن */
      --text-main: #24292f;  /* تغییر از #e6e8ec به تیره برای خوانایی */
      --text-dim: #57606a;  /* تغییر از #8b93a1 به خاکستری تیره‌تر */
      --edge-color: #8b949e;  /* تغییر از #57616f به خاکستری */
      --edge-hover: #d73a49;  /* قرمز برای hover */
      font-family: -apple-system, 'Segoe UI', Roboto, Arial, sans-serif;
      direction: ltr;
      position: relative;
      display: flex;
      width: 100%;
      height: {CANVAS_HEIGHT}px;
      border-radius: 12px;
      overflow: hidden;
      border: 1px solid var(--panel-border);
      background: var(--bg);
    }}
    #{component_id} .n8nv-toolbar {{
      position: absolute; top: 12px; left: 12px; z-index: 20;
      display: flex; gap: 8px; align-items: center;
      background: #ffffff;  /* تغییر از var(--panel-bg) به سفید */
      border: 1px solid #d0d7de;  /* تغییر از var(--panel-border) */
      border-radius: 10px; padding: 6px 10px; box-shadow: 0 2px 8px rgba(0,0,0,.08);
    }}
    #{component_id} .n8nv-toolbar input[type=text]::placeholder {{
      color: #8b949e;  /* رنگ placeholder به خاکستری */
    }}
    #{component_id} .n8nv-toolbar input[type=text] {{
      background: #ffffff;  /* تغییر از var(--panel-bg) به سفید */
      border: 1px solid #d0d7de;
    }}
    #{component_id} .n8nv-toolbar button {{
      background: #e8ecf0;  /* رنگ پس‌زمینه در حالت عادی - روشن */
      border: 1px solid #d0d7de;
      color: #24292f;  /* رنگ آیکون/متن - تیره */
      border-radius: 6px;
      width: 26px;
      height: 26px;
      cursor: pointer;
      font-size: 14px;
      transition: all 0.2s ease;
      display: flex;
      align-items: center;
      justify-content: center;
      line-height: 1;
    }}

    #{component_id} .n8nv-toolbar button:hover {{
      background: #d0d7de;  /* رنگ هنگام هاور - کمی تیره‌تر */
      border-color: #8b949e;
      transform: scale(1.05);
    }}

    #{component_id} .n8nv-toolbar button:active {{
      background: #b8c0c8;  /* رنگ هنگام کلیک */
      transform: scale(0.95);
    }}

    #{component_id} .n8nv-toolbar button:focus {{
      outline: 2px solid #8b949e;
      outline-offset: 1px;
    }}
    #{component_id} .n8nv-cat-panel {{
      position: absolute;
      top: 68px;  /* افزایش از 58px به 68px یا بیشتر */
      left: 12px;
      z-index: 20;
      background: #ffffff;  /* تغییر به سفید برای تم روشن */
      border: 1px solid #d0d7de;
      border-radius: 10px;
      padding: 8px 12px;
      box-shadow: 0 2px 10px rgba(0,0,0,.1);
      max-height: 260px;
      overflow-y: auto;
      font-size: 12px;
      color: #24292f;  /* رنگ متن تیره */
    }}
    #{component_id} .n8nv-viewport {{
      flex: 1; position: relative; overflow: hidden; cursor: grab;
      background-image: radial-gradient(var(--grid-dot) 1.2px, transparent 1.2px);
      background-size: 22px 22px;
    }}
    #{component_id} .n8nv-viewport.dragging {{ cursor: grabbing; }}
    #{component_id} .n8nv-world {{ position: absolute; top: 0; left: 0; transform-origin: 0 0; }}
    #{component_id} svg.n8nv-edges {{ position: absolute; top: 0; left: 0; overflow: visible; pointer-events: none; }}
    #{component_id} .n8nv-edge-path {{ fill: none; stroke: var(--edge-color); stroke-width: 2; transition: stroke .15s; }}
    #{component_id} .n8nv-edge-path.hi {{ stroke: var(--edge-hover); stroke-width: 2.5; }}

    #{component_id} .n8nv-node {{
      position: absolute; display: flex; flex-direction: column; align-items: center;
      width: {NODE_SIZE}px; cursor: pointer; user-select: none;
    }}
    #{component_id} .n8nv-node .box {{
      width: {NODE_SIZE}px; height: {NODE_SIZE}px;
      background: #ffffff; border: 2px solid #d0d7de; border-radius: 12px;
      display: flex; align-items: center; justify-content: center; font-size: 30px;
      box-shadow: 0 2px 6px rgba(0,0,0,.1); position: relative; transition: border-color .12s, box-shadow .12s;
    }}
    #{component_id} .n8nv-node.trigger .box {{
      border-radius: 34px 12px 12px 34px;
      clip-path: polygon(28% 0%, 100% 0%, 100% 100%, 28% 100%, 0% 50%);
    }}
    #{component_id} .n8nv-node .box .accent {{
      position: absolute; inset: 0; border-radius: inherit; opacity: .22;
    }}
    #{component_id} .n8nv-node.selected .box {{ border-color: #ff6d5a; box-shadow: 0 0 0 3px rgba(255,109,90,.25); }}
    #{component_id} .n8nv-node.disabled .box {{ opacity: .35; }}
    #{component_id} .n8nv-node.dimmed {{ opacity: .15; }}
    #{component_id} .n8nv-node .label {{
      margin-top: 6px; font-size: 11.5px; color: #24292f; text-align: center;
      max-width: 110px; line-height: 1.25; text-shadow: 0 1px 3px rgba(255,255,255,.8);
    }}
    #{component_id} .n8nv-node .disabled-tag {{
      font-size: 9.5px; color: #ff8a80; margin-top: 1px;
    }}
    #{component_id} .n8nv-port {{
      position: absolute; width: 9px; height: 9px; border-radius: 50%;
      background: #8b949e; border: 2px solid var(--bg); top: 50%; transform: translateY(-50%);
    }}
    #{component_id} .n8nv-port.in {{ left: -7px; }}
    #{component_id} .n8nv-port.out {{ right: -7px; }}
    #{component_id} .n8nv-node.trigger .n8nv-port.in {{ display: none; }}

    #{component_id} .n8nv-sticky {{
      position: absolute; background: rgba(245,215,110,.2); border: 1px solid rgba(245,215,110,.4);
      border-radius: 8px; padding: 10px 12px; color: #5a4e1a; font-size: 12px;
      overflow: hidden; white-space: pre-wrap; line-height: 1.4;
    }}

    #{component_id} .n8nv-panel {{
      width: 300px; min-width: 300px; background: var(--panel-bg); border-left: 1px solid var(--panel-border);
      padding: 16px; overflow-y: auto; color: var(--text-main); font-size: 12.5px; direction: ltr;
    }}
    #{component_id} .n8nv-panel h4 {{ margin: 0 0 4px 0; font-size: 15px; }}
    #{component_id} .n8nv-panel .badge {{
      display: inline-block; padding: 2px 9px; border-radius: 10px; font-size: 10.5px; color: #111; margin-bottom: 10px;
    }}
    #{component_id} .n8nv-panel .row {{ margin-bottom: 8px; color: var(--text-dim); }}
    #{component_id} .n8nv-panel .row b {{ color: var(--text-main); }}
    #{component_id} .n8nv-panel pre {{
      background: #f6f8fa; border: 1px solid var(--panel-border); border-radius: 6px;
      padding: 8px; font-size: 11px; overflow-x: auto; color: #24292f; white-space: pre-wrap; word-break: break-word;
    }}
    #{component_id} .n8nv-panel .empty {{ color: var(--text-dim); font-size: 12.5px; margin-top: 30px; text-align: center; }}
  </style>

  <div class="n8nv-toolbar">
    <input type="text" id="{component_id}_search" placeholder="جستجوی نام نود...">
    <button id="{component_id}_zin" title="بزرگ‌نمایی">+</button>
    <button id="{component_id}_zout" title="کوچک‌نمایی">−</button>
    <button id="{component_id}_fit" title="تناسب با صفحه">⤢</button>
  </div>
  <div class="n8nv-cat-panel" id="{component_id}_cats"></div>

  <div class="n8nv-viewport" id="{component_id}_viewport">
    <div class="n8nv-world" id="{component_id}_world">
      <svg class="n8nv-edges" id="{component_id}_svg"></svg>
      <div id="{component_id}_nodes"></div>
    </div>
  </div>

  <div class="n8nv-panel" id="{component_id}_panel">
    <div class="empty">روی یکی از نودها کلیک کنید تا جزئیاتش اینجا نمایش داده شود.</div>
  </div>
</div>

<script>
(function() {{
  const DATA = {data_json};
  const ns = "{component_id}";
  const $ = (id) => document.getElementById(ns + id);
  const worldEl = $("_world");
  const svgEl = $("_svg");
  const nodesEl = $("_nodes");
  const viewport = $("_viewport");
  const panel = $("_panel");
  const searchInput = $("_search");
  const catPanel = $("_cats");

  svgEl.setAttribute("width", DATA.canvasW);
  svgEl.setAttribute("height", DATA.canvasH);
  worldEl.style.width = DATA.canvasW + "px";
  worldEl.style.height = DATA.canvasH + "px";

  svgEl.innerHTML = '<defs><marker id="' + ns + '_arrow" markerWidth="8" markerHeight="8" ' +
    'refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="#57616f"/></marker></defs>';

  const nodeById = {{}};
  DATA.nodes.forEach(n => nodeById[n.id] = n);

  const hiddenCats = new Set();
  let selectedId = null;

  function portPos(node, side) {{
    if (node.isSticky) return {{x: node.x, y: node.y}};
    const half = DATA.nodeSize / 2;
    return {{ x: node.x + (side === "out" ? DATA.nodeSize + 7 : -7), y: node.y + half }};
  }}

  function edgePathD(sPos, tPos) {{
    const dx = Math.max(Math.abs(tPos.x - sPos.x) * 0.5, 40);
    return `M ${{sPos.x}} ${{sPos.y}} C ${{sPos.x+dx}} ${{sPos.y}}, ${{tPos.x-dx}} ${{tPos.y}}, ${{tPos.x}} ${{tPos.y}}`;
  }}

  function renderEdges() {{
    let out = '<defs><marker id="' + ns + '_arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="#57616f"/></marker></defs>';
    DATA.edges.forEach((e, i) => {{
      const s = nodeById[e.source], t = nodeById[e.target];
      if (!s || !t) return;
      if (hiddenCats.has(s.category) || hiddenCats.has(t.category)) return;
      const sp = portPos(s, "out"), tp = portPos(t, "in");
      out += `<path class="n8nv-edge-path" id="${{ns}}_edge${{i}}" d="${{edgePathD(sp, tp)}}" marker-end="url(#${{ns}}_arrow)"></path>`;
    }});
    svgEl.innerHTML = out;
  }}

  function selectNode(id) {{
    selectedId = id;
    document.querySelectorAll("#" + ns + " .n8nv-node").forEach(el => {{
      el.classList.toggle("selected", el.dataset.id === id);
    }});
    const n = nodeById[id];
    if (!n) {{ panel.innerHTML = '<div class="empty">نودی انتخاب نشده است.</div>'; return; }}
    let paramsHtml = n.parameters && Object.keys(n.parameters).length
      ? '<pre>' + escapeHtml(JSON.stringify(n.parameters, null, 2)) + '</pre>'
      : '<span style="color:var(--text-dim)">پارامتری ندارد.</span>';
    let credsHtml = n.credentials && Object.keys(n.credentials).length
      ? '<div class="row"><b>Credentials:</b></div><pre>' + escapeHtml(JSON.stringify(n.credentials, null, 2)) + '</pre>'
      : '';
    let notesHtml = n.notes ? '<div class="row"><b>یادداشت:</b><br>' + escapeHtml(n.notes) + '</div>' : '';
    panel.innerHTML = `
      <span class="badge" style="background:${{n.color}}">${{escapeHtml(n.category)}}</span>
      <h4>${{escapeHtml(n.name)}}</h4>
      <div class="row"><b>نوع:</b> ${{escapeHtml(n.type)}}</div>
      <div class="row"><b>نسخه:</b> ${{n.typeVersion || "—"}}</div>
      <div class="row"><b>وضعیت:</b> ${{n.disabled ? "غیرفعال" : "فعال"}}</div>
      ${{notesHtml}}
      <div class="row"><b>پارامترها:</b></div>
      ${{paramsHtml}}
      ${{credsHtml}}
    `;
  }}

  function escapeHtml(s) {{
    return String(s).replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]);
  }}

  function renderNodes() {{
    let out = "";
    DATA.nodes.forEach(n => {{
      if (n.isSticky) {{
        out += `<div class="n8nv-sticky" data-id="${{n.id}}" style="left:${{n.x}}px; top:${{n.y}}px; width:${{n.stickyW}}px; height:${{n.stickyH}}px;">${{escapeHtml(n.stickyContent || n.name)}}</div>`;
        return;
      }}
      const cls = ["n8nv-node"];
      if (n.isTrigger) cls.push("trigger");
      if (n.disabled) cls.push("disabled");
      out += `
        <div class="${{cls.join(' ')}}" data-id="${{n.id}}" data-category="${{escapeHtml(n.category)}}" style="left:${{n.x}}px; top:${{n.y}}px;">
          <div class="box" style="background:${{shade(n.color)}}; color:#fff;">
            <div class="accent" style="background:${{n.color}}"></div>
            <span style="position:relative;">${{n.glyph}}</span>
            <div class="n8nv-port in"></div>
            <div class="n8nv-port out"></div>
          </div>
          <div class="label">${{escapeHtml(n.name)}}${{n.disabled ? '<div class="disabled-tag">غیرفعال</div>' : ''}}</div>
        </div>`;
    }});
    nodesEl.innerHTML = out;

    nodesEl.querySelectorAll(".n8nv-node").forEach(el => {{
      el.addEventListener("mousedown", ev => ev.stopPropagation());
      el.addEventListener("click", ev => {{
        ev.stopPropagation();
        selectNode(el.dataset.id);
      }});
    }});
  }}

  function shade(hex) {{
    // پس‌زمینه‌ی تیره‌تر برای جعبه آیکون بر پایه رنگ دسته
    return "#f0f2f5";
  }}

  function renderCategoryFilter() {{
    let out = "";
    DATA.categories.forEach(cat => {{
      const id = ns + "_cat_" + cat.replace(/[^a-zA-Z0-9]/g, "");
      out += `<label><input type="checkbox" checked data-cat="${{escapeHtml(cat)}}" class="${{ns}}_catbox"> ${{escapeHtml(cat)}}</label>`;
    }});
    catPanel.innerHTML = out;
    catPanel.querySelectorAll("input[type=checkbox]").forEach(cb => {{
      cb.addEventListener("change", () => {{
        const cat = cb.dataset.cat;
        if (cb.checked) hiddenCats.delete(cat); else hiddenCats.add(cat);
        applyFilters();
      }});
    }});
  }}

  function applyFilters() {{
    const term = (searchInput.value || "").trim().toLowerCase();
    nodesEl.querySelectorAll(".n8nv-node, .n8nv-sticky").forEach(el => {{
      const id = el.dataset.id;
      const n = nodeById[id];
      const catHidden = hiddenCats.has(n.category);
      const searchMiss = term && !n.name.toLowerCase().includes(term);
      el.style.display = catHidden ? "none" : "";
      el.classList.toggle("dimmed", !catHidden && !!searchMiss);
    }});
    renderEdges();
  }}

  searchInput.addEventListener("input", applyFilters);

  // ---- Pan & Zoom ----
  let scale = 1, tx = 40, ty = 20;
  let dragging = false, lastX = 0, lastY = 0;

  function applyTransform() {{
    worldEl.style.transform = `translate(${{tx}}px, ${{ty}}px) scale(${{scale}})`;
  }}

  viewport.addEventListener("mousedown", e => {{
    dragging = true; lastX = e.clientX; lastY = e.clientY;
    viewport.classList.add("dragging");
  }});
  window.addEventListener("mouseup", () => {{ dragging = false; viewport.classList.remove("dragging"); }});
  window.addEventListener("mousemove", e => {{
    if (!dragging) return;
    tx += (e.clientX - lastX); ty += (e.clientY - lastY);
    lastX = e.clientX; lastY = e.clientY;
    applyTransform();
  }});
  viewport.addEventListener("wheel", e => {{
    e.preventDefault();
    const delta = e.deltaY > 0 ? -0.08 : 0.08;
    const newScale = Math.min(2.2, Math.max(0.15, scale + delta));
    const rect = viewport.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    tx = mx - ((mx - tx) / scale) * newScale;
    ty = my - ((my - ty) / scale) * newScale;
    scale = newScale;
    applyTransform();
  }}, {{passive: false}});

  $("_zin").addEventListener("click", () => {{ scale = Math.min(2.2, scale + 0.15); applyTransform(); }});
  $("_zout").addEventListener("click", () => {{ scale = Math.max(0.15, scale - 0.15); applyTransform(); }});
  $("_fit").addEventListener("click", () => {{
    const rect = viewport.getBoundingClientRect();
    const sx = rect.width / DATA.canvasW, sy = rect.height / DATA.canvasH;
    scale = Math.min(1, Math.min(sx, sy)) * 0.95;
    tx = 20; ty = 20;
    applyTransform();
  }});

  renderNodes();
  renderCategoryFilter();
  renderEdges();
  applyTransform();
  setTimeout(() => $("_fit").click(), 60);
}})();
</script>
"""

components.html(html_code, height=CANVAS_HEIGHT + 4, scrolling=False)


# ----------------------------------------------------------------------------
# لیست کامل نودها و خروجی خام JSON (بخش‌های تکمیلی، پایین صفحه)
# ----------------------------------------------------------------------------
with st.expander("📋 لیست کامل نودها"):
    rows = []
    for n in nodes_raw:
        label_fa, _, _ = categorize(n.get("type", ""))
        rows.append(
            {
                "نام": n.get("name"),
                "دسته": label_fa,
                "نوع فنی": n.get("type"),
                "غیرفعال": "بله" if n.get("disabled") else "خیر",
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)

with st.expander("🧾 خروجی خام JSON (فقط خواندنی، مقادیر حساس ماسک شده)"):
    st.json(mask_sensitive(data))

st.caption(
    f"بارگذاری‌شده در {datetime.now().strftime('%Y-%m-%d %H:%M')} — "
    "این ابزار صرفاً برای مشاهده است و هیچ workflow ای را اجرا نمی‌کند."
)
